#!/usr/bin/env bash
#
# tests/e2e/run.sh - manual E2E test for adversarial-ai-coding
#
# Calls real AI engines and consumes tokens/quota, roughly $2-5 equivalent and 20-40 minutes with defaults.
# Run manually after core workflow changes. Do not put the full E2E in CI or the unit test entrypoint.
#
# Flow: create a fixture git repo in a temp dir -> verify baseline gates locally -> run full workflow
#       -> run automated acceptance checks -> preserve the working area for inspection.
#
# Usage:
#   bash tests/e2e/run.sh
#
# Environment variables:
#   E2E_DIR         Work directory; defaults to mktemp. Kept after success and failure.
#   E2E_SETUP_ONLY  1=create fixture repo and verify baseline only, without calling AI (default: 0)
#   Other adversarial-ai-coding variables pass through. E2E defaults:
#     HUMAN_GATE=0  ENGINE_A=claude  MODEL_A=sonnet  CLAUDE_ARGS='--effort=low'
#     ENGINE_B=codex  MODEL_B=gpt-5.5  CODEX_ARGS='-c model_reasoning_effort=low'
set -Eeuo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
SCRIPT="$ROOT/adversarial-ai-coding.sh"
FIXTURE="$HERE/fixture"

# E2E defaults from real-run lessons: worker should be at least sonnet class; lower codex effort to save quota.
export HUMAN_GATE="${HUMAN_GATE:-0}"
export ENGINE_A="${ENGINE_A:-claude}"
export MODEL_A="${MODEL_A:-sonnet}"
export CLAUDE_ARGS="${CLAUDE_ARGS:---effort=low}"
export ENGINE_B="${ENGINE_B:-codex}"
export MODEL_B="${MODEL_B:-gpt-5.5}"
export CODEX_ARGS="${CODEX_ARGS:--c model_reasoning_effort=low}"

command -v go >/dev/null 2>&1 || { echo "missing go toolchain; fixture is a Go project" >&2; exit 1; }
[[ -f "$SCRIPT" && -d "$FIXTURE" ]] || { echo "missing $SCRIPT or $FIXTURE" >&2; exit 1; }

if [[ -n "${E2E_DIR:-}" ]]; then
  BASE="$E2E_DIR"
  if command -v cygpath >/dev/null 2>&1; then
    BASE="$(cygpath -u "$BASE" 2>/dev/null || printf '%s' "$BASE")"
  fi
else
  BASE="$(mktemp -d -t wf-e2e-XXXXXX)"
fi
REPO="$BASE/repo"
RUN_LOG="$BASE/run.log"   # Keep outside the repo so workflow commits do not include it.

echo "== E2E workspace:$BASE"
mkdir -p "$REPO"
cp -r "$FIXTURE"/. "$REPO"/
cd "$REPO"
git init -q -b main
git config user.email e2e@local
git config user.name e2e
git add -A
git commit -qm "chore: baseline fixture for adversarial-ai-coding E2E"

echo "== Baseline gate, verified locally instead of trusting AI output"
# Keep these as separate statements. In an && chain, errexit does not trigger for non-final commands.
go build ./...
go vet ./...
go test ./...
echo "Baseline is green"

if [[ "${E2E_SETUP_ONLY:-0}" == "1" ]]; then
  echo "(E2E_SETUP_ONLY=1: fixture was created and baseline verified; no AI was called)"
  exit 0
fi

echo "== Running workflow (A=$ENGINE_A/${MODEL_A:-default}  B=$ENGINE_B/${MODEL_B:-default})"
echo "   Engine args:CLAUDE_ARGS='$CLAUDE_ARGS'  CODEX_ARGS='$CODEX_ARGS'"
rc=0
"$SCRIPT" task.md 2>&1 | tee "$RUN_LOG" || rc=$?
if (( rc != 0 )); then
  echo "!! Workflow exited with code $rc; workspace and logs are kept at $BASE for diagnosis" >&2
  exit "$rc"
fi

# ---------- automated acceptance checklist ----------
echo
echo "== Acceptance checklist"
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "ok   - $1"; }
bad() { FAIL=$((FAIL+1)); echo "FAIL - $1"; }

grep -q 'All stages complete' "$RUN_LOG" && ok "all six stages completed" || bad "all six stages completed"
grep -q 'Quality gate passed' "$RUN_LOG" && ok "deterministic quality gate passed at least once" || bad "deterministic quality gate"

[[ "$(git branch --show-current)" == auto/* ]] && ok "on auto/* work branch" || bad "on auto/* work branch"

[[ -f AGENTS.md && -f CLAUDE.md ]] && ok "AGENTS.md / CLAUDE.md were bootstrapped" || bad "AGENTS.md / CLAUDE.md were bootstrapped"

spec_dir=$(ls -d specs/*/ 2>/dev/null | head -1 || true)
if [[ -n "$spec_dir" && -f "$spec_dir/spec.md" ]]; then
  grep -qi 'Assumptions and Open Questions' "$spec_dir/spec.md" \
    && ok "spec.md has Assumptions and Open Questions" || bad "spec.md has Assumptions section"
  if [[ -f "$spec_dir/plan.md" ]]; then
    if grep -qE '^- \[ \] ' "$spec_dir/plan.md"; then bad "plan.md tasks are all checked (unfinished item remains)"
    elif grep -qE '^- \[x\]' "$spec_dir/plan.md"; then ok "plan.md tasks are all checked"
    else bad "plan.md tasks are all checked (no checkbox found)"; fi
  else bad "plan.md exists"; fi
else
  bad "specs/<run>/spec.md exists"
fi

grep -q 'func IsPalindrome' strutil/*.go && ok "IsPalindrome was implemented" || bad "IsPalindrome was implemented"

if [[ -s .workflow/protected-tests.txt && -f .workflow/protected-base.sha ]]; then
  ok "protected test file list is non-empty"
  # Reuse the script helper to verify protected tests were not modified.
  # shellcheck disable=SC1090
  source "$SCRIPT"
  viol=$(protected_violations .workflow/protected-tests.txt "$(cat .workflow/protected-base.sha)")
  [[ -z "$viol" ]] && ok "protected acceptance tests were not modified" || bad "protected acceptance tests were modified:$viol"
else
  bad "protected test file list is non-empty"
fi

run_dir=""
if [[ -f .workflow/latest-run.txt ]]; then
  run_dir=$(cat .workflow/latest-run.txt)
  [[ -d "$run_dir" ]] && ok "latest-run.txt points to an existing run directory" || bad "latest-run.txt points to an existing run directory"
else
  bad "latest-run.txt exists"
fi

if [[ -n "$run_dir" && -d "$run_dir" ]]; then
  ls "$run_dir"/*-task-source.md >/dev/null 2>&1 && ok "task-source was saved" || bad "task-source was saved"
  ls "$run_dir"/*-task.txt >/dev/null 2>&1 && ok "resolved task text was saved" || bad "resolved task text was saved"
  ls "$run_dir"/*-prompt.md >/dev/null 2>&1 && ok "AI prompt artifact exists" || bad "AI prompt artifact exists"
  ls "$run_dir"/*-output.txt >/dev/null 2>&1 && ok "AI output artifact exists" || bad "AI output artifact exists"
  ls "$run_dir"/*-attempt-*-rc*.raw >/dev/null 2>&1 && ok "engine attempt raw artifact exists" || bad "engine attempt raw artifact exists"
  ls "$run_dir"/*-git-status.txt >/dev/null 2>&1 && ok "git status snapshot exists" || bad "git status snapshot exists"
  ls "$run_dir"/*-git-diff.patch >/dev/null 2>&1 && ok "git diff snapshot exists" || bad "git diff snapshot exists"
  ls "$run_dir"/*.meta.json >/dev/null 2>&1 && ok "artifact metadata exists" || bad "artifact metadata exists"
  [[ -f "$run_dir/logs/001-run.log.meta.json" ]] && ok "run log metadata exists" || bad "run log metadata exists"
  jq -e '.run_id and .generator_role=="workflow"' "$run_dir/logs/001-run.log.meta.json" >/dev/null \
    && ok "run log metadata includes run/generator" || bad "run log metadata includes run/generator"
fi

n=$(git rev-list --count main..HEAD)
(( n >= 5 )) && ok "small-batch commits (main..HEAD = $n >= 5)" || bad "small-batch commits (main..HEAD = $n, expected >= 5)"

echo "== Final gate, verified locally"
if go build ./... && go vet ./... && go test ./...; then ok "final build/vet/test is green"
else bad "final build/vet/test is green"; fi

if [[ -n "$run_dir" && -f "$run_dir/metrics.csv" ]]; then
  ok "metrics.csv exists"
  if python - "$run_dir/metrics.csv" <<'PY'
import csv, sys
with open(sys.argv[1], newline="", encoding="utf-8") as f:
    rows = list(csv.reader(f))
assert rows[0] == ["run_id","stage","role","engine","round","duration_s","cost_usd","model","model_args","generated_at"]
assert len(rows) > 1
assert all(len(r) == 10 for r in rows)
PY
  then ok "metrics.csv new columns are parseable"
  else bad "metrics.csv new columns are parseable"; fi
  echo
  echo "== Run metrics"
  # Rows are quoted. Strip outer quotes before summarizing or seconds/cost may parse as 0.
  awk -F, 'NR>1 {
      for (i = 1; i <= NF; i++) gsub(/^"|"$/, "", $i)
      calls[$2]++; secs[$2] += $6; cost[$2] += $7
    }
    END { for (s in calls) printf "  %-14s %d calls,%d seconds,$%.4f\n", s, calls[s], secs[s], cost[s] }' \
    "$run_dir/metrics.csv"
else
  bad "metrics.csv exists"
fi

echo
echo "Acceptance: passed $PASS, failed $FAIL; workspace kept at $BASE (delete it after inspection)"
(( FAIL == 0 ))
