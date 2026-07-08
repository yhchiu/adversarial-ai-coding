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
#   MODEL_A      A 槽引擎的模型(例 haiku;預設用 CLI 預設)
#   MODEL_B      B 槽引擎的模型;A、B 同為 claude 時 MODEL_A 優先
#   CLAUDE_ARGS / CODEX_ARGS / AGY_ARGS  各 CLI 的額外參數(空白切割後附加)
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
#   RETRY_ON_LIMIT   1=撞用量限額/429 時等待後重試(預設 1;0=直接失敗)
#   RETRY_MAX        每次引擎呼叫的限額重試上限(預設 6)
#   RETRY_BASE_WAIT  解析不到 reset 時間時的初始等待秒數,指數成長(預設 300)
#   RETRY_MAX_WAIT   指數退避的單次等待上限秒數(預設 3600)
#   RUNS_DIR         每次 run 的 archive 目錄根目錄         (預設 .workflow/runs)
set -Eeuo pipefail

# ---------- 設定 ----------
ENGINE_A="${ENGINE_A:-claude}"
ENGINE_B="${ENGINE_B:-codex}"
MODEL_A="${MODEL_A:-}"   # A 槽引擎的模型覆寫(空 = 各 CLI 的預設模型)
MODEL_B="${MODEL_B:-}"   # B 槽引擎的模型覆寫
# 各 CLI 的額外參數(依空白切割後原樣附加;例:CODEX_ARGS='-c model_reasoning_effort=low')
CLAUDE_ARGS="${CLAUDE_ARGS:-}"
CODEX_ARGS="${CODEX_ARGS:-}"
AGY_ARGS="${AGY_ARGS:-}"
MAX_ROUNDS="${MAX_ROUNDS:-3}"
AUTO_BRANCH="${AUTO_BRANCH:-1}"
USE_WORKTREE="${USE_WORKTREE:-0}"
HUMAN_GATE="${HUMAN_GATE:-1}"
OPEN_PR="${OPEN_PR:-0}"
NOTIFY_CMD="${NOTIFY_CMD:-}"
# 限額退避重試:限額是時間窗問題不是品質問題,等待重試才不會燒掉審查輪數
RETRY_ON_LIMIT="${RETRY_ON_LIMIT:-1}"
RETRY_MAX="${RETRY_MAX:-6}"
RETRY_BASE_WAIT="${RETRY_BASE_WAIT:-300}"
RETRY_MAX_WAIT="${RETRY_MAX_WAIT:-3600}"
# 最小權限:只放行 git 與明確的建置/測試指令。
# 注意 Bash(go *) 會包含 go run(任意程式碼執行),不要圖方便放寬。
TOOLS="${TOOLS:-Bash(git *),Bash(go test *),Bash(go build *),Bash(go vet *)}"

RUN_ID="$(date +%Y%m%d-%H%M%S)"
SPEC_DIR="${SPEC_DIR:-specs/$RUN_ID}"
WF=".workflow"
RUNS_DIR="${RUNS_DIR:-$WF/runs}"
WF_RUN="${WF_RUN:-}"
LOGS="${LOGS:-$WF/logs}"
LOG="${LOG:-$LOGS/$RUN_ID.log}"
METRICS="${METRICS:-$WF/metrics.csv}"
ENGINE_OUT="$WF/last-engine-output.txt"   # 每次引擎呼叫的輸出落地,供限額偵測用
ART_SEQ="${ART_SEQ:-0}"

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
  local model model_args generated
  [[ -d "$(dirname "$METRICS")" ]] || return 0
  [[ -f "$METRICS" ]] || echo "run_id,stage,role,engine,round,duration_s,cost_usd,model,model_args,generated_at" > "$METRICS"
  model=$(engine_model "$2")
  model_args=$(resolve_model_args "$2")
  generated=$(generated_at)
  write_csv_row "$METRICS" "$RUN_ID" "$CUR_STAGE" "$1" "$2" "$3" "$4" "$5" "$model" "$model_args" "$generated"
}

# ---------- 純函式 helpers(可被測試 source) ----------
generated_at() {
  if [[ -n "${WF_NOW:-}" ]]; then
    echo "$WF_NOW"
  else
    date '+%Y-%m-%dT%H:%M:%S%z'
  fi
}

safe_slug() {
  local s="$1"
  s="${s//\//-}"
  s="${s//\\/-}"
  s="${s// /-}"
  s="${s//:/-}"
  s="${s//;/-}"
  s="${s//|/-}"
  s="${s//</-}"
  s="${s//>/-}"
  s="${s//\"/-}"
  s="${s//\'/-}"
  printf '%s' "$s"
}

resolve_model_args() {
  case "$1" in
    claude) echo "$CLAUDE_ARGS" ;;
    codex) echo "$CODEX_ARGS" ;;
    agy) echo "$AGY_ARGS" ;;
    *) echo "" ;;
  esac
}

csv_row() {
  local first=1 field
  for field in "$@"; do
    [[ "$first" == "1" ]] || printf ','
    first=0
    field="${field//\"/\"\"}"
    printf '"%s"' "$field"
  done
  printf '\n'
}

write_csv_row() {
  local file="$1"
  shift
  csv_row "$@" >> "$file"
}

art_path() {
  local name="$1" seq_file seq
  [[ -n "$WF_RUN" ]] || { echo "WF_RUN is not set" >&2; return 1; }
  mkdir -p "$WF_RUN"
  seq_file="$WF_RUN/.artifact-seq"
  if [[ -f "$seq_file" ]]; then
    seq=$(cat "$seq_file")
  else
    seq="$ART_SEQ"
  fi
  seq=$(( seq + 1 ))
  printf '%s\n' "$seq" > "$seq_file"
  ART_SEQ="$seq"
  printf '%s/%03d-%s\n' "$WF_RUN" "$seq" "$name"
}

write_meta() {  # $1: artifact  $2: role  $3: engine  $4: model  $5: model_args  $6: stage  $7: round
  local artifact="$1" role="${2:-workflow}" engine="${3:-workflow}" model="${4:-}" model_args="${5:-}" stage="${6:-${CUR_STAGE:-startup}}" round="${7:-${CUR_ROUND:-0}}"
  jq -n \
    --arg generated_at "$(generated_at)" \
    --arg generator_role "$role" \
    --arg engine "$engine" \
    --arg model "$model" \
    --arg model_args "$model_args" \
    --arg stage "$stage" \
    --arg round "$round" \
    --arg run_id "$RUN_ID" \
    --arg artifact "$artifact" \
    '{generated_at:$generated_at,generator_role:$generator_role,engine:$engine,model:$model,model_args:$model_args,stage:$stage,round:$round,run_id:$run_id,artifact:$artifact}' \
    > "$artifact.meta.json"
}

archive_snapshot() {  # $1: source  $2: archive name  $3: role  $4: engine  $5: stage  $6: round
  local src="$1" name="$2" role="${3:-workflow}" engine="${4:-workflow}" stage="${5:-${CUR_STAGE:-startup}}" round="${6:-${CUR_ROUND:-0}}" dst model model_args
  [[ -f "$src" ]] || return 0
  dst=$(art_path "$name")
  cp "$src" "$dst"
  model=$(engine_model "$engine")
  model_args=$(resolve_model_args "$engine")
  write_meta "$dst" "$role" "$engine" "$model" "$model_args" "$stage" "$round"
  echo "$dst"
}

archive_text() {  # $1: archive name  $2: text  $3: role  $4: engine  $5: stage  $6: round
  local name="$1" text="$2" role="${3:-workflow}" engine="${4:-workflow}" stage="${5:-${CUR_STAGE:-startup}}" round="${6:-${CUR_ROUND:-0}}" dst model model_args
  dst=$(art_path "$name")
  printf '%s\n' "$text" > "$dst"
  model=$(engine_model "$engine")
  model_args=$(resolve_model_args "$engine")
  write_meta "$dst" "$role" "$engine" "$model" "$model_args" "$stage" "$round"
  echo "$dst"
}

archive_task() {  # $1: arg  $2: kind  $3: source path  $4: resolved text
  local task_arg="$1" kind="$2" source_path="$3" resolved="$4" src_art task_art
  src_art=$(art_path "task-source.md")
  {
    echo "# Task Source"
    echo
    echo "- kind: $kind"
    echo "- argument: $task_arg"
    if [[ "$kind" == "file" ]]; then
      echo "- path: $source_path"
    fi
    echo
    echo '```'
    printf '%s\n' "$resolved"
    echo '```'
  } > "$src_art"
  write_meta "$src_art" "workflow" "workflow" "" "" "startup" "0"

  task_art=$(art_path "task.txt")
  printf '%s\n' "$resolved" > "$task_art"
  write_meta "$task_art" "workflow" "workflow" "" "" "startup" "0"
}

archive_git_state() {  # $1: role  $2: engine  $3: artifact slug
  local role="${1:-worker}" engine="${2:-workflow}" slug="${3:-git-state}" status_art diff_art f model model_args
  [[ -n "$WF_RUN" ]] || return 0
  git rev-parse --is-inside-work-tree >/dev/null 2>&1 || return 0
  status_art=$(art_path "${slug}-git-status.txt")
  git status --porcelain > "$status_art"
  model=$(engine_model "$engine")
  model_args=$(resolve_model_args "$engine")
  write_meta "$status_art" "$role" "$engine" "$model" "$model_args" "$CUR_STAGE" "$CUR_ROUND"

  diff_art=$(art_path "${slug}-git-diff.patch")
  {
    echo "# git diff --binary HEAD --"
    git diff --binary HEAD -- || true
    echo
    echo "# untracked files"
    while IFS= read -r -d '' f; do
      echo
      echo "## $f"
      git diff --no-index --binary -- /dev/null "$f" || true
    done < <(git ls-files --others --exclude-standard -z)
  } > "$diff_art"
  write_meta "$diff_art" "$role" "$engine" "$model" "$model_args" "$CUR_STAGE" "$CUR_ROUND"
}

abs_path() {
  if command -v realpath >/dev/null 2>&1; then
    realpath "$1"
  else
    ( cd "$(dirname "$1")" && printf '%s/%s\n' "$(pwd -P)" "$(basename "$1")" )
  fi
}

establish_run_archive() {
  local base candidate n=2
  base="$RUNS_DIR/$RUN_ID"
  candidate="$base"
  while [[ -e "$candidate" ]]; do
    candidate="$base-$n"
    n=$(( n + 1 ))
  done
  WF_RUN="$candidate"
  LOGS="$WF_RUN/logs"
  LOG="$LOGS/001-run.log"
  METRICS="$WF_RUN/metrics.csv"
  mkdir -p "$WF" "$LOGS" "$SPEC_DIR"
}

write_run_metadata() {
  local dst
  dst=$(art_path "run-metadata.json")
  jq -n \
    --arg generated_at "$(generated_at)" \
    --arg run_id "$RUN_ID" \
    --arg spec_dir "$SPEC_DIR" \
    --arg wf "$WF" \
    --arg runs_dir "$RUNS_DIR" \
    --arg wf_run "$WF_RUN" \
    --arg log "$LOG" \
    --arg metrics "$METRICS" \
    --arg engine_a "$ENGINE_A" \
    --arg model_a "$(engine_model "$ENGINE_A")" \
    --arg args_a "$(resolve_model_args "$ENGINE_A")" \
    --arg engine_b "$ENGINE_B" \
    --arg model_b "$(engine_model "$ENGINE_B")" \
    --arg args_b "$(resolve_model_args "$ENGINE_B")" \
    --arg max_rounds "$MAX_ROUNDS" \
    --arg auto_branch "$AUTO_BRANCH" \
    --arg use_worktree "$USE_WORKTREE" \
    '{generated_at:$generated_at,run_id:$run_id,spec_dir:$spec_dir,wf:$wf,runs_dir:$runs_dir,wf_run:$wf_run,log:$log,metrics:$metrics,engine_a:$engine_a,model_a:$model_a,args_a:$args_a,engine_b:$engine_b,model_b:$model_b,args_b:$args_b,max_rounds:$max_rounds,auto_branch:$auto_branch,use_worktree:$use_worktree}' \
    > "$dst"
}

write_log_metadata() {
  mkdir -p "$(dirname "$LOG")"
  jq -n \
    --arg generated_at "$(generated_at)" \
    --arg generator_role "workflow" \
    --arg run_id "$RUN_ID" \
    --arg log_path "$LOG" \
    --arg engine_a "$ENGINE_A" \
    --arg model_a "$(engine_model "$ENGINE_A")" \
    --arg args_a "$(resolve_model_args "$ENGINE_A")" \
    --arg engine_b "$ENGINE_B" \
    --arg model_b "$(engine_model "$ENGINE_B")" \
    --arg args_b "$(resolve_model_args "$ENGINE_B")" \
    '{generated_at:$generated_at,generator_role:$generator_role,run_id:$run_id,log_path:$log_path,engine_a:$engine_a,model_a:$model_a,args_a:$args_a,engine_b:$engine_b,model_b:$model_b,args_b:$args_b}' \
    > "$LOG.meta.json"
}

log_section() {  # $1: title  $2: role  $3: engine  $4: stage  $5: round
  local title="$1" role="${2:-workflow}" engine="${3:-workflow}" stage="${4:-${CUR_STAGE:-startup}}" round="${5:-${CUR_ROUND:-0}}" model model_args ts
  ts=$(generated_at)
  model=$(engine_model "$engine")
  model_args=$(resolve_model_args "$engine")
  mkdir -p "$(dirname "$LOG")"
  {
    echo
    echo "--------------------------------------------------------------------------------"
    printf '[%s] %s | role=%s engine=%s model=%s args=%s stage=%s round=%s\n' \
      "$ts" "$title" "$role" "$engine" "$model" "$model_args" "$stage" "$round"
    echo "--------------------------------------------------------------------------------"
  } | tee -a "$LOG"
}

init_live_state() {
  mkdir -p "$WF"
  rm -f \
    "$WF/suggestions.md" \
    "$WF/protected-tests.txt" \
    "$WF/protected-base.sha" \
    "$WF/review.md" \
    "$WF/verdict.json" \
    "$WF/last-engine-output.txt" \
    "$WF/pr-body.md"
}

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

is_rate_limited() {  # $1: 引擎輸出檔;0 = 屬於用量限額/429 類可等待重試的錯誤
  [[ -f "$1" ]] || return 1
  grep -qiE '"api_error_status": *429|(hit|reached) your (session|usage|weekly|rate) limit|rate.?limit|too many requests|status.?429' "$1"
}

parse_reset_wait() {  # $1: 輸出檔  $2: now epoch(測試注入用);輸出等待秒數,解析失敗輸出空
  local f="$1" now="${2:-$(date +%s)}" t target wait m num unit
  [[ -f "$f" ]] || return 0

  # 格式一(claude):「resets 10:50am」→ 等到重置時刻 + 120 秒緩衝
  t=$(grep -oiE 'resets +[0-9]{1,2}:[0-9]{2} ?[ap]m' "$f" | head -1 | sed -E 's/^[Rr]esets +//; s/ //g' || true)
  if [[ -n "$t" ]]; then
    target=$(LC_ALL=C date -d "$t" +%s 2>/dev/null || true)
    if [[ -n "$target" ]]; then
      (( target <= now )) && target=$(( target + 86400 ))
      wait=$(( target - now + 120 ))
      (( wait > 21600 )) && return 0   # 超過 6 小時視為解析異常,改走指數退避
      echo "$wait"
      return 0
    fi
  fi

  # 格式二(OpenAI/codex):「try again in 20s / 2 minutes / 3 hours」→ 等待該時長 + 30 秒緩衝
  m=$(grep -oiE 'try again in [0-9]+(\.[0-9]+)? ?(ms|milliseconds?|seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)\b' "$f" | head -1 || true)
  if [[ -n "$m" ]]; then
    num=$(grep -oE '[0-9]+' <<<"$m" | head -1)
    unit=$(grep -oiE '(ms|milliseconds?|seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)$' <<<"$m")
    case "${unit,,}" in
      ms|millisecond*) wait=1 ;;
      s|sec*|second*)  wait=$num ;;
      m|min*|minute*)  wait=$(( num * 60 )) ;;
      h|hr*|hour*)     wait=$(( num * 3600 )) ;;
      *) return 0 ;;
    esac
    wait=$(( wait + 30 ))
    (( wait > 21600 )) && return 0
    echo "$wait"
    return 0
  fi
  return 0
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
  # shellcheck disable=SC2206  # 刻意依空白切割
  args+=($CLAUDE_ARGS)
  [[ -n "$WORKER_SESSION" ]] && args+=(--resume "$WORKER_SESSION")
  local out rc=0
  out=$(claude -p "$1" "${args[@]}") || rc=$?
  if (( rc != 0 )); then
    # 失敗時務必攤出原始輸出,否則像用量限額這類錯誤會被 $() 吞掉、無從診斷
    printf '%s\n' "$out" >&2
    printf '%s\n' "$out" > "$ENGINE_OUT"   # 供 engine_call 判斷是否為限額錯誤
    echo "(claude 退出碼 $rc,原始輸出如上)" >&2
    return "$rc"
  fi
  printf '%s\n' "$out" > "$ENGINE_OUT"
  WORKER_SESSION=$(jq -r '.session_id' <<<"$out")
  LAST_COST=$(jq -r '.total_cost_usd // empty' <<<"$out")
  jq -r '.result // empty' <<<"$out"
}

w_codex() {
  local m margs=()
  m=$(engine_model codex)
  [[ -n "$m" ]] && margs=(-c "model=\"$m\"")   # -c 在 exec 與 resume 都通用
  # shellcheck disable=SC2206  # 刻意依空白切割
  margs+=($CODEX_ARGS)
  if [[ -z "$WORKER_SESSION" ]]; then
    codex exec --sandbox workspace-write "${margs[@]}" "$1" 2>&1 | tee "$ENGINE_OUT"
    WORKER_SESSION="last"
  else
    # exec resume 沒有 --sandbox 旗標,改用 -c 覆寫設定
    codex exec resume --last -c 'sandbox_mode="workspace-write"' "${margs[@]}" "$1" 2>&1 | tee "$ENGINE_OUT"
  fi
}

w_agy() {
  # 注意:--dangerously-skip-permissions 會自動核准所有工具操作,
  # 建議只在隔離的 branch / 容器內使用(見 README 安全性一節)。
  local args=(--print-timeout 60m --dangerously-skip-permissions)
  local m; m=$(engine_model agy)
  [[ -n "$m" ]] && args+=(--model "$m")
  # shellcheck disable=SC2206  # 刻意依空白切割
  args+=($AGY_ARGS)
  [[ -n "$WORKER_SESSION" ]] && args+=(--continue)
  agy --print "$1" "${args[@]}" 2>&1 | tee "$ENGINE_OUT"
  WORKER_SESSION="continue"
}

# ---------- 限額退避重試 ----------
archive_engine_attempt() {  # $1: role  $2: engine  $3: slug  $4: attempt  $5: rc
  local role="$1" engine="$2" slug="$3" attempt="$4" rc="$5" dst model model_args
  dst=$(art_path "${slug}-attempt-${attempt}-rc${rc}.raw")
  if [[ -f "$ENGINE_OUT" ]]; then
    cp "$ENGINE_OUT" "$dst"
  else
    printf '(ENGINE_OUT was not written for this attempt)\n' > "$dst"
  fi
  model=$(engine_model "$engine")
  model_args=$(resolve_model_args "$engine")
  write_meta "$dst" "$role" "$engine" "$model" "$model_args" "$CUR_STAGE" "$CUR_ROUND"
}

engine_call() {  # $1: role  $2: engine  $3: artifact slug  $4: engine fn  $5: prompt
  local role="$1" engine="$2" slug="$3" fn="$4" prompt="$5"
  local n=0 attempt=1 rc w eta
  while true; do
    rc=0
    "$fn" "$prompt" || rc=$?
    archive_engine_attempt "$role" "$engine" "$slug" "$attempt" "$rc"
    (( rc == 0 )) && return 0
    [[ "$RETRY_ON_LIMIT" == "1" ]] || return "$rc"
    is_rate_limited "$ENGINE_OUT" || return "$rc"
    if (( n >= RETRY_MAX )); then
      echo "!! 用量限額重試 $RETRY_MAX 次仍未恢復,放棄。" >&2
      return "$rc"
    fi
    n=$(( n + 1 ))
    w=$(parse_reset_wait "$ENGINE_OUT")   # 優先精準等到限額重置時刻
    if [[ -z "$w" ]]; then
      w=$(( RETRY_BASE_WAIT * (1 << (n - 1)) ))
      (( w > RETRY_MAX_WAIT )) && w=$RETRY_MAX_WAIT
    fi
    eta=$(date -d "+$w seconds" +%H:%M 2>/dev/null || true)
    log_section "rate limit retry" "$role" "$engine" "$CUR_STAGE" "$CUR_ROUND"
    { echo "== 撞到用量限額,等待 $(( w / 60 )) 分(約 $eta)後進行第 $n/$RETRY_MAX 次重試 =="; } | tee -a "$LOG" >&2
    notify "auto-workflow:撞用量限額,約 $eta 重試(第 $n 次)"
    sleep "$w"
    attempt=$(( attempt + 1 ))
  done
}

work() {  # $1: 引擎  $2: 工作指示
  local t0=$SECONDS prompt_art output_art slug
  LAST_COST=""
  log_section "AI call" "worker" "$1" "$CUR_STAGE" "$CUR_ROUND"
  echo ">>> 工作者($1)執行中…"
  slug="worker-$(safe_slug "${CUR_STAGE:-startup}")-r${CUR_ROUND}"
  archive_text "${slug}-prompt.md" "$2" "worker" "$1" "$CUR_STAGE" "$CUR_ROUND" >/dev/null
  output_art=$(art_path "${slug}-output.txt")
  # 用 process substitution 而非 pipeline:引擎函式留在當前 shell,
  # WORKER_SESSION 的賦值才不會因 subshell 而遺失(session 續接才有效)。
  engine_call "worker" "$1" "$slug" "w_$1" "$2" > >(tee -a "$LOG" | tee "$output_art")
  write_meta "$output_art" "worker" "$1" "$(engine_model "$1")" "$(resolve_model_args "$1")" "$CUR_STAGE" "$CUR_ROUND"
  archive_snapshot "$ENGINE_OUT" "${slug}-final.raw" "worker" "$1" "$CUR_STAGE" "$CUR_ROUND" >/dev/null
  archive_git_state "worker" "$1" "$slug"
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
  log_section "protected check" "workflow" "workflow" "$CUR_STAGE" "$CUR_ROUND"
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
- 讀檔與搜尋一律用你內建的檔案工具,不要用 shell 的 cat/ls/cd(shell 指令受白名單限制,被擋會浪費回合)。
- 只審查與驗證(可以執行測試),除了 $WF/review.md 與 $WF/verdict.json 之外不要修改任何檔案。
- 把審查意見逐條寫入 $WF/review.md(直接覆蓋舊內容);若通過,寫下簡短的通過理由。
- 若 review.md 中已有工作者對前輪意見的回覆,先逐條確認回覆是否成立。
- 裁決分兩級:blocker(正確性錯誤、不符規格、測試被弱化、安全問題等必須修正者)
  與 suggestion(不必立即處理的改善建議)。只有零 blocker 才可判定通過;
  suggestion 不擋關,但要如實列出。
EOF
}

verdict_file_instr() {
  echo "- 最後把裁決寫入 $WF/verdict.json(檔案已存在、內容是「未通過」的預設值,直接覆寫),單行 JSON,格式:{\"approved\": true|false, \"blockers\": [\"必須修正的問題\"], \"suggestions\": [\"不擋關的建議\"]}。"
}

compose_review_prompt() {  # $1: 引擎  $2: 本輪審查範圍
  local engine="$1" scope="$2" prompt
  prompt="$(review_prompt "$scope")"
  case "$engine" in
    claude) printf '%s\n' "$prompt" ;;
    codex|agy) printf '%s\n%s\n' "$prompt" "$(verdict_file_instr)" ;;
    *) printf '%s\n' "$prompt" ;;
  esac
}

r_claude() {
  local out args=()
  local m; m=$(engine_model claude)
  [[ -n "$m" ]] && args+=(--model "$m")
  # shellcheck disable=SC2206  # 刻意依空白切割
  args+=($CLAUDE_ARGS)
  local rc=0
  out=$(claude -p "$1" "${args[@]}" \
    --output-format json --permission-mode acceptEdits --allowedTools "$TOOLS" \
    --json-schema "$VERDICT_SCHEMA") || rc=$?
  if (( rc != 0 )); then
    printf '%s\n' "$out" >&2
    printf '%s\n' "$out" > "$ENGINE_OUT"
    echo "(claude 退出碼 $rc,原始輸出如上)" >&2
    return "$rc"
  fi
  printf '%s\n' "$out" > "$ENGINE_OUT"
  LAST_COST=$(jq -r '.total_cost_usd // empty' <<<"$out")
  jq -c '.structured_output // {approved: false, blockers: ["審查者未產出結構化裁決"], suggestions: []}' <<<"$out" > "$WF/verdict.json"
  jq -r '.result // empty' <<<"$out"
}

r_codex() {
  local m margs=()
  m=$(engine_model codex)
  [[ -n "$m" ]] && margs=(-c "model=\"$m\"")
  # shellcheck disable=SC2206  # 刻意依空白切割
  margs+=($CODEX_ARGS)
  codex exec --sandbox workspace-write "${margs[@]}" "$1" 2>&1 | tee "$ENGINE_OUT"
}

r_agy() {
  local m margs=()
  m=$(engine_model agy)
  [[ -n "$m" ]] && margs=(--model "$m")
  # shellcheck disable=SC2206  # 刻意依空白切割
  margs+=($AGY_ARGS)
  agy --print "$1" --print-timeout 30m --dangerously-skip-permissions "${margs[@]}" 2>&1 | tee "$ENGINE_OUT"
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
  local t0=$SECONDS prompt output_art slug
  LAST_COST=""
  log_section "review" "reviewer" "$1" "$CUR_STAGE" "$CUR_ROUND"
  echo ">>> 審查者($1)審查中…"
  slug="reviewer-$(safe_slug "${CUR_STAGE:-startup}")-r${CUR_ROUND}"
  prompt="$(compose_review_prompt "$1" "$2")"
  archive_text "${slug}-prompt.md" "$prompt" "reviewer" "$1" "$CUR_STAGE" "$CUR_ROUND" >/dev/null
  output_art=$(art_path "${slug}-output.txt")
  # 預寫「未通過」哨兵而非刪檔:reviewer 沒寫入 = 未通過(語義相同),
  # 且避免 codex 的 apply_patch 對不存在的檔案報錯(E2E 實測踩到)
  printf '{"approved": false, "blockers": ["reviewer did not write a verdict"], "suggestions": []}\n' > "$WF/verdict.json"
  engine_call "reviewer" "$1" "$slug" "r_$1" "$prompt" > >(tee -a "$LOG" | tee "$output_art") || echo "(警告:審查者執行失敗)" >&2
  write_meta "$output_art" "reviewer" "$1" "$(engine_model "$1")" "$(resolve_model_args "$1")" "$CUR_STAGE" "$CUR_ROUND"
  archive_snapshot "$ENGINE_OUT" "${slug}-final.raw" "reviewer" "$1" "$CUR_STAGE" "$CUR_ROUND" >/dev/null
  metric reviewer "$1" "$CUR_ROUND" "$(( SECONDS - t0 ))" "$LAST_COST"
  if [[ ! -f "$WF/verdict.json" ]]; then
    echo "(審查者未產出 verdict.json,視為未通過)" >&2
    return 1
  fi
  collect_suggestions
  archive_snapshot "$WF/review.md" "review-$(safe_slug "$CUR_STAGE")-r${CUR_ROUND}.md" "reviewer" "$1" "$CUR_STAGE" "$CUR_ROUND" >/dev/null
  archive_snapshot "$WF/verdict.json" "verdict-$(safe_slug "$CUR_STAGE")-r${CUR_ROUND}.json" "reviewer" "$1" "$CUR_STAGE" "$CUR_ROUND" >/dev/null
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
    log_section "quality gate" "workflow" "workflow" "$CUR_STAGE" "$CUR_ROUND"
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
  log_section "stage begin" "workflow" "workflow" "$CUR_STAGE" "$CUR_ROUND"
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
    work "$2" "審查意見在 $WF/review.md。依 AGENTS.md 的互審規範逐條回應:同意就修正,並在該條目下方註明「已修正:<摘要>」;不同意就回覆「不同意:<理由>」,不得默默忽略。只處理審查意見中的問題,不要超出本階段(${CUR_STAGE})的工作範圍——規格階段只改規格、計畫階段只改計畫,不要提前實作。若本階段有程式碼變更,修改後確保測試通過。"
    archive_snapshot "$WF/review.md" "review-$(safe_slug "$CUR_STAGE")-r${CUR_ROUND}-worker.md" "worker" "$2" "$CUR_STAGE" "$CUR_ROUND" >/dev/null
    gate_loop "$2" "$gate_cmd"   # 修正後同樣要過確定性關卡,再交回審查
  done
  echo "[$CUR_STAGE] 審查通過 ✔" | tee -a "$LOG"
}

commit_work() {  # $1: 工作者引擎  $2: 本次提交的內容說明
  log_section "commit" "worker" "$1" "$CUR_STAGE" "$CUR_ROUND"
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
  log_section "human gate" "workflow" "workflow" "$CUR_STAGE" "$CUR_ROUND"
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
  log_section "finish" "workflow" "workflow" "$CUR_STAGE" "$CUR_ROUND"
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
  archive_snapshot "$WF/pr-body.md" "pr-body.md" "workflow" "workflow" "$CUR_STAGE" "$CUR_ROUND" >/dev/null
  archive_snapshot "$WF/suggestions.md" "suggestions.md" "workflow" "workflow" "$CUR_STAGE" "$CUR_ROUND" >/dev/null
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
  local task_arg task_source_kind task_source_path="" task_resolved_text
  [[ -n "$task" ]] || usage

  if [[ "$task" == "print-agents" ]]; then
    write_agents_section
    return 0
  fi
  task_arg="$task"
  if [[ -f "$task" ]]; then
    task_source_kind="file"
    task_source_path=$(abs_path "$task")
    echo "從檔案讀取任務描述:$task"
    task_resolved_text="$(cat "$task")"
    task="$task_resolved_text"
  else
    task_source_kind="literal"
    task_resolved_text="$task"
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

  establish_run_archive
  init_live_state
  echo '*' > "$WF/.gitignore"   # 讓 .workflow/ 整個目錄不進版控
  write_run_metadata
  write_log_metadata
  archive_task "$task_arg" "$task_source_kind" "$task_source_path" "$task_resolved_text"
  printf '%s\n' "$WF_RUN" > "$WF/latest-run.txt"
  log_section "startup settings" "workflow" "workflow" "startup" "0"

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
  archive_snapshot "$WF/protected-tests.txt" "protected-tests.txt" "workflow" "workflow" "$CUR_STAGE" "$CUR_ROUND" >/dev/null
  archive_snapshot "$WF/protected-base.sha" "protected-base.sha" "workflow" "workflow" "$CUR_STAGE" "$CUR_ROUND" >/dev/null
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
