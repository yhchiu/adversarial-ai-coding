#!/usr/bin/env bash
#
# tests/e2e/run.sh — adversarial-ai-coding 手動 E2E 測試
#
# 會呼叫真實 AI 引擎、消耗 token 與訂閱配額(sonnet 約 $2~5 等值、20~40 分鐘),
# 只在改動 script 核心邏輯後手動執行,絕不掛進 CI 或單元測試入口。
#
# 流程:臨時目錄現生 fixture git repo → 親測基線關卡全綠 → 跑完整工作流
#       → 自動驗收清單 → 成敗都保留現場供檢視。
#
# 用法:
#   bash tests/e2e/run.sh
#
# 環境變數:
#   E2E_DIR         工作目錄(預設 mktemp;失敗與成功現場都保留在此)
#   E2E_SETUP_ONLY  1=只建 fixture repo 並驗證基線,不呼叫任何 AI(預設 0)
#   其餘 adversarial-ai-coding 變數皆可透傳;本執行器的 E2E 預設:
#     HUMAN_GATE=0  ENGINE_A=claude  MODEL_A=sonnet  CLAUDE_ARGS='--effort=low'
#     ENGINE_B=codex  MODEL_B=gpt-5.5  CODEX_ARGS='-c model_reasoning_effort=low'
set -Eeuo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
SCRIPT="$ROOT/adversarial-ai-coding.sh"
FIXTURE="$HERE/fixture"

# E2E 預設(源自五次真實 run 的教訓:worker 至少 sonnet 級、codex 降 effort 省錢)
export HUMAN_GATE="${HUMAN_GATE:-0}"
export ENGINE_A="${ENGINE_A:-claude}"
export MODEL_A="${MODEL_A:-sonnet}"
export CLAUDE_ARGS="${CLAUDE_ARGS:---effort=low}"
export ENGINE_B="${ENGINE_B:-codex}"
export MODEL_B="${MODEL_B:-gpt-5.5}"
export CODEX_ARGS="${CODEX_ARGS:--c model_reasoning_effort=low}"

command -v go >/dev/null 2>&1 || { echo "缺少 go 工具鏈(fixture 是 Go 專案)" >&2; exit 1; }
[[ -f "$SCRIPT" && -d "$FIXTURE" ]] || { echo "找不到 $SCRIPT 或 $FIXTURE" >&2; exit 1; }

if [[ -n "${E2E_DIR:-}" ]]; then
  BASE="$E2E_DIR"
  if command -v cygpath >/dev/null 2>&1; then
    BASE="$(cygpath -u "$BASE" 2>/dev/null || printf '%s' "$BASE")"
  fi
else
  BASE="$(mktemp -d -t wf-e2e-XXXXXX)"
fi
REPO="$BASE/repo"
RUN_LOG="$BASE/run.log"   # 放 repo 外,免得被工作流的 commit 掃進去

echo "== E2E 現場:$BASE"
mkdir -p "$REPO"
cp -r "$FIXTURE"/. "$REPO"/
cd "$REPO"
git init -q -b main
git config user.email e2e@local
git config user.name e2e
git add -A
git commit -qm "chore: baseline fixture for adversarial-ai-coding E2E"

echo "== 基線關卡(親測,不信 AI 回報)"
# 刻意拆成三個獨立敘述:&& 串列中非末位指令的失敗不會觸發 errexit(bash 陷阱)
go build ./...
go vet ./...
go test ./...
echo "基線全綠 ✔"

if [[ "${E2E_SETUP_ONLY:-0}" == "1" ]]; then
  echo "(E2E_SETUP_ONLY=1:僅建置與驗證基線,未呼叫 AI)"
  exit 0
fi

echo "== 執行工作流(A=$ENGINE_A/${MODEL_A:-預設}  B=$ENGINE_B/${MODEL_B:-預設})"
echo "   引擎參數:CLAUDE_ARGS='$CLAUDE_ARGS'  CODEX_ARGS='$CODEX_ARGS'"
rc=0
"$SCRIPT" task.md 2>&1 | tee "$RUN_LOG" || rc=$?
if (( rc != 0 )); then
  echo "!! 工作流退出碼 $rc;現場與紀錄保留在 $BASE 供人工診斷" >&2
  exit "$rc"
fi

# ---------- 自動驗收清單 ----------
echo
echo "== 驗收清單"
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "ok   - $1"; }
bad() { FAIL=$((FAIL+1)); echo "FAIL - $1"; }

grep -q '全部 stage 完成' "$RUN_LOG" && ok "六個 stage 全部完成" || bad "六個 stage 全部完成"
grep -q '品質關卡通過' "$RUN_LOG" && ok "確定性品質關卡至少通過一次" || bad "確定性品質關卡"

[[ "$(git branch --show-current)" == auto/* ]] && ok "在 auto/* 工作分支上" || bad "在 auto/* 工作分支上"

[[ -f AGENTS.md && -f CLAUDE.md ]] && ok "AGENTS.md / CLAUDE.md 已 bootstrap" || bad "AGENTS.md / CLAUDE.md 已 bootstrap"

spec_dir=$(ls -d specs/*/ 2>/dev/null | head -1 || true)
if [[ -n "$spec_dir" && -f "$spec_dir/spec.md" ]]; then
  grep -qi 'Assumptions and Open Questions' "$spec_dir/spec.md" \
    && ok "spec.md 含 Assumptions and Open Questions" || bad "spec.md 含 Assumptions 節"
  if [[ -f "$spec_dir/plan.md" ]]; then
    if grep -qE '^- \[ \] ' "$spec_dir/plan.md"; then bad "plan.md 任務全部打勾(尚有未完成項)"
    elif grep -qE '^- \[x\]' "$spec_dir/plan.md"; then ok "plan.md 任務全部打勾"
    else bad "plan.md 任務全部打勾(沒有 checkbox)"; fi
  else bad "plan.md 存在"; fi
else
  bad "specs/<run>/spec.md 存在"
fi

grep -q 'func IsPalindrome' strutil/*.go && ok "IsPalindrome 已實作" || bad "IsPalindrome 已實作"

if [[ -s .workflow/protected-tests.txt && -f .workflow/protected-base.sha ]]; then
  ok "受保護測試檔清單非空"
  # 重用 script 本身的純函式驗證測試未被改動
  # shellcheck disable=SC1090
  source "$SCRIPT"
  viol=$(protected_violations .workflow/protected-tests.txt "$(cat .workflow/protected-base.sha)")
  [[ -z "$viol" ]] && ok "受保護驗收測試未被改動" || bad "受保護驗收測試未被改動:$viol"
else
  bad "受保護測試檔清單非空"
fi

run_dir=""
if [[ -f .workflow/latest-run.txt ]]; then
  run_dir=$(cat .workflow/latest-run.txt)
  [[ -d "$run_dir" ]] && ok "latest-run.txt 指向存在的 run 目錄" || bad "latest-run.txt 指向存在的 run 目錄"
else
  bad "latest-run.txt 存在"
fi

if [[ -n "$run_dir" && -d "$run_dir" ]]; then
  ls "$run_dir"/*-task-source.md >/dev/null 2>&1 && ok "task-source 已保存" || bad "task-source 已保存"
  ls "$run_dir"/*-task.txt >/dev/null 2>&1 && ok "task resolved text 已保存" || bad "task resolved text 已保存"
  ls "$run_dir"/*-prompt.md >/dev/null 2>&1 && ok "AI prompt artifact 存在" || bad "AI prompt artifact 存在"
  ls "$run_dir"/*-output.txt >/dev/null 2>&1 && ok "AI output artifact 存在" || bad "AI output artifact 存在"
  ls "$run_dir"/*-attempt-*-rc*.raw >/dev/null 2>&1 && ok "engine attempt raw artifact 存在" || bad "engine attempt raw artifact 存在"
  ls "$run_dir"/*-git-status.txt >/dev/null 2>&1 && ok "git status 快照存在" || bad "git status 快照存在"
  ls "$run_dir"/*-git-diff.patch >/dev/null 2>&1 && ok "git diff 快照存在" || bad "git diff 快照存在"
  ls "$run_dir"/*.meta.json >/dev/null 2>&1 && ok "artifact metadata 存在" || bad "artifact metadata 存在"
  [[ -f "$run_dir/logs/001-run.log.meta.json" ]] && ok "run log metadata 存在" || bad "run log metadata 存在"
  jq -e '.run_id and .generator_role=="workflow"' "$run_dir/logs/001-run.log.meta.json" >/dev/null \
    && ok "run log metadata 含 run/generator" || bad "run log metadata 含 run/generator"
fi

n=$(git rev-list --count main..HEAD)
(( n >= 5 )) && ok "小批次 commit(main..HEAD = $n ≥ 5)" || bad "小批次 commit(main..HEAD = $n,預期 ≥ 5)"

echo "== 最終關卡(親測)"
if go build ./... && go vet ./... && go test ./...; then ok "最終 build/vet/test 全綠"
else bad "最終 build/vet/test 全綠"; fi

if [[ -n "$run_dir" && -f "$run_dir/metrics.csv" ]]; then
  ok "metrics.csv 存在"
  if python - "$run_dir/metrics.csv" <<'PY'
import csv, sys
with open(sys.argv[1], newline="", encoding="utf-8") as f:
    rows = list(csv.reader(f))
assert rows[0] == ["run_id","stage","role","engine","round","duration_s","cost_usd","model","model_args","generated_at"]
assert len(rows) > 1
assert all(len(r) == 10 for r in rows)
PY
  then ok "metrics.csv 新欄位可解析"
  else bad "metrics.csv 新欄位可解析"; fi
  echo
  echo "== 執行統計"
  # 資料列每欄都用引號跳脫;彙總前先剝外層引號,否則秒數/費用會被當非數值加成 0
  awk -F, 'NR>1 {
      for (i = 1; i <= NF; i++) gsub(/^"|"$/, "", $i)
      calls[$2]++; secs[$2] += $6; cost[$2] += $7
    }
    END { for (s in calls) printf "  %-14s %d 次呼叫,%d 秒,$%.4f\n", s, calls[s], secs[s], cost[s] }' \
    "$run_dir/metrics.csv"
else
  bad "metrics.csv 存在"
fi

echo
echo "驗收:通過 $PASS,失敗 $FAIL;現場保留在 $BASE(檢視完可整批刪除)"
(( FAIL == 0 ))
