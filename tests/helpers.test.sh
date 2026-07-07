#!/usr/bin/env bash
#
# tests/helpers.test.sh — auto-workflow.sh 純函式與前置檢查的單元測試
#
# 執行:bash tests/helpers.test.sh
# 不呼叫任何 AI 引擎;每個案例在獨立 subshell + 暫存目錄中執行。
set -u

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/auto-workflow.sh"
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

new_repo() {  # 建立可 commit 的暫存 git repo,輸出路徑
  local d; d=$(tmpdir)
  git -C "$d" init -q -b main
  git -C "$d" config user.email test@test
  git -C "$d" config user.name test
  ( cd "$d" && echo base > base.txt && git add -A && git commit -qm base )
  echo "$d"
}

ok()  { PASS=$((PASS+1)); echo "ok   - $1"; }
bad() { FAIL=$((FAIL+1)); echo "FAIL - $1"; }

assert_eq()      { if [[ "$2" == "$3" ]]; then ok "$1"; else bad "$1(預期 [$2],實際 [$3])"; fi; }
assert_rc()      { if [[ "$2" -eq "$3" ]]; then ok "$1"; else bad "$1(預期 rc=$2,實際 rc=$3)"; fi; }
assert_nonzero() { if [[ "$2" -ne 0 ]]; then ok "$1"; else bad "$1(預期非 0,實際 rc=0)"; fi; }
assert_like()    { if [[ "$3" == $2 ]]; then ok "$1"; else bad "$1(預期符合 [$2],實際 [$3])"; fi; }

# ---------- detect_gate / detect_build_gate ----------
d=$(tmpdir); touch "$d/go.mod"
out=$( cd "$d" && source "$SCRIPT" && detect_gate )
assert_eq "detect_gate:go 專案" "go build ./... && go vet ./... && go test ./..." "$out"
out=$( cd "$d" && source "$SCRIPT" && detect_build_gate )
assert_eq "detect_build_gate:go 專案" "go build ./..." "$out"

d=$(tmpdir); echo '{"scripts":{"test":"jest"}}' > "$d/package.json"
out=$( cd "$d" && source "$SCRIPT" && detect_gate )
assert_eq "detect_gate:npm(有 test script)" "npm test" "$out"

d=$(tmpdir); echo '{"scripts":{}}' > "$d/package.json"
out=$( cd "$d" && source "$SCRIPT" && detect_gate )
assert_eq "detect_gate:npm(無 test script)→ 空" "" "$out"

d=$(tmpdir); touch "$d/Cargo.toml"
out=$( cd "$d" && source "$SCRIPT" && detect_gate )
assert_eq "detect_gate:cargo 專案" "cargo test" "$out"

d=$(tmpdir)
out=$( cd "$d" && source "$SCRIPT" && detect_gate )
assert_eq "detect_gate:未知專案 → 空" "" "$out"

# ---------- plan_tasks ----------
d=$(tmpdir)
printf -- '# plan\n\n- [ ] task one\n- [x] finished task\n- [ ] task two\nplain text\n' > "$d/plan.md"
out=$( cd "$d" && source "$SCRIPT" && plan_tasks plan.md )
assert_eq "plan_tasks:只取未完成項" $'task one\ntask two' "$out"
out=$( cd "$d" && source "$SCRIPT" && plan_tasks missing.md )
assert_eq "plan_tasks:檔案不存在 → 空" "" "$out"

# ---------- verdict_approved ----------
d=$(tmpdir)
echo '{"approved":true,"blockers":[],"suggestions":[]}' > "$d/v.json"
( cd "$d" && source "$SCRIPT" && verdict_approved v.json ) >/dev/null 2>&1
assert_rc "verdict:approved=true → 通過" 0 $?
echo '{"approved":false,"blockers":["x"],"suggestions":[]}' > "$d/v.json"
( cd "$d" && source "$SCRIPT" && verdict_approved v.json ) >/dev/null 2>&1
assert_nonzero "verdict:approved=false → 不通過" $?
( cd "$d" && source "$SCRIPT" && verdict_approved nothere.json ) >/dev/null 2>&1
assert_nonzero "verdict:檔案不存在 → 不通過" $?
echo 'not json at all' > "$d/v.json"
( cd "$d" && source "$SCRIPT" && verdict_approved v.json ) >/dev/null 2>&1
assert_nonzero "verdict:JSON 壞掉 → 不通過" $?

# ---------- protected_violations ----------
d=$(new_repo)
( cd "$d" && echo 'func TestAcc' > acc_test.go && git add -A && git commit -qm tests )
base=$(git -C "$d" rev-parse HEAD)
echo 'acc_test.go' > "$d/protected.txt"

out=$( cd "$d" && source "$SCRIPT" && protected_violations protected.txt "$base" )
assert_eq "protected:未改動 → 空" "" "$out"

echo 'weakened' > "$d/acc_test.go"
out=$( cd "$d" && source "$SCRIPT" && protected_violations protected.txt "$base" )
assert_eq "protected:未提交的竄改被抓到" "acc_test.go" "$out"

( cd "$d" && git add -A && git commit -qm hack )
out=$( cd "$d" && source "$SCRIPT" && protected_violations protected.txt "$base" )
assert_eq "protected:已提交的竄改也被抓到" "acc_test.go" "$out"

( cd "$d" && echo unrelated > other.txt )
: > "$d/protected.txt"
out=$( cd "$d" && source "$SCRIPT" && protected_violations protected.txt "$base" )
assert_eq "protected:空清單 → 保護停用、輸出空" "" "$out"

# ---------- metric ----------
d=$(tmpdir)
( cd "$d" && mkdir -p .workflow && source "$SCRIPT" \
    && CUR_STAGE=stage1 && metric worker claude 1 12 0.05 && metric reviewer codex 2 30 "" )
assert_eq "metric:標頭 + 兩筆" 3 "$(wc -l < "$d/.workflow/metrics.csv")"
assert_eq "metric:CSV 標頭正確" "run_id,stage,role,engine,round,duration_s,cost_usd" \
  "$(head -1 "$d/.workflow/metrics.csv")"

# ---------- bootstrap_agents_md ----------
d=$(tmpdir)
( cd "$d" && source "$SCRIPT" && bootstrap_agents_md ) >/dev/null
[[ -f "$d/AGENTS.md" && -f "$d/CLAUDE.md" ]] && ok "bootstrap:建立 AGENTS.md 與 CLAUDE.md" \
  || bad "bootstrap:建立 AGENTS.md 與 CLAUDE.md"
grep -qF '<!-- auto-workflow:begin -->' "$d/AGENTS.md" && ok "bootstrap:含 marker" || bad "bootstrap:含 marker"

d=$(tmpdir); echo 'my own rules' > "$d/AGENTS.md"
( cd "$d" && source "$SCRIPT" && bootstrap_agents_md ) >/dev/null 2>&1
assert_eq "bootstrap:不覆蓋既有 AGENTS.md" "my own rules" "$(cat "$d/AGENTS.md")"

# ---------- setup_workspace(worktree) ----------
d=$(new_repo)
out=$( cd "$d" && source "$SCRIPT" && USE_WORKTREE=1 setup_workspace >/dev/null && git branch --show-current )
assert_like "worktree:建立並切到 auto/* branch" "auto/*" "$out"
git -C "$d" worktree prune 2>/dev/null

# ---------- human_gate_spec ----------
d=$(tmpdir)
( cd "$d" && mkdir -p .workflow && HUMAN_GATE=0 && source "$SCRIPT" && human_gate_spec ) >/dev/null 2>&1
assert_rc "human gate:HUMAN_GATE=0 → 直接通過" 0 $?
if ! { : </dev/tty; } 2>/dev/null; then
  ( cd "$d" && mkdir -p .workflow && source "$SCRIPT" && human_gate_spec ) >/dev/null 2>&1
  assert_rc "human gate:無 tty → 中止" 1 $?
else
  echo "skip - human gate 無 tty 中止(目前環境有 tty,無法模擬)"
fi

# ---------- 前置檢查(直接執行 script,不會呼叫 AI) ----------
"$SCRIPT" >/dev/null 2>&1
assert_rc "前置:無參數 → 印用法並 exit 1" 1 $?

d=$(tmpdir)
( cd "$d" && "$SCRIPT" "任務" ) >/dev/null 2>&1
assert_rc "前置:非 git repo → 擋下" 1 $?

d=$(new_repo)
( cd "$d" && ENGINE_A=codex ENGINE_B=codex "$SCRIPT" "任務" ) >/dev/null 2>&1
assert_rc "前置:codex+codex → 擋下" 1 $?
assert_eq "前置:被擋下時無任何副作用(不建 branch)" "main" "$(git -C "$d" branch --show-current)"

"$SCRIPT" print-agents 2>/dev/null | grep -qF '<!-- auto-workflow:begin -->'
assert_rc "print-agents:輸出規範範本" 0 $?

# ---------- 總結 ----------
echo ""
echo "通過 $PASS,失敗 $FAIL"
(( FAIL == 0 ))
