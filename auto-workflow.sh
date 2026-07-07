#!/usr/bin/env bash
#
# auto-workflow.sh — SDD/TDD 雙 AI 互審自動化工作流
#
# 每個 stage 的流程:
#   A(工作者)執行工作 → B(審查者)審查
#     → 未通過:A 依 .workflow/review.md 修改 → B 再審(即「最後確認」)
#     → 通過:A 以 conventional commit 提交 → 進入下一個 stage
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
#   TOOLS        Claude Code 的 --allowedTools 清單 (預設 git 與 go 指令)
set -Eeuo pipefail

TASK="${1:-}"
if [[ -z "$TASK" ]]; then
  echo "用法:$0 \"任務描述\"" >&2
  exit 1
fi

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

# ---------- 前置檢查 ----------
need() { command -v "$1" >/dev/null 2>&1 || { echo "缺少必要指令:$1" >&2; exit 1; }; }
need git; need jq
for e in "$ENGINE_A" "$ENGINE_B"; do
  case "$e" in
    claude|codex|agy) need "$e" ;;
    *) echo "不支援的引擎:$e(可用:claude | codex | agy)" >&2; exit 1 ;;
  esac
done

git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || { echo "請在目標專案的 git repo 根目錄執行本 script" >&2; exit 1; }

# codex 與 agy 都是用「續接最近一次 session」來延續對話,
# 若 A、B 用同一套,B 的審查 session 會蓋掉「最近一次」,讓 A 續接到錯的對話。
if [[ "$ENGINE_A" == "$ENGINE_B" && "$ENGINE_A" != "claude" ]]; then
  echo "A 與 B 不能同時是 $ENGINE_A(session 續接會互相干擾),請改用不同引擎。" >&2
  exit 1
fi

mkdir -p "$LOGS" "$SPEC_DIR"
echo '*' > "$WF/.gitignore"   # 讓 .workflow/ 整個目錄不進版控

trap 'echo "!! 工作流中斷(exit=$?)。完整過程見 $LOG" >&2' ERR

# ---------- A:工作者(同一 stage 內延續 session,跨 stage 重置) ----------
A_SESSION=""

a() {  # $1: 給 A 的工作指示
  echo ">>> A($ENGINE_A)工作中…"
  "a_$ENGINE_A" "$1" | tee -a "$LOG"
}

a_claude() {
  local args=(--output-format json --permission-mode acceptEdits --allowedTools "$TOOLS")
  [[ -n "$A_SESSION" ]] && args+=(--resume "$A_SESSION")
  local out
  out=$(claude -p "$1" "${args[@]}")
  A_SESSION=$(jq -r '.session_id' <<<"$out")
  jq -r '.result // empty' <<<"$out"
}

a_codex() {
  if [[ -z "$A_SESSION" ]]; then
    codex exec --sandbox workspace-write "$1"
    A_SESSION="last"
  else
    # exec resume 沒有 --sandbox 旗標,改用 -c 覆寫設定
    codex exec resume --last -c 'sandbox_mode="workspace-write"' "$1"
  fi
}

a_agy() {
  # 注意:--dangerously-skip-permissions 會自動核准所有工具操作,
  # 建議只在隔離的 branch / 容器內使用(見 README 安全性一節)。
  local args=(--print-timeout 60m --dangerously-skip-permissions)
  [[ -n "$A_SESSION" ]] && args+=(--continue)
  agy --print "$1" "${args[@]}"
  A_SESSION="continue"
}

# ---------- B:審查者(每輪全新 context,靠檔案與 diff 取得狀態) ----------
VERDICT_SCHEMA='{"type":"object","properties":{"approved":{"type":"boolean"}},"required":["approved"]}'

review_prompt() {  # $1: 本輪審查範圍
  cat <<EOF
你是嚴格的程式審查者。本輪審查範圍:$1

規則:
- 只審查與驗證(可以執行測試),除了 $WF/review.md 與 $WF/verdict.json 之外不要修改任何檔案。
- 把審查意見逐條寫入 $WF/review.md(直接覆蓋舊內容);若通過,寫下簡短的通過理由。
- 若 review.md 中已有工作者對前輪意見的回覆,先逐條確認回覆是否成立。
- 只有在沒有任何「必須修正」的問題時才判定通過;建議性的小改進不擋關,但要記在 review.md。
EOF
}

b_review() {  # $1: 審查範圍;回傳 0 = 通過
  echo ">>> B($ENGINE_B)審查中…"
  rm -f "$WF/verdict.json"
  "b_$ENGINE_B" "$1" | tee -a "$LOG" || echo "(警告:B 執行失敗)" >&2
  if [[ ! -f "$WF/verdict.json" ]]; then
    echo "(B 未產出 verdict.json,視為未通過)" >&2
    return 1
  fi
  jq -e '.approved == true' "$WF/verdict.json" >/dev/null
}

b_claude() {
  local out
  out=$(claude -p "$(review_prompt "$1")" \
    --output-format json --permission-mode acceptEdits --allowedTools "$TOOLS" \
    --json-schema "$VERDICT_SCHEMA")
  jq -c '.structured_output // {approved: false}' <<<"$out" > "$WF/verdict.json"
  jq -r '.result // empty' <<<"$out"
}

b_codex() {
  codex exec --sandbox workspace-write "$(review_prompt "$1")
- 最後把裁決寫入 $WF/verdict.json,內容只有一行:{\"approved\": true} 或 {\"approved\": false}。"
}

b_agy() {
  agy --print "$(review_prompt "$1")
- 最後把裁決寫入 $WF/verdict.json,內容只有一行:{\"approved\": true} 或 {\"approved\": false}。" \
    --print-timeout 30m --dangerously-skip-permissions
}

# ---------- 一個 stage = 偽碼中的一輪 for loop ----------
stage() {  # $1: 名稱  $2: A 的工作指示  $3: B 的審查範圍
  local name="$1" work="$2" review="$3" round=1
  A_SESSION=""   # 每個 stage A 從乾淨 context 開始,靠 $SPEC_DIR 的檔案接續
  printf '\n================ [%s] ================\n' "$name" | tee -a "$LOG"

  a "$work"

  until b_review "$review"; do
    if (( round >= MAX_ROUNDS )); then
      echo "!! [$name] 已審 $MAX_ROUNDS 輪仍未通過,停止。請閱讀 $WF/review.md 後人工處理。" >&2
      exit 1
    fi
    round=$(( round + 1 ))
    echo "--- [$name] 第 $round 輪:A 依審查意見修改 ---" | tee -a "$LOG"
    a "審查者的意見在 $WF/review.md。逐條評估:同意就修正,並在該條目下方註明改了什麼;不同意就在該條目下方回覆理由。修改後確保測試通過。"
  done

  echo "[$name] 審查通過 ✔" | tee -a "$LOG"
  a "本階段「${name}」已通過審查。請用 conventional commit 格式提交本階段的所有變更:訊息用簡單易懂的英文,body 詳細記錄完成了哪些工作,不要加 Co-Authored-By。"
}

# ---------- 主流程 ----------
echo "工作流設定:A=$ENGINE_A  B=$ENGINE_B  MAX_ROUNDS=$MAX_ROUNDS  規格目錄=$SPEC_DIR"
echo "任務:$TASK"

if [[ "$AUTO_BRANCH" == "1" ]]; then
  git switch -c "auto/$RUN_ID"
  echo "已建立並切換到 branch:auto/$RUN_ID"
fi

stage "訂規格" \
  "為以下需求撰寫規格,存到 $SPEC_DIR/spec.md,內容須包含:功能描述、驗收條件(可測試)、邊界情況與不做的範圍。需求:$TASK" \
  "$SPEC_DIR/spec.md:需求完整性、驗收條件是否可測試、邊界情況是否有遺漏。"

stage "規劃實作計畫" \
  "依 $SPEC_DIR/spec.md 撰寫實作計畫,存到 $SPEC_DIR/plan.md,內容須包含:實作步驟、測試策略(判斷需要 unit / integration / E2E 中的哪些並說明理由)、commit 切分方式。" \
  "$SPEC_DIR/plan.md(對照 $SPEC_DIR/spec.md):可行性、測試涵蓋度、步驟與 commit 切分是否合理。"

stage "撰寫程式碼" \
  "依 $SPEC_DIR/plan.md 以 TDD 方式實作:先寫測試、再實作,直到所有測試通過。" \
  "本 branch 目前的程式變更(用 git diff 與 git log 檢視):程式碼品質、是否符合 $SPEC_DIR/spec.md,並實際執行測試驗證。"

stage "整體 review 與修 bug" \
  "對本 branch 的所有變更做一次完整的自我 review:修掉發現的問題、補上遺漏的測試,並執行完整測試套件確認全數通過。" \
  "最終驗收:對照 $SPEC_DIR/spec.md 逐項確認整個 branch 的行為與品質,實際執行完整測試。"

printf '\n全部 stage 完成 🎉  規格與計畫在 %s/,執行紀錄在 %s\n' "$SPEC_DIR" "$LOG"
