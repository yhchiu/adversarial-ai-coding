#!/usr/bin/env bash
#
# tests/helpers.test.sh - unit tests for adversarial-ai-coding.sh helpers and preflight checks
#
# Run:bash tests/helpers.test.sh
# Does not call any AI engine. Each case runs in its own subshell and temp directory.
set -u

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/adversarial-ai-coding.sh"
PASS=0; FAIL=0
TMPDIRS=()

cleanup() {
  local d
  for d in ${TMPDIRS[@]+"${TMPDIRS[@]}"}; do
    rm -rf "$d" "$d"-auto-* 2>/dev/null
  done
}
trap cleanup EXIT

tmpdir() { local d; d=$(mktemp -d); TMPDIRS+=("$d"); echo "$d"; }

new_repo() {  # Create a temporary git repo that can commit, then output its path.
  local d; d=$(tmpdir)
  git -C "$d" init -q -b main
  git -C "$d" config user.email test@test
  git -C "$d" config user.name test
  ( cd "$d" && echo base > base.txt && git add -A && git commit -qm base )
  echo "$d"
}

ok()  { PASS=$((PASS+1)); echo "ok   - $1"; }
bad() { FAIL=$((FAIL+1)); echo "FAIL - $1"; }

assert_eq()      { if [[ "$2" == "$3" ]]; then ok "$1"; else bad "$1(expected [$2], got [$3])"; fi; }
assert_rc()      { if [[ "$2" -eq "$3" ]]; then ok "$1"; else bad "$1(expected rc=$2, got rc=$3)"; fi; }
assert_nonzero() { if [[ "$2" -ne 0 ]]; then ok "$1"; else bad "$1(expected non-zero rc, got rc=0)"; fi; }
assert_like()    { if [[ "$3" == $2 ]]; then ok "$1"; else bad "$1(expected match [$2], got [$3])"; fi; }

# ---------- detect_gate / detect_build_gate ----------
d=$(tmpdir); touch "$d/go.mod"
out=$( cd "$d" && source "$SCRIPT" && detect_gate )
assert_eq "detect_gate:go project" "go build ./... && go vet ./... && go test ./..." "$out"
out=$( cd "$d" && source "$SCRIPT" && detect_build_gate )
assert_eq "detect_build_gate:go project" "go build ./..." "$out"

d=$(tmpdir); echo '{"scripts":{"test":"jest"}}' > "$d/package.json"
out=$( cd "$d" && source "$SCRIPT" && detect_gate )
assert_eq "detect_gate:npm with test script" "npm test" "$out"

d=$(tmpdir); echo '{"scripts":{}}' > "$d/package.json"
out=$( cd "$d" && source "$SCRIPT" && detect_gate )
assert_eq "detect_gate:npm without test script -> empty" "" "$out"

d=$(tmpdir); touch "$d/Cargo.toml"
out=$( cd "$d" && source "$SCRIPT" && detect_gate )
assert_eq "detect_gate:cargo project" "cargo test" "$out"

d=$(tmpdir)
out=$( cd "$d" && source "$SCRIPT" && detect_gate )
assert_eq "detect_gate:unknown project -> empty" "" "$out"

# ---------- engine_model ----------
d=$(tmpdir)
out=$( cd "$d" && ENGINE_A=claude ENGINE_B=codex MODEL_A=haiku MODEL_B=mini \
      && source "$SCRIPT" && engine_model claude )
assert_eq "engine_model:A slot uses MODEL_A" "haiku" "$out"
out=$( cd "$d" && ENGINE_A=claude ENGINE_B=codex MODEL_A=haiku MODEL_B=mini \
      && source "$SCRIPT" && engine_model codex )
assert_eq "engine_model:B slot uses MODEL_B" "mini" "$out"
out=$( cd "$d" && ENGINE_A=claude ENGINE_B=codex \
      && source "$SCRIPT" && engine_model claude )
assert_eq "engine_model:unset -> empty for CLI default" "" "$out"

# ---------- plan_tasks ----------
d=$(tmpdir)
printf -- '# plan\n\n- [ ] task one\n- [x] finished task\n- [ ] task two\nplain text\n' > "$d/plan.md"
out=$( cd "$d" && source "$SCRIPT" && plan_tasks plan.md )
assert_eq "plan_tasks:only unfinished tasks" $'task one\ntask two' "$out"
out=$( cd "$d" && source "$SCRIPT" && plan_tasks missing.md )
assert_eq "plan_tasks:missing file -> empty" "" "$out"

# ---------- verdict_approved ----------
d=$(tmpdir)
echo '{"approved":true,"blockers":[],"suggestions":[]}' > "$d/v.json"
( cd "$d" && source "$SCRIPT" && verdict_approved v.json ) >/dev/null 2>&1
assert_rc "verdict:approved=true -> pass" 0 $?
echo '{"approved":false,"blockers":["x"],"suggestions":[]}' > "$d/v.json"
( cd "$d" && source "$SCRIPT" && verdict_approved v.json ) >/dev/null 2>&1
assert_nonzero "verdict:approved=false -> fail" $?
( cd "$d" && source "$SCRIPT" && verdict_approved nothere.json ) >/dev/null 2>&1
assert_nonzero "verdict:missing file -> fail" $?
echo 'not json at all' > "$d/v.json"
( cd "$d" && source "$SCRIPT" && verdict_approved v.json ) >/dev/null 2>&1
assert_nonzero "verdict:broken JSON -> fail" $?

# ---------- protected_violations ----------
d=$(new_repo)
( cd "$d" && echo 'func TestAcc' > acc_test.go && git add -A && git commit -qm tests )
base=$(git -C "$d" rev-parse HEAD)
echo 'acc_test.go' > "$d/protected.txt"

out=$( cd "$d" && source "$SCRIPT" && protected_violations protected.txt "$base" )
assert_eq "protected:unchanged -> empty" "" "$out"

echo 'weakened' > "$d/acc_test.go"
out=$( cd "$d" && source "$SCRIPT" && protected_violations protected.txt "$base" )
assert_eq "protected:unstaged modification is detected" "acc_test.go" "$out"

( cd "$d" && git add -A && git commit -qm hack )
out=$( cd "$d" && source "$SCRIPT" && protected_violations protected.txt "$base" )
assert_eq "protected:committed modification is also detected" "acc_test.go" "$out"

( cd "$d" && echo unrelated > other.txt )
: > "$d/protected.txt"
out=$( cd "$d" && source "$SCRIPT" && protected_violations protected.txt "$base" )
assert_eq "protected:empty list disables protection and outputs empty" "" "$out"

# ---------- metric ----------
d=$(tmpdir)
( cd "$d" && mkdir -p .workflow/runs/test/logs && source "$SCRIPT" \
    && WF_RUN=.workflow/runs/test && METRICS="$WF_RUN/metrics.csv" \
    && CUR_STAGE=stage1 && metric worker claude 1 12 0.05 && metric reviewer codex 2 30 "" )
assert_eq "metric:header plus two rows" 3 "$(wc -l < "$d/.workflow/runs/test/metrics.csv")"
assert_eq "metric:CSV header is correct" "run_id,stage,role,engine,round,duration_s,cost_usd,model,model_args,generated_at" \
  "$(head -1 "$d/.workflow/runs/test/metrics.csv")"

d=$(tmpdir)
( cd "$d" && mkdir -p .workflow/runs/test/logs && CODEX_ARGS='-c model="x,y" --flag "quoted value"' \
    && source "$SCRIPT" && WF_RUN=.workflow/runs/test && METRICS="$WF_RUN/metrics.csv" \
    && CUR_STAGE=stage1 && metric reviewer codex 2 30 "" )
line=$(tail -1 "$d/.workflow/runs/test/metrics.csv")
python -c 'import csv,sys; row=next(csv.reader([sys.argv[1]])); assert row[7] == ""; assert row[8] == "-c model=\"x,y\" --flag \"quoted value\""; assert len(row) == 10' "$line"
assert_rc "metric:CSV escaping preserves model_args with comma and quotes" 0 $?

# ---------- metrics_summary regression: quoted rows still sum correctly ----------
d=$(tmpdir)
( cd "$d" && mkdir -p .workflow/runs/test && source "$SCRIPT" \
    && WF_RUN=.workflow/runs/test && METRICS="$WF_RUN/metrics.csv" \
    && CUR_STAGE=stageX && metric worker claude 1 12 0.05 && metric worker claude 3 8 0.10 )
sm=$( cd "$d" && source "$SCRIPT" && metrics_summary .workflow/runs/test/metrics.csv )
grep -q '20 seconds' <<<"$sm"; assert_rc "metrics_summary:seconds sum correctly despite quotes" 0 $?
grep -qF '$0.1500' <<<"$sm"; assert_rc "metrics_summary:cost sums correctly" 0 $?
grep -q 'review rounds 3' <<<"$sm"; assert_rc "metrics_summary:max round is correct" 0 $?

# ---------- archive helpers ----------
d=$(tmpdir)
out=$( cd "$d" && mkdir -p .workflow/runs/test && WF_NOW=2026-01-02T03:04:05+0800 \
      && source "$SCRIPT" && WF_RUN=.workflow/runs/test && ART_SEQ=0 \
      && a=$(art_path first.txt) && b=$(art_path second.txt) && printf '%s\n%s\n' "$a" "$b" )
assert_eq "art_path:increments sequence" $'.workflow/runs/test/001-first.txt\n.workflow/runs/test/002-second.txt' "$out"

d=$(tmpdir)
( cd "$d" && mkdir -p .workflow/runs/test && WF_NOW=2026-01-02T03:04:05+0800 \
    && source "$SCRIPT" && WF_RUN=.workflow/runs/test && ART_SEQ=0 \
    && printf 'data\n' > src.txt && dst=$(archive_snapshot src.txt snap.txt worker claude stage 3) \
    && jq -e '.generated_at=="2026-01-02T03:04:05+0800" and .generator_role=="worker" and .engine=="claude" and .stage=="stage" and .round=="3" and (.run_id|length > 0)' "$dst.meta.json" >/dev/null )
assert_rc "write_meta/archive_snapshot:write required metadata" 0 $?

d=$(tmpdir)
printf 'file task\n' > "$d/task.md"
( cd "$d" && mkdir -p .workflow/runs/test && source "$SCRIPT" && WF_RUN=.workflow/runs/test && ART_SEQ=0 \
    && archive_task task.md file "$(abs_path task.md)" "$(cat task.md)" )
[[ -f "$d/.workflow/runs/test/001-task-source.md" && -f "$d/.workflow/runs/test/002-task.txt" ]] \
  && ok "archive_task:saves file task source and resolved text" || bad "archive_task:saves file task source and resolved text"
grep -q "$(cd "$d" && pwd -P)" "$d/.workflow/runs/test/001-task-source.md" \
  && ok "archive_task:file source records absolute path" || bad "archive_task:file source records absolute path"

d=$(tmpdir)
( cd "$d" && mkdir -p .workflow/runs/test && source "$SCRIPT" && WF_RUN=.workflow/runs/test && ART_SEQ=0 \
    && archive_task 'literal task' literal '' 'literal task' )
grep -q 'kind: literal' "$d/.workflow/runs/test/001-task-source.md" \
  && ok "archive_task:saves literal task source" || bad "archive_task:saves literal task source"

d=$(new_repo)
( cd "$d" && mkdir -p .workflow && echo '*' > .workflow/.gitignore && git add .workflow/.gitignore && git commit -qm workflow-ignore )
printf 'changed\n' > "$d/base.txt"
printf 'new content\n' > "$d/new.txt"
before=$( cd "$d" && git status --porcelain )
( cd "$d" && source "$SCRIPT" && WF_RUN=.workflow/runs/test && mkdir -p "$WF_RUN" && ART_SEQ=0 \
    && CUR_STAGE=code && CUR_ROUND=2 && archive_git_state worker claude worker-code-r2 )
after=$( cd "$d" && git status --porcelain )
assert_eq "archive_git_state:leaves no index/status side effects" "$before" "$after"
grep -q 'new content' "$d/.workflow/runs/test/002-worker-code-r2-git-diff.patch" \
  && ok "archive_git_state:saves untracked file content" || bad "archive_git_state:saves untracked file content"

# ---------- compose_review_prompt ----------
d=$(tmpdir)
out=$( cd "$d" && source "$SCRIPT" && compose_review_prompt claude scope )
if [[ "$out" != *"Finally write the verdict"* ]]; then ok "compose_review_prompt:claude omits verdict_file_instr"
else bad "compose_review_prompt:claude omits verdict_file_instr"; fi
out=$( cd "$d" && source "$SCRIPT" && compose_review_prompt codex scope )
if [[ "$out" == *"Finally write the verdict"* ]]; then ok "compose_review_prompt:codex includes verdict_file_instr"
else bad "compose_review_prompt:codex includes verdict_file_instr"; fi

# ---------- dual spec helpers ----------
d=$(tmpdir)
out=$( cd "$d" && source "$SCRIPT" && echo "$DUAL_SPEC" )
assert_eq "dual_spec:default disabled" "0" "$out"

d=$(tmpdir)
out=$( cd "$d" && source "$SCRIPT" && normalize_dual_spec_decision A )
assert_eq "dual_spec:decision A adopts candidate A" "adopt-a" "$out"
out=$( cd "$d" && source "$SCRIPT" && normalize_dual_spec_decision mb )
assert_eq "dual_spec:decision mb merges from base B" "merge-b" "$out"
( cd "$d" && source "$SCRIPT" && normalize_dual_spec_decision nope ) >/dev/null 2>&1
assert_nonzero "dual_spec:invalid decision fails" $?

d=$(tmpdir)
out=$( cd "$d" && ENGINE_A=claude && ENGINE_B=codex && source "$SCRIPT" && dual_spec_owner_slot adopt-a )
assert_eq "dual_spec:adopt-a owner slot" "A" "$out"
out=$( cd "$d" && ENGINE_A=claude && ENGINE_B=codex && source "$SCRIPT" && dual_spec_owner_slot merge-b )
assert_eq "dual_spec:merge-b owner slot" "B" "$out"
out=$( cd "$d" && ENGINE_A=claude && ENGINE_B=codex && source "$SCRIPT" && engine_for_slot B )
assert_eq "dual_spec:engine_for_slot B" "codex" "$out"
out=$( cd "$d" && ENGINE_A=claude && ENGINE_B=codex && source "$SCRIPT" && set_spec_roles_from_slot B && printf '%s/%s\n' "$SPEC_OWNER_ENGINE" "$SPEC_REVIEWER_ENGINE" )
assert_eq "dual_spec:set roles from slot B" "codex/claude" "$out"

d=$(new_repo)
( cd "$d" && DUAL_SPEC=1 HUMAN_GATE=0 "$SCRIPT" "task" ) >/dev/null 2>&1
assert_rc "dual_spec:HUMAN_GATE=0 is blocked before branch setup" 1 $?
assert_eq "dual_spec:block leaves no branch side effect" "main" "$(git -C "$d" branch --show-current)"

# ---------- init_live_state ----------
d=$(tmpdir)
mkdir -p "$d/.workflow"
printf 'old suggestion\n' > "$d/.workflow/suggestions.md"
printf 'old-test.go\n' > "$d/.workflow/protected-tests.txt"
printf 'abc123\n' > "$d/.workflow/protected-base.sha"
printf 'old review\n' > "$d/.workflow/review.md"
printf '{}\n' > "$d/.workflow/verdict.json"
printf 'old output\n' > "$d/.workflow/last-engine-output.txt"
( cd "$d" && source "$SCRIPT" && init_live_state )
[[ ! -e "$d/.workflow/suggestions.md" && ! -e "$d/.workflow/protected-tests.txt" && ! -e "$d/.workflow/protected-base.sha" ]] \
  && ok "init_live_state:clears cross-run contamination files" || bad "init_live_state:clears cross-run contamination files"
[[ ! -e "$d/.workflow/review.md" && ! -e "$d/.workflow/verdict.json" && ! -e "$d/.workflow/last-engine-output.txt" ]] \
  && ok "init_live_state:clears self-healing live files" || bad "init_live_state:clears self-healing live files"

# ---------- bootstrap_agents_md ----------
d=$(tmpdir)
( cd "$d" && source "$SCRIPT" && bootstrap_agents_md ) >/dev/null
[[ -f "$d/AGENTS.md" && -f "$d/CLAUDE.md" ]] && ok "bootstrap:creates AGENTS.md and CLAUDE.md" \
  || bad "bootstrap:creates AGENTS.md and CLAUDE.md"
grep -qF '<!-- adversarial-ai-coding:begin -->' "$d/AGENTS.md" && ok "bootstrap:has marker" || bad "bootstrap:has marker"

d=$(tmpdir); echo 'my own rules' > "$d/AGENTS.md"
( cd "$d" && source "$SCRIPT" && bootstrap_agents_md ) >/dev/null 2>&1
assert_eq "bootstrap:does not overwrite existing AGENTS.md" "my own rules" "$(cat "$d/AGENTS.md")"

d=$(tmpdir)
( cd "$d" && AGENTS_TEMPLATE=/nonexistent-template && source "$SCRIPT" && bootstrap_agents_md ) >/dev/null 2>&1
assert_rc "bootstrap:missing template -> does not stop workflow" 0 $?
[[ ! -f "$d/AGENTS.md" && ! -f "$d/CLAUDE.md" ]] && ok "bootstrap:missing template -> leaves no empty files" \
  || bad "bootstrap:missing template -> leaves no empty files"

AGENTS_TEMPLATE=/nonexistent-template "$SCRIPT" print-agents >/dev/null 2>&1
assert_nonzero "print-agents:missing template -> fails with message" $?

# ---------- setup_workspace(worktree) ----------
d=$(new_repo)
out=$( cd "$d" && source "$SCRIPT" && USE_WORKTREE=1 setup_workspace >/dev/null && git branch --show-current )
assert_like "worktree:creates and switches to auto/* branch" "auto/*" "$out"
git -C "$d" worktree prune 2>/dev/null

# ---------- human_gate_spec ----------
d=$(tmpdir)
( cd "$d" && mkdir -p .workflow && HUMAN_GATE=0 && source "$SCRIPT" && human_gate_spec ) >/dev/null 2>&1
assert_rc "human gate:HUMAN_GATE=0 -> passes immediately" 0 $?
if ! { : </dev/tty; } 2>/dev/null; then
  ( cd "$d" && mkdir -p .workflow && source "$SCRIPT" && human_gate_spec ) >/dev/null 2>&1
  assert_rc "human gate:no tty -> aborts" 1 $?
else
  echo "skip - human gate no-tty abort (current environment has a tty, cannot simulate)"
fi

# ---------- preflight checks, direct script execution without AI calls ----------
"$SCRIPT" >/dev/null 2>&1
assert_rc "preflight:no args -> usage and exit 1" 1 $?

d=$(tmpdir)
( cd "$d" && "$SCRIPT" "task" ) >/dev/null 2>&1
assert_rc "preflight:not a git repo -> blocked" 1 $?

d=$(new_repo)
( cd "$d" && ENGINE_A=codex ENGINE_B=codex "$SCRIPT" "task" ) >/dev/null 2>&1
assert_rc "preflight:codex+codex -> blocked" 1 $?
assert_eq "preflight:block leaves no branch side effect" "main" "$(git -C "$d" branch --show-current)"

"$SCRIPT" print-agents 2>/dev/null | grep -qF '<!-- adversarial-ai-coding:begin -->'
assert_rc "print-agents:prints rule template" 0 $?

# ---------- is_rate_limited ----------
d=$(tmpdir)
cat > "$d/hit.txt" <<'EOF'
{"type":"result","subtype":"success","is_error":true,"api_error_status":429,"result":"You've hit your session limit - resets 10:50am (Asia/Taipei)"}
EOF
( cd "$d" && source "$SCRIPT" && is_rate_limited hit.txt ) >/dev/null 2>&1
assert_rc "rate-limit detection:claude 429 JSON sample" 0 $?
printf 'HTTP 429 Too Many Requests\n' > "$d/tmr.txt"
( cd "$d" && source "$SCRIPT" && is_rate_limited tmr.txt ) >/dev/null 2>&1
assert_rc "rate-limit detection:Too Many Requests" 0 $?
cat > "$d/codex429.txt" <<'EOF'
ERROR: {"type":"error","status":429,"error":{"type":"rate_limit_exceeded","message":"Rate limit reached for gpt-5.5. Please try again in 90s."}}
EOF
( cd "$d" && source "$SCRIPT" && is_rate_limited codex429.txt ) >/dev/null 2>&1
assert_rc "rate-limit detection:codex/OpenAI 429 JSON" 0 $?
printf "You've reached your usage limit.\n" > "$d/reached.txt"
( cd "$d" && source "$SCRIPT" && is_rate_limited reached.txt ) >/dev/null 2>&1
assert_rc "rate-limit detection:reached your usage limit wording" 0 $?
printf 'strutil_test.go:47:14: undefined: IsPalindrome\n' > "$d/plain.txt"
( cd "$d" && source "$SCRIPT" && is_rate_limited plain.txt ) >/dev/null 2>&1
assert_nonzero "rate-limit detection:ordinary error is not misclassified" $?
( cd "$d" && source "$SCRIPT" && is_rate_limited nothere.txt ) >/dev/null 2>&1
assert_nonzero "rate-limit detection:missing file -> no" $?

# ---------- parse_reset_wait ----------
now=$(date +%s)
fut=$(LC_ALL=C date -d "@$(( now + 7200 ))" +%-I:%M%P)   # Force English am/pm, matching Claude messages.
printf 'You have hit your session limit - resets %s (Asia/Taipei)\n' "$fut" > "$d/fut.txt"
w=$( cd "$d" && source "$SCRIPT" && parse_reset_wait fut.txt "$now" )
if [[ -n "$w" && "$w" -ge 7000 && "$w" -le 7500 ]]; then ok "reset parser:2 hours later -> waits about 2h plus buffer"
else bad "reset parser:2 hours later (got [$w])"; fi

past=$(LC_ALL=C date -d "@$(( now - 3600 ))" +%-I:%M%P)
printf 'resets %s\n' "$past" > "$d/past.txt"
w=$( cd "$d" && source "$SCRIPT" && parse_reset_wait past.txt "$now" )
if [[ -z "$w" ]]; then ok "reset parser:past time -> tomorrow is >6h, use backoff"
else bad "reset parser:past time (got [$w], expected empty)"; fi

printf 'no reset info here\n' > "$d/none.txt"
w=$( cd "$d" && source "$SCRIPT" && parse_reset_wait none.txt "$now" )
assert_eq "reset parser:no info -> empty for backoff" "" "$w"

w=$( cd "$d" && source "$SCRIPT" && parse_reset_wait codex429.txt "$now" )
assert_eq "reset parser:try again in 90s -> 120 with 30s buffer" "120" "$w"
printf 'Rate limit reached. Please try again in 2 minutes.\n' > "$d/mins.txt"
w=$( cd "$d" && source "$SCRIPT" && parse_reset_wait mins.txt "$now" )
assert_eq "reset parser:try again in 2 minutes -> 150" "150" "$w"
printf 'usage cap, try again in 3 hours\n' > "$d/hrs.txt"
w=$( cd "$d" && source "$SCRIPT" && parse_reset_wait hrs.txt "$now" )
assert_eq "reset parser:try again in 3 hours -> 10830" "10830" "$w"
printf 'try again in 12 hours\n' > "$d/toolong.txt"
w=$( cd "$d" && source "$SCRIPT" && parse_reset_wait toolong.txt "$now" )
assert_eq "reset parser:try again over 6 hours -> empty safety guard" "" "$w"

# ---------- engine_call with stub engine and RETRY_BASE_WAIT=1 for fast tests ----------
d=$(tmpdir)
run_engine_call() {  # $1: RETRY_ON_LIMIT  $2: stub output, empty means success
  ( cd "$d" && mkdir -p .workflow/logs \
    && RETRY_ON_LIMIT="$1" RETRY_BASE_WAIT=1 RETRY_MAX=2 STUB="$2" \
    && source "$SCRIPT" && WF_RUN=.workflow/runs/test && mkdir -p "$WF_RUN" && ART_SEQ=0 && LOG=.workflow/logs/t.log && touch "$LOG" \
    && CALLS=0 \
    && fake_engine() {
         CALLS=$(( CALLS + 1 ))
         [[ -z "$STUB" ]] && return 0
         printf '%s\n' "$STUB" > "$ENGINE_OUT"
         return 1
       } \
    && rc=0 && engine_call worker claude worker-stage-r1 fake_engine prompt >/dev/null 2>&1 || rc=$? \
    ;  echo "rc=$rc calls=$CALLS" )
}
out=$(run_engine_call 1 'api_error_status":429 hit your session limit')
assert_eq "engine_call:default retries to limit (1 call + 2 retries)" "rc=1 calls=3" "$out"
out=$(run_engine_call 0 'api_error_status":429 hit your session limit')
assert_eq "engine_call:RETRY_ON_LIMIT=0 -> no retry" "rc=1 calls=1" "$out"
out=$(run_engine_call 1 'ordinary build failure')
assert_eq "engine_call:non-rate-limit error -> no retry" "rc=1 calls=1" "$out"
out=$(run_engine_call 1 '')
assert_eq "engine_call:success passes immediately" "rc=0 calls=1" "$out"

d=$(tmpdir)
( cd "$d" && mkdir -p .workflow/logs .workflow/runs/test \
    && RETRY_ON_LIMIT=1 RETRY_BASE_WAIT=1 RETRY_MAX=1 STUB='api_error_status":429 hit your session limit' \
    && source "$SCRIPT" && WF_RUN=.workflow/runs/test && ART_SEQ=0 && LOG=.workflow/logs/t.log && touch "$LOG" \
    && fake_engine() { printf '%s\n' "$STUB" > "$ENGINE_OUT"; return 1; } \
    && rc=0 && engine_call worker claude worker-stage-r1 fake_engine prompt >/dev/null 2>&1 || rc=$? )
[[ -f "$d/.workflow/runs/test/001-worker-stage-r1-attempt-1-rc1.raw" && -f "$d/.workflow/runs/test/002-worker-stage-r1-attempt-2-rc1.raw" ]] \
  && ok "engine_call:saves raw output for every retry attempt" || bad "engine_call:saves raw output for every retry attempt"
jq -e '.generator_role=="worker" and .engine=="claude"' "$d/.workflow/runs/test/001-worker-stage-r1-attempt-1-rc1.raw.meta.json" >/dev/null
assert_rc "engine_call:attempt metadata includes role/engine" 0 $?

# ---------- summary ----------
echo ""
echo "Passed $PASS, failed $FAIL"
(( FAIL == 0 ))
