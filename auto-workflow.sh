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
#
# 環境變數(皆可選):
#   ENGINE_A     工作者引擎:claude | codex | agy   (預設 claude)
#   ENGINE_B     審查者引擎:claude | codex | agy   (預設 codex)
#   MAX_ROUNDS   每個 stage 最多審查輪數            (預設 3)
#   AUTO_BRANCH  1=自動開新 branch;0=用目前 branch (預設 1)
#   SPEC_DIR     規格與計畫的存放目錄               (預設 specs/<執行時間戳>)
#   TOOLS        Claude Code 的 --allowedTools 清單
#   GATE_CMD     確定性品質關卡指令(build+lint+test,預設依專案自動偵測)
#   BUILD_GATE_CMD  逐任務的輕量關卡(只驗編譯,預設依專案自動偵測)
set -Eeuo pipefail

# ---------- 設定 ----------
ENGINE_A="${ENGINE_A:-claude}"
ENGINE_B="${ENGINE_B:-codex}"
MAX_ROUNDS="${MAX_ROUNDS:-3}"
AUTO_BRANCH="${AUTO_BRANCH:-1}"
TOOLS="${TOOLS:-Bash(git *),Bash(go *)}"

RUN_ID="$(date +%Y%m%d-%H%M%S)"
SPEC_DIR="${SPEC_DIR:-specs/$RUN_ID}"
WF=".workflow"
LOGS="$WF/logs"
LOG="$LOGS/$RUN_ID.log"

usage() {
  echo "用法:$0 \"任務描述\"" >&2
  exit 1
}

need() { command -v "$1" >/dev/null 2>&1 || { echo "缺少必要指令:$1" >&2; exit 1; }; }

# ---------- 純函式 helpers(可被測試 source) ----------
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

# ---------- 工作者引擎(同一 stage 內延續 session,跨 stage 重置) ----------
WORKER_SESSION=""

w_claude() {
  local args=(--output-format json --permission-mode acceptEdits --allowedTools "$TOOLS")
  [[ -n "$WORKER_SESSION" ]] && args+=(--resume "$WORKER_SESSION")
  local out
  out=$(claude -p "$1" "${args[@]}")
  WORKER_SESSION=$(jq -r '.session_id' <<<"$out")
  jq -r '.result // empty' <<<"$out"
}

w_codex() {
  if [[ -z "$WORKER_SESSION" ]]; then
    codex exec --sandbox workspace-write "$1"
    WORKER_SESSION="last"
  else
    # exec resume 沒有 --sandbox 旗標,改用 -c 覆寫設定
    codex exec resume --last -c 'sandbox_mode="workspace-write"' "$1"
  fi
}

w_agy() {
  # 注意:--dangerously-skip-permissions 會自動核准所有工具操作,
  # 建議只在隔離的 branch / 容器內使用(見 README 安全性一節)。
  local args=(--print-timeout 60m --dangerously-skip-permissions)
  [[ -n "$WORKER_SESSION" ]] && args+=(--continue)
  agy --print "$1" "${args[@]}"
  WORKER_SESSION="continue"
}

work() {  # $1: 引擎  $2: 工作指示
  echo ">>> 工作者($1)執行中…"
  # 用 process substitution 而非 pipeline:引擎函式留在當前 shell,
  # WORKER_SESSION 的賦值才不會因 subshell 而遺失(session 續接才有效)。
  "w_$1" "$2" > >(tee -a "$LOG")
}

# ---------- 審查者引擎(每輪全新 context,靠檔案與 diff 取得狀態) ----------
# 裁決分級:blocker = 必須修正才放行;suggestion = 不擋關,累積到收尾階段統一評估。
VERDICT_SCHEMA='{"type":"object","properties":{"approved":{"type":"boolean"},"blockers":{"type":"array","items":{"type":"string"}},"suggestions":{"type":"array","items":{"type":"string"}}},"required":["approved","blockers","suggestions"]}'

review_prompt() {  # $1: 本輪審查範圍
  cat <<EOF
你是嚴格的程式審查者。本輪審查範圍:$1

規則:
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
  local out
  out=$(claude -p "$(review_prompt "$1")" \
    --output-format json --permission-mode acceptEdits --allowedTools "$TOOLS" \
    --json-schema "$VERDICT_SCHEMA")
  jq -c '.structured_output // {approved: false, blockers: ["審查者未產出結構化裁決"], suggestions: []}' <<<"$out" > "$WF/verdict.json"
  jq -r '.result // empty' <<<"$out"
}

r_codex() {
  codex exec --sandbox workspace-write "$(review_prompt "$1")
$(verdict_file_instr)"
}

r_agy() {
  agy --print "$(review_prompt "$1")
$(verdict_file_instr)" --print-timeout 30m --dangerously-skip-permissions
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
  echo ">>> 審查者($1)審查中…"
  rm -f "$WF/verdict.json"
  "r_$1" "$2" > >(tee -a "$LOG") || echo "(警告:審查者執行失敗)" >&2
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
      exit 1
    fi
    CUR_ROUND=$(( CUR_ROUND + 1 ))
    echo "--- [$CUR_STAGE] 第 $CUR_ROUND 輪:工作者依審查意見修改 ---" | tee -a "$LOG"
    work "$2" "審查意見在 $WF/review.md。逐條回應:同意就修正,並在該條目下方註明改了什麼;不同意就在該條目下方回覆理由。修改後確保測試通過。"
    gate_loop "$2" "$gate_cmd"   # 修正後同樣要過確定性關卡,再交回審查
  done
  echo "[$CUR_STAGE] 審查通過 ✔" | tee -a "$LOG"
}

commit_work() {  # $1: 工作者引擎  $2: 本次提交的內容說明
  work "$1" "$2 已完成並通過審查。請用 conventional commit 格式提交目前所有變更:訊息用簡單易懂的英文,body 詳細記錄完成了哪些工作,不要加 Co-Authored-By。"
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

# ---------- 主流程 ----------
main() {
  local task="${1:-}"
  [[ -n "$task" ]] || usage

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

  mkdir -p "$LOGS" "$SPEC_DIR"
  echo '*' > "$WF/.gitignore"   # 讓 .workflow/ 整個目錄不進版控

  trap 'echo "!! 工作流中斷(exit=$?)。完整過程見 '"$LOG"'" >&2' ERR

  echo "工作流設定:A=$ENGINE_A  B=$ENGINE_B  MAX_ROUNDS=$MAX_ROUNDS  規格目錄=$SPEC_DIR"
  echo "任務:$task"

  if [[ "$AUTO_BRANCH" == "1" ]]; then
    git switch -c "auto/$RUN_ID"
    echo "已建立並切換到 branch:auto/$RUN_ID"
  fi

  GATE_CMD="${GATE_CMD:-$(detect_gate)}"
  BUILD_GATE_CMD="${BUILD_GATE_CMD:-$(detect_build_gate)}"
  if [[ -n "$GATE_CMD" ]]; then
    echo "品質關卡:$GATE_CMD"
  else
    echo "(警告:偵測不到品質關卡指令,確定性關卡停用;可用 GATE_CMD 環境變數指定)" >&2
  fi

  begin_stage "訂規格"
  work "$ENGINE_A" "為以下需求撰寫規格,存到 $SPEC_DIR/spec.md,內容須包含:功能描述、驗收條件(可測試)、邊界情況與不做的範圍。需求:$task"
  review_loop "$ENGINE_B" "$ENGINE_A" "$SPEC_DIR/spec.md:需求完整性、驗收條件是否可測試、邊界情況是否有遺漏。"
  commit_work "$ENGINE_A" "規格"

  begin_stage "規劃實作計畫"
  work "$ENGINE_A" "依 $SPEC_DIR/spec.md 撰寫實作計畫,存到 $SPEC_DIR/plan.md,內容須包含:實作步驟、測試策略(判斷需要 unit / integration / E2E 中的哪些並說明理由)、commit 切分方式。"
  review_loop "$ENGINE_B" "$ENGINE_A" "$SPEC_DIR/plan.md(對照 $SPEC_DIR/spec.md):可行性、測試涵蓋度、步驟與 commit 切分是否合理。"
  commit_work "$ENGINE_A" "實作計畫"

  begin_stage "撰寫程式碼"
  work "$ENGINE_A" "依 $SPEC_DIR/plan.md 以 TDD 方式實作:先寫測試、再實作,直到所有測試通過。"
  gate_loop "$ENGINE_A" "$GATE_CMD"
  review_loop "$ENGINE_B" "$ENGINE_A" "本 branch 目前的程式變更(用 git diff 與 git log 檢視):程式碼品質、是否符合 $SPEC_DIR/spec.md,並實際執行測試驗證。" "$GATE_CMD"
  commit_work "$ENGINE_A" "程式實作"

  begin_stage "整體 review 與修 bug"
  work "$ENGINE_A" "對本 branch 的所有變更做一次完整的自我 review:1) 修掉發現的問題、補上遺漏的測試;2) 若 $WF/suggestions.md 存在,逐條評估歷輪審查累積的建議——採納就實作,不採納就在該條目下方註明理由;3) 執行完整測試套件確認全數通過。"
  gate_loop "$ENGINE_A" "$GATE_CMD"
  review_loop "$ENGINE_B" "$ENGINE_A" "最終驗收:對照 $SPEC_DIR/spec.md 逐項確認整個 branch 的行為與品質,實際執行完整測試。" "$GATE_CMD"
  commit_work "$ENGINE_A" "最終修正"

  printf '\n全部 stage 完成 🎉  規格與計畫在 %s/,執行紀錄在 %s\n' "$SPEC_DIR" "$LOG"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
