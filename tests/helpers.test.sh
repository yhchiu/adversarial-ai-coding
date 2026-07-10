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
out=$( cd "$d" && AGENT_A=codex AGENT_B=claude source "$SCRIPT" && engine_for_slot A )
assert_eq "agent aliases:AGENT_A configures slot A" "codex" "$out"
out=$( cd "$d" && AGENT_A=custom-agent AGENT_B=codex AGENT_A_ARGS='--model custom --flag' \
      && source "$SCRIPT" && resolve_model_args custom-agent )
assert_eq "agent aliases:custom agent uses AGENT_A_ARGS" "--model custom --flag" "$out"
out=$( cd "$d" && rc=0; AGENT_A=claude ENGINE_A=codex source "$SCRIPT" >/dev/null 2>&1 || rc=$?; printf '%s' "$rc" )
assert_eq "agent aliases:conflicting AGENT_A and ENGINE_A fail fast" "1" "$out"

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
out=$( cd "$d" && ENGINE_A=custom-agent ENGINE_B=codex MODEL_A=ignored ENGINE_A_ARGS='--model custom' \
      && source "$SCRIPT" && engine_model custom-agent )
assert_eq "engine_model:custom engine ignores MODEL_A" "" "$out"
out=$( cd "$d" && ENGINE_A=custom-agent ENGINE_B=codex ENGINE_A_ARGS='--model custom --flag' \
      && source "$SCRIPT" && resolve_model_args custom-agent )
assert_eq "resolve_model_args:custom engine uses ENGINE_A_ARGS" "--model custom --flag" "$out"

# ---------- generic engine helpers ----------
d=$(tmpdir)
out=$( cd "$d" && source "$SCRIPT" && is_builtin_engine claude; printf '%s' "$?" )
assert_eq "generic:is_builtin_engine detects built-in" "0" "$out"
out=$( cd "$d" && source "$SCRIPT" && if is_builtin_engine custom-agent; then printf '0'; else printf '1'; fi )
assert_eq "generic:is_builtin_engine rejects custom" "1" "$out"

d=$(tmpdir)
cat > "$d/fake-agent" <<'EOF'
#!/usr/bin/env bash
{
  printf 'argc=%s\n' "$#"
  i=1
  for arg in "$@"; do
    printf 'arg%s=%s\n' "$i" "$arg"
    i=$((i + 1))
  done
} > generic-capture.txt
printf 'custom engine ran\n'
EOF
chmod +x "$d/fake-agent"
out=$(
  cd "$d" \
    && mkdir -p .workflow \
    && ENGINE_A="$d/fake-agent" ENGINE_A_ARGS='--flag value' ENGINE_OUT="$d/engine-out.txt" \
    && source "$SCRIPT" \
    && CURRENT_ENGINE="$ENGINE_A" \
    && w_generic "hello prompt" >/dev/null \
    && cat generic-capture.txt \
    && printf -- '---\n' \
    && cat "$ENGINE_OUT"
)
assert_eq "generic:w_generic passes args and prompt as final arg" \
  $'argc=3\narg1=--flag\narg2=value\narg3=hello prompt\n---\ncustom engine ran' "$out"

# ---------- prompt file handoff ----------
d=$(tmpdir)
out=$( cd "$d" && source "$SCRIPT" && prompt_file_instruction .workflow/runs/test/001-worker-prompt.md )
if [[ "$out" == *"Read the full workflow prompt"* && "$out" == *".workflow/runs/test/001-worker-prompt.md"* ]]; then
  ok "prompt_file_instruction:points engine at prompt file"
else
  bad "prompt_file_instruction:points engine at prompt file(got [$out])"
fi

# ---------- workflow prompt templates ----------
expected_prompts_dir="$(cd "$(dirname "$SCRIPT")" && pwd)/resources/prompts"
out=$( cd "$(dirname "$SCRIPT")" && source "$SCRIPT" && printf '%s' "$PROMPTS_DIR" )
assert_eq "prompts:default directory lives under resources" "$expected_prompts_dir" "$out"

d=$(tmpdir)
mkdir -p "$d/prompts"
cat > "$d/prompts/sample.md" <<'EOF'
Hello {{NAME}}.
Path: {{PATH}}
Message:
{{MESSAGE}}
EOF
message=$'line one\nline two'
out=$(
  cd "$d" \
    && PROMPTS_DIR="$d/prompts" \
    && source "$SCRIPT" \
    && render_prompt sample "NAME=worker" "PATH=specs/run/spec.md" "MESSAGE=$message"
)
rc=$?
assert_rc "prompts:render_prompt succeeds" 0 "$rc"
assert_eq "prompts:render_prompt replaces placeholders" \
  $'Hello worker.\nPath: specs/run/spec.md\nMessage:\nline one\nline two' "$out"

d=$(tmpdir)
err=$(
  {
    cd "$d" \
      && mkdir -p prompts \
      && PROMPTS_DIR="$d/prompts" \
      && source "$SCRIPT" \
      && render_prompt missing >/dev/null
  } 2>&1
)
rc=$?
assert_nonzero "prompts:missing template fails" "$rc"
assert_like "prompts:missing template names the file" "*prompt template not found:*missing.md*" "$err"

d=$(tmpdir)
out=$(
  cd "$d" \
    && mkdir -p .workflow/runs/test .workflow/logs \
    && source "$SCRIPT" \
    && WF_RUN=.workflow/runs/test && ART_SEQ=0 && LOG=.workflow/logs/t.log && ENGINE_OUT=.workflow/engine-out.txt \
    && write_meta() { :; } \
    && printf 'FULL_PROMPT_SENTINEL\n' > .workflow/runs/test/001-worker-prompt.md \
    && fake_engine() { printf '%s\n' "$1" > captured-prompt.txt; printf 'ok\n' > "$ENGINE_OUT"; } \
    && engine_call worker custom worker-stage-r1 fake_engine .workflow/runs/test/001-worker-prompt.md >/dev/null \
    && cat captured-prompt.txt
)
if [[ "$out" == *"Read the full workflow prompt"* && "$out" == *".workflow/runs/test/001-worker-prompt.md"* && "$out" != *"FULL_PROMPT_SENTINEL"* ]]; then
  ok "engine_call:passes short prompt-file instruction"
else
  bad "engine_call:passes short prompt-file instruction(got [$out])"
fi

d=$(tmpdir)
cat > "$d/fake-agent" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "${@: -1}" > captured-prompt.txt
printf 'custom engine ran\n'
EOF
chmod +x "$d/fake-agent"
(
  cd "$d" \
    && mkdir -p .workflow/runs/test .workflow/logs \
    && ENGINE_A="$d/fake-agent" ENGINE_B=codex \
    && source "$SCRIPT" \
    && WF=.workflow && WF_RUN=.workflow/runs/test && ART_SEQ=0 && LOG=.workflow/logs/t.log \
    && ENGINE_OUT=.workflow/engine-out.txt && METRICS=.workflow/runs/test/metrics.csv \
    && CUR_STAGE=stage && CUR_ROUND=1 && RETRY_ON_LIMIT=0 \
    && write_meta() { :; } \
    && archive_git_state() { :; } \
    && metric() { :; } \
    && work "$ENGINE_A" "FULL_PROMPT_SENTINEL for worker" >/dev/null
)
worker_prompt=$(cat "$d/captured-prompt.txt")
worker_art=$(ls "$d/.workflow/runs/test/"*-worker-stage-r1-prompt.md 2>/dev/null | head -1 || true)
if [[ "$worker_prompt" == *"Read the full workflow prompt"* && "$worker_prompt" == *"worker-stage-r1-prompt.md"* && "$worker_prompt" != *"FULL_PROMPT_SENTINEL"* && -n "$worker_art" ]] \
  && grep -q 'FULL_PROMPT_SENTINEL for worker' "$worker_art"; then
  ok "work:archives full prompt and sends file reference"
else
  bad "work:archives full prompt and sends file reference(prompt [$worker_prompt], artifact [$worker_art])"
fi

d=$(tmpdir)
cat > "$d/fake-reviewer" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "${@: -1}" > captured-review-prompt.txt
mkdir -p .workflow
printf 'approved\n' > .workflow/review.md
printf '{"approved":true,"blockers":[],"suggestions":[]}\n' > .workflow/verdict.json
printf 'custom reviewer ran\n'
EOF
chmod +x "$d/fake-reviewer"
(
  cd "$d" \
    && mkdir -p .workflow/runs/test .workflow/logs \
    && ENGINE_A=claude ENGINE_B="$d/fake-reviewer" \
    && source "$SCRIPT" \
    && WF=.workflow && WF_RUN=.workflow/runs/test && ART_SEQ=0 && LOG=.workflow/logs/t.log \
    && ENGINE_OUT=.workflow/engine-out.txt && METRICS=.workflow/runs/test/metrics.csv \
    && CUR_STAGE=review && CUR_ROUND=2 && RETRY_ON_LIMIT=0 && COLLECT_REVIEW_SUGGESTIONS=0 \
    && write_meta() { :; } \
    && metric() { :; } \
    && verdict_approved() { return 0; } \
    && run_review "$ENGINE_B" "FULL_PROMPT_SENTINEL review scope" >/dev/null
)
reviewer_prompt=$(cat "$d/captured-review-prompt.txt")
reviewer_art=$(ls "$d/.workflow/runs/test/"*-reviewer-review-r2-prompt.md 2>/dev/null | head -1 || true)
if [[ "$reviewer_prompt" == *"Read the full workflow prompt"* && "$reviewer_prompt" == *"reviewer-review-r2-prompt.md"* && "$reviewer_prompt" != *"FULL_PROMPT_SENTINEL"* && -n "$reviewer_art" ]] \
  && grep -q 'FULL_PROMPT_SENTINEL review scope' "$reviewer_art"; then
  ok "run_review:archives full prompt and sends file reference"
else
  bad "run_review:archives full prompt and sends file reference(prompt [$reviewer_prompt], artifact [$reviewer_art])"
fi

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
out=$( cd "$d" && source "$SCRIPT" && compose_review_prompt custom-agent scope )
if [[ "$out" == *"Finally write the verdict"* ]]; then ok "compose_review_prompt:custom includes verdict_file_instr"
else bad "compose_review_prompt:custom includes verdict_file_instr"; fi

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

d=$(tmpdir)
out=$( cd "$d" && source "$SCRIPT" && COLLECT_REVIEW_SUGGESTIONS=0 && collect_review_suggestions_enabled && echo yes || echo no )
assert_eq "dual_spec:candidate review can disable suggestion collection" "no" "$out"

d=$(tmpdir)
out=$(
  cd "$d" && mkdir -p .workflow && source "$SCRIPT" && WF=.workflow \
    && write_spec_merge_request_template A B \
    && merge_request_has_content && echo yes || echo no
)
assert_eq "dual_spec:untouched merge request template is not content" "no" "$out"
printf '%s\n' \
  '# Dual Spec Merge Request' \
  '' \
  '## Items to adopt from B' \
  '' \
  '- adopt from Candidate B the stricter timeout acceptance criterion.' \
  '- edge cases, especially empty task files, must be covered.' \
  > "$d/.workflow/spec-merge-request.md"
out=$( cd "$d" && source "$SCRIPT" && WF=.workflow && merge_request_has_content && echo yes || echo no )
assert_eq "dual_spec:merge request accepts realistic human instructions" "yes" "$out"
printf '%s\n' \
  '# Dual Spec Merge Request' \
  '' \
  '## Items to adopt from B' \
  '' \
  'adopt from Candidate B the stricter timeout acceptance criterion.' \
  'edge cases, especially empty task files, must be covered.' \
  > "$d/.workflow/spec-merge-request.md"
out=$( cd "$d" && source "$SCRIPT" && WF=.workflow && merge_request_has_content && echo yes || echo no )
assert_eq "dual_spec:merge request accepts paragraph instructions with template-like prefixes" "yes" "$out"

d=$(tmpdir)
out=$( cd "$d" && source "$SCRIPT" && SPEC_DIR=specs && dual_spec_final_review_scope merge-b )
if [[ "$out" == *".workflow/spec-merge-request.md"* && "$out" == *"block approval"* ]]; then
  ok "dual_spec:merge final review scope checks merge request adoption"
else
  bad "dual_spec:merge final review scope checks merge request adoption"
fi

d=$(tmpdir)
out=$(
  cd "$d" && mkdir -p specs .workflow && printf 'candidate A\n' > specs/spec-a.md && printf 'candidate B\n' > specs/spec-b.md \
    && source "$SCRIPT" && SPEC_DIR=specs && WF=.workflow && ENGINE_A=claude && ENGINE_B=codex \
    && review_loop() { printf 'review:%s/%s/%s\n' "$1" "$2" "$3" >> call.log; } \
    && human_gate_spec() { printf 'human\n' >> call.log; } \
    && work() { printf 'work:%s\n' "$1" >> call.log; } \
    && apply_dual_spec_decision adopt-a 'task text' \
    && printf '%s\n---\n' "$(cat specs/spec.md)" && cat call.log
)
assert_eq "dual_spec:direct adopt reviews final spec and asks for human approval" \
  $'candidate A\n---\nreview:codex/claude/specs/spec.md after dual spec selection: review the final selected spec before implementation planning. Check requirement completeness, testable acceptance criteria, edge cases, out-of-scope items, and assumptions.\nhuman' "$out"

d=$(tmpdir)
out=$(
  cd "$d" && mkdir -p specs .workflow && printf 'candidate A\n' > specs/spec-a.md && printf 'candidate B\n' > specs/spec-b.md \
    && printf 'adopt item\n' > .workflow/spec-merge-request.md \
    && source "$SCRIPT" && SPEC_DIR=specs && WF=.workflow && ENGINE_A=claude && ENGINE_B=codex \
    && review_loop() { printf 'review:%s/%s/%s\n' "$1" "$2" "$3" >> call.log; } \
    && human_gate_spec() { printf 'human\n' >> call.log; } \
    && work() { printf 'work:%s\n' "$1" >> call.log; printf 'merged B\n' > specs/spec.md; } \
    && apply_dual_spec_decision merge-b 'task text' \
    && printf '%s\n---\n' "$(cat specs/spec.md)" && cat call.log
)
assert_eq "dual_spec:merge path uses selected owner then reviews final spec and asks for human approval" \
  $'merged B\n---\nwork:codex\nreview:claude/codex/specs/spec.md after dual spec selection: review the final selected spec before implementation planning. Check requirement completeness, testable acceptance criteria, edge cases, out-of-scope items, and assumptions. Also compare specs/spec.md with .workflow/spec-merge-request.md and block approval if any requested adoption item is missing, distorted, or contradicted.\nhuman' "$out"

d=$(tmpdir)
out=$(
  cd "$d" && mkdir -p specs .workflow \
    && source "$SCRIPT" && SPEC_DIR=specs && WF=.workflow && ENGINE_A=claude && ENGINE_B=codex \
    && begin_stage() { :; } \
    && work() {
         case "$2" in
           *spec-a.md*) printf 'candidate A\n' > specs/spec-a.md ;;
           *spec-b.md*) printf 'candidate B\n' > specs/spec-b.md ;;
           *spec-comparison-a.md*) printf 'comparison A\n' > specs/spec-comparison-a.md ;;
           *spec-comparison-b.md*) printf 'comparison B\n' > specs/spec-comparison-b.md ;;
         esac
       } \
    && run_candidate_spec_review() { printf 'review\n' > "$3"; printf '{"approved":false,"blockers":[],"suggestions":[]}\n' > "$4"; } \
    && write_spec_comparison_index() { printf 'index\n' > specs/spec-comparison.md; } \
    && human_gate_dual_spec_decision() { printf 'log noise\n'; DUAL_SPEC_DECISION=adopt-a; } \
    && review_loop() { printf 'review:%s/%s\n' "$1" "$2" >> call.log; } \
    && human_gate_spec() { printf 'human\n' >> call.log; } \
    && run_dual_spec_spec_stage 'task text' \
    && printf '%s\n---\n' "$(cat specs/spec.md)" && cat call.log
)
assert_eq "dual_spec:runner uses decision variable instead of captured log output" \
  $'log noise\ncandidate A\n---\nreview:codex/claude\nhuman' "$out"

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
expected_agents_template="$(cd "$(dirname "$SCRIPT")" && pwd)/resources/AGENTS.template.md"
out=$( cd "$(dirname "$SCRIPT")" && source "$SCRIPT" && printf '%s' "$AGENTS_TEMPLATE" )
assert_eq "bootstrap:default template lives under resources" "$expected_agents_template" "$out"

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

d=$(new_repo)
( cd "$d" && ENGINE_A=sh ENGINE_B=sh "$SCRIPT" "task" ) >/dev/null 2>&1
assert_rc "preflight:custom same command -> blocked" 1 $?
assert_eq "preflight:custom block leaves no branch side effect" "main" "$(git -C "$d" branch --show-current)"

d=$(tmpdir)
( cd "$d" && ENGINE_A=sh && ENGINE_B=pwd && source "$SCRIPT" && validate_engines ) >/dev/null 2>&1
assert_rc "preflight:custom commands can be validated" 0 $?

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
# Real codex CLI quota message, wrapped across lines exactly as the CLI prints it.
cat > "$d/codexquota.txt" <<'EOF'
You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), visit
https://chatgpt.com/codex/settings/usage to purchase more credits or try again at Jul
14th, 2026 7:23 PM.
EOF
( cd "$d" && source "$SCRIPT" && is_rate_limited codexquota.txt ) >/dev/null 2>&1
assert_rc "rate-limit detection:real codex quota message" 0 $?
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
if [[ -n "$w" && "$w" -ge 82000 && "$w" -le 83000 ]]; then ok "reset parser:past clock time rolls to tomorrow"
else bad "reset parser:past clock time (got [$w], expected about 82920)"; fi

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
assert_eq "reset parser:12 hours parsed as-is; the caller applies policy" "43230" "$w"
printf 'try again in 900 hours\n' > "$d/absurd.txt"
w=$( cd "$d" && source "$SCRIPT" && parse_reset_wait absurd.txt "$now" )
assert_eq "reset parser:beyond 30 days -> empty sanity guard" "" "$w"

# Format 3: absolute quota reset timestamp, wrapped across lines, with an ordinal suffix.
now_fixed=$(LC_ALL=C date -d "2026-07-08 07:00:00" +%s)
target=$(LC_ALL=C date -d "Jul 14, 2026 7:23 PM" +%s)
w=$( cd "$d" && source "$SCRIPT" && parse_reset_wait codexquota.txt "$now_fixed" )
assert_eq "reset parser:real codex 'try again at <date>' across a line break" "$(( target - now_fixed + 30 ))" "$w"

printf 'try again at Jan 2nd, 2020 7:23 PM.\n' > "$d/elapsed.txt"
w=$( cd "$d" && source "$SCRIPT" && parse_reset_wait elapsed.txt "$now" )
assert_eq "reset parser:absolute date already elapsed -> short retry buffer" "30" "$w"

# ---------- engine_call with stub engine and RETRY_BASE_WAIT=1 for fast tests ----------
d=$(tmpdir)
run_engine_call() {  # $1: RETRY_ON_LIMIT  $2: stub output, empty means success
  ( cd "$d" && mkdir -p .workflow/logs \
    && RETRY_ON_LIMIT="$1" RETRY_BASE_WAIT=1 RETRY_MAX=2 STUB="$2" \
    && source "$SCRIPT" && WF_RUN=.workflow/runs/test && mkdir -p "$WF_RUN" && ART_SEQ=0 && LOG=.workflow/logs/t.log && touch "$LOG" \
    && CALLS=0 \
    && sleep() { :; } \
    && fake_engine() {
         CALLS=$(( CALLS + 1 ))
         [[ -z "$STUB" ]] && return 0
         printf '%s\n' "$STUB" > "$ENGINE_OUT"
         return 1
       } \
    && rc=0 && engine_call worker claude worker-stage-r1 fake_engine prompt >/dev/null 2>&1 || rc=$? \
    ;  echo "rc=$rc calls=$CALLS" )
}
# Quota/rate-limit give-ups return the typed QUOTA_ABORT_RC (75) so the run aborts as resumable.
out=$(run_engine_call 1 'api_error_status":429 hit your session limit')
assert_eq "engine_call:default retries to limit (1 call + 2 retries)" "rc=75 calls=3" "$out"
out=$(run_engine_call 0 'api_error_status":429 hit your session limit')
assert_eq "engine_call:RETRY_ON_LIMIT=0 -> no retry, typed quota abort" "rc=75 calls=1" "$out"
out=$(run_engine_call 1 'ordinary build failure')
assert_eq "engine_call:non-rate-limit error -> no retry" "rc=1 calls=1" "$out"
out=$(run_engine_call 1 '')
assert_eq "engine_call:success passes immediately" "rc=0 calls=1" "$out"
# A quota that resets days from now must fail fast: backing off would sleep for hours and still fail.
far=$(LC_ALL=C date -d "+10 days" '+%b %-d, %Y %-I:%M %p')
out=$(run_engine_call 1 "You've hit your usage limit. try again at $far.")
assert_eq "engine_call:reset beyond RETRY_MAX_RESET_WAIT -> abort without sleeping" "rc=75 calls=1" "$out"
near=$(LC_ALL=C date -d "+1 hour" '+%b %-d, %Y %-I:%M %p')
out=$(run_engine_call 1 "You've hit your usage limit. try again at $near.")
assert_eq "engine_call:reset within the ceiling -> waits and retries" "rc=75 calls=3" "$out"

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

# ---------- resume state: conf parser and writer ----------
d=$(new_repo)
out=$(
  cd "$d" && mkdir -p .workflow/state/rt && source "$SCRIPT" \
    && RUN_STATE_DIR=.workflow/state/rt \
    && SPEC_DIR='specs/x y' && CODEX_ARGS='-c model="x,y" --flag "quoted value"' && GATE_CMD='go test ./...' \
    && RUN_TASK_ARG='task.md' && RUN_TASK_SOURCE_KIND=file && RUN_TASK_SOURCE_PATH='/tmp/task dir/task.md' \
    && write_resume_conf \
    && parse_resume_conf .workflow/state/rt/resume.conf \
    && printf '%s|%s|%s|%s\n' "$RESUMED_SPEC_DIR" "$RESUMED_CODEX_ARGS" "$RESUMED_GATE_CMD" "$RESUMED_TASK_SOURCE_PATH"
)
assert_eq "resume conf:write/parse roundtrip keeps spaces and quotes" \
  'specs/x y|-c model="x,y" --flag "quoted value"|go test ./...|/tmp/task dir/task.md' "$out"

d=$(tmpdir); mkdir -p "$d/st"
printf 'schema=1\nevil_key=x\n' > "$d/st/resume.conf"
( cd "$d" && source "$SCRIPT" && parse_resume_conf st/resume.conf ) >/dev/null 2>&1
assert_nonzero "resume conf:unknown key is rejected" $?
printf 'spec_dir=specs/x\n' > "$d/st/resume.conf"
( cd "$d" && source "$SCRIPT" && parse_resume_conf st/resume.conf ) >/dev/null 2>&1
assert_nonzero "resume conf:missing schema line is rejected" $?
printf 'schema=2\nspec_dir=specs/x\n' > "$d/st/resume.conf"
( cd "$d" && source "$SCRIPT" && parse_resume_conf st/resume.conf ) >/dev/null 2>&1
assert_nonzero "resume conf:schema=2 is rejected" $?
: > "$d/st/resume.conf"
( cd "$d" && source "$SCRIPT" && parse_resume_conf st/resume.conf ) >/dev/null 2>&1
assert_nonzero "resume conf:empty file is rejected" $?
printf 'schema=1\ntruncated line without equals\n' > "$d/st/resume.conf"
( cd "$d" && source "$SCRIPT" && parse_resume_conf st/resume.conf ) >/dev/null 2>&1
assert_nonzero "resume conf:line without = is rejected" $?

d=$(new_repo)
rc=0
err=$( cd "$d" && mkdir -p st && source "$SCRIPT" && RUN_STATE_DIR=st && SPEC_DIR=$'a\nb' && write_resume_conf 2>&1 ) || rc=$?
assert_nonzero "resume conf:value with newline fails fast" "$rc"
assert_like "resume conf:newline failure names the key" "*spec_dir*newline*" "$err"

# ---------- resume state: RESUME_RUN resolution and locking ----------
d=$(new_repo)
( cd "$d" && RESUME_RUN='../../x' "$SCRIPT" ) >/dev/null 2>&1
assert_rc "resume load:path traversal id is rejected" 1 $?

mkdir -p "$d/.workflow/state/aaa-run"
printf 'schema=1\n' > "$d/.workflow/state/aaa-run/resume.conf"
err=$( cd "$d" && RESUME_RUN=nope "$SCRIPT" 2>&1 ); rc=$?
assert_rc "resume load:unknown id fails" 1 "$rc"
assert_like "resume load:unknown id lists available runs" "*aaa-run*" "$err"

touch "$d/.workflow/state/aaa-run/completed"
err=$( cd "$d" && RESUME_RUN=aaa-run "$SCRIPT" 2>&1 ); rc=$?
assert_rc "resume load:completed run is refused" 1 "$rc"
assert_like "resume load:completed run message says so" "*already completed*" "$err"

d=$(new_repo)
mkdir -p "$d/.workflow/state/20260101-000000" "$d/.workflow/state/20260102-000000"
printf 'schema=1\n' > "$d/.workflow/state/20260101-000000/resume.conf"
printf 'schema=1\n' > "$d/.workflow/state/20260102-000000/resume.conf"
touch "$d/.workflow/state/20260102-000000/completed"
out=$( cd "$d" && RESUME_RUN=last source "$SCRIPT" 2>/dev/null && printf '%s' "$RUN_ID" )
assert_eq "resume load:last picks the newest unfinished run" "20260101-000000" "$out"
touch "$d/.workflow/state/20260101-000000/completed"
( cd "$d" && RESUME_RUN=last "$SCRIPT" ) >/dev/null 2>&1
assert_rc "resume load:last with everything completed fails" 1 $?

d=$(new_repo); st="$d/.workflow/state/r1"; mkdir -p "$st"
printf 'schema=1\nengine_a=sh\nengine_b=pwd\nmax_rounds=5\n' > "$st/resume.conf"
out=$( cd "$d" && RESUME_RUN=r1 source "$SCRIPT" 2>/dev/null && printf '%s/%s/%s' "$ENGINE_A" "$ENGINE_B" "$MAX_ROUNDS" )
assert_eq "resume load:snapshot supplies engine and round defaults" "sh/pwd/5" "$out"
out=$( cd "$d" && AGENT_B=codex && MAX_ROUNDS=2 && RESUME_RUN=r1 && source "$SCRIPT" 2>/dev/null && printf '%s/%s/%s' "$ENGINE_A" "$ENGINE_B" "$MAX_ROUNDS" )
assert_eq "resume load:explicit env overrides the snapshot" "sh/codex/2" "$out"
out=$( cd "$d" && AGENT_A=claude RESUME_RUN=r1 source "$SCRIPT" 2>/dev/null && printf '%s' "$ENGINE_A" )
assert_eq "resume load:AGENT_A override does not trip the alias conflict" "claude" "$out"

st2="$d/.workflow/state/r2"; mkdir -p "$st2"
printf 'schema=1\ndual_spec=0\n' > "$st2/resume.conf"
err=$( cd "$d" && DUAL_SPEC=1 RESUME_RUN=r2 "$SCRIPT" 2>&1 ); rc=$?
assert_rc "resume load:immutable field conflict is rejected" 1 "$rc"
assert_like "resume load:immutable conflict names the variable" "*DUAL_SPEC=1*" "$err"

st3="$d/.workflow/state/r3"; mkdir -p "$st3"
printf 'schema=1\nengine_a=sh\nengine_b=pwd\n' > "$st3/resume.conf"
printf 'snapshot task\n' > "$st3/task.txt"
err=$( cd "$d" && RESUME_RUN=r3 "$SCRIPT" "different task" 2>&1 ); rc=$?
assert_rc "resume load:conflicting task argument fails" 1 "$rc"
assert_like "resume load:task conflict points at the snapshot" "*task snapshot*" "$err"

st4="$d/.workflow/state/r4"; mkdir -p "$st4/lock"
printf 'schema=1\n' > "$st4/resume.conf"
err=$( cd "$d" && RESUME_RUN=r4 "$SCRIPT" 2>&1 ); rc=$?
assert_rc "resume load:busy lock is refused" 1 "$rc"
assert_like "resume load:busy lock explains manual removal" "*rm -r*lock*" "$err"

d=$(new_repo)
( cd "$d" && source "$SCRIPT" && RUN_ID=xdup && mkdir -p .workflow/state/xdup && init_run_state task ) >/dev/null 2>&1
assert_nonzero "fresh state:same-second run id collision fails clearly" $?

# ---------- resume state: stage ledger ----------
d=$(new_repo)
out=$(
  cd "$d" && mkdir -p .workflow/state/run .workflow/logs && source "$SCRIPT" \
    && RUN_STATE_DIR=.workflow/state/run && STAGE_LEDGER=.workflow/state/run/completed-stages \
    && : > "$STAGE_LEDGER" && LOG=.workflow/logs/t.log \
    && log_section() { :; } \
    && begin_stage stage-one >/dev/null \
    && end_stage \
    && stage_done stage-one && echo recorded \
    && if ! stage_done stage-two; then echo unrecorded-still-runs; fi \
    && [[ -f .workflow/state/run/last-head ]] && echo head-checkpoint
)
assert_eq "ledger:end_stage records the stage and a HEAD checkpoint" \
  $'recorded\nunrecorded-still-runs\nhead-checkpoint' "$out"

out=$(
  cd "$d" && source "$SCRIPT" \
    && RUN_STATE_DIR=.workflow/state/run && STAGE_LEDGER=.workflow/state/run/completed-stages \
    && LOG=.workflow/logs/t.log \
    && rc=0 && begin_stage stage-one || rc=$? ; echo "rc=$rc"
)
assert_like "ledger:completed stage is skipped and returns 1" "*== skip ?stage-one? (already completed in run *rc=1*" "$out"

out=$(
  cd "$d" && source "$SCRIPT" \
    && RUN_STATE_DIR=.workflow/state/run && STAGE_LEDGER=.workflow/state/run/completed-stages \
    && LOG=.workflow/logs/t.log \
    && touch artifact.md \
    && rc=0 && begin_stage stage-one artifact.md || rc=$? ; echo "rc=$rc"
)
assert_like "ledger:skip verifies required artifacts first" "*== skip ?stage-one?*rc=1*" "$out"

rc=0
err=$(
  cd "$d" && source "$SCRIPT" \
    && RUN_STATE_DIR=.workflow/state/run && STAGE_LEDGER=.workflow/state/run/completed-stages \
    && LOG=.workflow/logs/t.log \
    && begin_stage stage-one missing-artifact.md 2>&1 >/dev/null
) || rc=$?
assert_nonzero "ledger:missing artifact fails closed" "$rc"
assert_like "ledger:missing artifact points at the run archive" "*run archive*" "$err"

d=$(tmpdir)
out=$(
  cd "$d" && mkdir -p .workflow/logs && source "$SCRIPT" && LOG=.workflow/logs/t.log \
    && log_section() { :; } \
    && begin_stage some-stage >/dev/null \
    && end_stage \
    && echo "cur=$CUR_STAGE" \
    && [[ ! -e .workflow/state ]] && echo no-state-written
)
assert_eq "ledger:without claimed run state begin/end behave as before" $'cur=some-stage\nno-state-written' "$out"

# ---------- resume state: init_live_state resume mode ----------
d=$(tmpdir)
mkdir -p "$d/.workflow"
for f in suggestions.md protected-tests.txt protected-base.sha spec-merge-request.md review.md verdict.json last-engine-output.txt pr-body.md; do
  printf 'x\n' > "$d/.workflow/$f"
done
( cd "$d" && source "$SCRIPT" && init_live_state resume )
[[ -f "$d/.workflow/suggestions.md" && -f "$d/.workflow/protected-tests.txt" && -f "$d/.workflow/protected-base.sha" && -f "$d/.workflow/spec-merge-request.md" ]] \
  && ok "init_live_state:resume keeps durable cross-stage files" || bad "init_live_state:resume keeps durable cross-stage files"
[[ ! -e "$d/.workflow/review.md" && ! -e "$d/.workflow/verdict.json" && ! -e "$d/.workflow/last-engine-output.txt" && ! -e "$d/.workflow/pr-body.md" ]] \
  && ok "init_live_state:resume clears self-healing transients" || bad "init_live_state:resume clears self-healing transients"

# ---------- resume state: last-head checkpoint and workspace ----------
d=$(new_repo)
( cd "$d" && git commit --allow-empty -qm second )
first=$(git -C "$d" rev-parse HEAD~1)
mkdir -p "$d/.workflow/state/r"
printf '%s\n' "$first" > "$d/.workflow/state/r/last-head"
rc=0
err=$( cd "$d" && source "$SCRIPT" && RUN_STATE_DIR=.workflow/state/r && verify_last_head 2>&1 ) || rc=$?
assert_rc "last-head:ancestor checkpoint only warns" 0 "$rc"
assert_like "last-head:ancestor warning mentions new commits" "*new commits*" "$err"

printf '0123456789abcdef0123456789abcdef01234567\n' > "$d/.workflow/state/r/last-head"
( cd "$d" && source "$SCRIPT" && RUN_STATE_DIR=.workflow/state/r && verify_last_head ) >/dev/null 2>&1
assert_nonzero "last-head:unreachable checkpoint fails closed" $?

rm "$d/.workflow/state/r/last-head"
printf 'write-spec\n' > "$d/.workflow/state/r/completed-stages"
( cd "$d" && source "$SCRIPT" && RUN_STATE_DIR=.workflow/state/r && STAGE_LEDGER=.workflow/state/r/completed-stages && verify_last_head ) >/dev/null 2>&1
assert_nonzero "last-head:ledger without checkpoint fails closed" $?

d=$(new_repo)
git -C "$d" branch auto-r
mkdir -p "$d/.workflow/state/r"
out=$(
  cd "$d" && source "$SCRIPT" \
    && RUN_STATE_DIR=.workflow/state/r && RESUMED_BRANCH=auto-r \
    && resume_workspace >/dev/null 2>&1 \
    && git branch --show-current
)
assert_eq "resume workspace:switches back to the recorded branch" "auto-r" "$out"

( cd "$d" && source "$SCRIPT" && RUN_STATE_DIR=.workflow/state/r && RESUMED_BRANCH=gone-branch && resume_workspace ) >/dev/null 2>&1
assert_nonzero "resume workspace:missing branch fails with a clear error" $?

printf 'dirty change\n' >> "$d/base.txt"
err=$(
  cd "$d" && source "$SCRIPT" \
    && RUN_STATE_DIR=.workflow/state/r && RESUMED_BRANCH=$(git branch --show-current) \
    && resume_workspace 2>&1 >/dev/null
)
assert_like "resume workspace:dirty tree warns about auto-commit absorption" "*absorbed into the next automatic commit*" "$err"

# ---------- resume state: dual-spec decision restore ----------
d=$(tmpdir)
mkdir -p "$d/specs" "$d/.workflow"
printf -- '# Dual Spec Decision\n\n- decision: adopt-b\n- selected owner slot: B\n' > "$d/specs/spec-decision.md"
out=$(
  cd "$d" && ENGINE_A=claude && ENGINE_B=codex && source "$SCRIPT" && SPEC_DIR=specs && WF=.workflow \
    && DUAL_SPEC=1 && DUAL_SPEC_DECISION= \
    && restore_dual_spec_decision 2>/dev/null \
    && printf '%s|%s|%s' "$DUAL_SPEC_DECISION" "$SPEC_OWNER_ENGINE" "$SPEC_REVIEWER_ENGINE"
)
assert_eq "dual_spec restore:adopt-b restores owner engine B" "adopt-b|codex|claude" "$out"

printf -- '- decision: merge-b\n' > "$d/specs/spec-decision.md"
printf 'adopt item\n' > "$d/.workflow/spec-merge-request.md"
out=$(
  cd "$d" && ENGINE_A=claude && ENGINE_B=codex && source "$SCRIPT" && SPEC_DIR=specs && WF=.workflow \
    && DUAL_SPEC=1 && DUAL_SPEC_DECISION= \
    && restore_dual_spec_decision 2>/dev/null \
    && printf '%s|%s' "$DUAL_SPEC_DECISION" "$SPEC_OWNER_ENGINE"
)
assert_eq "dual_spec restore:merge-b with merge request restores" "merge-b|codex" "$out"

rm "$d/.workflow/spec-merge-request.md"
rc=0
err=$(
  cd "$d" && ENGINE_A=claude && ENGINE_B=codex && source "$SCRIPT" && SPEC_DIR=specs && WF=.workflow \
    && DUAL_SPEC=1 && DUAL_SPEC_DECISION= && restore_dual_spec_decision 2>&1
) || rc=$?
assert_nonzero "dual_spec restore:merge without merge request fails closed" "$rc"
assert_like "dual_spec restore:merge failure points at the archive" "*run archive*" "$err"

printf -- '- decision: bogus\n' > "$d/specs/spec-decision.md"
( cd "$d" && source "$SCRIPT" && SPEC_DIR=specs && WF=.workflow && DUAL_SPEC=1 && DUAL_SPEC_DECISION= && restore_dual_spec_decision ) >/dev/null 2>&1
assert_nonzero "dual_spec restore:invalid decision fails" $?

out=$(
  cd "$d" && ENGINE_A=claude && ENGINE_B=codex && source "$SCRIPT" && SPEC_DIR=specs && WF=.workflow \
    && DUAL_SPEC=1 && DUAL_SPEC_DECISION=adopt-a && SPEC_OWNER_ENGINE=claude \
    && restore_dual_spec_decision && printf '%s|%s' "$DUAL_SPEC_DECISION" "$SPEC_OWNER_ENGINE"
)
assert_eq "dual_spec restore:existing decision is left alone" "adopt-a|claude" "$out"

# ---------- resume state: acceptance test base ----------
d=$(new_repo)
mkdir -p "$d/.workflow/state/r"
printf 'cafebabe\n' > "$d/.workflow/state/r/acceptance-test-base"
out=$( cd "$d" && source "$SCRIPT" && RUN_STATE_DIR=.workflow/state/r && restore_or_record_acceptance_base )
assert_eq "acceptance base:persisted value is reused" "cafebabe" "$out"

rm "$d/.workflow/state/r/acceptance-test-base"
head_sha=$(git -C "$d" rev-parse HEAD)
out=$( cd "$d" && source "$SCRIPT" && RUN_STATE_DIR=.workflow/state/r && restore_or_record_acceptance_base )
assert_eq "acceptance base:first entry records HEAD" "$head_sha" "$out"
assert_eq "acceptance base:first entry persists the sha" "$head_sha" "$(cat "$d/.workflow/state/r/acceptance-test-base")"

# ---------- resume state: write-code task queue ----------
d=$(new_repo)
mkdir -p "$d/.workflow/state/r"
printf -- '- [ ] task one\n- [x] done task\n- [ ] task two\n' > "$d/plan.md"
out=$(
  cd "$d" && source "$SCRIPT" && RUN_STATE_DIR=.workflow/state/r \
    && ensure_task_queue plan.md \
    && cat .workflow/state/r/tasks-remaining
)
assert_eq "task queue:created from unfinished plan tasks" $'task one\ntask two' "$out"

printf 'custom remaining task\n' > "$d/.workflow/state/r/tasks-remaining"
out=$(
  cd "$d" && source "$SCRIPT" && RUN_STATE_DIR=.workflow/state/r \
    && ensure_task_queue plan.md \
    && cat .workflow/state/r/tasks-remaining
)
assert_eq "task queue:existing queue is not rebuilt from the plan" "custom remaining task" "$out"

out=$(
  cd "$d" && source "$SCRIPT" && RUN_STATE_DIR=.workflow/state/r \
    && pop_task_queue \
    && wc -l < .workflow/state/r/tasks-remaining
)
assert_eq "task queue:pop removes the finished first line" "0" "$out"

out=$(
  cd "$d" && source "$SCRIPT" && RUN_STATE_DIR=.workflow/state/r \
    && ensure_task_queue plan.md \
    && wc -l < .workflow/state/r/tasks-remaining
)
assert_eq "task queue:empty queue means all done and gets no fallback" "0" "$out"

rm "$d/.workflow/state/r/tasks-remaining"
printf 'prose only, no checkbox list\n' > "$d/plan2.md"
out=$(
  cd "$d" && source "$SCRIPT" && RUN_STATE_DIR=.workflow/state/r \
    && ensure_task_queue plan2.md 2>/dev/null \
    && cat .workflow/state/r/tasks-remaining
)
assert_like "task queue:plan without checkboxes still falls back" "*Complete the full implementation*plan2.md*" "$out"

d=$(tmpdir)
printf -- '- [ ] task one\n- [ ] task one extra\nplain line\n' > "$d/plan.md"
( cd "$d" && source "$SCRIPT" && mark_plan_task_done plan.md "task one" && mark_plan_task_done plan.md "task one" )
assert_eq "plan checkbox:exact line is ticked once, prefix matches untouched" \
  $'- [x] task one\n- [ ] task one extra\nplain line' "$(cat "$d/plan.md")"

# ---------- resume state: abort reporting and idempotent finish ----------
d=$(new_repo)
out=$(
  cd "$d" && source "$SCRIPT" \
    && RUN_ID=myrun \
    && print_resume_hint 2>&1 \
    && print_resume_hint 2>&1 \
    && echo "end"
)
assert_like "resume hint:contains RESUME_RUN=<id>" "*RESUME_RUN=myrun*" "$out"
assert_eq "resume hint:deduplicated to a single print" "1" "$(grep -c 'RESUME_RUN=' <<<"$out")"

out=$(
  cd "$d" && source "$SCRIPT" \
    && RUN_ID=myrun && USE_WORKTREE=1 \
    && print_resume_hint 2>&1
)
assert_like "resume hint:worktree variant includes a cd command" "*cd *RESUME_RUN=myrun*" "$out"

( cd "$d" && bash -c "source '$SCRIPT'; install_run_traps; exit 7" ) >/dev/null 2>&1
assert_rc "traps:EXIT trap preserves the original exit code" 7 $?

mkdir -p "$d/.workflow/state/hintrun"
err=$( cd "$d" && bash -c "source '$SCRIPT'; RUN_STATE_DIR=.workflow/state/hintrun; RUN_ID=hintrun; install_run_traps; exit 9" 2>&1 ); rc=$?
assert_rc "traps:failing run keeps its exit code" 9 "$rc"
assert_like "traps:failing run prints the resume hint" "*RESUME_RUN=hintrun*" "$err"

touch "$d/.workflow/state/hintrun/completed"
err=$( cd "$d" && bash -c "source '$SCRIPT'; RUN_STATE_DIR=.workflow/state/hintrun; RUN_ID=hintrun; install_run_traps; exit 9" 2>&1 ); rc=$?
assert_rc "traps:completed run still keeps its exit code" 9 "$rc"
if [[ "$err" != *"RESUME_RUN=hintrun"* ]]; then
  ok "traps:completed run does not advertise a resume"
else
  bad "traps:completed run does not advertise a resume(got [$err])"
fi

( cd "$d" && bash -c "source '$SCRIPT'; install_run_traps; kill -INT \$\$; sleep 1" ) >/dev/null 2>&1
assert_rc "traps:SIGINT exits 130 through the EXIT trap" 130 $?

d=$(tmpdir)
( cd "$d" && mkdir -p .workflow/runs/test .workflow/logs && source "$SCRIPT" \
    && WF=.workflow && WF_RUN=.workflow/runs/test && ART_SEQ=0 && LOG=.workflow/logs/t.log \
    && CUR_STAGE=review && CUR_ROUND=1 && COLLECT_REVIEW_SUGGESTIONS=0 \
    && write_meta() { :; } && metric() { :; } && log_section() { :; } \
    && engine_call() { return 75; } \
    && run_review codex scope ) >/dev/null 2>&1
assert_rc "run_review:quota abort exits 75 instead of starting repair rounds" 75 $?

d=$(new_repo)
bare=$(tmpdir)
git init -q --bare "$bare"
git -C "$d" remote add origin "$bare"
mkdir -p "$d/bin" "$d/.workflow"
cat > "$d/bin/gh" <<'EOF'
#!/usr/bin/env bash
if [[ "$1 $2" == "pr view" ]]; then
  if [[ -n "${GH_HAS_PR:-}" ]]; then echo "https://example.com/pr/1"; exit 0; fi
  exit 1
fi
if [[ "$1 $2" == "pr create" ]]; then echo "CREATE-CALLED"; exit 0; fi
exit 1
EOF
chmod +x "$d/bin/gh"
out=$(
  cd "$d" && PATH="$d/bin:$PATH" && GH_HAS_PR=1 && export GH_HAS_PR && source "$SCRIPT" \
    && WF=.workflow && OPEN_PR=1 && SPEC_DIR=specs && LOG=/dev/null && METRICS= \
    && log_section() { :; } && archive_snapshot() { :; } && notify() { :; } \
    && finish "task title"
)
assert_like "finish:existing PR is reported instead of re-created" "*PR already exists: https://example.com/pr/1*" "$out"
if [[ "$out" != *CREATE-CALLED* ]]; then
  ok "finish:gh pr create is skipped when the PR exists"
else
  bad "finish:gh pr create is skipped when the PR exists(got [$out])"
fi
out=$(
  cd "$d" && PATH="$d/bin:$PATH" && source "$SCRIPT" \
    && WF=.workflow && OPEN_PR=1 && SPEC_DIR=specs && LOG=/dev/null && METRICS= \
    && log_section() { :; } && archive_snapshot() { :; } && notify() { :; } \
    && finish "task title"
)
assert_like "finish:missing PR still runs gh pr create" "*CREATE-CALLED*" "$out"

# ---------- summary ----------
echo ""
echo "Passed $PASS, failed $FAIL"
(( FAIL == 0 ))
