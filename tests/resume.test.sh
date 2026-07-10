#!/usr/bin/env bash
#
# tests/resume.test.sh - offline interrupt->resume integration tests
#
# Run:bash tests/resume.test.sh
# Uses fake agent scripts instead of real AI engines: no tokens, no quota, safe for CI.
# Each scenario runs the full workflow in a temporary git repo, interrupts it
# (typed quota abort, plain engine failure, SIGINT, or simulated state damage),
# then resumes with RESUME_RUN and asserts completed stages are not re-paid.
set -u

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/adversarial-ai-coding.sh"
PASS=0; FAIL=0
TMPDIRS=()

cleanup() {
  local d
  for d in ${TMPDIRS[@]+"${TMPDIRS[@]}"}; do
    rm -rf "$d" 2>/dev/null
  done
}
trap cleanup EXIT

tmpdir() { local d; d=$(mktemp -d); TMPDIRS+=("$d"); echo "$d"; }

ok()  { PASS=$((PASS+1)); echo "ok   - $1"; }
bad() { FAIL=$((FAIL+1)); echo "FAIL - $1"; }

assert_eq()      { if [[ "$2" == "$3" ]]; then ok "$1"; else bad "$1(expected [$2], got [$3])"; fi; }
assert_rc()      { if [[ "$2" -eq "$3" ]]; then ok "$1"; else bad "$1(expected rc=$2, got rc=$3)"; fi; }
assert_nonzero() { if [[ "$2" -ne 0 ]]; then ok "$1"; else bad "$1(expected non-zero rc, got rc=0)"; fi; }
assert_like()    { if [[ "$3" == $2 ]]; then ok "$1"; else bad "$1(expected match [$2], got [$3])"; fi; }

# ---------- fake agents ----------
# The workflow hands engines a one-line instruction pointing at the archived
# prompt file. The fake reads that file and dispatches on the template's first
# sentence, doing just enough real work (files + git commits happen through the
# script's own ensure_committed) for every stage to pass.
write_fake_agent() {  # $1: target path  $2: agent name
  printf '#!/usr/bin/env bash\nNAME=%s\n' "$2" > "$1"
  cat >> "$1" <<'FAKE'
set -u
last="${!#}"
pf="${last##*: }"
if [[ -f "$pf" ]]; then
  prompt="$(cat "$pf")"
else
  prompt="$last"
fi

kind=other
case "$prompt" in
  "You are a strict code reviewer."*"after dual spec selection"*) kind=review-dual-final ;;
  "You are a strict code reviewer."*) kind=review ;;
  "Write a spec for the following request"*) kind=write-spec ;;
  "Write an independent candidate spec"*) kind=write-candidate ;;
  "Write an implementation plan"*) kind=write-plan ;;
  "Write acceptance tests"*) kind=write-acceptance ;;
  "Implement this task from"*) kind=implement ;;
  "Compare the dual spec candidates"*) kind=compare ;;
  "Do a complete self-review"*) kind=final-review ;;
  *"is complete and approved. Commit all current changes"*) kind=commit ;;
esac

printf '%s %s\n' "$NAME" "$kind" >> "${FAKE_CALLS_LOG:-calls.log}"

if [[ -n "${FAKE_ABORT_ON:-}" && -f "$FAKE_ABORT_ON" && "$kind" == "$(cat "$FAKE_ABORT_ON")" ]]; then
  if [[ "${FAKE_ABORT_MODE:-quota}" == "plain" ]]; then
    echo "fake agent plain failure"
    exit 1
  fi
  # Reset within the 30-day parser sanity cap but beyond RETRY_MAX_RESET_WAIT,
  # so engine_call aborts with the typed quota code instead of sleeping.
  reset_at=$(LC_ALL=C date -d "+2 days" '+%b %-d, %Y %-I:%M %p')
  echo "You've hit your usage limit. Please try again at $reset_at."
  exit 1
fi

target=""
case "$kind" in
  review|review-dual-final)
    mkdir -p .workflow
    printf 'approved by %s\n' "$NAME" > .workflow/review.md
    printf '{"approved":true,"blockers":[],"suggestions":[]}\n' > .workflow/verdict.json
    ;;
  write-spec)
    target=$(grep -oE 'specs/[^ ]+/spec\.md' <<<"$prompt" | head -1)
    mkdir -p "$(dirname "$target")"
    printf '# Spec\n\nDemo feature.\n\n## Assumptions and Open Questions\n\n- none\n' > "$target"
    ;;
  write-candidate)
    target=$(grep -oE 'specs/[^ ]+/spec-[ab]\.md' <<<"$prompt" | head -1)
    mkdir -p "$(dirname "$target")"
    printf '# Candidate spec %s\n' "$target" > "$target"
    ;;
  write-plan)
    target=$(grep -oE 'specs/[^ ]+/plan\.md' <<<"$prompt" | head -1)
    mkdir -p "$(dirname "$target")"
    printf '# Plan\n\n- [ ] add feature one\n- [ ] add feature two\n' > "$target"
    ;;
  write-acceptance)
    mkdir -p acc
    printf 'ACCEPTANCE CHECK\n' > acc/acceptance.txt
    ;;
  implement)
    printf 'implemented\n' >> src.txt
    ;;
  compare)
    target=$(grep -oE 'specs/[^ ]+/spec-comparison-[ab]\.md' <<<"$prompt" | head -1)
    mkdir -p "$(dirname "$target")"
    printf 'comparison table\n' > "$target"
    ;;
esac

echo "$NAME did $kind"
FAKE
  chmod +x "$1"
}

new_workflow_repo() {  # Create a work area: repo/ (git), bin/ (fake agents); echo the work dir.
  local work repo
  work=$(tmpdir)
  repo="$work/repo"
  mkdir -p "$repo" "$work/bin"
  git -C "$repo" init -q -b main
  git -C "$repo" config user.email test@test
  git -C "$repo" config user.name test
  ( cd "$repo" && echo base > base.txt && git add -A && git commit -qm base )
  write_fake_agent "$work/bin/fake-worker" fake-worker
  write_fake_agent "$work/bin/fake-reviewer" fake-reviewer
  echo "$work"
}

std_env() {  # $1: work dir; fill WF_ENV with the standard offline workflow environment.
  WF_ENV=(
    HUMAN_GATE=0 DUAL_SPEC=0 AUTO_BRANCH=1 USE_WORKTREE=0 OPEN_PR=0
    GATE_CMD=true BUILD_GATE_CMD=true RETRY_ON_LIMIT=1 NOTIFY_CMD=
    FAKE_CALLS_LOG="$1/calls.log" FAKE_ABORT_ON="$1/abort-on"
    AGENT_A="$1/bin/fake-worker" AGENT_B="$1/bin/fake-reviewer"
  )
}

calls() {  # $1: work dir  $2: "<agent> <kind>" pattern; count matching call log lines.
  grep -c "^$2\$" "$1/calls.log" 2>/dev/null || true
}

run_id_of() {  # $1: work dir; echo the single run id recorded in the repo state.
  ls -1 "$1/repo/.workflow/state" 2>/dev/null | head -1
}

# ---------- scenario 1: quota abort mid-run, then resume ----------
work=$(new_workflow_repo)
std_env "$work"
echo write-plan > "$work/abort-on"
( cd "$work/repo" && env "${WF_ENV[@]}" "$SCRIPT" "demo task" ) > "$work/run1.out" 2>&1
rc=$?
assert_rc "quota:first run aborts with the typed quota code" 75 "$rc"
assert_like "quota:abort prints a resume hint" "*RESUME_RUN=*" "$(cat "$work/run1.out")"
id=$(run_id_of "$work")
ledger="$work/repo/.workflow/state/$id/completed-stages"
grep -Fxq "write-spec" "$ledger" && grep -Fxq "commit-spec" "$ledger"
assert_rc "quota:ledger records the completed stages" 0 $?
if ! grep -Fxq "write-implementation-plan" "$ledger"; then
  ok "quota:interrupted stage is not in the ledger"
else
  bad "quota:interrupted stage is not in the ledger"
fi
spec_calls_before=$(calls "$work" "fake-worker write-spec")

rm "$work/abort-on"
( cd "$work/repo" && env "${WF_ENV[@]}" RESUME_RUN="$id" "$SCRIPT" ) > "$work/run2.out" 2>&1
rc=$?
assert_rc "quota:resume completes the run" 0 "$rc"
assert_like "quota:resume skips recorded stages" "*== skip ?write-spec?*" "$(cat "$work/run2.out")"
assert_eq "quota:completed stage cost zero new agent calls" "$spec_calls_before" "$(calls "$work" "fake-worker write-spec")"
[[ -f "$work/repo/.workflow/state/$id/completed" ]] \
  && ok "quota:completed marker is written" || bad "quota:completed marker is written"
plan_file=$(ls "$work/repo/specs"/*/plan.md 2>/dev/null | head -1)
if [[ -n "$plan_file" ]] && ! grep -qE '^- \[ \] ' "$plan_file" && grep -qE '^- \[x\]' "$plan_file"; then
  ok "quota:plan checkboxes all ticked by script or worker"
else
  bad "quota:plan checkboxes all ticked by script or worker(plan [$plan_file])"
fi

( cd "$work/repo" && env "${WF_ENV[@]}" RESUME_RUN=last "$SCRIPT" ) >/dev/null 2>&1
assert_rc "quota:RESUME_RUN=last refuses when everything completed" 1 $?
out=$( cd "$work/repo" && env "${WF_ENV[@]}" RESUME_RUN=nonexistent "$SCRIPT" 2>&1 ); rc=$?
assert_rc "quota:unknown id fails" 1 "$rc"
assert_like "quota:unknown id lists the real run id" "*$id*" "$out"

# ---------- scenario 2: ledger line lost after commit (at-least-once re-run) ----------
work=$(new_workflow_repo)
std_env "$work"
( cd "$work/repo" && env "${WF_ENV[@]}" "$SCRIPT" "demo task" ) > "$work/run1.out" 2>&1
assert_rc "ledger-loss:baseline run completes" 0 $?
id=$(run_id_of "$work")
state="$work/repo/.workflow/state/$id"
rm "$state/completed"
head -n -1 "$state/completed-stages" > "$state/completed-stages.tmp" \
  && mv "$state/completed-stages.tmp" "$state/completed-stages"
before=$(calls "$work" "fake-worker final-review")
( cd "$work/repo" && env "${WF_ENV[@]}" RESUME_RUN="$id" "$SCRIPT" ) > "$work/run2.out" 2>&1
assert_rc "ledger-loss:resume completes" 0 $?
assert_eq "ledger-loss:unrecorded stage re-runs (at-least-once)" "$(( before + 1 ))" "$(calls "$work" "fake-worker final-review")"

# ---------- scenario 3: acceptance crash window keeps its persisted base ----------
work=$(new_workflow_repo)
std_env "$work"
( cd "$work/repo" && env "${WF_ENV[@]}" "$SCRIPT" "demo task" ) > "$work/run1.out" 2>&1
assert_rc "acceptance-window:baseline run completes" 0 $?
id=$(run_id_of "$work")
state="$work/repo/.workflow/state/$id"
base_before=$(cat "$state/acceptance-test-base")
rm "$state/completed"
grep -Fxv "write-acceptance-tests" "$state/completed-stages" > "$state/completed-stages.tmp" \
  && mv "$state/completed-stages.tmp" "$state/completed-stages"
rm "$work/repo/.workflow/protected-tests.txt" "$work/repo/.workflow/protected-base.sha"
( cd "$work/repo" && env "${WF_ENV[@]}" RESUME_RUN="$id" "$SCRIPT" ) > "$work/run2.out" 2>&1
assert_rc "acceptance-window:resume completes" 0 $?
grep -q 'acc/acceptance.txt' "$work/repo/.workflow/protected-tests.txt" 2>/dev/null
assert_rc "acceptance-window:protected list is rebuilt non-empty" 0 $?
assert_eq "acceptance-window:persisted base sha is reused" "$base_before" "$(cat "$state/acceptance-test-base")"

# ---------- scenario 4: write-code finished; empty queue must not fall back ----------
work=$(new_workflow_repo)
std_env "$work"
( cd "$work/repo" && env "${WF_ENV[@]}" "$SCRIPT" "demo task" ) > "$work/run1.out" 2>&1
assert_rc "empty-queue:baseline run completes" 0 $?
id=$(run_id_of "$work")
state="$work/repo/.workflow/state/$id"
rm "$state/completed"
grep -Fxv "write-code" "$state/completed-stages" > "$state/completed-stages.tmp" \
  && mv "$state/completed-stages.tmp" "$state/completed-stages"
before=$(calls "$work" "fake-worker implement")
( cd "$work/repo" && env "${WF_ENV[@]}" RESUME_RUN="$id" "$SCRIPT" ) > "$work/run2.out" 2>&1
assert_rc "empty-queue:resume completes" 0 $?
assert_eq "empty-queue:no worker task re-runs from an empty queue" "$before" "$(calls "$work" "fake-worker implement")"
if ! grep -q 'falling back to one whole-plan implementation task' "$work/run2.out"; then
  ok "empty-queue:whole-plan fallback is not used"
else
  bad "empty-queue:whole-plan fallback is not used"
fi

# ---------- scenario 5: SIGINT (Ctrl-C), then resume with RESUME_RUN=last ----------
work=$(new_workflow_repo)
std_env "$work"
( cd "$work/repo" && exec env "${WF_ENV[@]}" "$SCRIPT" "demo task" ) > "$work/run1.out" 2>&1 &
pid=$!
found=""
for _ in $(seq 1 150); do
  if compgen -G "$work/repo/specs/*/spec.md" >/dev/null 2>&1; then found=1; break; fi
  sleep 0.2
done
[[ -n "$found" ]] || bad "sigint:workflow never reached write-spec (timeout)"
kill -INT "$pid" 2>/dev/null
wait "$pid"
rc=$?
assert_rc "sigint:interrupt exits 130 through the trap" 130 "$rc"
assert_like "sigint:interrupt prints a resume hint" "*RESUME_RUN=*" "$(cat "$work/run1.out")"
( cd "$work/repo" && env "${WF_ENV[@]}" RESUME_RUN=last "$SCRIPT" ) > "$work/run2.out" 2>&1
assert_rc "sigint:RESUME_RUN=last resumes and completes" 0 $?
id=$(run_id_of "$work")
[[ -f "$work/repo/.workflow/state/$id/completed" ]] \
  && ok "sigint:resumed run reaches the completed marker" || bad "sigint:resumed run reaches the completed marker"

# ---------- scenario 6: plain engine failure, damaged state is refused ----------
work=$(new_workflow_repo)
std_env "$work"
echo implement > "$work/abort-on"
( cd "$work/repo" && env "${WF_ENV[@]}" FAKE_ABORT_MODE=plain "$SCRIPT" "demo task" ) > "$work/run1.out" 2>&1
rc=$?
assert_nonzero "bad-state:plain engine failure aborts the run" "$rc"
assert_like "bad-state:plain failure still prints a resume hint" "*RESUME_RUN=*" "$(cat "$work/run1.out")"
id=$(run_id_of "$work")
state="$work/repo/.workflow/state/$id"
cp "$state/resume.conf" "$work/resume.conf.backup"

printf 'evil_key=1\n' >> "$state/resume.conf"
( cd "$work/repo" && env "${WF_ENV[@]}" RESUME_RUN="$id" "$SCRIPT" ) >/dev/null 2>&1
assert_rc "bad-state:unknown conf key refuses to resume" 1 $?

tail -n +2 "$work/resume.conf.backup" > "$state/resume.conf"
( cd "$work/repo" && env "${WF_ENV[@]}" RESUME_RUN="$id" "$SCRIPT" ) >/dev/null 2>&1
assert_rc "bad-state:conf without schema line refuses to resume" 1 $?

cp "$work/resume.conf.backup" "$state/resume.conf"
rm "$work/abort-on"
( cd "$work/repo" && env "${WF_ENV[@]}" RESUME_RUN="$id" "$SCRIPT" ) > "$work/run2.out" 2>&1
assert_rc "bad-state:restored conf resumes and completes" 0 $?

# ---------- scenario 7: dual-spec resume between select-spec and finalize-spec ----------
# Needs a pty for the human selection; script(1) provides one on Linux CI.
# Without it (Windows Git Bash) the decision-restore logic is covered by unit tests.
if command -v script >/dev/null 2>&1 && printf '' | script -qec true /dev/null >/dev/null 2>&1; then
  work=$(new_workflow_repo)
  std_env "$work"
  echo review-dual-final > "$work/abort-on"
  dual_env="HUMAN_GATE=1 DUAL_SPEC=1 AUTO_BRANCH=1 USE_WORKTREE=0 OPEN_PR=0 GATE_CMD=true BUILD_GATE_CMD=true RETRY_ON_LIMIT=1 NOTIFY_CMD= FAKE_CALLS_LOG='$work/calls.log' FAKE_ABORT_ON='$work/abort-on' AGENT_A='$work/bin/fake-worker' AGENT_B='$work/bin/fake-reviewer'"
  printf 'b\n' | script -qec "cd '$work/repo' && env $dual_env '$SCRIPT' 'demo task'" /dev/null > "$work/run1.out" 2>&1
  rc=$?
  assert_rc "dual-spec:finalize review quota abort exits 75" 75 "$rc"
  id=$(run_id_of "$work")
  grep -Fxq "select-spec" "$work/repo/.workflow/state/$id/completed-stages"
  assert_rc "dual-spec:select-spec is recorded before the abort" 0 $?
  candidates_before=$(calls "$work" "fake-worker write-candidate")

  rm "$work/abort-on"
  printf 'y\n' | script -qec "cd '$work/repo' && env $dual_env RESUME_RUN='$id' '$SCRIPT'" /dev/null > "$work/run2.out" 2>&1
  rc=$?
  assert_rc "dual-spec:resume completes without re-asking the selection" 0 "$rc"
  assert_like "dual-spec:decision is restored from spec-decision.md" "*restored dual-spec decision: adopt-b*" "$(cat "$work/run2.out")"
  assert_eq "dual-spec:candidate specs are not re-written" "$candidates_before" "$(calls "$work" "fake-worker write-candidate")"
else
  echo "skip - dual-spec pty scenario (script(1) not available; covered by unit tests)"
fi

# ---------- summary ----------
echo ""
echo "Passed $PASS, failed $FAIL"
(( FAIL == 0 ))
