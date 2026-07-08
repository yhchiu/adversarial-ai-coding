#!/usr/bin/env bash
#
# adversarial-ai-coding.sh - adversarial two-AI coding workflow
#
# Each stage follows this flow:
#   Worker does the work -> reviewer reviews it
#     -> Not approved: worker updates from .workflow/review.md -> reviewer checks again
#     -> Approved: worker commits with a Conventional Commit -> next stage
#
# Usage:
#   ./adversarial-ai-coding.sh "task description"
#   ./adversarial-ai-coding.sh task.md        # If the argument is a file, use its contents as the task
#   ./adversarial-ai-coding.sh print-agents   # Print the AGENTS.md rule template and exit
#
# Environment variables, all optional:
#   ENGINE_A     Worker engine: claude | codex | agy   (default: claude)
#   ENGINE_B     Reviewer engine: claude | codex | agy (default: codex)
#   MODEL_A      Model override for the A slot, for example haiku
#   MODEL_B      Model override for the B slot; MODEL_A wins when A and B are both claude
#   CLAUDE_ARGS / CODEX_ARGS / AGY_ARGS  Extra CLI args, split on whitespace
#   MAX_ROUNDS   Maximum review or gate repair rounds per stage (default: 3)
#   AUTO_BRANCH  1=create a new branch; 0=use current branch (default: 1)
#   USE_WORKTREE 1=run in a separate git worktree (default: 0)
#   SPEC_DIR     Directory for spec and plan files (default: specs/<run timestamp>)
#   TOOLS        Claude Code --allowedTools value
#   GATE_CMD     Deterministic quality gate command, auto-detected by default
#   BUILD_GATE_CMD  Lightweight per-task build gate, auto-detected by default
#   HUMAN_GATE   1=pause for human spec approval (default: 1; set 0 for unattended runs)
#   DUAL_SPEC    1=run dual independent spec candidates before selecting final spec (default: 0)
#   OPEN_PR      1=push and create a GitHub PR at the end (default: 0, print commands only)
#   NOTIFY_CMD   Notification command. The message is passed as the first argument.
#   AGENTS_TEMPLATE  Path to AGENTS.md template (default: AGENTS.template.md beside this script)
#   RETRY_ON_LIMIT   1=wait and retry on quota/rate-limit errors (default: 1; 0=fail fast)
#   RETRY_MAX        Maximum rate-limit retries per engine call (default: 6)
#   RETRY_BASE_WAIT  Initial fallback wait in seconds when reset time cannot be parsed (default: 300)
#   RETRY_MAX_WAIT   Maximum fallback wait in seconds (default: 3600)
#   RUNS_DIR         Root archive directory for each run (default: .workflow/runs)
set -Eeuo pipefail

# ---------- Settings ----------
ENGINE_A="${ENGINE_A:-claude}"
ENGINE_B="${ENGINE_B:-codex}"
MODEL_A="${MODEL_A:-}"   # Model override for the A slot; empty means use the CLI default.
MODEL_B="${MODEL_B:-}"   # Model override for the B slot.
# Extra args for each CLI, split on whitespace. Example: CODEX_ARGS='-c model_reasoning_effort=low'
CLAUDE_ARGS="${CLAUDE_ARGS:-}"
CODEX_ARGS="${CODEX_ARGS:-}"
AGY_ARGS="${AGY_ARGS:-}"
MAX_ROUNDS="${MAX_ROUNDS:-3}"
AUTO_BRANCH="${AUTO_BRANCH:-1}"
USE_WORKTREE="${USE_WORKTREE:-0}"
HUMAN_GATE="${HUMAN_GATE:-1}"
DUAL_SPEC="${DUAL_SPEC:-0}"
OPEN_PR="${OPEN_PR:-0}"
NOTIFY_CMD="${NOTIFY_CMD:-}"
# Rate-limit retry: quota windows are not quality failures, so wait instead of burning review rounds.
RETRY_ON_LIMIT="${RETRY_ON_LIMIT:-1}"
RETRY_MAX="${RETRY_MAX:-6}"
RETRY_BASE_WAIT="${RETRY_BASE_WAIT:-300}"
RETRY_MAX_WAIT="${RETRY_MAX_WAIT:-3600}"
# Minimum permissions: allow git and explicit build/test commands.
# Note that Bash(go *) includes go run, which executes arbitrary code. Do not broaden this casually.
TOOLS="${TOOLS:-Bash(git *),Bash(go test *),Bash(go build *),Bash(go vet *)}"

RUN_ID="$(date +%Y%m%d-%H%M%S)"
SPEC_DIR="${SPEC_DIR:-specs/$RUN_ID}"
WF=".workflow"
RUNS_DIR="${RUNS_DIR:-$WF/runs}"
WF_RUN="${WF_RUN:-}"
LOGS="${LOGS:-$WF/logs}"
LOG="${LOG:-$LOGS/$RUN_ID.log}"
METRICS="${METRICS:-$WF/metrics.csv}"
ENGINE_OUT="$WF/last-engine-output.txt"   # Engine output for the latest call, used for rate-limit detection.
ART_SEQ="${ART_SEQ:-0}"
SPEC_OWNER_SLOT="${SPEC_OWNER_SLOT:-A}"
SPEC_REVIEWER_SLOT="${SPEC_REVIEWER_SLOT:-B}"
SPEC_OWNER_ENGINE="${SPEC_OWNER_ENGINE:-$ENGINE_A}"
SPEC_REVIEWER_ENGINE="${SPEC_REVIEWER_ENGINE:-$ENGINE_B}"

usage() {
  {
    echo "Usage:$0 \"task description\""
    echo "      $0 task.md         # If the argument is a file, use its contents as the task"
    echo "      $0 print-agents    # Print the AGENTS.md rule template and exit"
  } >&2
  exit 1
}

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing required command:$1" >&2; exit 1; }; }

notify() {  # $1: message; NOTIFY_CMD receives it as the first argument.
  [[ -z "$NOTIFY_CMD" ]] && return 0
  $NOTIFY_CMD "$1" || echo "(notification command failed:$NOTIFY_CMD)" >&2
}

metric() {  # $1: role  $2: engine  $3: round  $4: seconds  $5: cost in USD, optional
  local model model_args generated
  [[ -d "$(dirname "$METRICS")" ]] || return 0
  [[ -f "$METRICS" ]] || echo "run_id,stage,role,engine,round,duration_s,cost_usd,model,model_args,generated_at" > "$METRICS"
  model=$(engine_model "$2")
  model_args=$(resolve_model_args "$2")
  generated=$(generated_at)
  write_csv_row "$METRICS" "$RUN_ID" "$CUR_STAGE" "$1" "$2" "$3" "$4" "$5" "$model" "$model_args" "$generated"
}

# ---------- Pure helpers, safe to source from tests ----------
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

metrics_summary() {  # $1: metrics.csv; summarize calls, max round, seconds, and cost per stage.
  [[ -f "$1" ]] || return 0
  # Rows are quoted by csv_row so model_args can contain commas.
  # Strip outer quotes first or awk treats "12" as non-numeric and sums it as 0.
  awk -F, 'NR>1 {
      for (i = 1; i <= NF; i++) gsub(/^"|"$/, "", $i)
      calls[$2]++; secs[$2] += $6; cost[$2] += $7
      if ($5 + 0 > r[$2] + 0) r[$2] = $5
    }
    END { for (s in calls) printf "  %-14s AI calls %d, review rounds %d, %d seconds, $%.4f\n", s, calls[s], r[s], secs[s], cost[s] }' \
    "$1"
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
    --arg dual_spec "$DUAL_SPEC" \
    --arg max_rounds "$MAX_ROUNDS" \
    --arg auto_branch "$AUTO_BRANCH" \
    --arg use_worktree "$USE_WORKTREE" \
    '{generated_at:$generated_at,run_id:$run_id,spec_dir:$spec_dir,wf:$wf,runs_dir:$runs_dir,wf_run:$wf_run,log:$log,metrics:$metrics,engine_a:$engine_a,model_a:$model_a,args_a:$args_a,engine_b:$engine_b,model_b:$model_b,args_b:$args_b,dual_spec:$dual_spec,max_rounds:$max_rounds,auto_branch:$auto_branch,use_worktree:$use_worktree}' \
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
    --arg dual_spec "$DUAL_SPEC" \
    '{generated_at:$generated_at,generator_role:$generator_role,run_id:$run_id,log_path:$log_path,engine_a:$engine_a,model_a:$model_a,args_a:$args_a,engine_b:$engine_b,model_b:$model_b,args_b:$args_b,dual_spec:$dual_spec}' \
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

engine_model() {  # $1: engine; output its slot's model override, or empty if unset.
  if [[ "$1" == "$ENGINE_A" && -n "$MODEL_A" ]]; then
    echo "$MODEL_A"
  elif [[ "$1" == "$ENGINE_B" && -n "$MODEL_B" ]]; then
    echo "$MODEL_B"
  fi
}

verdict_approved() {  # $1: verdict.json path; returns 0 when review approved.
  [[ -f "$1" ]] && jq -e '.approved == true' "$1" >/dev/null 2>&1
}

is_rate_limited() {  # $1: engine output file; returns 0 for quota/rate-limit errors that can be retried.
  [[ -f "$1" ]] || return 1
  grep -qiE '"api_error_status": *429|(hit|reached) your (session|usage|weekly|rate) limit|rate.?limit|too many requests|status.?429' "$1"
}

parse_reset_wait() {  # $1: output file  $2: now epoch for tests; output wait seconds, or empty on parse failure.
  local f="$1" now="${2:-$(date +%s)}" t target wait m num unit
  [[ -f "$f" ]] || return 0

  # Format 1, Claude: "resets 10:50am" -> wait until reset time plus a 120 second buffer.
  t=$(grep -oiE 'resets +[0-9]{1,2}:[0-9]{2} ?[ap]m' "$f" | head -1 | sed -E 's/^[Rr]esets +//; s/ //g' || true)
  if [[ -n "$t" ]]; then
    target=$(LC_ALL=C date -d "$t" +%s 2>/dev/null || true)
    if [[ -n "$target" ]]; then
      (( target <= now )) && target=$(( target + 86400 ))
      wait=$(( target - now + 120 ))
      (( wait > 21600 )) && return 0   # More than 6 hours likely means bad parsing; use exponential backoff.
      echo "$wait"
      return 0
    fi
  fi

  # Format 2, OpenAI/Codex: "try again in 20s / 2 minutes / 3 hours" -> wait plus a 30 second buffer.
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

detect_gate() {  # Detect a full quality gate by project type; output empty if unknown.
  if [[ -f go.mod ]]; then
    echo "go build ./... && go vet ./... && go test ./..."
  elif [[ -f package.json ]] && jq -e '.scripts.test // empty' package.json >/dev/null 2>&1; then
    echo "npm test"
  elif [[ -f Cargo.toml ]]; then
    echo "cargo test"
  fi
}

detect_build_gate() {  # Lightweight per-task gate: build only, while acceptance tests may still be red.
  if [[ -f go.mod ]]; then
    echo "go build ./..."
  elif [[ -f Cargo.toml ]]; then
    echo "cargo build"
  fi
}

protected_violations() {  # $1: protected list file  $2: base commit; output modified protected files.
  [[ -f "$1" && -s "$1" ]] || return 0
  git diff --name-only "$2" -- 2>/dev/null | grep -Fx -f "$1" || true
}

plan_tasks() {  # $1: plan.md; output unfinished task lines without the "- [ ] " prefix.
  [[ -f "$1" ]] || return 0
  grep -E '^- \[ \] ' "$1" | sed -E 's/^- \[ \] //' || true
}

normalize_dual_spec_decision() {  # $1: a|b|ma|mb; output canonical decision or fail.
  local decision="${1:-}"
  decision="${decision,,}"
  case "$decision" in
    a)  echo "adopt-a" ;;
    b)  echo "adopt-b" ;;
    ma) echo "merge-a" ;;
    mb) echo "merge-b" ;;
    *)  return 1 ;;
  esac
}

dual_spec_owner_slot() {  # $1: canonical dual-spec decision; output A or B.
  case "$1" in
    adopt-a|merge-a) echo "A" ;;
    adopt-b|merge-b) echo "B" ;;
    *) return 1 ;;
  esac
}

engine_for_slot() {  # $1: A or B; output configured engine for that slot.
  case "$1" in
    A) echo "$ENGINE_A" ;;
    B) echo "$ENGINE_B" ;;
    *) return 1 ;;
  esac
}

reviewer_slot_for_owner_slot() {  # $1: owner slot A or B; output the opposite slot.
  case "$1" in
    A) echo "B" ;;
    B) echo "A" ;;
    *) return 1 ;;
  esac
}

set_spec_roles_from_slot() {  # $1: owner slot A or B; set owner/reviewer slot and engine globals.
  SPEC_OWNER_SLOT="$1"
  SPEC_REVIEWER_SLOT=$(reviewer_slot_for_owner_slot "$SPEC_OWNER_SLOT")
  SPEC_OWNER_ENGINE=$(engine_for_slot "$SPEC_OWNER_SLOT")
  SPEC_REVIEWER_ENGINE=$(engine_for_slot "$SPEC_REVIEWER_SLOT")
}

dual_spec_preflight() {
  [[ "$DUAL_SPEC" == "1" ]] || return 0
  if [[ "$HUMAN_GATE" != "1" ]]; then
    echo "DUAL_SPEC=1 requires HUMAN_GATE=1 because a human must choose the final spec owner." >&2
    return 1
  fi
  if ! { : </dev/tty; } 2>/dev/null; then
    echo "DUAL_SPEC=1 requires an interactive terminal for spec selection. Run interactively or set DUAL_SPEC=0." >&2
    return 1
  fi
}

# ---------- AGENTS.md shared cross-review rules ----------
# Keep the rules in AGENTS.template.md for easier maintenance.
# Simple English works best across all supported models.
AGENTS_MARKER='<!-- adversarial-ai-coding:begin -->'
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENTS_TEMPLATE="${AGENTS_TEMPLATE:-$SCRIPT_DIR/AGENTS.template.md}"

write_agents_section() {  # Print the rule template; return 1 when the template is missing.
  if [[ ! -f "$AGENTS_TEMPLATE" ]]; then
    echo "(AGENTS.md template not found:$AGENTS_TEMPLATE; put AGENTS.template.md next to the script or set AGENTS_TEMPLATE)" >&2
    return 1
  fi
  cat "$AGENTS_TEMPLATE"
}

bootstrap_agents_md() {  # Create missing files only; never overwrite existing user rules.
  if [[ -f AGENTS.md ]]; then
    grep -qF "$AGENTS_MARKER" AGENTS.md \
      || echo "(note: AGENTS.md exists but does not include adversarial-ai-coding rules; run \"$0 print-agents\" and merge them manually)" >&2
  elif write_agents_section > AGENTS.md; then
    echo "Created AGENTS.md with adversarial-ai-coding cross-review rules."
  else
    rm -f AGENTS.md   # Missing template: do not leave an empty file; warning already printed.
    return 0
  fi
  [[ -f CLAUDE.md ]] || printf 'Follow the adversarial-ai-coding cross-review rules in AGENTS.md.\n' > CLAUDE.md
}

# ---------- Worker engines; same stage resumes session, new stage resets it ----------
WORKER_SESSION=""
LAST_COST=""   # Claude reports total_cost_usd; other engines leave this empty.

w_claude() {
  local args=(--output-format json --permission-mode acceptEdits --allowedTools "$TOOLS")
  local m; m=$(engine_model claude)
  [[ -n "$m" ]] && args+=(--model "$m")
  # shellcheck disable=SC2206  # Deliberately split on whitespace.
  args+=($CLAUDE_ARGS)
  [[ -n "$WORKER_SESSION" ]] && args+=(--resume "$WORKER_SESSION")
  local out rc=0
  out=$(claude -p "$1" "${args[@]}") || rc=$?
  if (( rc != 0 )); then
    # Print raw output on failure; otherwise command substitution hides useful errors such as quota limits.
    printf '%s\n' "$out" >&2
    printf '%s\n' "$out" > "$ENGINE_OUT"   # Let engine_call detect rate-limit errors.
    echo "(claude exited with code $rc; raw output is shown above)" >&2
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
  [[ -n "$m" ]] && margs=(-c "model=\"$m\"")   # -c works for both exec and resume.
  # shellcheck disable=SC2206  # Deliberately split on whitespace.
  margs+=($CODEX_ARGS)
  if [[ -z "$WORKER_SESSION" ]]; then
    codex exec --sandbox workspace-write "${margs[@]}" "$1" 2>&1 | tee "$ENGINE_OUT"
    WORKER_SESSION="last"
  else
    # exec resume has no --sandbox flag, so override config with -c.
    codex exec resume --last -c 'sandbox_mode="workspace-write"' "${margs[@]}" "$1" 2>&1 | tee "$ENGINE_OUT"
  fi
}

w_agy() {
  # --dangerously-skip-permissions approves every tool action.
  # Prefer an isolated branch, worktree, or container when using agy.
  local args=(--print-timeout 60m --dangerously-skip-permissions)
  local m; m=$(engine_model agy)
  [[ -n "$m" ]] && args+=(--model "$m")
  # shellcheck disable=SC2206  # Deliberately split on whitespace.
  args+=($AGY_ARGS)
  [[ -n "$WORKER_SESSION" ]] && args+=(--continue)
  agy --print "$1" "${args[@]}" 2>&1 | tee "$ENGINE_OUT"
  WORKER_SESSION="continue"
}

# ---------- Rate-limit retry ----------
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
      echo "!! Rate limit did not clear after $RETRY_MAX retries; giving up." >&2
      return "$rc"
    fi
    n=$(( n + 1 ))
    w=$(parse_reset_wait "$ENGINE_OUT")   # Prefer waiting until the parsed reset time.
    if [[ -z "$w" ]]; then
      w=$(( RETRY_BASE_WAIT * (1 << (n - 1)) ))
      (( w > RETRY_MAX_WAIT )) && w=$RETRY_MAX_WAIT
    fi
    eta=$(date -d "+$w seconds" +%H:%M 2>/dev/null || true)
    log_section "rate limit retry" "$role" "$engine" "$CUR_STAGE" "$CUR_ROUND"
    { echo "== Rate limit hit; waiting $(( w / 60 )) minutes, about until $eta, before retry $n/$RETRY_MAX =="; } | tee -a "$LOG" >&2
    notify "adversarial-ai-coding: rate limit hit; retry around $eta (attempt $n)"
    sleep "$w"
    attempt=$(( attempt + 1 ))
  done
}

work() {  # $1: engine  $2: work instruction
  local t0=$SECONDS prompt_art output_art slug
  LAST_COST=""
  log_section "AI call" "worker" "$1" "$CUR_STAGE" "$CUR_ROUND"
  echo ">>> Worker($1) is running..."
  slug="worker-$(safe_slug "${CUR_STAGE:-startup}")-r${CUR_ROUND}"
  archive_text "${slug}-prompt.md" "$2" "worker" "$1" "$CUR_STAGE" "$CUR_ROUND" >/dev/null
  output_art=$(art_path "${slug}-output.txt")
  # Use process substitution instead of a pipeline so engine functions stay in the current shell.
  # Otherwise WORKER_SESSION updates would be lost in a subshell.
  engine_call "worker" "$1" "$slug" "w_$1" "$2" > >(tee -a "$LOG" | tee "$output_art")
  write_meta "$output_art" "worker" "$1" "$(engine_model "$1")" "$(resolve_model_args "$1")" "$CUR_STAGE" "$CUR_ROUND"
  archive_snapshot "$ENGINE_OUT" "${slug}-final.raw" "worker" "$1" "$CUR_STAGE" "$CUR_ROUND" >/dev/null
  archive_git_state "worker" "$1" "$slug"
  metric worker "$1" "$CUR_ROUND" "$(( SECONDS - t0 ))" "$LAST_COST"
  # Hard-check protected tests after each worker action. The flag prevents recursive recovery loops.
  if (( ! CHECKING_PROTECTED )); then
    check_protected "$1"
  fi
}

# ---------- Protected acceptance tests, adversarial TDD ----------
# The reviewer writes acceptance tests and protected-tests.txt records them.
# During implementation, the worker must not edit them. Git diff enforces this.
CHECKING_PROTECTED=0

check_protected() {  # $1: worker engine; force recovery when protected files are modified.
  local base viol n=0
  [[ -f "$WF/protected-tests.txt" && -f "$WF/protected-base.sha" ]] || return 0
  log_section "protected check" "workflow" "workflow" "$CUR_STAGE" "$CUR_ROUND"
  base=$(cat "$WF/protected-base.sha")
  while viol=$(protected_violations "$WF/protected-tests.txt" "$base"); [[ -n "$viol" ]]; do
    { echo "!! Protected acceptance test files were modified:"; sed 's/^/  - /' <<<"$viol"; } | tee -a "$LOG" >&2
    if (( n >= 2 )); then
      echo "!! Worker repeatedly modified protected tests and did not restore them; stopping for human intervention." >&2
      notify "adversarial-ai-coding:[$CUR_STAGE] protected tests were modified and not restored; human intervention required"
      exit 1
    fi
    n=$(( n + 1 ))
    CHECKING_PROTECTED=1
    work "$1" "You modified protected acceptance test files, which the workflow rules forbid:
$viol
Restore these files exactly to commit $base, for example with git checkout $base -- <file>, and commit that restoration. If you believe a test is wrong, record the objection in the Assumptions and Open Questions section of $SPEC_DIR/spec.md, but do not modify the test file."
    CHECKING_PROTECTED=0
  done
}

# ---------- Reviewer engines; each round starts with fresh context from files and diffs ----------
# Verdict grading: blockers must be fixed; suggestions do not block and are evaluated at the end.
VERDICT_SCHEMA='{"type":"object","properties":{"approved":{"type":"boolean"},"blockers":{"type":"array","items":{"type":"string"}},"suggestions":{"type":"array","items":{"type":"string"}}},"required":["approved","blockers","suggestions"]}'

review_prompt() {  # $1: review scope for this round
  cat <<EOF
You are a strict code reviewer. Review scope for this round:$1

Follow the adversarial-ai-coding cross-review rules in AGENTS.md. Key rules:
- Use your built-in file read/search tools instead of shell cat/ls/cd commands. Shell commands are allowlisted; a blocked command wastes a turn.
- Review and verify only. You may run tests, but do not modify any files except $WF/review.md and $WF/verdict.json.
- Write findings one by one to $WF/review.md, overwriting old content. If approved, write a short approval reason.
- If review.md already contains worker replies from the previous round, verify each reply first.
- Grade the verdict with blockers and suggestions. Blockers include correctness bugs, spec violations, weakened tests, and security problems that must be fixed.
  Suggestions do not block approval, but list them honestly.
EOF
}

verdict_file_instr() {
  echo "- Finally write the verdict to $WF/verdict.json. The file already exists with a default failed verdict, so overwrite it. Use one line of JSON: {\"approved\": true|false, \"blockers\": [\"must-fix issue\"], \"suggestions\": [\"non-blocking suggestion\"]}."
}

compose_review_prompt() {  # $1: engine  $2: review scope for this round
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
  # shellcheck disable=SC2206  # Deliberately split on whitespace.
  args+=($CLAUDE_ARGS)
  local rc=0
  out=$(claude -p "$1" "${args[@]}" \
    --output-format json --permission-mode acceptEdits --allowedTools "$TOOLS" \
    --json-schema "$VERDICT_SCHEMA") || rc=$?
  if (( rc != 0 )); then
    printf '%s\n' "$out" >&2
    printf '%s\n' "$out" > "$ENGINE_OUT"
    echo "(claude exited with code $rc; raw output is shown above)" >&2
    return "$rc"
  fi
  printf '%s\n' "$out" > "$ENGINE_OUT"
  LAST_COST=$(jq -r '.total_cost_usd // empty' <<<"$out")
  jq -c '.structured_output // {approved: false, blockers: ["reviewer did not produce a structured verdict"], suggestions: []}' <<<"$out" > "$WF/verdict.json"
  jq -r '.result // empty' <<<"$out"
}

r_codex() {
  local m margs=()
  m=$(engine_model codex)
  [[ -n "$m" ]] && margs=(-c "model=\"$m\"")
  # shellcheck disable=SC2206  # Deliberately split on whitespace.
  margs+=($CODEX_ARGS)
  codex exec --sandbox workspace-write "${margs[@]}" "$1" 2>&1 | tee "$ENGINE_OUT"
}

r_agy() {
  local m margs=()
  m=$(engine_model agy)
  [[ -n "$m" ]] && margs=(--model "$m")
  # shellcheck disable=SC2206  # Deliberately split on whitespace.
  margs+=($AGY_ARGS)
  agy --print "$1" --print-timeout 30m --dangerously-skip-permissions "${margs[@]}" 2>&1 | tee "$ENGINE_OUT"
}

collect_suggestions() {  # Accumulate this round's suggestions for final evaluation.
  [[ -f "$WF/verdict.json" ]] || return 0
  local s
  s=$(jq -r '.suggestions[]? // empty' "$WF/verdict.json" 2>/dev/null) || return 0
  [[ -z "$s" ]] && return 0
  {
    echo "## ${CUR_STAGE}(round ${CUR_ROUND})"
    sed 's/^/- /' <<<"$s"
    echo
  } >> "$WF/suggestions.md"
}

show_blockers() {
  [[ -f "$WF/verdict.json" ]] || return 0
  echo "Review did not pass; blockers:" | tee -a "$LOG"
  jq -r '.blockers[]? // empty' "$WF/verdict.json" 2>/dev/null | sed 's/^/  - /' | tee -a "$LOG"
}

run_review() {  # $1: engine  $2: review scope; returns 0 when approved.
  local t0=$SECONDS prompt output_art slug
  LAST_COST=""
  log_section "review" "reviewer" "$1" "$CUR_STAGE" "$CUR_ROUND"
  echo ">>> Reviewer($1) is reviewing..."
  slug="reviewer-$(safe_slug "${CUR_STAGE:-startup}")-r${CUR_ROUND}"
  prompt="$(compose_review_prompt "$1" "$2")"
  archive_text "${slug}-prompt.md" "$prompt" "reviewer" "$1" "$CUR_STAGE" "$CUR_ROUND" >/dev/null
  output_art=$(art_path "${slug}-output.txt")
  # Prewrite a failed sentinel instead of deleting the file:
  # if the reviewer does not write a verdict, the run stays failed.
  # This also avoids apply_patch failures against a missing file.
  printf '{"approved": false, "blockers": ["reviewer did not write a verdict"], "suggestions": []}\n' > "$WF/verdict.json"
  engine_call "reviewer" "$1" "$slug" "r_$1" "$prompt" > >(tee -a "$LOG" | tee "$output_art") || echo "(warning: reviewer execution failed)" >&2
  write_meta "$output_art" "reviewer" "$1" "$(engine_model "$1")" "$(resolve_model_args "$1")" "$CUR_STAGE" "$CUR_ROUND"
  archive_snapshot "$ENGINE_OUT" "${slug}-final.raw" "reviewer" "$1" "$CUR_STAGE" "$CUR_ROUND" >/dev/null
  metric reviewer "$1" "$CUR_ROUND" "$(( SECONDS - t0 ))" "$LAST_COST"
  if [[ ! -f "$WF/verdict.json" ]]; then
    echo "(reviewer did not write verdict.json; treating as failed)" >&2
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

# ---------- Deterministic quality gates ----------
# Anything machine-verifiable is run by the script. AI claims about test status are only hints.
gate_loop() {  # $1: worker engine  $2: gate command, empty to skip; failures are sent back to the worker.
  local engine="$1" cmd="$2" n=1 out
  [[ -z "$cmd" ]] && return 0
  while true; do
    log_section "quality gate" "workflow" "workflow" "$CUR_STAGE" "$CUR_ROUND"
    echo ">>> Quality gate:$cmd"
    if out=$(bash -c "$cmd" 2>&1); then
      echo "Quality gate passed" | tee -a "$LOG"
      return 0
    fi
    printf '%s\n' "$out" >> "$LOG"
    echo "Quality gate failed (attempt $n)" | tee -a "$LOG"
    if (( n >= MAX_ROUNDS )); then
      echo "!! [$CUR_STAGE] Quality gate failed $MAX_ROUNDS times; stopping for human intervention. Output:" >&2
      printf '%s\n' "$out" | tail -50 >&2
      notify "adversarial-ai-coding:[$CUR_STAGE] quality gate failed repeatedly; human intervention required"
      exit 1
    fi
    n=$(( n + 1 ))
    work "$engine" "The quality gate command \"$cmd\" failed. Here is the output, limited to the last 150 lines. Fix the problem until this command passes:
$(printf '%s\n' "$out" | tail -150)"
  done
}

# ---------- Stage flow ----------
CUR_STAGE=""
CUR_ROUND=1

begin_stage() {  # $1: name; worker session resumes within a stage and resets across stages.
  CUR_STAGE="$1"
  WORKER_SESSION=""
  CUR_ROUND=1
  log_section "stage begin" "workflow" "workflow" "$CUR_STAGE" "$CUR_ROUND"
  printf '\n================ [%s] ================\n' "$1" | tee -a "$LOG"
}

review_loop() {  # $1: reviewer engine  $2: worker engine  $3: review scope  $4: optional gate command for repair rounds
  local gate_cmd="${4:-}"
  CUR_ROUND=1
  until run_review "$1" "$3"; do
    if (( CUR_ROUND >= MAX_ROUNDS )); then
      echo "!! [$CUR_STAGE] Review still failed after $MAX_ROUNDS rounds; stopping. Read $WF/review.md and handle it manually." >&2
      notify "adversarial-ai-coding:[$CUR_STAGE] review failed after $MAX_ROUNDS rounds; human intervention required"
      exit 1
    fi
    CUR_ROUND=$(( CUR_ROUND + 1 ))
    echo "--- [$CUR_STAGE] round $CUR_ROUND: worker updates from review findings ---" | tee -a "$LOG"
    work "$2" "Review findings are in $WF/review.md. Follow the AGENTS.md cross-review rules and reply under each finding. If you agree, fix it and write \"Fixed: <summary>\" under that item. If you disagree, write \"Disagree: <reason>\". Do not ignore findings silently. Only handle issues from the review, and stay within this stage (${CUR_STAGE}): spec stage only edits the spec, plan stage only edits the plan, and no implementation starts early. If this stage changes code, make sure tests pass after the change."
    archive_snapshot "$WF/review.md" "review-$(safe_slug "$CUR_STAGE")-r${CUR_ROUND}-worker.md" "worker" "$2" "$CUR_STAGE" "$CUR_ROUND" >/dev/null
    gate_loop "$2" "$gate_cmd"   # Repairs must pass the deterministic gate before review resumes.
  done
  echo "[$CUR_STAGE] Review approved" | tee -a "$LOG"
}

commit_work() {  # $1: worker engine  $2: description of this commit
  log_section "commit" "worker" "$1" "$CUR_STAGE" "$CUR_ROUND"
  work "$1" "$2 is complete and approved. Commit all current changes using the AGENTS.md commit rules: Conventional Commit format, simple English subject, detailed body describing the completed work, and no Co-Authored-By trailer."
  ensure_committed
}

ensure_committed() {  # If the worker forgot to commit, the script commits to keep workflow state consistent.
  if [[ -n "$(git status --porcelain)" ]]; then
    echo "(worker left uncommitted changes; script is creating a fallback commit)" >&2
    git add -A
    git commit -m "chore: commit remaining ${CUR_STAGE} changes" \
      -m "Auto-committed by adversarial-ai-coding because the worker left uncommitted changes."
  fi
}

commit_if_dirty() {  # $1: engine  $2: description; skip if clean to avoid a wasted AI call.
  [[ -z "$(git status --porcelain)" ]] && return 0
  commit_work "$1" "$2"
}

# ---------- Human checkpoint ----------
# A bad spec amplifies into many bad changes, so approval happens before costly implementation.
human_gate_spec() {
  [[ "$HUMAN_GATE" == "1" ]] || return 0
  log_section "human gate" "workflow" "workflow" "$CUR_STAGE" "$CUR_ROUND"
  notify "adversarial-ai-coding: spec awaits human approval ($SPEC_DIR/spec.md)"
  echo ""
  echo "### Human checkpoint: review $SPEC_DIR/spec.md, especially the Assumptions and Open Questions section."
  echo "### You may edit the file before continuing; your edits will be committed with the spec."
  if ! { : </dev/tty; } 2>/dev/null; then
    echo "!! No interactive terminal is available for approval. Run from an interactive terminal, or set HUMAN_GATE=0 to skip this gate (not recommended)." >&2
    exit 1
  fi
  local ans
  read -rp "Enter y to approve and continue; anything else aborts:" ans </dev/tty
  if [[ "$ans" != "y" && "$ans" != "Y" ]]; then
    echo "Aborted: spec was not approved." >&2
    exit 1
  fi
  echo "Spec approved by human" | tee -a "$LOG"
}

# ---------- Finish: hand off to a human ----------
# The endpoint is a PR ready for human merge, not a silent exit. OPEN_PR=1 executes push/PR creation.
finish() {  # $1: task description
  local branch title
  log_section "finish" "workflow" "workflow" "$CUR_STAGE" "$CUR_ROUND"
  branch=$(git rev-parse --abbrev-ref HEAD)
  title=$(head -1 <<<"$1")
  title="${title:0:72}"   # Truncate by character; cut -c can split multi-byte text.

  cat > "$WF/pr-body.md" <<EOF
## Task

$1

## Artifacts

- Spec with assumptions and open questions:\`$SPEC_DIR/spec.md\`
- Implementation plan:\`$SPEC_DIR/plan.md\`

Generated by adversarial-ai-coding, with worker A=$ENGINE_A and reviewer B=$ENGINE_B.
Each stage passed deterministic quality gates and cross-review. Acceptance tests were written by the reviewer and protected against worker edits.
EOF

  printf '\nAll stages complete. Spec and plan are in %s/, and the run log is at %s\n' "$SPEC_DIR" "$LOG"
  if [[ -f "$METRICS" ]]; then
    echo ""
    echo "Run metrics (details:$METRICS; review rounds are a prompt-quality signal):"
    metrics_summary "$METRICS"
  fi
  archive_snapshot "$WF/pr-body.md" "pr-body.md" "workflow" "workflow" "$CUR_STAGE" "$CUR_ROUND" >/dev/null
  archive_snapshot "$WF/suggestions.md" "suggestions.md" "workflow" "workflow" "$CUR_STAGE" "$CUR_ROUND" >/dev/null
  if [[ "$OPEN_PR" == "1" ]] && command -v gh >/dev/null 2>&1 && git remote get-url origin >/dev/null 2>&1; then
    git push -u origin "$branch"
    gh pr create --title "$title" --body-file "$WF/pr-body.md"
  else
    echo ""
    echo "Next steps, run manually:"
    echo "  git push -u origin $branch"
    echo "  gh pr create --title \"$title\" --body-file $WF/pr-body.md"
    [[ "$OPEN_PR" == "1" ]] && echo "(OPEN_PR=1 but gh or origin remote is missing; printed commands instead)" >&2
  fi
  notify "adversarial-ai-coding: all stages complete ($branch)"
}

setup_workspace() {  # Prepare an isolated workspace according to USE_WORKTREE / AUTO_BRANCH.
  if [[ "$USE_WORKTREE" == "1" ]]; then
    local root name wt
    root=$(git rev-parse --show-toplevel)
    name=$(basename "$root")
    wt="$root/../${name}-auto-$RUN_ID"
    git worktree add -b "auto/$RUN_ID" "$wt"
    cd "$wt"
    echo "Created worktree:$wt (branch auto/$RUN_ID; remove later with git worktree remove)"
  elif [[ "$AUTO_BRANCH" == "1" ]]; then
    git switch -c "auto/$RUN_ID"
    echo "Created and switched to branch:auto/$RUN_ID"
  fi
}

# ---------- Main flow ----------
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
    echo "Reading task description from file:$task"
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
      *) echo "Unsupported engine:$e (available: claude | codex | agy)" >&2; exit 1 ;;
    esac
  done

  git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || { echo "Run this script from the root of the target git repository." >&2; exit 1; }

  # codex and agy resume the most recent session.
  # If A and B both use one of them, the review session can become the worker's next resumed session.
  if [[ "$ENGINE_A" == "$ENGINE_B" && "$ENGINE_A" != "claude" ]]; then
    echo "A and B cannot both use $ENGINE_A because session resume would interfere. Use different engines." >&2
    exit 1
  fi
  dual_spec_preflight || exit 1

  echo "Workflow settings:A=$ENGINE_A  B=$ENGINE_B  DUAL_SPEC=$DUAL_SPEC  MAX_ROUNDS=$MAX_ROUNDS  SPEC_DIR=$SPEC_DIR"
  echo "Task:$task"

  setup_workspace   # May cd into a worktree; relative paths after this point use that workspace.

  establish_run_archive
  init_live_state
  echo '*' > "$WF/.gitignore"   # Keep the whole .workflow/ directory out of version control.
  write_run_metadata
  write_log_metadata
  archive_task "$task_arg" "$task_source_kind" "$task_source_path" "$task_resolved_text"
  printf '%s\n' "$WF_RUN" > "$WF/latest-run.txt"
  log_section "startup settings" "workflow" "workflow" "startup" "0"

  trap 'echo "!! Workflow interrupted (exit=$?). Full run log: '"$LOG"'" >&2' ERR

  bootstrap_agents_md

  GATE_CMD="${GATE_CMD:-$(detect_gate)}"
  BUILD_GATE_CMD="${BUILD_GATE_CMD:-$(detect_build_gate)}"
  if [[ -n "$GATE_CMD" ]]; then
    echo "Quality gate:$GATE_CMD"
  else
    echo "(warning: no quality gate command detected; deterministic gates are disabled. Set GATE_CMD to enable one.)" >&2
  fi

  begin_stage "write-spec"
  work "$ENGINE_A" "Write a spec for the following request and save it to $SPEC_DIR/spec.md. The spec must include: feature description, testable acceptance criteria, edge cases, out-of-scope items, and an Assumptions and Open Questions section. In non-interactive mode you cannot ask a human questions, so list every assumption honestly in that section instead of silently guessing. Request:$task"
  review_loop "$ENGINE_B" "$ENGINE_A" "$SPEC_DIR/spec.md: review requirement completeness, whether acceptance criteria are testable, and whether edge cases are missing. Review the Assumptions and Open Questions section item by item. Treat unreasonable assumptions or missing assumptions as blockers."
  human_gate_spec
  commit_work "$ENGINE_A" "Spec, approved by review and human gate"

  begin_stage "write-implementation-plan"
  work "$ENGINE_A" "Write an implementation plan from $SPEC_DIR/spec.md and save it to $SPEC_DIR/plan.md. Include an implementation task list, one task per line, using the \"- [ ] \" checkbox format. Each task must be independently implementable and verifiable, and each task maps to one commit. Include a test strategy that decides whether unit, integration, or E2E tests are needed, with reasons."
  review_loop "$ENGINE_B" "$ENGINE_A" "$SPEC_DIR/plan.md compared with $SPEC_DIR/spec.md: feasibility, test coverage, whether tasks are small and independent, and whether checkbox format is correct."
  commit_work "$ENGINE_A" "Implementation plan"

  # Adversarial TDD: reviewer B writes acceptance tests from the spec, worker A reviews them.
  # The test author and implementer are separated, and A cannot edit these tests during implementation.
  begin_stage "write-acceptance-tests"
  local test_base
  test_base=$(git rev-parse HEAD)
  work "$ENGINE_B" "Write acceptance tests from the acceptance criteria in $SPEC_DIR/spec.md, using the project's normal test location. The implementation does not exist yet, so tests may fail to compile or be red; this is the TDD red phase. Do not write product code and do not modify files under $SPEC_DIR."
  review_loop "$ENGINE_A" "$ENGINE_B" "Acceptance tests, using git diff from $test_base: do they fully cover every acceptance criterion in $SPEC_DIR/spec.md, are the tests correct, and did the change avoid product code?"
  commit_work "$ENGINE_B" "Acceptance tests"
  git diff --name-only "$test_base" HEAD | grep -v "^$SPEC_DIR/" > "$WF/protected-tests.txt" || true
  git rev-parse HEAD > "$WF/protected-base.sha"
  archive_snapshot "$WF/protected-tests.txt" "protected-tests.txt" "workflow" "workflow" "$CUR_STAGE" "$CUR_ROUND" >/dev/null
  archive_snapshot "$WF/protected-base.sha" "protected-base.sha" "workflow" "workflow" "$CUR_STAGE" "$CUR_ROUND" >/dev/null
  if [[ -s "$WF/protected-tests.txt" ]]; then
    { echo "Protected acceptance test files:"; sed 's/^/  - /' "$WF/protected-tests.txt"; } | tee -a "$LOG"
  else
    echo "(warning: acceptance-test stage produced no files; test protection is disabled)" >&2
  fi

  # Small batches: one task per commit makes review and rollback easier.
  begin_stage "write-code"
  local tasks=() t i=1
  mapfile -t tasks < <(plan_tasks "$SPEC_DIR/plan.md")
  if (( ${#tasks[@]} == 0 )); then
    echo "(warning: plan.md has no \"- [ ] \" task list; falling back to one whole-plan implementation task)" >&2
    tasks=("Complete the full implementation described in $SPEC_DIR/plan.md")
  fi
  for t in "${tasks[@]}"; do
    echo "--- Task $i/${#tasks[@]}:$t ---" | tee -a "$LOG"
    work "$ENGINE_A" "Implement this task from $SPEC_DIR/plan.md:$t
Use TDD. Run the tests related to this task and confirm they pass. The full acceptance test suite may stay red until all tasks are complete. You may add your own unit tests, but you must not modify protected acceptance test files listed in $WF/protected-tests.txt. When done, change this task in plan.md from \"- [ ]\" to \"- [x]\"."
    gate_loop "$ENGINE_A" "$BUILD_GATE_CMD"
    commit_work "$ENGINE_A" "Task \"$t\""
    i=$(( i + 1 ))
  done

  echo "--- All tasks complete; running full quality gate. Acceptance tests must pass. ---" | tee -a "$LOG"
  gate_loop "$ENGINE_A" "$GATE_CMD"
  review_loop "$ENGINE_B" "$ENGINE_A" "Current code changes on this branch, using git diff and git log: code quality, conformance with $SPEC_DIR/spec.md, actual test execution, and protected acceptance-test integrity using $WF/protected-tests.txt. Confirm tests were not weakened or bypassed." "$GATE_CMD"
  commit_if_dirty "$ENGINE_A" "Review fixes"

  begin_stage "final-review-and-fixes"
  work "$ENGINE_A" "Do a complete self-review of all changes on this branch: 1) fix any problems you find and add missing tests; 2) if $WF/suggestions.md exists, evaluate every accumulated review suggestion, implementing accepted suggestions and writing a reason under suggestions you reject; 3) run the full test suite and confirm it passes."
  gate_loop "$ENGINE_A" "$GATE_CMD"
  review_loop "$ENGINE_B" "$ENGINE_A" "Final acceptance: compare the full branch against $SPEC_DIR/spec.md item by item, run the full tests, and confirm acceptance tests were not weakened." "$GATE_CMD"
  commit_if_dirty "$ENGINE_A" "Final fixes"

  finish "$task"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
