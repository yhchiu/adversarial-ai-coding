#!/usr/bin/env bash
#
# auto-workflow.sh — SDD/TDD 雙 AI 互審自動化工作流
#
# 每個 stage 的流程:
#   工作者執行工作 → 審查者審查
#     → 未通過:工作者依 .workflow/review.md 修改 → 審查者再審(即「最後確認」)
#     → 通過:工作者以 conventional commit 提交 → 進入下一個 stage
#
# 用法:
#   ./auto-workflow.sh "任務描述"
#   ./auto-workflow.sh 任務描述.md      # 參數是存在的檔案時,讀取檔案內容當任務
#   ./auto-workflow.sh print-agents    # 輸出 AGENTS.md 規範範本後結束
#
# 環境變數(皆可選):
#   ENGINE_A     工作者引擎:claude | codex | agy   (預設 claude)
#   ENGINE_B     審查者引擎:claude | codex | agy   (預設 codex)
#   MODEL_A      A 槽引擎的模型(例 haiku / gpt-5.1-codex-mini;預設用 CLI 預設)
#   MODEL_B      B 槽引擎的模型;A、B 同為 claude 時 MODEL_A 優先
#   MAX_ROUNDS   每個 stage 最多審查輪數            (預設 3)
#   AUTO_BRANCH  1=自動開新 branch;0=用目前 branch (預設 1)
#   USE_WORKTREE 1=在獨立 git worktree 中執行(比 branch 更隔離,預設 0)
#   SPEC_DIR     規格與計畫的存放目錄               (預設 specs/<執行時間戳>)
#   TOOLS        Claude Code 的 --allowedTools 清單
#   GATE_CMD     確定性品質關卡指令(build+lint+test,預設依專案自動偵測)
#   BUILD_GATE_CMD  逐任務的輕量關卡(只驗編譯,預設依專案自動偵測)
#   HUMAN_GATE   1=規格通過審查後暫停等人工核准(預設 1;無人值守請設 0)
#   OPEN_PR      1=結尾自動 push 並開 GitHub PR(預設 0,只印出指令)
#   NOTIFY_CMD   通知指令,訊息以第一個參數傳入(例:NOTIFY_CMD="ntfy publish mytopic")
#   AGENTS_TEMPLATE  AGENTS.md 範本路徑(預設:script 同目錄的 AGENTS.template.md)
set -Eeuo pipefail

# ---------- 設定 ----------
ENGINE_A="${ENGINE_A:-claude}"
ENGINE_B="${ENGINE_B:-codex}"
MODEL_A="${MODEL_A:-}"   # A 槽引擎的模型覆寫(空 = 各 CLI 的預設模型)
MODEL_B="${MODEL_B:-}"   # B 槽引擎的模型覆寫
MAX_ROUNDS="${MAX_ROUNDS:-3}"
AUTO_BRANCH="${AUTO_BRANCH:-1}"
USE_WORKTREE="${USE_WORKTREE:-0}"
HUMAN_GATE="${HUMAN_GATE:-1}"
OPEN_PR="${OPEN_PR:-0}"
NOTIFY_CMD="${NOTIFY_CMD:-}"
# 最小權限:只放行 git 與明確的建置/測試指令。
# 注意 Bash(go *) 會包含 go run(任意程式碼執行),不要圖方便放寬。
TOOLS="${TOOLS:-Bash(git *),Bash(go test *),Bash(go build *),Bash(go vet *)}"

RUN_ID="$(date +%Y%m%d-%H%M%S)"
SPEC_DIR="${SPEC_DIR:-specs/$RUN_ID}"
WF=".workflow"
LOGS="$WF/logs"
LOG="$LOGS/$RUN_ID.log"
METRICS="$WF/metrics.csv"

usage() {
  {
    echo "用法:$0 \"任務描述\""
    echo "      $0 任務描述.md      # 參數是存在的檔案時,讀取檔案內容當任務"
    echo "      $0 print-agents    # 輸出 AGENTS.md 規範範本後結束"
  } >&2
  exit 1
}

need() { command -v "$1" >/dev/null 2>&1 || { echo "缺少必要指令:$1" >&2; exit 1; }; }

notify() {  # $1: 訊息;NOTIFY_CMD 會以第一個參數收到訊息
  [[ -z "$NOTIFY_CMD" ]] && return 0
  $NOTIFY_CMD "$1" || echo "(通知指令執行失敗:$NOTIFY_CMD)" >&2
}

metric() {  # $1: 角色  $2: 引擎  $3: 輪次  $4: 秒數  $5: 費用(USD,可空)
  [[ -d "$WF" ]] || return 0
  [[ -f "$METRICS" ]] || echo "run_id,stage,role,engine,round,duration_s,cost_usd" > "$METRICS"
  echo "$RUN_ID,$CUR_STAGE,$1,$2,$3,$4,$5" >> "$METRICS"
}

# ---------- 純函式 helpers(可被測試 source) ----------
engine_model() {  # $1: 引擎;輸出該引擎所屬槽位的模型覆寫(未設定輸出空)
  if [[ "$1" == "$ENGINE_A" && -n "$MODEL_A" ]]; then
    echo "$MODEL_A"
  elif [[ "$1" == "$ENGINE_B" && -n "$MODEL_B" ]]; then
    echo "$MODEL_B"
  fi
}

verdict_approved() {  # $1: verdict.json 路徑;0 = 審查通過
  [[ -f "$1" ]] && jq -e '.approved == true' "$1" >/dev/null 2>&1
}

detect_gate() {  # 依專案類型偵測完整品質關卡(build+lint+test);偵測不到輸出空字串
  if [[ -f go.mod ]]; then
    echo "go build ./... && go vet ./... && go test ./..."
  elif [[ -f package.json ]] && jq -e '.scripts.test // empty' package.json >/dev/null 2>&1; then
    echo "npm test"
  elif [[ -f Cargo.toml ]]; then
    echo "cargo test"
  fi
}

detect_build_gate() {  # 逐任務的輕量關卡:只驗編譯,容忍驗收測試還是紅燈
  if [[ -f go.mod ]]; then
    echo "go build ./..."
  elif [[ -f Cargo.toml ]]; then
    echo "cargo build"
  fi
}

protected_violations() {  # $1: 受保護清單檔  $2: 基準 commit;輸出被改動的受保護檔
  [[ -f "$1" && -s "$1" ]] || return 0
  git diff --name-only "$2" -- 2>/dev/null | grep -Fx -f "$1" || true
}

plan_tasks() {  # $1: plan.md;每行輸出一個未完成任務(去掉「- [ ] 」前綴)
  [[ -f "$1" ]] || return 0
  grep -E '^- \[ \] ' "$1" | sed -E 's/^- \[ \] //' || true
}

# ---------- AGENTS.md:三種引擎共用的互審規範(跨工具標準檔) ----------
# 規範內文放在獨立的 AGENTS.template.md 方便維護;以簡單英文撰寫,
# 對各家模型最通用(避免個別模型中文能力不足)。
AGENTS_MARKER='<!-- auto-workflow:begin -->'
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENTS_TEMPLATE="${AGENTS_TEMPLATE:-$SCRIPT_DIR/AGENTS.template.md}"

write_agents_section() {  # 輸出規範範本;找不到範本檔時回傳 1
  if [[ ! -f "$AGENTS_TEMPLATE" ]]; then
    echo "(找不到 AGENTS.md 範本:$AGENTS_TEMPLATE;請把 AGENTS.template.md 與 script 放在同一目錄,或用 AGENTS_TEMPLATE 指定路徑)" >&2
    return 1
  fi
  cat "$AGENTS_TEMPLATE"
}

bootstrap_agents_md() {  # 缺檔才建立;既有檔案不動,只提示(避免覆蓋使用者內容)
  if [[ -f AGENTS.md ]]; then
    grep -qF "$AGENTS_MARKER" AGENTS.md \
      || echo "(提示:AGENTS.md 已存在但沒有 auto-workflow 規範段;可用「$0 print-agents」輸出範本後手動合併)" >&2
  elif write_agents_section > AGENTS.md; then
    echo "已產生 AGENTS.md(auto-workflow 互審規範)"
  else
    rm -f AGENTS.md   # 範本遺失:別留下空檔;警告已印出,流程照常繼續
    return 0
  fi
  [[ -f CLAUDE.md ]] || printf 'Follow the auto-workflow cross-review rules in AGENTS.md.\n' > CLAUDE.md
}

# ---------- 工作者引擎(同一 stage 內延續 session,跨 stage 重置) ----------
WORKER_SESSION=""
LAST_COST=""   # claude 引擎會回報 total_cost_usd;其他引擎留空

w_claude() {
  local args=(--output-format json --permission-mode acceptEdits --allowedTools "$TOOLS")
  local m; m=$(engine_model claude)
  [[ -n "$m" ]] && args+=(--model "$m")
  [[ -n "$WORKER_SESSION" ]] && args+=(--resume "$WORKER_SESSION")
  local out
  out=$(claude -p "$1" "${args[@]}")
  WORKER_SESSION=$(jq -r '.session_id' <<<"$out")
  LAST_COST=$(jq -r '.total_cost_usd // empty' <<<"$out")
  jq -r '.result // empty' <<<"$out"
}

w_codex() {
  local m margs=()
  m=$(engine_model codex)
  [[ -n "$m" ]] && margs=(-c "model=\"$m\"")   # -c 在 exec 與 resume 都通用
  if [[ -z "$WORKER_SESSION" ]]; then
    codex exec --sandbox workspace-write "${margs[@]}" "$1"
    WORKER_SESSION="last"
  else
    # exec resume 沒有 --sandbox 旗標,改用 -c 覆寫設定
    codex exec resume --last -c 'sandbox_mode="workspace-write"' "${margs[@]}" "$1"
  fi
}

w_agy() {
  # 注意:--dangerously-skip-permissions 會自動核准所有工具操作,
  # 建議只在隔離的 branch / 容器內使用(見 README 安全性一節)。
  local args=(--print-timeout 60m --dangerously-skip-permissions)
  local m; m=$(engine_model agy)
  [[ -n "$m" ]] && args+=(--model "$m")
  [[ -n "$WORKER_SESSION" ]] && args+=(--continue)
  agy --print "$1" "${args[@]}"
  WORKER_SESSION="continue"
}

work() {  # $1: 引擎  $2: 工作指示
  local t0=$SECONDS
  LAST_COST=""
  echo ">>> 工作者($1)執行中…"
  # 用 process substitution 而非 pipeline:引擎函式留在當前 shell,
  # WORKER_SESSION 的賦值才不會因 subshell 而遺失(session 續接才有效)。
  "w_$1" "$2" > >(tee -a "$LOG")
  metric worker "$1" "$CUR_ROUND" "$(( SECONDS - t0 ))" "$LAST_COST"
  # 每次工作者動作後硬性檢查受保護測試檔;旗標防止回復動作本身造成遞迴
  if (( ! CHECKING_PROTECTED )); then
    check_protected "$1"
  fi
}

# ---------- 受保護的驗收測試(對抗式 TDD) ----------
# 驗收測試由審查者撰寫、清單記錄於 protected-tests.txt;實作階段工作者一律禁改,
# script 用 git diff 硬性檢查——提示詞約束擋不住 reward hacking,diff 擋得住。
CHECKING_PROTECTED=0

check_protected() {  # $1: 工作者引擎;受保護檔被改動時強制回復,屢犯即中止
  local base viol n=0
  [[ -f "$WF/protected-tests.txt" && -f "$WF/protected-base.sha" ]] || return 0
  base=$(cat "$WF/protected-base.sha")
  while viol=$(protected_violations "$WF/protected-tests.txt" "$base"); [[ -n "$viol" ]]; do
    { echo "!! 受保護的驗收測試檔被改動:"; sed 's/^/  - /' <<<"$viol"; } | tee -a "$LOG" >&2
    if (( n >= 2 )); then
      echo "!! 工作者多次改動受保護測試檔仍未回復,停止,需人工介入。" >&2
      notify "auto-workflow:[$CUR_STAGE] 受保護測試檔遭改動且未回復,需人工介入"
      exit 1
    fi
    n=$(( n + 1 ))
    CHECKING_PROTECTED=1
    work "$1" "你改動了受保護的驗收測試檔(工作流規範禁止):
$viol
請立刻用 git 把這些檔案完整回復到 commit $base 的內容(例如 git checkout $base -- <檔案>),並提交這個回復。若你認為測試本身有誤,把異議記錄在 $SPEC_DIR/spec.md 的「Assumptions and Open Questions」一節,但不得修改測試檔。"
    CHECKING_PROTECTED=0
  done
}

# ---------- 審查者引擎(每輪全新 context,靠檔案與 diff 取得狀態) ----------
# 裁決分級:blocker = 必須修正才放行;suggestion = 不擋關,累積到收尾階段統一評估。
VERDICT_SCHEMA='{"type":"object","properties":{"approved":{"type":"boolean"},"blockers":{"type":"array","items":{"type":"string"}},"suggestions":{"type":"array","items":{"type":"string"}}},"required":["approved","blockers","suggestions"]}'

review_prompt() {  # $1: 本輪審查範圍
  cat <<EOF
你是嚴格的程式審查者。本輪審查範圍:$1

遵循專案 AGENTS.md 中的「auto-workflow 互審規範」。重點規則:
- 只審查與驗證(可以執行測試),除了 $WF/review.md 與 $WF/verdict.json 之外不要修改任何檔案。
- 把審查意見逐條寫入 $WF/review.md(直接覆蓋舊內容);若通過,寫下簡短的通過理由。
- 若 review.md 中已有工作者對前輪意見的回覆,先逐條確認回覆是否成立。
- 裁決分兩級:blocker(正確性錯誤、不符規格、測試被弱化、安全問題等必須修正者)
  與 suggestion(不必立即處理的改善建議)。只有零 blocker 才可判定通過;
  suggestion 不擋關,但要如實列出。
EOF
}

verdict_file_instr() {
  echo "- 最後把裁決寫入 $WF/verdict.json,單行 JSON,格式:{\"approved\": true|false, \"blockers\": [\"必須修正的問題\"], \"suggestions\": [\"不擋關的建議\"]}。"
}

r_claude() {
  local out args=()
  local m; m=$(engine_model claude)
  [[ -n "$m" ]] && args+=(--model "$m")
  out=$(claude -p "$(review_prompt "$1")" "${args[@]}" \
    --output-format json --permission-mode acceptEdits --allowedTools "$TOOLS" \
    --json-schema "$VERDICT_SCHEMA")
  LAST_COST=$(jq -r '.total_cost_usd // empty' <<<"$out")
  jq -c '.structured_output // {approved: false, blockers: ["審查者未產出結構化裁決"], suggestions: []}' <<<"$out" > "$WF/verdict.json"
  jq -r '.result // empty' <<<"$out"
}

r_codex() {
  local m margs=()
  m=$(engine_model codex)
  [[ -n "$m" ]] && margs=(-c "model=\"$m\"")
  codex exec --sandbox workspace-write "${margs[@]}" "$(review_prompt "$1")
$(verdict_file_instr)"
}

r_agy() {
  local m margs=()
  m=$(engine_model agy)
  [[ -n "$m" ]] && margs=(--model "$m")
  agy --print "$(review_prompt "$1")
$(verdict_file_instr)" --print-timeout 30m --dangerously-skip-permissions "${margs[@]}"
}

collect_suggestions() {  # 把本輪 suggestions 累積到 backlog,收尾階段統一評估
  [[ -f "$WF/verdict.json" ]] || return 0
  local s
  s=$(jq -r '.suggestions[]? // empty' "$WF/verdict.json" 2>/dev/null) || return 0
  [[ -z "$s" ]] && return 0
  {
    echo "## ${CUR_STAGE}(第 ${CUR_ROUND} 輪)"
    sed 's/^/- /' <<<"$s"
    echo
  } >> "$WF/suggestions.md"
}

show_blockers() {
  [[ -f "$WF/verdict.json" ]] || return 0
  echo "審查未通過,blockers:" | tee -a "$LOG"
  jq -r '.blockers[]? // empty' "$WF/verdict.json" 2>/dev/null | sed 's/^/  - /' | tee -a "$LOG"
}

run_review() {  # $1: 引擎  $2: 審查範圍;回傳 0 = 通過
  local t0=$SECONDS
  LAST_COST=""
  echo ">>> 審查者($1)審查中…"
  rm -f "$WF/verdict.json"
  "r_$1" "$2" > >(tee -a "$LOG") || echo "(警告:審查者執行失敗)" >&2
  metric reviewer "$1" "$CUR_ROUND" "$(( SECONDS - t0 ))" "$LAST_COST"
  if [[ ! -f "$WF/verdict.json" ]]; then
    echo "(審查者未產出 verdict.json,視為未通過)" >&2
    return 1
  fi
  collect_suggestions
  if ! verdict_approved "$WF/verdict.json"; then
    show_blockers
    return 1
  fi
}

# ---------- 確定性品質關卡 ----------
# 業界共識:能用機器驗證的一律由 script 直接執行,AI 的「測試通過」回報只當參考。
gate_loop() {  # $1: 工作者引擎  $2: 關卡指令(空字串 = 跳過);失敗輸出餵給工作者修
  local engine="$1" cmd="$2" n=1 out
  [[ -z "$cmd" ]] && return 0
  while true; do
    echo ">>> 品質關卡:$cmd"
    if out=$(bash -c "$cmd" 2>&1); then
      echo "品質關卡通過 ✔" | tee -a "$LOG"
      return 0
    fi
    printf '%s\n' "$out" >> "$LOG"
    echo "品質關卡失敗(第 $n 次)" | tee -a "$LOG"
    if (( n >= MAX_ROUNDS )); then
      echo "!! [$CUR_STAGE] 品質關卡連續 $MAX_ROUNDS 次未過,停止,需人工介入。輸出:" >&2
      printf '%s\n' "$out" | tail -50 >&2
      notify "auto-workflow:[$CUR_STAGE] 品質關卡連續失敗,需人工介入"
      exit 1
    fi
    n=$(( n + 1 ))
    work "$engine" "品質關卡指令「$cmd」失敗,以下是輸出(只保留最後 150 行)。請修正問題,直到這個指令能通過:
$(printf '%s\n' "$out" | tail -150)"
  done
}

# ---------- stage 流程 ----------
CUR_STAGE=""
CUR_ROUND=1

begin_stage() {  # $1: 名稱;工作者在 stage 內延續 session、跨 stage 重置
  CUR_STAGE="$1"
  WORKER_SESSION=""
  CUR_ROUND=1
  printf '\n================ [%s] ================\n' "$1" | tee -a "$LOG"
}

review_loop() {  # $1: 審查者引擎  $2: 工作者引擎  $3: 審查範圍  $4: 修正輪的關卡指令(可省略)
  local gate_cmd="${4:-}"
  CUR_ROUND=1
  until run_review "$1" "$3"; do
    if (( CUR_ROUND >= MAX_ROUNDS )); then
      echo "!! [$CUR_STAGE] 已審 $MAX_ROUNDS 輪仍未通過,停止。請閱讀 $WF/review.md 後人工處理。" >&2
      notify "auto-workflow:[$CUR_STAGE] 審查 $MAX_ROUNDS 輪未過,需人工介入"
      exit 1
    fi
    CUR_ROUND=$(( CUR_ROUND + 1 ))
    echo "--- [$CUR_STAGE] 第 $CUR_ROUND 輪:工作者依審查意見修改 ---" | tee -a "$LOG"
    work "$2" "審查意見在 $WF/review.md。依 AGENTS.md 的互審規範逐條回應:同意就修正,並在該條目下方註明「已修正:<摘要>」;不同意就回覆「不同意:<理由>」,不得默默忽略。修改後確保測試通過。"
    gate_loop "$2" "$gate_cmd"   # 修正後同樣要過確定性關卡,再交回審查
  done
  echo "[$CUR_STAGE] 審查通過 ✔" | tee -a "$LOG"
}

commit_work() {  # $1: 工作者引擎  $2: 本次提交的內容說明
  work "$1" "$2 已完成並通過審查。依 AGENTS.md 的 commit 規範提交目前所有變更:conventional commit、簡單易懂的英文訊息、body 詳細記錄完成了哪些工作、不加 Co-Authored-By。"
  ensure_committed
}

ensure_committed() {  # 工作者漏提交時由 script 補提交,確保流程狀態一致
  if [[ -n "$(git status --porcelain)" ]]; then
    echo "(工作者未完整提交,由 script 補提交)" >&2
    git add -A
    git commit -m "chore: commit remaining ${CUR_STAGE} changes" \
      -m "Auto-committed by auto-workflow because the worker left uncommitted changes."
  fi
}

commit_if_dirty() {  # $1: 引擎  $2: 說明;沒有變更就跳過,不浪費一次 AI 呼叫
  [[ -z "$(git status --porcelain)" ]] && return 0
  commit_work "$1" "$2"
}

# ---------- 人工檢查點 ----------
# 規格是錯誤放大器:spec 錯一行,後面的 stage 會忠實地放大成幾百行程式碼,
# 所以人工核准放在最高槓桿處——spec 通過 AI 互審之後、開始花大錢實作之前。
human_gate_spec() {
  [[ "$HUMAN_GATE" == "1" ]] || return 0
  notify "auto-workflow:規格待人工核准($SPEC_DIR/spec.md)"
  echo ""
  echo "### 人工檢查點:請審閱 $SPEC_DIR/spec.md,特別是「Assumptions and Open Questions(假設與未決問題)」一節。"
  echo "### 可直接編輯該檔後再繼續,你的改動會一併提交。"
  if ! { : </dev/tty; } 2>/dev/null; then
    echo "!! 沒有互動終端可供核准。請在互動終端執行,或設 HUMAN_GATE=0 跳過(不建議)。" >&2
    exit 1
  fi
  local ans
  read -rp "輸入 y 核准並繼續,其他任意輸入中止:" ans </dev/tty
  if [[ "$ans" != "y" && "$ans" != "Y" ]]; then
    echo "已中止:規格未獲人工核准。" >&2
    exit 1
  fi
  echo "規格已人工核准 ✔" | tee -a "$LOG"
}

# ---------- 收尾:交棒給人 ----------
# 流程的終點是「等人 merge 的 PR」,不是靜默結束;預設只印指令(OPEN_PR=1 才執行)。
finish() {  # $1: 任務描述
  local branch title
  branch=$(git rev-parse --abbrev-ref HEAD)
  title=$(head -1 <<<"$1")
  title="${title:0:72}"   # 以字元計截斷(cut -c 以 byte 計,會把多位元組字切爛)

  cat > "$WF/pr-body.md" <<EOF
## 任務

$1

## 產物

- 規格(含假設與未決問題):\`$SPEC_DIR/spec.md\`
- 實作計畫:\`$SPEC_DIR/plan.md\`

由 auto-workflow(雙 AI 互審:A=$ENGINE_A、B=$ENGINE_B)產生;
每個 stage 均通過確定性品質關卡與交叉審查,驗收測試由審查方撰寫且受改動保護。
EOF

  printf '\n全部 stage 完成 🎉  規格與計畫在 %s/,執行紀錄在 %s\n' "$SPEC_DIR" "$LOG"
  if [[ -f "$METRICS" ]]; then
    echo ""
    echo "本次執行統計(明細:$METRICS;輪數是提示詞品質的量化訊號):"
    awk -F, 'NR>1 { calls[$2]++; secs[$2]+=$6; cost[$2]+=$7; if ($5>r[$2]) r[$2]=$5 }
      END { for (s in calls) printf "  %-14s AI呼叫 %d 次,審查 %d 輪,%d 秒,$%.4f\n", s, calls[s], r[s], secs[s], cost[s] }' \
      "$METRICS"
  fi
  if [[ "$OPEN_PR" == "1" ]] && command -v gh >/dev/null 2>&1 && git remote get-url origin >/dev/null 2>&1; then
    git push -u origin "$branch"
    gh pr create --title "$title" --body-file "$WF/pr-body.md"
  else
    echo ""
    echo "下一步(人工執行):"
    echo "  git push -u origin $branch"
    echo "  gh pr create --title \"$title\" --body-file $WF/pr-body.md"
    [[ "$OPEN_PR" == "1" ]] && echo "(OPEN_PR=1 但缺 gh 或 origin remote,已改為只印指令)" >&2
  fi
  notify "auto-workflow:全部 stage 完成($branch)"
}

setup_workspace() {  # 依 USE_WORKTREE / AUTO_BRANCH 準備隔離的工作區
  if [[ "$USE_WORKTREE" == "1" ]]; then
    local root name wt
    root=$(git rev-parse --show-toplevel)
    name=$(basename "$root")
    wt="$root/../${name}-auto-$RUN_ID"
    git worktree add -b "auto/$RUN_ID" "$wt"
    cd "$wt"
    echo "已建立 worktree:$wt(branch auto/$RUN_ID;結束後可用 git worktree remove 清理)"
  elif [[ "$AUTO_BRANCH" == "1" ]]; then
    git switch -c "auto/$RUN_ID"
    echo "已建立並切換到 branch:auto/$RUN_ID"
  fi
}

# ---------- 主流程 ----------
main() {
  local task="${1:-}"
  [[ -n "$task" ]] || usage

  if [[ "$task" == "print-agents" ]]; then
    write_agents_section
    return 0
  fi
  if [[ -f "$task" ]]; then
    echo "從檔案讀取任務描述:$task"
    task="$(cat "$task")"
  fi

  need git; need jq
  local e
  for e in "$ENGINE_A" "$ENGINE_B"; do
    case "$e" in
      claude|codex|agy) need "$e" ;;
      *) echo "不支援的引擎:$e(可用:claude | codex | agy)" >&2; exit 1 ;;
    esac
  done

  git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || { echo "請在目標專案的 git repo 根目錄執行本 script" >&2; exit 1; }

  # codex 與 agy 都是用「續接最近一次 session」來延續對話,
  # 若 A、B 用同一套,審查 session 會蓋掉「最近一次」,讓工作者續接到錯的對話。
  if [[ "$ENGINE_A" == "$ENGINE_B" && "$ENGINE_A" != "claude" ]]; then
    echo "A 與 B 不能同時是 $ENGINE_A(session 續接會互相干擾),請改用不同引擎。" >&2
    exit 1
  fi

  echo "工作流設定:A=$ENGINE_A  B=$ENGINE_B  MAX_ROUNDS=$MAX_ROUNDS  規格目錄=$SPEC_DIR"
  echo "任務:$task"

  setup_workspace   # 可能 cd 進 worktree,之後的相對路徑都以工作區為準

  mkdir -p "$LOGS" "$SPEC_DIR"
  echo '*' > "$WF/.gitignore"   # 讓 .workflow/ 整個目錄不進版控

  trap 'echo "!! 工作流中斷(exit=$?)。完整過程見 '"$LOG"'" >&2' ERR

  bootstrap_agents_md

  GATE_CMD="${GATE_CMD:-$(detect_gate)}"
  BUILD_GATE_CMD="${BUILD_GATE_CMD:-$(detect_build_gate)}"
  if [[ -n "$GATE_CMD" ]]; then
    echo "品質關卡:$GATE_CMD"
  else
    echo "(警告:偵測不到品質關卡指令,確定性關卡停用;可用 GATE_CMD 環境變數指定)" >&2
  fi

  begin_stage "訂規格"
  work "$ENGINE_A" "為以下需求撰寫規格,存到 $SPEC_DIR/spec.md,內容須包含:功能描述、驗收條件(可測試)、邊界情況、不做的範圍,以及「Assumptions and Open Questions(假設與未決問題)」一節——非互動模式下你無法向人提問,所有自行假設之處都必須誠實列在這一節,不得默默腦補。需求:$task"
  review_loop "$ENGINE_B" "$ENGINE_A" "$SPEC_DIR/spec.md:需求完整性、驗收條件是否可測試、邊界情況是否有遺漏;逐條檢視「Assumptions and Open Questions」,假設不合理或該列未列的,列為 blocker。"
  human_gate_spec
  commit_work "$ENGINE_A" "規格(已通過審查與人工核准)"

  begin_stage "規劃實作計畫"
  work "$ENGINE_A" "依 $SPEC_DIR/spec.md 撰寫實作計畫,存到 $SPEC_DIR/plan.md,內容須包含:實作任務清單(每項一行、用「- [ ] 」checkbox 格式、可獨立實作與驗證、將逐項對應一個 commit)、測試策略(判斷需要 unit / integration / E2E 中的哪些並說明理由)。"
  review_loop "$ENGINE_B" "$ENGINE_A" "$SPEC_DIR/plan.md(對照 $SPEC_DIR/spec.md):可行性、測試涵蓋度、任務切分是否夠小且各自獨立、checkbox 格式是否正確。"
  commit_work "$ENGINE_A" "實作計畫"

  # 對抗式 TDD:由審查方(B)依規格寫驗收測試,工作方(A)審查——
  # 出題者與答題者分離,實作階段 A 禁改這些測試(script 硬性檢查)。
  begin_stage "撰寫驗收測試"
  local test_base
  test_base=$(git rev-parse HEAD)
  work "$ENGINE_B" "依 $SPEC_DIR/spec.md 的驗收條件撰寫驗收測試,放在專案慣例的測試位置。現在還沒有實作,測試可以編譯失敗或紅燈——這是 TDD 的紅燈階段。不要撰寫任何產品程式碼,也不要修改 $SPEC_DIR 下的檔案。"
  review_loop "$ENGINE_A" "$ENGINE_B" "驗收測試(檢視 git diff $test_base 之後的變更):是否完整覆蓋 $SPEC_DIR/spec.md 的每一條驗收條件、測試本身是否正確、有無夾帶產品程式碼。"
  commit_work "$ENGINE_B" "驗收測試"
  git diff --name-only "$test_base" HEAD | grep -v "^$SPEC_DIR/" > "$WF/protected-tests.txt" || true
  git rev-parse HEAD > "$WF/protected-base.sha"
  if [[ -s "$WF/protected-tests.txt" ]]; then
    { echo "受保護的驗收測試檔:"; sed 's/^/  - /' "$WF/protected-tests.txt"; } | tee -a "$LOG"
  else
    echo "(警告:驗收測試 stage 沒有產出任何檔案,測試保護機制停用)" >&2
  fi

  # 小批次原則:一個任務一個 commit,批次越小、審查與回退都越容易
  begin_stage "撰寫程式碼"
  local tasks=() t i=1
  mapfile -t tasks < <(plan_tasks "$SPEC_DIR/plan.md")
  if (( ${#tasks[@]} == 0 )); then
    echo "(警告:plan.md 沒有「- [ ] 」任務清單,退回整包實作)" >&2
    tasks=("依 $SPEC_DIR/plan.md 完成全部實作")
  fi
  for t in "${tasks[@]}"; do
    echo "--- 任務 $i/${#tasks[@]}:$t ---" | tee -a "$LOG"
    work "$ENGINE_A" "依 $SPEC_DIR/plan.md 實作這個任務:$t
以 TDD 方式進行,執行與本任務相關的測試並確認通過(整體驗收測試在所有任務完成前允許紅燈)。可以新增自己的單元測試,但不得修改受保護的驗收測試檔($WF/protected-tests.txt)。完成後把 plan.md 中該任務改成「- [x]」。"
    gate_loop "$ENGINE_A" "$BUILD_GATE_CMD"
    commit_work "$ENGINE_A" "任務「$t」"
    i=$(( i + 1 ))
  done

  echo "--- 全部任務完成,執行完整品質關卡(驗收測試須全綠)---" | tee -a "$LOG"
  gate_loop "$ENGINE_A" "$GATE_CMD"
  review_loop "$ENGINE_B" "$ENGINE_A" "本 branch 目前的程式變更(用 git diff 與 git log 檢視):程式碼品質、是否符合 $SPEC_DIR/spec.md、實際執行測試驗證;並對照 $WF/protected-tests.txt 檢視測試 diff,確認驗收測試未被弱化或繞過。" "$GATE_CMD"
  commit_if_dirty "$ENGINE_A" "審查修正"

  begin_stage "整體 review 與修 bug"
  work "$ENGINE_A" "對本 branch 的所有變更做一次完整的自我 review:1) 修掉發現的問題、補上遺漏的測試;2) 若 $WF/suggestions.md 存在,逐條評估歷輪審查累積的建議——採納就實作,不採納就在該條目下方註明理由;3) 執行完整測試套件確認全數通過。"
  gate_loop "$ENGINE_A" "$GATE_CMD"
  review_loop "$ENGINE_B" "$ENGINE_A" "最終驗收:對照 $SPEC_DIR/spec.md 逐項確認整個 branch 的行為與品質,實際執行完整測試,並確認驗收測試未被弱化。" "$GATE_CMD"
  commit_if_dirty "$ENGINE_A" "最終修正"

  finish "$task"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
