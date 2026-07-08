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

# ---------- engine_model ----------
d=$(tmpdir)
out=$( cd "$d" && ENGINE_A=claude ENGINE_B=codex MODEL_A=haiku MODEL_B=mini \
      && source "$SCRIPT" && engine_model claude )
assert_eq "engine_model:A 槽取 MODEL_A" "haiku" "$out"
out=$( cd "$d" && ENGINE_A=claude ENGINE_B=codex MODEL_A=haiku MODEL_B=mini \
      && source "$SCRIPT" && engine_model codex )
assert_eq "engine_model:B 槽取 MODEL_B" "mini" "$out"
out=$( cd "$d" && ENGINE_A=claude ENGINE_B=codex \
      && source "$SCRIPT" && engine_model claude )
assert_eq "engine_model:未設定 → 空(用 CLI 預設)" "" "$out"

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
( cd "$d" && mkdir -p .workflow/runs/test/logs && source "$SCRIPT" \
    && WF_RUN=.workflow/runs/test && METRICS="$WF_RUN/metrics.csv" \
    && CUR_STAGE=stage1 && metric worker claude 1 12 0.05 && metric reviewer codex 2 30 "" )
assert_eq "metric:標頭 + 兩筆" 3 "$(wc -l < "$d/.workflow/runs/test/metrics.csv")"
assert_eq "metric:CSV 標頭正確" "run_id,stage,role,engine,round,duration_s,cost_usd" \
  "$(head -1 "$d/.workflow/runs/test/metrics.csv")"

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
  && ok "init_live_state:清除跨 run 污染檔" || bad "init_live_state:清除跨 run 污染檔"
[[ ! -e "$d/.workflow/review.md" && ! -e "$d/.workflow/verdict.json" && ! -e "$d/.workflow/last-engine-output.txt" ]] \
  && ok "init_live_state:清除自癒 live 檔" || bad "init_live_state:清除自癒 live 檔"

# ---------- bootstrap_agents_md ----------
d=$(tmpdir)
( cd "$d" && source "$SCRIPT" && bootstrap_agents_md ) >/dev/null
[[ -f "$d/AGENTS.md" && -f "$d/CLAUDE.md" ]] && ok "bootstrap:建立 AGENTS.md 與 CLAUDE.md" \
  || bad "bootstrap:建立 AGENTS.md 與 CLAUDE.md"
grep -qF '<!-- auto-workflow:begin -->' "$d/AGENTS.md" && ok "bootstrap:含 marker" || bad "bootstrap:含 marker"

d=$(tmpdir); echo 'my own rules' > "$d/AGENTS.md"
( cd "$d" && source "$SCRIPT" && bootstrap_agents_md ) >/dev/null 2>&1
assert_eq "bootstrap:不覆蓋既有 AGENTS.md" "my own rules" "$(cat "$d/AGENTS.md")"

d=$(tmpdir)
( cd "$d" && AGENTS_TEMPLATE=/nonexistent-template && source "$SCRIPT" && bootstrap_agents_md ) >/dev/null 2>&1
assert_rc "bootstrap:範本遺失 → 不中斷流程" 0 $?
[[ ! -f "$d/AGENTS.md" && ! -f "$d/CLAUDE.md" ]] && ok "bootstrap:範本遺失 → 不留空檔" \
  || bad "bootstrap:範本遺失 → 不留空檔"

AGENTS_TEMPLATE=/nonexistent-template "$SCRIPT" print-agents >/dev/null 2>&1
assert_nonzero "print-agents:範本遺失 → 失敗並提示" $?

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

# ---------- is_rate_limited ----------
d=$(tmpdir)
cat > "$d/hit.txt" <<'EOF'
{"type":"result","subtype":"success","is_error":true,"api_error_status":429,"result":"You've hit your session limit · resets 10:50am (Asia/Taipei)"}
EOF
( cd "$d" && source "$SCRIPT" && is_rate_limited hit.txt ) >/dev/null 2>&1
assert_rc "限額偵測:claude 429 JSON(run5 實樣)" 0 $?
printf 'HTTP 429 Too Many Requests\n' > "$d/tmr.txt"
( cd "$d" && source "$SCRIPT" && is_rate_limited tmr.txt ) >/dev/null 2>&1
assert_rc "限額偵測:Too Many Requests" 0 $?
cat > "$d/codex429.txt" <<'EOF'
ERROR: {"type":"error","status":429,"error":{"type":"rate_limit_exceeded","message":"Rate limit reached for gpt-5.5. Please try again in 90s."}}
EOF
( cd "$d" && source "$SCRIPT" && is_rate_limited codex429.txt ) >/dev/null 2>&1
assert_rc "限額偵測:codex/OpenAI 429 JSON" 0 $?
printf "You've reached your usage limit.\n" > "$d/reached.txt"
( cd "$d" && source "$SCRIPT" && is_rate_limited reached.txt ) >/dev/null 2>&1
assert_rc "限額偵測:reached your usage limit(OpenAI 措辭)" 0 $?
printf 'strutil_test.go:47:14: undefined: IsPalindrome\n' > "$d/plain.txt"
( cd "$d" && source "$SCRIPT" && is_rate_limited plain.txt ) >/dev/null 2>&1
assert_nonzero "限額偵測:一般錯誤不誤判" $?
( cd "$d" && source "$SCRIPT" && is_rate_limited nothere.txt ) >/dev/null 2>&1
assert_nonzero "限額偵測:檔案不存在 → 否" $?

# ---------- parse_reset_wait ----------
now=$(date +%s)
fut=$(LC_ALL=C date -d "@$(( now + 7200 ))" +%-I:%M%P)   # 強制英文 am/pm(claude 訊息即此格式)
printf 'You have hit your session limit · resets %s (Asia/Taipei)\n' "$fut" > "$d/fut.txt"
w=$( cd "$d" && source "$SCRIPT" && parse_reset_wait fut.txt "$now" )
if [[ -n "$w" && "$w" -ge 7000 && "$w" -le 7500 ]]; then ok "reset 解析:2 小時後 → 等待約 2h+緩衝"
else bad "reset 解析:2 小時後(得到 [$w])"; fi

past=$(LC_ALL=C date -d "@$(( now - 3600 ))" +%-I:%M%P)
printf 'resets %s\n' "$past" > "$d/past.txt"
w=$( cd "$d" && source "$SCRIPT" && parse_reset_wait past.txt "$now" )
if [[ -z "$w" ]]; then ok "reset 解析:已過時刻 → 視為明天,>6h 交給指數退避(空)"
else bad "reset 解析:已過時刻(得到 [$w],預期空)"; fi

printf 'no reset info here\n' > "$d/none.txt"
w=$( cd "$d" && source "$SCRIPT" && parse_reset_wait none.txt "$now" )
assert_eq "reset 解析:無資訊 → 空(走指數退避)" "" "$w"

w=$( cd "$d" && source "$SCRIPT" && parse_reset_wait codex429.txt "$now" )
assert_eq "reset 解析:try again in 90s → 120(含 30s 緩衝)" "120" "$w"
printf 'Rate limit reached. Please try again in 2 minutes.\n' > "$d/mins.txt"
w=$( cd "$d" && source "$SCRIPT" && parse_reset_wait mins.txt "$now" )
assert_eq "reset 解析:try again in 2 minutes → 150" "150" "$w"
printf 'usage cap, try again in 3 hours\n' > "$d/hrs.txt"
w=$( cd "$d" && source "$SCRIPT" && parse_reset_wait hrs.txt "$now" )
assert_eq "reset 解析:try again in 3 hours → 10830" "10830" "$w"
printf 'try again in 12 hours\n' > "$d/toolong.txt"
w=$( cd "$d" && source "$SCRIPT" && parse_reset_wait toolong.txt "$now" )
assert_eq "reset 解析:try again 超過 6 小時 → 空(異常防護)" "" "$w"

# ---------- engine_call(stub 引擎,RETRY_BASE_WAIT=1 快速跑) ----------
d=$(tmpdir)
run_engine_call() {  # $1: RETRY_ON_LIMIT  $2: stub 輸出內容("" = 成功)
  ( cd "$d" && mkdir -p .workflow/logs \
    && RETRY_ON_LIMIT="$1" RETRY_BASE_WAIT=1 RETRY_MAX=2 STUB="$2" \
    && source "$SCRIPT" && LOG=.workflow/logs/t.log && touch "$LOG" \
    && CALLS=0 \
    && fake_engine() {
         CALLS=$(( CALLS + 1 ))
         [[ -z "$STUB" ]] && return 0
         printf '%s\n' "$STUB" > "$ENGINE_OUT"
         return 1
       } \
    && rc=0 && engine_call fake_engine >/dev/null 2>&1 || rc=$? \
    ;  echo "rc=$rc calls=$CALLS" )
}
out=$(run_engine_call 1 'api_error_status":429 hit your session limit')
assert_eq "engine_call:預設重試到上限(1 次呼叫 + 2 次重試)" "rc=1 calls=3" "$out"
out=$(run_engine_call 0 'api_error_status":429 hit your session limit')
assert_eq "engine_call:RETRY_ON_LIMIT=0 → 不重試" "rc=1 calls=1" "$out"
out=$(run_engine_call 1 'ordinary build failure')
assert_eq "engine_call:非限額錯誤 → 不重試" "rc=1 calls=1" "$out"
out=$(run_engine_call 1 '')
assert_eq "engine_call:成功直接通過" "rc=0 calls=1" "$out"

# ---------- 總結 ----------
echo ""
echo "通過 $PASS,失敗 $FAIL"
(( FAIL == 0 ))
