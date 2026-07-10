# adversarial-ai-coding:可續跑的中斷復原(RESUME_RUN)

## Context

上一個 commit(1fbc3b8)讓腳本在「配額重置時刻太遠」時立即放棄,訊息卻只說 "rerun after the reset"——而實際上沒有
resume 機制:begin_stage 沒有跳過邏輯,RUN_ID 每次重新產生,SPEC_DIR=specs/$RUN_ID 因此換新目錄,setup_workspace
又切一條新分支。結果是每次重跑都從第一個 stage 全額重付 AI 費用。

材料其實都在:.workflow/runs/<id>/run-metadata.json 已記錄 engines/spec_dir/dual_spec,establish_run_archive 已有
<id>-2 防撞號,逐任務迴圈靠 plan 的 - [ ] checkbox 天然可續。缺的只有 stage 層級的完成台帳與狀態還原。

## 目標

中斷後能用 RESUME_RUN=<run-id> 從斷點續跑,已完成的 stage 直接跳過;所有中止路徑都印出可直接貼上的續跑指令。

## 設計

### 狀態檔($WF/state/$RUN_ID/,.workflow/ 已整個 gitignore)

- completed-stages — 每行一個已完成的 stage 名稱
- resume.env — printf '%s=%q\n' 安全引號寫出的可 source 檔,記錄 RESUMED_TASK_ARG / SPEC_DIR / ENGINE_A / ENGINE_B
    / MODEL_A / MODEL_B / CLAUDE_ARGS / CODEX_ARGS / AGY_ARGS / DUAL_SPEC / MAX_ROUNDS / AUTO_BRANCH / USE_WORKTREE /
    BRANCH / GATE_CMD / BUILD_GATE_CMD

### 設定優先序(必須在既有預設值區塊之前載入)

在 adversarial-ai-coding.sh 設定區頂端加入:RESUME_RUN 若有值 → 驗證 ${WF:-.workflow}/state/$RESUME_RUN/resume.env
存在並 source,然後把所有預設值改為 VAR="${VAR:-${RESUMED_VAR:-預設}}"。優先序:顯式環境變數 > 續跑狀態 > 內建預設。

- RUN_ID="${RESUME_RUN:-$(date +%Y%m%d-%H%M%S)}" — 續跑沿用同一 run id,SPEC_DIR
    與狀態目錄自然對上;establish_run_archive 會把本次歸檔放進 <id>-2,不覆蓋前次。
- RESUME_RUN=last 解析為 $WF/state/ 下最新的目錄(便利用法)。
- 續跑且未給 task 參數時,沿用 RESUMED_TASK_ARG 並印出。
- 找不到狀態目錄 → 列出可用的 run id 後 exit 1。

### Stage 台帳(新函式,置於 begin_stage 附近)

```
STAGE_LEDGER=""          # init_live_state 時設為 $RUN_STATE_DIR/completed-stages
stage_done <name>        # 台帳存在且含該行 → 0
begin_stage <name>       # 已完成 → 印 "== skip [name] (completed in run <id>)" 並 return 1;否則照舊
end_stage                # 把 CUR_STAGE 追加進台帳
```

STAGE_LEDGER 空字串時 stage_done 恆為假、end_stage 為 no-op,既有單元測試直接呼叫 begin_stage 的行為不變。

main() 與 run_dual_spec_spec_stage 的每個 stage 區塊改寫為:

```
if begin_stage "write-spec-a"; then
    ...原本的內容...
    end_stage
fi
```

涵蓋 stage:dual-spec 的 write-spec-a/write-spec-b/review-spec-a/review-spec-b/compare-specs-a/compare-specs-b/sele
ct-spec/finalize-spec,單規格的 write-spec,以及 commit-spec(包住 main() 現有那個裸露的
commit_work,避免續跑時為了「沒有變更」再叫一次
AI)、write-implementation-plan、write-acceptance-tests、write-code、final-review-and-fixes。

失敗模式是安全的:忘記 end_stage 只會讓該 stage 重跑,不會跳過未完成的工作。

### 跨 stage 狀態還原(resume 的真正難點)

- spec 角色:SPEC_OWNER_ENGINE/SPEC_REVIEWER_ENGINE 由 set_spec_roles_from_slot(:600)設定,dual-spec 模式下只在
    apply_dual_spec_decision(:673,即 finalize-spec)內呼叫。跳過該 stage 會讓角色停在 :102 的預設值(可能是錯的 slot)。
    → 新增 restore_spec_roles_if_needed():若 DUAL_SPEC=1,從 $SPEC_DIR/spec-decision.md 的 - decision: 行讀回決策,經
    dual_spec_owner_slot 換成 slot 後呼叫 set_spec_roles_from_slot;否則沿用 slot A。在 main() 的 commit-spec
    區塊之前無條件呼叫。
- DUAL_SPEC_DECISION:select-spec 被跳過時為空 → 同樣由上述函式從 spec-decision.md 讀回,供 finalize-spec 使用。
- 受保護測試:$WF/protected-tests.txt 與 protected-base.sha 位於 gitignore 的
    .workflow/,續跑時仍在原地,check_protected 照常運作。跳過 write-acceptance-tests 不需重建。
- write-code:整個 stage 只在全部任務完成後才 end_stage,中途中斷則重跑該 stage,而 plan_tasks 只取 - [ ]
    未打勾項目,已完成的任務自動跳過。

### 工作區還原(setup_workspace)

續跑時不再 git switch -c:

- 已在 RESUMED_BRANCH → 不動
- 分支存在 → git switch 過去
- 分支不存在 → 明確報錯

USE_WORKTREE=1 的續跑:.workflow/ 位於 worktree 內,因此必須在該 worktree
目錄中執行續跑(狀態檔就在那裡)。setup_workspace 在續跑時不再建立 worktree;若使用者在主 repo
執行、狀態目錄不存在,錯誤訊息要指出可能要 cd 進 worktree。README 記載此限制。

工作區若有前次中斷留下的未提交變更:不動它(commit 才是檢查點),但啟動時印出警告。

### 續跑提示(所有中止路徑)

新增 print_resume_hint(),輸出:

```
Resume this run after the quota returns:
RESUME_RUN=20260708-063650 ./adversarial-ai-coding.sh
```

呼叫點:engine_call 的配額過遠中止、engine_call 的 RETRY_MAX 用盡、review_loop 的 MAX_ROUNDS 中止、gate_loop
的關卡連續失敗中止,以及 main() 的 ERR trap(在既有的 log 路徑訊息之後)。

## 修改檔案

- C:\Project\adversarial-ai-coding\adversarial-ai-coding.sh — header 環境變數說明;設定區的 resume
    載入與優先序;RUN_STATE_DIR/STAGE_LEDGER 於 init_live_state;write_resume_state(在 setup_workspace +
    establish_run_archive 之後、任何 AI
    呼叫之前寫出);stage_done/begin_stage/end_stage;restore_spec_roles_if_needed;print_resume_hint;setup_workspace
    續跑分支;各 stage 區塊加守衛
- C:\Project\adversarial-ai-coding\tests\helpers.test.sh — 新增測試(見驗證)
- C:\Project\adversarial-ai-coding\README.md / README.zh-TW.md — 環境變數表加 RESUME_RUN;新增「Resuming an
    interrupted run / 中斷後續跑」小節,含 worktree 限制

## Commit 計畫

1. feat: resume interrupted runs from a stage ledger(狀態檔、台帳、stage 守衛、角色還原、分支還原 + 對應單元測試)
2. feat: print the resume command on every abort path(+ 測試)
3. docs: document resuming an interrupted run(兩份 README)

每個 commit 前跑 bash -n 與完整單元測試。

## 驗證

1. bash -n adversarial-ai-coding.sh + LF 檢查
2. bash tests/helpers.test.sh 全綠(現有 115 + 新增約 10),新增涵蓋:
    - stage_done/end_stage 往返;begin_stage 對已完成 stage 回傳 1 並印 skip;STAGE_LEDGER 為空時行為不變(回歸保護)
    - resume.env 往返:含空白與引號的值能被 %q 正確還原;顯式環境變數覆蓋續跑值
    - RESUME_RUN=last 解析到最新狀態目錄
    - restore_spec_roles_if_needed:spec-decision.md 指定 B 時 owner 變成 ENGINE_B;無 dual-spec 時維持 A
    - print_resume_hint 輸出含 RESUME_RUN=<id>
    - engine_call 配額過遠中止時,stderr 同時含中止原因與續跑指令
3. 煙霧測試(不呼叫 AI):臨時 git repo 中 RESUME_RUN=nonexistent ./adversarial-ai-coding.sh task.md → exit 1
    且列出可用 run id
4. 離線 stage 跳過模擬:臨時 repo 內手工建立 state/<id>/{completed-stages,resume.env},以 source 腳本後直接呼叫
    begin_stage 驗證跳過與 CUR_STAGE 行為
5. 完整 E2E(tests/e2e/run.sh)不在本次執行:Arthur 的 codex 週配額 2026-07-14 19:23 才重置,屆時可用真實中斷驗證續跑

## 不做

- RESUME_FROM=<stage>(指定從某 stage 重做):中斷的 stage 本來就未入台帳、會自動重跑,暫不需要
- 跨 worktree 移除後的續跑(狀態檔隨 worktree 消失,明確報錯即可)

