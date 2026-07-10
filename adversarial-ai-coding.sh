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
#   AGENT_A      Worker agent command: claude | codex | agy, or a custom command (default: claude)
#   AGENT_B      Reviewer agent command: claude | codex | agy, or a custom command (default: codex)
#   MODEL_A      Model override for the A slot, for example haiku
#   MODEL_B      Model override for the B slot; MODEL_A wins when A and B are both claude
#   CLAUDE_ARGS / CODEX_ARGS / AGY_ARGS  Extra CLI args, split on whitespace
#   AGENT_A_ARGS / AGENT_B_ARGS  Extra args for custom agent commands, split on whitespace
#   ENGINE_A / ENGINE_B and ENGINE_A_ARGS / ENGINE_B_ARGS are legacy aliases
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
#   AGENTS_TEMPLATE  Path to AGENTS.md template (default: resources/AGENTS.template.md beside this script)
#   PROMPTS_DIR  Directory for workflow prompt templates (default: resources/prompts beside this script)
#   RETRY_ON_LIMIT   1=wait and retry on quota/rate-limit errors (default: 1; 0=fail fast)
#   RETRY_MAX        Maximum rate-limit retries per agent call (default: 6)
#   RETRY_BASE_WAIT  Initial fallback wait in seconds when reset time cannot be parsed (default: 300)
#   RETRY_MAX_WAIT   Maximum fallback wait in seconds (default: 3600)
#   RETRY_MAX_RESET_WAIT  Abort instead of waiting when a parsed reset time is
#                    farther away than this many seconds (default: 21600)
#   RUNS_DIR         Root archive directory for each run (default: .workflow/runs)
#   RESUME_RUN       Resume an interrupted run: a run id, or "last" for the newest
#                    unfinished run. Completed stages are skipped; the task and
#                    immutable settings come from the run's saved snapshot.
set -Eeuo pipefail

# ---------- Settings ----------
alias_env_or_default() {  # $1: preferred public env  $2: legacy env  $3: default
  local preferred="$1" legacy="$2" default="$3" preferred_value legacy_value
  preferred_value="${!preferred-}"
  legacy_value="${!legacy-}"
  if [[ -n "$preferred_value" && -n "$legacy_value" && "$preferred_value" != "$legacy_value" ]]; then
    echo "Conflicting $preferred and $legacy; set only one or use the same value." >&2
    return 1
  fi
  if [[ -n "$preferred_value" ]]; then
    echo "$preferred_value"
  elif [[ -n "$legacy_value" ]]; then
    echo "$legacy_value"
  else
    echo "$default"
  fi
}

# ---------- Resume state (RESUME_RUN) ----------
# Per-run state lives in $WF/state/<run-id>/. resume.conf is parsed as data only:
# it is never sourced and never eval'd, and keys must match the allowlist below.
# That keeps "resume a run" from becoming "execute workspace file contents".
WF=".workflow"
RUN_STATE_ROOT="$WF/state"
QUOTA_ABORT_RC=75   # EX_TEMPFAIL: an agent call gave up on quota/rate limit; the run is resumable.
RUN_STATE_DIR=""
STAGE_LEDGER=""     # Empty until a run claims its state dir; stage_done is always false then.
RUN_LOCK_DIR=""
RESUME_HINT_PRINTED=""
RUN_TASK_ARG=""
RUN_TASK_SOURCE_KIND=""
RUN_TASK_SOURCE_PATH=""

list_run_state_ids() {  # Print known run ids, newest first, for error messages.
  [[ -d "$RUN_STATE_ROOT" ]] || return 0
  ls -1 "$RUN_STATE_ROOT" 2>/dev/null | sort -r
}

release_run_lock() {
  [[ -n "$RUN_LOCK_DIR" && -d "$RUN_LOCK_DIR" ]] && rmdir "$RUN_LOCK_DIR" 2>/dev/null
  return 0
}

on_workflow_exit() {
  local rc=$?
  release_run_lock
  exit "$rc"
}

install_run_traps() {
  trap on_workflow_exit EXIT
}

acquire_run_lock() {  # $1: run state dir; mkdir is the atomic lock primitive.
  if ! mkdir "$1/lock" 2>/dev/null; then
    echo "!! Run state $1 is locked; another attempt may still be running." >&2
    echo "   If you are sure the previous attempt is dead, remove the lock: rm -r $1/lock" >&2
    return 1
  fi
  RUN_LOCK_DIR="$1/lock"
  install_run_traps
}

parse_resume_conf() {  # $1: resume.conf path; sets RESUMED_* variables. Data-only: never source, never eval.
  local f="$1" line key value lineno=0
  [[ -f "$f" ]] || { echo "!! Missing resume settings snapshot: $f" >&2; return 1; }
  while IFS= read -r line || [[ -n "$line" ]]; do
    lineno=$(( lineno + 1 ))
    if (( lineno == 1 )); then
      [[ "$line" == "schema=1" ]] || { echo "!! $f: first line must be schema=1 (got [$line]); refusing to resume." >&2; return 1; }
      continue
    fi
    [[ -z "$line" ]] && continue
    [[ "$line" == *=* ]] || { echo "!! $f: malformed line [$line]; the state may be truncated. Refusing to resume." >&2; return 1; }
    key="${line%%=*}"
    value="${line#*=}"
    case "$key" in
      spec_dir)         RESUMED_SPEC_DIR="$value" ;;
      dual_spec)        RESUMED_DUAL_SPEC="$value" ;;
      auto_branch)      RESUMED_AUTO_BRANCH="$value" ;;
      use_worktree)     RESUMED_USE_WORKTREE="$value" ;;
      branch)           RESUMED_BRANCH="$value" ;;
      engine_a)         RESUMED_ENGINE_A="$value" ;;
      engine_b)         RESUMED_ENGINE_B="$value" ;;
      engine_a_args)    RESUMED_ENGINE_A_ARGS="$value" ;;
      engine_b_args)    RESUMED_ENGINE_B_ARGS="$value" ;;
      model_a)          RESUMED_MODEL_A="$value" ;;
      model_b)          RESUMED_MODEL_B="$value" ;;
      claude_args)      RESUMED_CLAUDE_ARGS="$value" ;;
      codex_args)       RESUMED_CODEX_ARGS="$value" ;;
      agy_args)         RESUMED_AGY_ARGS="$value" ;;
      max_rounds)       RESUMED_MAX_ROUNDS="$value" ;;
      human_gate)       RESUMED_HUMAN_GATE="$value" ;;
      open_pr)          RESUMED_OPEN_PR="$value" ;;
      tools)            RESUMED_TOOLS="$value" ;;
      gate_cmd)         RESUMED_GATE_CMD="$value" ;;
      build_gate_cmd)   RESUMED_BUILD_GATE_CMD="$value" ;;
      task_arg)         RESUMED_TASK_ARG="$value" ;;
      task_source_kind) RESUMED_TASK_SOURCE_KIND="$value" ;;
      task_source_path) RESUMED_TASK_SOURCE_PATH="$value" ;;
      *) echo "!! $f: unknown key [$key]; the state may be truncated or written by a newer version. Refusing to resume." >&2; return 1 ;;
    esac
  done < "$f"
  (( lineno >= 1 )) || { echo "!! $f is empty; refusing to resume." >&2; return 1; }
}

write_resume_conf() {  # Rewrite the effective settings snapshot atomically (temp file + mv).
  [[ -n "$RUN_STATE_DIR" ]] || return 0
  local f="$RUN_STATE_DIR/resume.conf" tmp branch kv content="schema=1"
  branch=$(git rev-parse --abbrev-ref HEAD)
  # task_arg is informational only; keep its first line so multi-line literal tasks stay persistable.
  local task_arg_line="${RUN_TASK_ARG%%$'\n'*}"
  for kv in \
    "spec_dir=$SPEC_DIR" \
    "dual_spec=$DUAL_SPEC" \
    "auto_branch=$AUTO_BRANCH" \
    "use_worktree=$USE_WORKTREE" \
    "branch=$branch" \
    "engine_a=$ENGINE_A" \
    "engine_b=$ENGINE_B" \
    "engine_a_args=$ENGINE_A_ARGS" \
    "engine_b_args=$ENGINE_B_ARGS" \
    "model_a=$MODEL_A" \
    "model_b=$MODEL_B" \
    "claude_args=$CLAUDE_ARGS" \
    "codex_args=$CODEX_ARGS" \
    "agy_args=$AGY_ARGS" \
    "max_rounds=$MAX_ROUNDS" \
    "human_gate=$HUMAN_GATE" \
    "open_pr=$OPEN_PR" \
    "tools=$TOOLS" \
    "gate_cmd=${GATE_CMD-}" \
    "build_gate_cmd=${BUILD_GATE_CMD-}" \
    "task_arg=$task_arg_line" \
    "task_source_kind=$RUN_TASK_SOURCE_KIND" \
    "task_source_path=$RUN_TASK_SOURCE_PATH"
  do
    case "$kv" in
      *$'\n'*)
        echo "!! Cannot persist resume settings: [${kv%%=*}] contains a newline." >&2
        return 1
        ;;
    esac
    content+=$'\n'"$kv"
  done
  tmp="$f.tmp.$$"
  printf '%s\n' "$content" > "$tmp"
  mv "$tmp" "$f"
}

resume_check_immutable() {  # $1: env var name  $2: snapshot value
  local current="${!1-}"
  [[ -z "$current" || "$current" == "$2" ]] && return 0
  echo "!! $1=$current conflicts with the resumed run's snapshot ($1=$2)." >&2
  echo "   SPEC_DIR/DUAL_SPEC/AUTO_BRANCH/USE_WORKTREE decide the stage graph and cannot change across resume." >&2
  echo "   Unset $1 to keep the snapshot value, or start a fresh run." >&2
  return 1
}

resume_load() {  # Resolve and validate RESUME_RUN, load the snapshot, and take the run lock.
  local id="$RESUME_RUN" d
  if [[ "$id" == "last" ]]; then
    id=""
    for d in $(list_run_state_ids); do
      [[ -f "$RUN_STATE_ROOT/$d/completed" ]] && continue
      id="$d"
      break
    done
    if [[ -z "$id" ]]; then
      echo "!! RESUME_RUN=last: no unfinished run found under $RUN_STATE_ROOT." >&2
      echo "   Known run ids: $(list_run_state_ids | tr '\n' ' ')" >&2
      return 1
    fi
  fi
  if [[ ! "$id" =~ ^[A-Za-z0-9_-]+$ ]]; then
    echo "!! Invalid RESUME_RUN [$id]: only letters, digits, - and _ are allowed." >&2
    return 1
  fi
  RUN_STATE_DIR="$RUN_STATE_ROOT/$id"
  if [[ ! -d "$RUN_STATE_DIR" ]]; then
    echo "!! No run state at $RUN_STATE_DIR." >&2
    echo "   Known run ids: $(list_run_state_ids | tr '\n' ' ')" >&2
    echo "   If that run used USE_WORKTREE=1, cd into its worktree first; the state lives there." >&2
    return 1
  fi
  if [[ -f "$RUN_STATE_DIR/completed" ]]; then
    echo "!! Run $id already completed; nothing to resume." >&2
    return 1
  fi
  parse_resume_conf "$RUN_STATE_DIR/resume.conf" || return 1
  resume_check_immutable SPEC_DIR "${RESUMED_SPEC_DIR-}" || return 1
  resume_check_immutable DUAL_SPEC "${RESUMED_DUAL_SPEC-}" || return 1
  resume_check_immutable AUTO_BRANCH "${RESUMED_AUTO_BRANCH-}" || return 1
  resume_check_immutable USE_WORKTREE "${RESUMED_USE_WORKTREE-}" || return 1
  acquire_run_lock "$RUN_STATE_DIR" || return 1
  STAGE_LEDGER="$RUN_STATE_DIR/completed-stages"
  RESUME_RUN="$id"
  echo "Resuming run $id (state: $RUN_STATE_DIR)" >&2
}

init_run_state() {  # $1: resolved task text; claim a fresh run's state dir atomically.
  mkdir -p "$RUN_STATE_ROOT"
  RUN_STATE_DIR="$RUN_STATE_ROOT/$RUN_ID"
  if ! mkdir "$RUN_STATE_DIR" 2>/dev/null; then
    echo "!! Run state $RUN_STATE_DIR already exists (same-second run id collision?). Rerun for a fresh id." >&2
    exit 1
  fi
  acquire_run_lock "$RUN_STATE_DIR" || exit 1
  STAGE_LEDGER="$RUN_STATE_DIR/completed-stages"
  : > "$STAGE_LEDGER"
  printf '%s\n' "$1" > "$RUN_STATE_DIR/task.txt"
}

RESUME_RUN="${RESUME_RUN:-}"
if [[ -n "$RESUME_RUN" ]]; then
  resume_load || { rc=$?; return "$rc" 2>/dev/null || exit "$rc"; }
fi
RUN_ID="${RESUME_RUN:-$(date +%Y%m%d-%H%M%S)}"

ENGINE_A="$(alias_env_or_default AGENT_A ENGINE_A "${RESUMED_ENGINE_A:-claude}")" || { rc=$?; return "$rc" 2>/dev/null || exit "$rc"; }
ENGINE_B="$(alias_env_or_default AGENT_B ENGINE_B "${RESUMED_ENGINE_B:-codex}")" || { rc=$?; return "$rc" 2>/dev/null || exit "$rc"; }
MODEL_A="${MODEL_A:-${RESUMED_MODEL_A:-}}"   # Model override for the A slot; empty means use the CLI default.
MODEL_B="${MODEL_B:-${RESUMED_MODEL_B:-}}"   # Model override for the B slot.
# Extra args for each CLI, split on whitespace. Example: CODEX_ARGS='-c model_reasoning_effort=low'
CLAUDE_ARGS="${CLAUDE_ARGS:-${RESUMED_CLAUDE_ARGS:-}}"
CODEX_ARGS="${CODEX_ARGS:-${RESUMED_CODEX_ARGS:-}}"
AGY_ARGS="${AGY_ARGS:-${RESUMED_AGY_ARGS:-}}"
ENGINE_A_ARGS="$(alias_env_or_default AGENT_A_ARGS ENGINE_A_ARGS "${RESUMED_ENGINE_A_ARGS:-}")" || { rc=$?; return "$rc" 2>/dev/null || exit "$rc"; }
ENGINE_B_ARGS="$(alias_env_or_default AGENT_B_ARGS ENGINE_B_ARGS "${RESUMED_ENGINE_B_ARGS:-}")" || { rc=$?; return "$rc" 2>/dev/null || exit "$rc"; }
MAX_ROUNDS="${MAX_ROUNDS:-${RESUMED_MAX_ROUNDS:-3}}"
AUTO_BRANCH="${AUTO_BRANCH:-${RESUMED_AUTO_BRANCH:-1}}"
USE_WORKTREE="${USE_WORKTREE:-${RESUMED_USE_WORKTREE:-0}}"
HUMAN_GATE="${HUMAN_GATE:-${RESUMED_HUMAN_GATE:-1}}"
DUAL_SPEC="${DUAL_SPEC:-${RESUMED_DUAL_SPEC:-0}}"
OPEN_PR="${OPEN_PR:-${RESUMED_OPEN_PR:-0}}"
NOTIFY_CMD="${NOTIFY_CMD:-}"   # Deliberately not persisted for resume; provide it again per attempt.
# Rate-limit retry: quota windows are not quality failures, so wait instead of burning review rounds.
RETRY_ON_LIMIT="${RETRY_ON_LIMIT:-1}"
RETRY_MAX="${RETRY_MAX:-6}"
RETRY_BASE_WAIT="${RETRY_BASE_WAIT:-300}"
RETRY_MAX_WAIT="${RETRY_MAX_WAIT:-3600}"
RETRY_MAX_RESET_WAIT="${RETRY_MAX_RESET_WAIT:-21600}"
# Minimum permissions: allow git and explicit build/test commands.
# Note that Bash(go *) includes go run, which executes arbitrary code. Do not broaden this casually.
TOOLS="${TOOLS:-${RESUMED_TOOLS:-Bash(git *),Bash(go test *),Bash(go build *),Bash(go vet *)}}"

SPEC_DIR="${SPEC_DIR:-${RESUMED_SPEC_DIR:-specs/$RUN_ID}}"
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
DUAL_SPEC_DECISION="${DUAL_SPEC_DECISION:-}"

usage() {
  {
    echo "Usage:$0 \"task description\""
    echo "      $0 task.md         # If the argument is a file, use its contents as the task"
    echo "      $0 print-agents    # Print the AGENTS.md rule template and exit"
  } >&2
  exit 1
}

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing required command:$1" >&2; exit 1; }; }

validate_engines() {
  local e
  for e in "$ENGINE_A" "$ENGINE_B"; do
    need "$e"
  done

  # codex and agy resume the most recent session.
  # Custom engines may have the same limitation, so v1 requires distinct command names.
  if [[ "$ENGINE_A" == "$ENGINE_B" && "$ENGINE_A" != "claude" ]]; then
    if is_builtin_engine "$ENGINE_A"; then
      echo "A and B cannot both use $ENGINE_A because session resume would interfere. Use different engines." >&2
    else
      echo "A and B cannot both use custom engine command $ENGINE_A. Use separate wrapper command names for worker and reviewer." >&2
    fi
    return 1
  fi
}

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

is_builtin_engine() {
  case "$1" in
    claude|codex|agy) return 0 ;;
    *) return 1 ;;
  esac
}

resolve_model_args() {
  case "$1" in
    claude) echo "$CLAUDE_ARGS" ;;
    codex) echo "$CODEX_ARGS" ;;
    agy) echo "$AGY_ARGS" ;;
    *)
      if [[ "$1" == "$ENGINE_A" ]]; then
        echo "$ENGINE_A_ARGS"
      elif [[ "$1" == "$ENGINE_B" ]]; then
        echo "$ENGINE_B_ARGS"
      else
        echo ""
      fi
      ;;
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

prompt_file_instruction() {  # $1: prompt artifact path; output the short prompt sent to CLIs.
  printf 'Read the full workflow prompt from this repository file and follow it exactly: %s\n' "$1"
}

prompt_template_path() {  # $1: prompt template name without .md
  printf '%s/%s.md\n' "$PROMPTS_DIR" "$1"
}

read_prompt_template() {  # $1: prompt template name without .md
  local path
  path=$(prompt_template_path "$1")
  if [[ ! -f "$path" ]]; then
    echo "(workflow prompt template not found:$path; keep resources/prompts with the script or set PROMPTS_DIR)" >&2
    return 1
  fi
  cat "$path"
}

render_prompt() {  # $1: prompt template name; rest: KEY=value placeholder replacements.
  local name="$1" text pair key value
  shift
  text=$(read_prompt_template "$name") || return 1
  for pair in "$@"; do
    key="${pair%%=*}"
    value="${pair#*=}"
    text="${text//"{{${key}}}"/$value}"
  done
  printf '%s\n' "$text"
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

init_live_state() {  # $1: optional "resume"
  mkdir -p "$WF"
  # Self-healing transients are always cleared; a resume must keep the durable
  # files later stages depend on (check_protected, apply_dual_spec_decision,
  # final-self-review), otherwise resuming deletes its own inputs (C3).
  local files=(
    "$WF/review.md"
    "$WF/verdict.json"
    "$WF/last-engine-output.txt"
    "$WF/pr-body.md"
  )
  if [[ "${1:-}" != "resume" ]]; then
    files+=(
      "$WF/suggestions.md"
      "$WF/protected-tests.txt"
      "$WF/protected-base.sha"
      "$WF/spec-merge-request.md"
    )
  fi
  rm -f "${files[@]}"
}

engine_model() {  # $1: engine; output its slot's model override, or empty if unset.
  is_builtin_engine "$1" || return 0
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

RESET_SANITY_MAX=2592000   # 30 days; anything beyond this is a parsing artefact, not a real reset time.

parse_reset_wait() {  # $1: output file  $2: now epoch for tests; output wait seconds, or empty on parse failure.
  # Only parses. The caller decides whether the wait is worth sitting through:
  # a large value here means "we know exactly when it resets", not "retry soon".
  local f="$1" now="${2:-$(date +%s)}" norm t target wait m num unit
  [[ -f "$f" ]] || return 0
  # Agents wrap their output, so a timestamp can straddle a newline. Flatten before matching.
  norm=$(tr '\n\r\t' '   ' < "$f" | tr -s ' ')

  # Format 1, Claude: "resets 10:50am" -> next occurrence of that clock time, plus a 120 second buffer.
  t=$(grep -oiE 'resets +[0-9]{1,2}:[0-9]{2} ?[ap]m' <<<"$norm" | head -1 | sed -E 's/^[Rr]esets +//; s/ //g' || true)
  if [[ -n "$t" ]]; then
    target=$(LC_ALL=C date -d "$t" +%s 2>/dev/null || true)
    if [[ -n "$target" ]]; then
      (( target <= now )) && target=$(( target + 86400 ))
      wait=$(( target - now + 120 ))
      (( wait > RESET_SANITY_MAX )) && return 0
      echo "$wait"
      return 0
    fi
  fi

  # Format 2, OpenAI/Codex: "try again in 20s / 2 minutes / 3 hours" -> that duration plus a 30 second buffer.
  m=$(grep -oiE 'try again in [0-9]+(\.[0-9]+)? ?(ms|milliseconds?|seconds?|secs?|s|minutes?|mins?|m|hours?|hrs?|h)\b' <<<"$norm" | head -1 || true)
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
    (( wait > RESET_SANITY_MAX )) && return 0
    echo "$wait"
    return 0
  fi

  # Format 3, Codex quota: "try again at Jul 14th, 2026 7:23 PM" -> absolute timestamp plus a 30 second buffer.
  # GNU date rejects the ordinal suffix, so strip it before parsing.
  m=$(grep -oiE '(try again at|resets at|resets on) [A-Za-z]{3,9} +[0-9]{1,2}(st|nd|rd|th)?,? +[0-9]{4},? +[0-9]{1,2}:[0-9]{2} *[ap]\.?m' <<<"$norm" | head -1 || true)
  if [[ -n "$m" ]]; then
    t=$(sed -E 's/^[^ ]+ [^ ]+ [^ ]+ //; s/([0-9]+)(st|nd|rd|th)/\1/I' <<<"$m")
    target=$(LC_ALL=C date -d "$t" +%s 2>/dev/null || true)
    if [[ -n "$target" ]]; then
      (( target <= now )) && { echo 30; return 0; }   # Already elapsed; retry after a short buffer.
      wait=$(( target - now + 30 ))
      (( wait > RESET_SANITY_MAX )) && return 0
      echo "$wait"
      return 0
    fi
  fi
  return 0
}

human_duration() {  # $1: seconds -> "3h 12m" / "45m"
  local s="$1"
  if (( s >= 3600 )); then echo "$(( s / 3600 ))h $(( (s % 3600) / 60 ))m"
  else echo "$(( s / 60 ))m"; fi
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

ensure_task_queue() {  # $1: plan.md; create the run's task queue from the plan on first entry (C2).
  # The script-held queue is the control flow; plan.md checkboxes are UI only.
  # An existing empty queue means every task already committed, so no fallback then.
  local queue="$RUN_STATE_DIR/tasks-remaining" tmp
  [[ -f "$queue" ]] && return 0
  tmp="$queue.tmp.$$"
  plan_tasks "$1" > "$tmp"
  if [[ ! -s "$tmp" ]]; then
    echo "(warning: plan.md has no \"- [ ] \" task list; falling back to one whole-plan implementation task)" >&2
    printf 'Complete the full implementation described in %s\n' "$1" > "$tmp"
  fi
  mv "$tmp" "$queue"
}

pop_task_queue() {  # Remove the queue's first line atomically, after that task committed.
  local queue="$RUN_STATE_DIR/tasks-remaining" tmp
  tmp="$queue.tmp.$$"
  tail -n +2 "$queue" > "$tmp"
  mv "$tmp" "$queue"
}

mark_plan_task_done() {  # $1: plan.md  $2: task text; flip the exact "- [ ]" line to "- [x]". No-op when already done.
  local plan="$1" tmp
  [[ -f "$plan" ]] || return 0
  tmp="$plan.tmp.$$"
  # Pass the task through the environment: awk -v would expand backslash escapes in task text.
  TASK="$2" awk '
    $0 == "- [ ] " ENVIRON["TASK"] { print "- [x] " ENVIRON["TASK"]; next }
    { print }
  ' "$plan" > "$tmp"
  mv "$tmp" "$plan"
}

restore_or_record_acceptance_base() {  # Output the acceptance-test base sha, persisting it on first entry (C4).
  # Without persistence, an interrupt between the acceptance commit and the protected-list
  # write would recompute an empty diff on resume and silently disable test protection.
  local f base
  if [[ -n "$RUN_STATE_DIR" ]]; then
    f="$RUN_STATE_DIR/acceptance-test-base"
    if [[ -f "$f" ]]; then
      cat "$f"
      return 0
    fi
    base=$(git rev-parse HEAD)
    printf '%s\n' "$base" > "$f.tmp.$$"
    mv "$f.tmp.$$" "$f"
    printf '%s\n' "$base"
  else
    git rev-parse HEAD
  fi
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

candidate_spec_for_slot() {  # $1: A or B; output candidate spec path for that slot.
  case "$1" in
    A) echo "$SPEC_DIR/spec-a.md" ;;
    B) echo "$SPEC_DIR/spec-b.md" ;;
    *) return 1 ;;
  esac
}

collect_review_suggestions_enabled() {
  [[ "${COLLECT_REVIEW_SUGGESTIONS:-1}" == "1" ]]
}

dual_spec_final_review_scope() {  # $1: canonical decision, optional
  local decision="${1:-}"
  local merge_instruction=""
  if [[ "$decision" == merge-* ]]; then
    merge_instruction=" Also compare $SPEC_DIR/spec.md with $WF/spec-merge-request.md and block approval if any requested adoption item is missing, distorted, or contradicted."
  fi
  render_prompt review-scope-dual-final \
    "SPEC_FILE=$SPEC_DIR/spec.md" \
    "MERGE_INSTRUCTION=$merge_instruction"
}

write_spec_merge_request_template() {  # $1: base slot  $2: other slot
  local base_slot="$1" other_slot="$2" base_file other_file
  base_file=$(candidate_spec_for_slot "$base_slot")
  other_file=$(candidate_spec_for_slot "$other_slot")
  mkdir -p "$WF"
  cat > "$WF/spec-merge-request.md" <<EOF
# Dual Spec Merge Request

- base owner: $base_slot
- base spec: $base_file
- adopt from owner: $other_slot
- adopt from spec: $other_file

## Items to adopt from $other_slot

Replace this paragraph with the concrete requirements, acceptance criteria,
edge cases, non-goals, assumptions, or wording that the final spec owner must
adopt from $other_file.
EOF
}

merge_request_has_content() {
  local items template_prefix
  [[ -f "$WF/spec-merge-request.md" ]] || return 1
  items=$(awk '
    /^## Items to adopt / { in_items=1; next }
    in_items {
      lines[++n]=$0
      if ($0 !~ /^[[:space:]]*$/) last=n
    }
    END {
      first=1
      while (first <= last && lines[first] ~ /^[[:space:]]*$/) first++
      for (i=first; i<=last; i++) print lines[i]
    }
  ' "$WF/spec-merge-request.md")
  template_prefix=$'Replace this paragraph with the concrete requirements, acceptance criteria,\nedge cases, non-goals, assumptions, or wording that the final spec owner must\nadopt from '
  case "$items" in
    "$template_prefix"*.) return 1 ;;
  esac
  [[ -n "$items" ]]
}

apply_dual_spec_decision() {  # $1: canonical decision  $2: task text
  local decision="$1" task="$2" owner_slot other_slot base_file other_file prompt
  owner_slot=$(dual_spec_owner_slot "$decision")
  set_spec_roles_from_slot "$owner_slot"
  other_slot="$SPEC_REVIEWER_SLOT"
  base_file=$(candidate_spec_for_slot "$owner_slot")
  other_file=$(candidate_spec_for_slot "$other_slot")
  mkdir -p "$SPEC_DIR"

  case "$decision" in
    adopt-a|adopt-b)
      cp "$base_file" "$SPEC_DIR/spec.md"
      ;;
    merge-a|merge-b)
      cp "$base_file" "$SPEC_DIR/spec.md"
      prompt=$(render_prompt dual-spec-merge-final \
        "BASE_FILE=$base_file" \
        "OTHER_FILE=$other_file" \
        "MERGE_REQUEST_FILE=$WF/spec-merge-request.md" \
        "SPEC_FILE=$SPEC_DIR/spec.md" \
        "TASK=$task")
      work "$SPEC_OWNER_ENGINE" "$prompt"
      ;;
    *)
      echo "Unsupported dual spec decision:$decision" >&2
      return 1
      ;;
  esac

  review_loop "$SPEC_REVIEWER_ENGINE" "$SPEC_OWNER_ENGINE" "$(dual_spec_final_review_scope "$decision")"
  human_gate_spec
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
# Keep the rules in resources/AGENTS.template.md for easier maintenance.
# Simple English works best across all supported models.
AGENTS_MARKER='<!-- adversarial-ai-coding:begin -->'
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESOURCES_DIR="${RESOURCES_DIR:-$SCRIPT_DIR/resources}"
PROMPTS_DIR="${PROMPTS_DIR:-$RESOURCES_DIR/prompts}"
AGENTS_TEMPLATE="${AGENTS_TEMPLATE:-$RESOURCES_DIR/AGENTS.template.md}"

write_agents_section() {  # Print the rule template; return 1 when the template is missing.
  if [[ ! -f "$AGENTS_TEMPLATE" ]]; then
    echo "(AGENTS.md template not found:$AGENTS_TEMPLATE; keep resources/AGENTS.template.md with the script or set AGENTS_TEMPLATE)" >&2
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
CURRENT_ENGINE=""
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

generic_engine_args() {  # $1: custom engine command; output slot args for metadata/execution.
  if [[ "$1" == "$ENGINE_A" ]]; then
    echo "$ENGINE_A_ARGS"
  elif [[ "$1" == "$ENGINE_B" ]]; then
    echo "$ENGINE_B_ARGS"
  fi
}

run_generic_engine() {  # $1: custom engine command  $2: prompt
  local engine="$1" prompt="$2" args=()
  # shellcheck disable=SC2206  # Custom args intentionally follow existing whitespace-split behavior.
  args+=($(generic_engine_args "$engine"))
  "$engine" "${args[@]}" "$prompt" 2>&1 | tee "$ENGINE_OUT"
}

w_generic() {
  run_generic_engine "$CURRENT_ENGINE" "$1"
}

worker_fn_for_engine() {  # $1: engine; output worker function name.
  if is_builtin_engine "$1"; then
    echo "w_$1"
  else
    echo "w_generic"
  fi
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

engine_call() {  # $1: role  $2: engine  $3: artifact slug  $4: engine fn  $5: prompt file
  local role="$1" engine="$2" slug="$3" fn="$4" prompt_file="$5" prompt
  local n=0 attempt=1 rc w eta
  prompt=$(prompt_file_instruction "$prompt_file")
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
    w=$(parse_reset_wait "$ENGINE_OUT")   # Prefer waiting until the parsed reset time.
    if [[ -n "$w" ]] && (( w > RETRY_MAX_RESET_WAIT )); then
      # The message told us exactly when the quota returns and it is far away.
      # Backing off here would burn hours of sleep and still fail, so stop now.
      eta=$(date -d "+$w seconds" '+%Y-%m-%d %H:%M' 2>/dev/null || true)
      echo "!! Quota resets in $(human_duration "$w") (about $eta), beyond RETRY_MAX_RESET_WAIT=${RETRY_MAX_RESET_WAIT}s. Not waiting; rerun after the reset." >&2
      notify "adversarial-ai-coding: quota exhausted until $eta; run aborted"
      return "$rc"
    fi
    n=$(( n + 1 ))
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
  local t0=$SECONDS prompt_art output_art slug fn
  LAST_COST=""
  CURRENT_ENGINE="$1"
  log_section "AI call" "worker" "$1" "$CUR_STAGE" "$CUR_ROUND"
  echo ">>> Worker($1) is running..."
  slug="worker-$(safe_slug "${CUR_STAGE:-startup}")-r${CUR_ROUND}"
  prompt_art=$(archive_text "${slug}-prompt.md" "$2" "worker" "$1" "$CUR_STAGE" "$CUR_ROUND")
  output_art=$(art_path "${slug}-output.txt")
  fn=$(worker_fn_for_engine "$1")
  # Use process substitution instead of a pipeline so engine functions stay in the current shell.
  # Otherwise WORKER_SESSION updates would be lost in a subshell.
  engine_call "worker" "$1" "$slug" "$fn" "$prompt_art" > >(tee -a "$LOG" | tee "$output_art")
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
  local base viol n=0 prompt
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
    prompt=$(render_prompt protected-tests-modified \
      "VIOLATIONS=$viol" \
      "BASE=$base" \
      "SPEC_FILE=$SPEC_DIR/spec.md")
    work "$1" "$prompt"
    CHECKING_PROTECTED=0
  done
}

# ---------- Reviewer engines; each round starts with fresh context from files and diffs ----------
# Verdict grading: blockers must be fixed; suggestions do not block and are evaluated at the end.
VERDICT_SCHEMA='{"type":"object","properties":{"approved":{"type":"boolean"},"blockers":{"type":"array","items":{"type":"string"}},"suggestions":{"type":"array","items":{"type":"string"}}},"required":["approved","blockers","suggestions"]}'

review_prompt() {  # $1: review scope for this round
  render_prompt review "SCOPE=$1" "WF=$WF"
}

verdict_file_instr() {
  render_prompt verdict-file-instruction "WF=$WF"
}

compose_review_prompt() {  # $1: engine  $2: review scope for this round
  local engine="$1" scope="$2" prompt
  prompt="$(review_prompt "$scope")"
  case "$engine" in
    claude) printf '%s\n' "$prompt" ;;
    *) printf '%s\n%s\n' "$prompt" "$(verdict_file_instr)" ;;
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

r_generic() {
  run_generic_engine "$CURRENT_ENGINE" "$1"
}

reviewer_fn_for_engine() {  # $1: engine; output reviewer function name.
  if is_builtin_engine "$1"; then
    echo "r_$1"
  else
    echo "r_generic"
  fi
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
  local t0=$SECONDS prompt prompt_art output_art slug fn
  LAST_COST=""
  CURRENT_ENGINE="$1"
  log_section "review" "reviewer" "$1" "$CUR_STAGE" "$CUR_ROUND"
  echo ">>> Reviewer($1) is reviewing..."
  slug="reviewer-$(safe_slug "${CUR_STAGE:-startup}")-r${CUR_ROUND}"
  prompt="$(compose_review_prompt "$1" "$2")"
  prompt_art=$(archive_text "${slug}-prompt.md" "$prompt" "reviewer" "$1" "$CUR_STAGE" "$CUR_ROUND")
  output_art=$(art_path "${slug}-output.txt")
  fn=$(reviewer_fn_for_engine "$1")
  # Prewrite a failed sentinel instead of deleting the file:
  # if the reviewer does not write a verdict, the run stays failed.
  # This also avoids apply_patch failures against a missing file.
  printf '{"approved": false, "blockers": ["reviewer did not write a verdict"], "suggestions": []}\n' > "$WF/verdict.json"
  engine_call "reviewer" "$1" "$slug" "$fn" "$prompt_art" > >(tee -a "$LOG" | tee "$output_art") || echo "(warning: reviewer execution failed)" >&2
  write_meta "$output_art" "reviewer" "$1" "$(engine_model "$1")" "$(resolve_model_args "$1")" "$CUR_STAGE" "$CUR_ROUND"
  archive_snapshot "$ENGINE_OUT" "${slug}-final.raw" "reviewer" "$1" "$CUR_STAGE" "$CUR_ROUND" >/dev/null
  metric reviewer "$1" "$CUR_ROUND" "$(( SECONDS - t0 ))" "$LAST_COST"
  if [[ ! -f "$WF/verdict.json" ]]; then
    echo "(reviewer did not write verdict.json; treating as failed)" >&2
    return 1
  fi
  collect_review_suggestions_enabled && collect_suggestions
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
  local engine="$1" cmd="$2" n=1 out prompt
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
    prompt=$(render_prompt quality-gate-failed \
      "COMMAND=$cmd" \
      "OUTPUT=$(printf '%s\n' "$out" | tail -150)")
    work "$engine" "$prompt"
  done
}

# ---------- Stage flow ----------
CUR_STAGE=""
CUR_ROUND=1

stage_done() {  # $1: stage name; true when the run ledger records it. Always false before a run claims state.
  [[ -n "$STAGE_LEDGER" && -f "$STAGE_LEDGER" ]] && grep -Fxq "$1" "$STAGE_LEDGER"
}

begin_stage() {  # $1: name  $2...: artifacts the stage must have left behind to be skippable.
  # Returns 1 when the ledger already records the stage, so callers guard with:
  #   if begin_stage "name" artifacts...; then ...; end_stage; fi
  local name="$1" artifact
  shift
  if stage_done "$name"; then
    for artifact in "$@"; do
      if [[ ! -e "$artifact" ]]; then
        echo "!! Stage $name is recorded complete but its artifact $artifact is missing." >&2
        echo "   Restore it from the run archive under $RUNS_DIR, or delete $RUN_STATE_DIR to start over." >&2
        exit 1
      fi
    done
    echo "== skip [$name] (already completed in run $RUN_ID)" | tee -a "$LOG"
    return 1
  fi
  CUR_STAGE="$name"
  WORKER_SESSION=""   # Worker session resumes within a stage and resets across stages.
  CUR_ROUND=1
  log_section "stage begin" "workflow" "workflow" "$CUR_STAGE" "$CUR_ROUND"
  printf '\n================ [%s] ================\n' "$name" | tee -a "$LOG"
}

end_stage() {  # Record stage completion and the checkpoint HEAD. No-op before a run claims state.
  [[ -n "$STAGE_LEDGER" ]] || return 0
  printf '%s\n' "$CUR_STAGE" >> "$STAGE_LEDGER"
  git rev-parse HEAD > "$RUN_STATE_DIR/last-head.tmp.$$"
  mv "$RUN_STATE_DIR/last-head.tmp.$$" "$RUN_STATE_DIR/last-head"
}

review_loop() {  # $1: reviewer engine  $2: worker engine  $3: review scope  $4: optional gate command for repair rounds
  local gate_cmd="${4:-}" prompt
  CUR_ROUND=1
  until run_review "$1" "$3"; do
    if (( CUR_ROUND >= MAX_ROUNDS )); then
      echo "!! [$CUR_STAGE] Review still failed after $MAX_ROUNDS rounds; stopping. Read $WF/review.md and handle it manually." >&2
      notify "adversarial-ai-coding:[$CUR_STAGE] review failed after $MAX_ROUNDS rounds; human intervention required"
      exit 1
    fi
    CUR_ROUND=$(( CUR_ROUND + 1 ))
    echo "--- [$CUR_STAGE] round $CUR_ROUND: worker updates from review findings ---" | tee -a "$LOG"
    prompt=$(render_prompt review-findings-repair \
      "REVIEW_FILE=$WF/review.md" \
      "STAGE=$CUR_STAGE")
    work "$2" "$prompt"
    archive_snapshot "$WF/review.md" "review-$(safe_slug "$CUR_STAGE")-r${CUR_ROUND}-worker.md" "worker" "$2" "$CUR_STAGE" "$CUR_ROUND" >/dev/null
    gate_loop "$2" "$gate_cmd"   # Repairs must pass the deterministic gate before review resumes.
  done
  echo "[$CUR_STAGE] Review approved" | tee -a "$LOG"
}

commit_work() {  # $1: worker engine  $2: description of this commit
  local prompt
  log_section "commit" "worker" "$1" "$CUR_STAGE" "$CUR_ROUND"
  prompt=$(render_prompt commit-approved-work "DESCRIPTION=$2")
  work "$1" "$prompt"
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

run_candidate_spec_review() {  # $1: reviewer engine  $2: scope  $3: review output  $4: verdict output
  local reviewer="$1" scope="$2" review_out="$3" verdict_out="$4" old_collect="${COLLECT_REVIEW_SUGGESTIONS-__unset__}"
  rm -f "$WF/review.md" "$WF/verdict.json"
  COLLECT_REVIEW_SUGGESTIONS=0
  if ! run_review "$reviewer" "$scope"; then
    echo "(candidate spec review recorded a non-approved verdict; continuing to comparison)" | tee -a "$LOG" >&2
  fi
  if [[ "$old_collect" == "__unset__" ]]; then
    unset COLLECT_REVIEW_SUGGESTIONS
  else
    COLLECT_REVIEW_SUGGESTIONS="$old_collect"
  fi
  [[ -f "$WF/review.md" ]] || printf '(reviewer did not write review.md)\n' > "$WF/review.md"
  [[ -f "$WF/verdict.json" ]] || printf '{"approved": false, "blockers": ["reviewer did not write a verdict"], "suggestions": []}\n' > "$WF/verdict.json"
  cp "$WF/review.md" "$review_out"
  cp "$WF/verdict.json" "$verdict_out"
  archive_snapshot "$review_out" "$(basename "$review_out")" "reviewer" "$reviewer" "$CUR_STAGE" "$CUR_ROUND" >/dev/null
  archive_snapshot "$verdict_out" "$(basename "$verdict_out")" "reviewer" "$reviewer" "$CUR_STAGE" "$CUR_ROUND" >/dev/null
}

write_spec_comparison_index() {
  cat > "$SPEC_DIR/spec-comparison.md" <<EOF
# Dual Spec Comparison

Review these files before choosing the final spec owner:

- Candidate A: $SPEC_DIR/spec-a.md
- Candidate B: $SPEC_DIR/spec-b.md
- A's review of B: $SPEC_DIR/spec-b.review-by-a.md
- B's review of A: $SPEC_DIR/spec-a.review-by-b.md
- A's comparison table: $SPEC_DIR/spec-comparison-a.md
- B's comparison table: $SPEC_DIR/spec-comparison-b.md

Decision commands:

- a: adopt Candidate A as the base final spec
- b: adopt Candidate B as the base final spec
- ma: use Candidate A as base and explicitly adopt selected items from Candidate B
- mb: use Candidate B as base and explicitly adopt selected items from Candidate A
EOF
  archive_snapshot "$SPEC_DIR/spec-comparison.md" "spec-comparison.md" "workflow" "workflow" "$CUR_STAGE" "$CUR_ROUND" >/dev/null
}

write_dual_spec_decision_file() {  # $1: canonical decision
  local decision="$1" owner_slot reviewer_slot
  owner_slot=$(dual_spec_owner_slot "$decision")
  reviewer_slot=$(reviewer_slot_for_owner_slot "$owner_slot")
  cat > "$SPEC_DIR/spec-decision.md" <<EOF
# Dual Spec Decision

- decision: $decision
- selected owner slot: $owner_slot
- selected owner engine: $(engine_for_slot "$owner_slot")
- reviewer slot: $reviewer_slot
- reviewer engine: $(engine_for_slot "$reviewer_slot")
- candidate A: $SPEC_DIR/spec-a.md
- candidate B: $SPEC_DIR/spec-b.md

The selected owner produces or owns the final $SPEC_DIR/spec.md.
The reviewer must approve the final spec before implementation planning starts.
EOF
  archive_snapshot "$SPEC_DIR/spec-decision.md" "spec-decision.md" "workflow" "workflow" "$CUR_STAGE" "$CUR_ROUND" >/dev/null
}

human_gate_dual_spec_decision() {
  local raw decision owner_slot other_slot
  log_section "dual spec human selection" "workflow" "workflow" "$CUR_STAGE" "$CUR_ROUND"
  notify "adversarial-ai-coding: dual spec comparison awaits human selection ($SPEC_DIR/spec-comparison.md)"
  {
    echo ""
    echo "### Human checkpoint: compare dual spec candidates."
    echo "### Read:"
    echo "### - $SPEC_DIR/spec-a.md"
    echo "### - $SPEC_DIR/spec-b.md"
    echo "### - $SPEC_DIR/spec-comparison-a.md"
    echo "### - $SPEC_DIR/spec-comparison-b.md"
    echo "### - $SPEC_DIR/spec-comparison.md"
    echo "### Choose: a, b, ma, or mb. Final spec review and human approval run after this selection."
  } >/dev/tty

  while true; do
    read -rp "Dual spec decision [a/b/ma/mb]:" raw </dev/tty
    if decision=$(normalize_dual_spec_decision "$raw"); then
      break
    fi
    echo "Invalid decision. Enter a, b, ma, or mb." >/dev/tty
  done

  owner_slot=$(dual_spec_owner_slot "$decision")
  other_slot=$(reviewer_slot_for_owner_slot "$owner_slot")
  if [[ "$decision" == merge-* ]]; then
    write_spec_merge_request_template "$owner_slot" "$other_slot"
    {
      echo ""
      echo "### Edit $WF/spec-merge-request.md now."
      echo "### List the exact items the selected owner must adopt from $(candidate_spec_for_slot "$other_slot")."
    } >/dev/tty
    read -rp "Enter y after editing the merge request; anything else aborts:" raw </dev/tty
    if [[ "$raw" != "y" && "$raw" != "Y" ]]; then
      echo "Aborted: merge request was not approved." >&2
      exit 1
    fi
    if ! merge_request_has_content; then
      echo "Aborted: $WF/spec-merge-request.md does not contain explicit adoption instructions." >&2
      exit 1
    fi
    archive_snapshot "$WF/spec-merge-request.md" "spec-merge-request.md" "workflow" "workflow" "$CUR_STAGE" "$CUR_ROUND" >/dev/null
  fi

  write_dual_spec_decision_file "$decision"
  DUAL_SPEC_DECISION="$decision"
}

restore_dual_spec_decision() {  # Rebuild DUAL_SPEC_DECISION and the owner roles from spec-decision.md (C5).
  # On resume, a skipped select-spec stage leaves DUAL_SPEC_DECISION empty; every later
  # stage reads the owner/reviewer roles, so restore them before anything uses them.
  [[ "$DUAL_SPEC" == "1" && -z "$DUAL_SPEC_DECISION" ]] || return 0
  local f="$SPEC_DIR/spec-decision.md" decision slot
  if [[ ! -f "$f" ]]; then
    echo "!! DUAL_SPEC run has no decision yet and no $f to restore it from." >&2
    exit 1
  fi
  decision=$(sed -n 's/^- decision: //p' "$f" | head -1)
  if ! slot=$(dual_spec_owner_slot "$decision"); then
    echo "!! Invalid decision [$decision] in $f; cannot restore the dual-spec selection." >&2
    exit 1
  fi
  if [[ "$decision" == merge-* && ! -f "$WF/spec-merge-request.md" ]]; then
    echo "!! Decision $decision needs $WF/spec-merge-request.md, which is missing." >&2
    echo "   Restore it from the run archive under $RUNS_DIR, then resume again." >&2
    exit 1
  fi
  DUAL_SPEC_DECISION="$decision"
  set_spec_roles_from_slot "$slot"
  echo "(restored dual-spec decision: $decision; owner $SPEC_OWNER_SLOT=$SPEC_OWNER_ENGINE)" >&2
}

run_dual_spec_spec_stage() {  # $1: task text
  local task="$1" decision prompt scope
  mkdir -p "$SPEC_DIR"

  if begin_stage "write-spec-a" "$SPEC_DIR/spec-a.md"; then
    prompt=$(render_prompt dual-spec-write-candidate \
      "SPEC_FILE=$SPEC_DIR/spec-a.md" \
      "OTHER_SPEC_FILE=$SPEC_DIR/spec-b.md" \
      "TASK=$task")
    work "$ENGINE_A" "$prompt"
    end_stage
  fi

  if begin_stage "write-spec-b" "$SPEC_DIR/spec-b.md"; then
    prompt=$(render_prompt dual-spec-write-candidate \
      "SPEC_FILE=$SPEC_DIR/spec-b.md" \
      "OTHER_SPEC_FILE=$SPEC_DIR/spec-a.md" \
      "TASK=$task")
    work "$ENGINE_B" "$prompt"
    end_stage
  fi

  if begin_stage "review-spec-a" "$SPEC_DIR/spec-a.review-by-b.md" "$SPEC_DIR/spec-a.verdict-by-b.json"; then
    scope=$(render_prompt review-scope-candidate-spec \
      "SPEC_FILE=$SPEC_DIR/spec-a.md" \
      "CANDIDATE=A" \
      "OTHER_CANDIDATE=B")
    run_candidate_spec_review "$ENGINE_B" "$scope" "$SPEC_DIR/spec-a.review-by-b.md" "$SPEC_DIR/spec-a.verdict-by-b.json"
    end_stage
  fi

  if begin_stage "review-spec-b" "$SPEC_DIR/spec-b.review-by-a.md" "$SPEC_DIR/spec-b.verdict-by-a.json"; then
    scope=$(render_prompt review-scope-candidate-spec \
      "SPEC_FILE=$SPEC_DIR/spec-b.md" \
      "CANDIDATE=B" \
      "OTHER_CANDIDATE=A")
    run_candidate_spec_review "$ENGINE_A" "$scope" "$SPEC_DIR/spec-b.review-by-a.md" "$SPEC_DIR/spec-b.verdict-by-a.json"
    end_stage
  fi

  if begin_stage "compare-specs-a" "$SPEC_DIR/spec-comparison-a.md"; then
    prompt=$(render_prompt dual-spec-compare \
      "OUTPUT_FILE=$SPEC_DIR/spec-comparison-a.md" \
      "SPEC_A_FILE=$SPEC_DIR/spec-a.md" \
      "SPEC_B_FILE=$SPEC_DIR/spec-b.md" \
      "SPEC_A_REVIEW_FILE=$SPEC_DIR/spec-a.review-by-b.md" \
      "SPEC_B_REVIEW_FILE=$SPEC_DIR/spec-b.review-by-a.md")
    work "$ENGINE_A" "$prompt"
    end_stage
  fi

  if begin_stage "compare-specs-b" "$SPEC_DIR/spec-comparison-b.md"; then
    prompt=$(render_prompt dual-spec-compare \
      "OUTPUT_FILE=$SPEC_DIR/spec-comparison-b.md" \
      "SPEC_A_FILE=$SPEC_DIR/spec-a.md" \
      "SPEC_B_FILE=$SPEC_DIR/spec-b.md" \
      "SPEC_A_REVIEW_FILE=$SPEC_DIR/spec-a.review-by-b.md" \
      "SPEC_B_REVIEW_FILE=$SPEC_DIR/spec-b.review-by-a.md")
    work "$ENGINE_B" "$prompt"
    end_stage
  fi
  # Kept outside the stage guards: idempotent pure file write, no AI cost.
  write_spec_comparison_index

  if begin_stage "select-spec" "$SPEC_DIR/spec-decision.md"; then
    human_gate_dual_spec_decision
    end_stage
  fi
  restore_dual_spec_decision   # A skipped select-spec leaves the decision empty on resume.
  decision="$DUAL_SPEC_DECISION"

  if begin_stage "finalize-spec" "$SPEC_DIR/spec.md"; then
    apply_dual_spec_decision "$decision" "$task"
    end_stage
  fi
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

Generated by adversarial-ai-coding, with original slots A=$ENGINE_A and B=$ENGINE_B.
Final spec owner/worker: $SPEC_OWNER_SLOT=$SPEC_OWNER_ENGINE. Reviewer: $SPEC_REVIEWER_SLOT=$SPEC_REVIEWER_ENGINE.
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

verify_last_head() {  # Fail closed when the recorded checkpoint is not reachable from HEAD (C6 light).
  local lh_file="$RUN_STATE_DIR/last-head" lh head
  if [[ ! -f "$lh_file" ]]; then
    if [[ -n "$STAGE_LEDGER" && -s "$STAGE_LEDGER" ]]; then
      echo "!! Run $RUN_ID has completed stages but no last-head checkpoint; the state is damaged." >&2
      echo "   Start a fresh run, or delete $RUN_STATE_DIR if you no longer need it." >&2
      exit 1
    fi
    return 0
  fi
  lh=$(cat "$lh_file")
  head=$(git rev-parse HEAD)
  [[ "$head" == "$lh" ]] && return 0
  if git merge-base --is-ancestor "$lh" HEAD 2>/dev/null; then
    echo "(warning: new commits exist after the resume checkpoint $lh; continuing)" >&2
    return 0
  fi
  echo "!! The resume checkpoint $lh is not reachable from HEAD (branch reset/rebase, or the wrong repository)." >&2
  echo "   Fix the branch first, or delete $lh_file to force the resume." >&2
  exit 1
}

resume_workspace() {  # Reattach to the resumed run's branch; never create branches or worktrees here.
  local current
  current=$(git rev-parse --abbrev-ref HEAD)
  if [[ -z "${RESUMED_BRANCH:-}" ]]; then
    echo "(warning: the resume snapshot has no branch record; staying on $current)" >&2
  elif [[ "$current" != "$RESUMED_BRANCH" ]]; then
    if git show-ref --verify --quiet "refs/heads/$RESUMED_BRANCH"; then
      git switch "$RESUMED_BRANCH"
    else
      echo "!! The resumed run's branch $RESUMED_BRANCH no longer exists in this repository." >&2
      echo "   If the run used USE_WORKTREE=1, cd into its worktree and resume there." >&2
      exit 1
    fi
  fi
  verify_last_head
  if [[ -n "$(git status --porcelain)" ]]; then
    echo "!! The working tree is dirty. These changes will be absorbed into the next automatic commit (git add -A):" >&2
    git status --short >&2
  fi
}

setup_workspace() {  # Prepare an isolated workspace according to USE_WORKTREE / AUTO_BRANCH.
  if [[ -n "$RESUME_RUN" ]]; then
    resume_workspace
    return 0
  fi
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
  local task_arg task_source_kind task_source_path="" task_resolved_text prompt scope
  [[ -n "$task" || -n "$RESUME_RUN" ]] || usage

  if [[ "$task" == "print-agents" ]]; then
    write_agents_section
    return 0
  fi
  task_arg="$task"
  if [[ -n "$task" && -f "$task" ]]; then
    task_source_kind="file"
    task_source_path=$(abs_path "$task")
    echo "Reading task description from file:$task"
    task_resolved_text="$(cat "$task")"
    task="$task_resolved_text"
  else
    task_source_kind="literal"
    task_resolved_text="$task"
  fi

  if [[ -n "$RESUME_RUN" ]]; then
    # The snapshot is the single source of truth for the task (C6): resuming must not
    # silently re-read a file that may have changed since the original run started.
    local task_snapshot
    task_snapshot=$(cat "$RUN_STATE_DIR/task.txt" 2>/dev/null) \
      || { echo "!! Missing $RUN_STATE_DIR/task.txt; the run state is damaged. Start a fresh run." >&2; exit 1; }
    if [[ -n "$task" && "$task_resolved_text" != "$task_snapshot" ]]; then
      echo "!! The task argument resolves to different text than the resumed run's task snapshot." >&2
      echo "   Resume without a task argument (the snapshot is used), or start a fresh run." >&2
      exit 1
    fi
    task="$task_snapshot"
    task_resolved_text="$task_snapshot"
    task_arg="${RESUMED_TASK_ARG:-}"
    task_source_kind="${RESUMED_TASK_SOURCE_KIND:-literal}"
    task_source_path="${RESUMED_TASK_SOURCE_PATH:-}"
    echo "Resumed task from snapshot (original source: $task_source_kind${task_source_path:+ $task_source_path})"
  fi
  RUN_TASK_ARG="$task_arg"
  RUN_TASK_SOURCE_KIND="$task_source_kind"
  RUN_TASK_SOURCE_PATH="$task_source_path"

  need git; need jq
  validate_engines || exit 1

  git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || { echo "Run this script from the root of the target git repository." >&2; exit 1; }

  dual_spec_preflight || exit 1

  echo "Workflow settings:A=$ENGINE_A  B=$ENGINE_B  DUAL_SPEC=$DUAL_SPEC  MAX_ROUNDS=$MAX_ROUNDS  SPEC_DIR=$SPEC_DIR"
  echo "Task:$task"

  setup_workspace   # May cd into a worktree; relative paths after this point use that workspace.

  establish_run_archive
  if [[ -n "$RESUME_RUN" ]]; then
    init_live_state resume
  else
    init_run_state "$task_resolved_text"
    init_live_state
  fi
  echo '*' > "$WF/.gitignore"   # Keep the whole .workflow/ directory out of version control.
  write_run_metadata
  write_log_metadata
  archive_task "$task_arg" "$task_source_kind" "$task_source_path" "$task_resolved_text"
  printf '%s\n' "$WF_RUN" > "$WF/latest-run.txt"
  log_section "startup settings" "workflow" "workflow" "startup" "0"

  trap 'echo "!! Workflow interrupted (exit=$?). Full run log: '"$LOG"'" >&2' ERR

  bootstrap_agents_md

  GATE_CMD="${GATE_CMD:-${RESUMED_GATE_CMD:-$(detect_gate)}}"
  BUILD_GATE_CMD="${BUILD_GATE_CMD:-${RESUMED_BUILD_GATE_CMD:-$(detect_build_gate)}}"
  if [[ -n "$GATE_CMD" ]]; then
    echo "Quality gate:$GATE_CMD"
  else
    echo "(warning: no quality gate command detected; deterministic gates are disabled. Set GATE_CMD to enable one.)" >&2
  fi
  # Snapshot the effective settings before the first AI call. Rewritten every attempt,
  # so explicit overrides given on a resume carry over to the next resume.
  write_resume_conf

  if [[ "$DUAL_SPEC" == "1" ]]; then
    run_dual_spec_spec_stage "$task"
  else
    set_spec_roles_from_slot A
    if begin_stage "write-spec" "$SPEC_DIR/spec.md"; then
      prompt=$(render_prompt write-spec \
        "SPEC_FILE=$SPEC_DIR/spec.md" \
        "TASK=$task")
      work "$SPEC_OWNER_ENGINE" "$prompt"
      scope=$(render_prompt review-scope-spec "SPEC_FILE=$SPEC_DIR/spec.md")
      review_loop "$SPEC_REVIEWER_ENGINE" "$SPEC_OWNER_ENGINE" "$scope"
      human_gate_spec
      end_stage
    fi
  fi
  # Covers the resume where finalize-spec was also skipped: every stage below reads
  # the owner/reviewer roles chosen by the dual-spec decision (v1 review C5).
  restore_dual_spec_decision
  if begin_stage "commit-spec"; then
    commit_work "$SPEC_OWNER_ENGINE" "Spec, approved by review and human gate"
    end_stage
  fi

  if begin_stage "write-implementation-plan" "$SPEC_DIR/plan.md"; then
    prompt=$(render_prompt write-implementation-plan \
      "SPEC_FILE=$SPEC_DIR/spec.md" \
      "PLAN_FILE=$SPEC_DIR/plan.md")
    work "$SPEC_OWNER_ENGINE" "$prompt"
    scope=$(render_prompt review-scope-plan \
      "PLAN_FILE=$SPEC_DIR/plan.md" \
      "SPEC_FILE=$SPEC_DIR/spec.md")
    review_loop "$SPEC_REVIEWER_ENGINE" "$SPEC_OWNER_ENGINE" "$scope"
    commit_work "$SPEC_OWNER_ENGINE" "Implementation plan"
    end_stage
  fi

  # Adversarial TDD: reviewer B writes acceptance tests from the spec, worker A reviews them.
  # The test author and implementer are separated, and A cannot edit these tests during implementation.
  if begin_stage "write-acceptance-tests" "$WF/protected-tests.txt" "$WF/protected-base.sha"; then
    local test_base
    test_base=$(restore_or_record_acceptance_base)
    prompt=$(render_prompt write-acceptance-tests \
      "SPEC_FILE=$SPEC_DIR/spec.md" \
      "SPEC_DIR=$SPEC_DIR")
    work "$SPEC_REVIEWER_ENGINE" "$prompt"
    scope=$(render_prompt review-scope-acceptance-tests \
      "TEST_BASE=$test_base" \
      "SPEC_FILE=$SPEC_DIR/spec.md")
    review_loop "$SPEC_OWNER_ENGINE" "$SPEC_REVIEWER_ENGINE" "$scope"
    commit_work "$SPEC_REVIEWER_ENGINE" "Acceptance tests"
    git diff --name-only "$test_base" HEAD | grep -v "^$SPEC_DIR/" > "$WF/protected-tests.txt" || true
    git rev-parse HEAD > "$WF/protected-base.sha"
    archive_snapshot "$WF/protected-tests.txt" "protected-tests.txt" "workflow" "workflow" "$CUR_STAGE" "$CUR_ROUND" >/dev/null
    archive_snapshot "$WF/protected-base.sha" "protected-base.sha" "workflow" "workflow" "$CUR_STAGE" "$CUR_ROUND" >/dev/null
    if [[ -s "$WF/protected-tests.txt" ]]; then
      { echo "Protected acceptance test files:"; sed 's/^/  - /' "$WF/protected-tests.txt"; } | tee -a "$LOG"
    else
      echo "(warning: acceptance-test stage produced no files; test protection is disabled)" >&2
    fi
    end_stage
  fi

  # Small batches: one task per commit makes review and rollback easier.
  if begin_stage "write-code"; then
    local t i=1 total
    ensure_task_queue "$SPEC_DIR/plan.md"
    total=$(wc -l < "$RUN_STATE_DIR/tasks-remaining")
    while [[ -s "$RUN_STATE_DIR/tasks-remaining" ]]; do
      t=$(head -1 "$RUN_STATE_DIR/tasks-remaining")
      echo "--- Task $i/$total:$t ---" | tee -a "$LOG"
      prompt=$(render_prompt implement-plan-task \
        "PLAN_FILE=$SPEC_DIR/plan.md" \
        "TASK=$t" \
        "PROTECTED_TESTS_FILE=$WF/protected-tests.txt")
      work "$SPEC_OWNER_ENGINE" "$prompt"
      gate_loop "$SPEC_OWNER_ENGINE" "$BUILD_GATE_CMD"
      commit_work "$SPEC_OWNER_ENGINE" "Task \"$t\""
      pop_task_queue
      mark_plan_task_done "$SPEC_DIR/plan.md" "$t"   # UI only; no-op when the worker already ticked it.
      i=$(( i + 1 ))
    done

    echo "--- All tasks complete; running full quality gate. Acceptance tests must pass. ---" | tee -a "$LOG"
    gate_loop "$SPEC_OWNER_ENGINE" "$GATE_CMD"
    scope=$(render_prompt review-scope-branch \
      "SPEC_FILE=$SPEC_DIR/spec.md" \
      "PROTECTED_TESTS_FILE=$WF/protected-tests.txt")
    review_loop "$SPEC_REVIEWER_ENGINE" "$SPEC_OWNER_ENGINE" "$scope" "$GATE_CMD"
    commit_if_dirty "$SPEC_OWNER_ENGINE" "Review fixes"
    end_stage
  fi

  if begin_stage "final-review-and-fixes"; then
    prompt=$(render_prompt final-self-review "SUGGESTIONS_FILE=$WF/suggestions.md")
    work "$SPEC_OWNER_ENGINE" "$prompt"
    gate_loop "$SPEC_OWNER_ENGINE" "$GATE_CMD"
    scope=$(render_prompt review-scope-final-acceptance "SPEC_FILE=$SPEC_DIR/spec.md")
    review_loop "$SPEC_REVIEWER_ENGINE" "$SPEC_OWNER_ENGINE" "$scope" "$GATE_CMD"
    commit_if_dirty "$SPEC_OWNER_ENGINE" "Final fixes"
    end_stage
  fi

  finish "$task"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
