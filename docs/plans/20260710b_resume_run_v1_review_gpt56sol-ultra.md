# RESUME_RUN v1 計畫 Review

- Review 對象：`docs/plans/20260710_resume_run_v1_opus48high.md`
- Review 日期：2026-07-10
- 判定：**Request changes；目前不應直接實作或 rollout**

## 結論

計畫方向正確，但目前的設計最多只能提供「stage 級 best-effort、at-least-once 重跑」，還不能達成「任何中斷後都能安全 resume run」。主要問題不是少幾個 guard，而是 checkpoint 的權威性、交易邊界與可驗證性尚未定義：有些斷點會跳過尚未 gate／commit 的工作，有些會刪除後續 stage 需要的狀態，也有些會重複 AI、push 或建立 PR 等外部副作用。

在開始實作前，至少要先修正下列 Critical findings，並把目標契約改成：

> 自動續跑只能從「已驗證的 durable checkpoint」開始；若中斷發生在外部副作用已可能完成、但本地 receipt 尚未持久化的窗口，run 必須進入 `needs_reconciliation`，不得盲目跳過或重做。

建議的保證範圍如下：

| 中斷類型 | 可以誠實承諾的行為 |
|---|---|
| 可捕捉的 command failure、網路錯誤、rate limit、`SIGINT`、`SIGTERM` | 從最後一個已驗證 checkpoint 自動續跑，並保留原 exit reason |
| `SIGKILL`、process crash | 已完成的 atomic checkpoint 可恢復；in-flight transition 可能需要重跑或 reconciliation |
| kernel crash、斷電 | 只能保證檔案系統已 durable flush 的 checkpoint；若不實作並驗證 fsync semantics，就應明列為 best effort |
| AI/provider 已收到請求，但本地未收到結果 | provider 有 request ID、查詢或 idempotency key 時可 reconcile；否則無法保證 exactly once |
| state/worktree 被刪除、磁碟損毀、Git object/index 損壞 | 無法透明恢復，必須明列為不支援或人工修復 |

## Blocking findings

### C1. `resume.env` 與 stage ledger 不是可信任的權威狀態

計畫將狀態放在 agent 可修改的 `.workflow/state`，再直接 `source resume.env`（原計畫 `:18-28`）。`printf %q` 只保證原 writer 產生的字串可 round-trip，不能防止檔案之後被 agent、使用者、symlink 或損壞的寫入竄改。現有 worker 本來就有修改 workspace 的能力，例如 Codex 使用 `workspace-write`，agy 使用 `--dangerously-skip-permissions`（`adversarial-ai-coding.sh:775-800`）；同一位置的 `completed-stages` 也可被偽造，直接跳過安全關卡。若被竄改的 state 還原 `GATE_CMD`，最終會進入 `bash -c`（`adversarial-ai-coding.sh:1058-1065`）。

此外，計畫沒有定義 `RESUME_RUN` 的 grammar、canonical-path containment 或 symlink policy；`RESUME_RUN=../../...` 可能逃出 state root。

必須修訂為：

- 權威 state 使用有 `schema_version` 的 data-only 格式，例如 JSON；只允許 `jq -e` 加 allowlist／type validation，禁止 `source`、`eval`。
- state 放在 agent 無法寫入的位置，或使用 agent 無法偽造的完整性驗證；否則 JSON 仍可被改成「stage 已完成」。
- 嚴格驗證 run ID、real path、state-root containment，拒絕 symlink traversal、unknown key、partial file 與 future schema。
- 以 restrictive permissions 建立 state；不得盲目持久化可能含 token 的 CLI args。
- `GATE_CMD`、`NOTIFY_CMD` 等可執行值只能來自可信 snapshot 或 resume 時重新明確提供，不能信任 agent-writable state。

### C2. `plan.md` checkbox 會跳過未驗證工作，也可能重做整份 plan

計畫把 checkbox 當作天然 task checkpoint（原計畫 `:9-10,73-74`），但目前 worker 被要求在返回前自行把 task 改成 `[x]`（`resources/prompts/implement-plan-task.md:1-2`），實際順序則是 `work -> gate -> commit`（`adversarial-ai-coding.sh:1507-1516`）。因此：

- worker 打勾後、gate 或 commit 前中斷：`plan_tasks` 只讀 `[ ]`（`adversarial-ai-coding.sh:559-562`），resume 會跳過尚未驗證、尚未 checkpoint 的 task。
- 最後一個 task 已 commit、但 `write-code` 尚未 `end_stage` 時中斷：resume 看到零個 `[ ]`，現行邏輯會落入 whole-plan fallback（`adversarial-ai-coding.sh:1502-1506`），反而重做整份 plan。

必須修訂為：

- checkbox 只能是 UI，不得是權威 checkpoint；最好由 script 在 checkpoint 成功後更新，worker 不得直接管理。
- 每個 task 有穩定 ID，記錄 `pending -> running -> gate_passed -> committed -> complete`、`input_head` 與 `commit_oid`。
- 只有 gate 通過且 commit reachable、內容符合 postcondition 後才能完成 task。
- 明確區分「plan 沒有 task list」與「task list 全部完成」。
- commit 後、task marker 前中斷時，以 task receipt／commit 對帳，不得再次呼叫 AI。

### C3. resume 會刪除仍被後續 stage 依賴的 durable state

原計畫宣稱 `protected-tests.txt` 與 `protected-base.sha` 仍在原地，跳過 acceptance-test stage 不必重建（`:71-72`）。但 `init_live_state` 目前會刪除：

- `protected-tests.txt`
- `protected-base.sha`
- `suggestions.md`
- `spec-merge-request.md`

證據在 `adversarial-ai-coding.sh:441-451`，而 `main` 每次都無條件呼叫它（`:1428-1430`）；現有單測甚至明確要求這些檔案被刪除（`tests/helpers.test.sh:495-508`）。後果包括：

- protected state 消失時，`check_protected` 直接成功返回（`adversarial-ai-coding.sh:910-915`），驗收測試保護被靜默關閉。
- merge 型 dual-spec 在 finalize 時仍需要 `spec-merge-request.md`（`:686-694`），resume 後會缺檔。
- final self-review 使用累積的 `suggestions.md`（`:1006-1015,1527-1529`），resume 後會漏掉先前 review 建議。

必須把初始化拆成 fresh run 與 resume attempt。所有由已完成 stage 產生、後續仍會使用的資料都要成為 run-scoped durable state；resume 只能清除可安全重建的 `review.md`、`verdict.json`、`last-engine-output.txt` 等暫態檔。跳過 stage 前要驗證其必要檔案與 hash，缺失時 fail closed 或從已驗證 archive 還原。

### C4. acceptance-test stage 存在會永久關閉 protected-test enforcement 的 crash window

`test_base` 只存在於當次 shell 的 local variable（`adversarial-ai-coding.sh:1477-1479`）。流程先 commit acceptance tests（`:1483-1488`），之後才依 `test_base..HEAD` 產生 protected list（`:1489-1492`）。若在 commit 後、protected files 寫入前中斷，stage 尚未完成，resume 會重跑並把目前 acceptance commit 當成新的 `test_base`；若第二次沒有產生新 diff，protected list 就變空，而現行程式只警告後繼續（`:1493-1497`）。

必須修訂為：

- 在任何 AI 副作用前先 durable 記錄原始 `test_base/input_head`，所有重試沿用同一值。
- checkpoint 同時保存 acceptance commit、protected path list 及內容 hash。
- 驗證 base commit 存在、屬於同一 repo、且是目前 checkpoint 的 ancestor。
- acceptance stage 產生空 protected list 應 fail closed，除非有顯式、可審核的「此 spec 不需要驗收測試」結果。
- 加入「acceptance commit 後、protected list 前 `SIGKILL`」的跨 process 故障注入測試。

### C5. dual-spec decision 的還原點晚於它第一次被使用

原計畫把 `restore_spec_roles_if_needed` 放在 `main()` 的 `commit-spec` 前（`:65-70`），但 `finalize-spec` 在 `run_dual_spec_spec_stage` 返回前就會執行（`adversarial-ai-coding.sh:1274-1331`）。`decision` 又是 local，只在 `select-spec` 實際執行時才被賦值（`:1275,1325-1330`）。

因此當 `select-spec` 已完成、`finalize-spec` 尚未完成時，resume 會 skip selection，接著以空的 local `decision` 呼叫 finalize；等到 `commit-spec` 前再還原已經太晚。

必須在 stage dispatch 前，或最晚在進入 `finalize-spec` 前，讀取並嚴格驗證 decision receipt，同時還原：

- `DUAL_SPEC_DECISION`
- local `decision`
- owner／reviewer slot 與 engine roles
- merge 模式所需、具 hash 的 `spec-merge-request.md`

整合測試至少要涵蓋 `select-spec` 完成、`finalize-spec` 未完成時的 `adopt-b` 與 `merge-b`。

### C6. run identity 可被新 task、環境變數或不同 Git state 悄悄替換

原計畫允許「顯式環境變數 > resume state」（`:25-34`），但 `SPEC_DIR`、`DUAL_SPEC`、branch/worktree 與 task 都會決定 stage graph 或產物。若在舊 ledger 上改用新的 `SPEC_DIR`、把 dual-spec 改成 single-spec，或傳入另一個 task，已完成 stage 仍會被跳過，形成混合 run。

只保存 `RESUMED_TASK_ARG` 也不夠。task argument 若是檔案，`main` 每次都重新讀取目前內容；檔案被修改、刪除或路徑在 worktree 中不同時，同一 run 的需求就會改變（`adversarial-ai-coding.sh:1395-1413`）。現有 `archive_task` 已會保存 resolved text（`:311-331`），resume 應使用 immutable snapshot，而不是重讀原始檔。

state 還缺少 repo identity、checkpoint HEAD 與 artifact hashes。原計畫的 workspace restore 只驗證 branch 名稱是否存在（`:76-88`）；branch 被 reset、rebase，或在錯誤 repo 中存在同名 branch 時，ledger 仍可能跳過已不存在的工作。

必須定義 immutable run identity，至少包含：

- resolved task bytes／hash 與 source metadata
- repository identity、Git common dir、absolute worktree path
- initial HEAD、branch 與每個 checkpoint 的 commit OID
- `SPEC_DIR`、`DUAL_SPEC`、stage-graph/workflow version
- script 與 prompt-bundle compatibility version

resume 時 immutable field 不得覆寫，只能驗證相等。位置參數若存在，必須與保存的 resolved task hash 相符；不明 dirty changes、branch divergence 或 artifact mismatch 必須在任何 AI／Git mutation 前 fail closed 或進入 reconciliation。

## High-severity findings

### H1. `completed-stages` 不是 transaction，也與「commit 才是 checkpoint」矛盾

計畫只把 stage 名稱 append 到文字檔（`:36-45`），沒有 schema、stage sequence、input/output HEAD、artifact hash、atomic replace 或 durability 規格，卻宣稱「忘記 `end_stage` 只會安全重跑」（`:61`）。實際上：

- commit 後、ledger 前中斷會重做已完成的 AI／commit 工作。
- marker 存在但 branch 被 reset、artifact 遺失或 state 截斷時，會錯誤 skip。
- dual-spec 子 stages 會在 spec commit 前被標完成，而原計畫又說 commit 才是 checkpoint（`:56-59,88`）。

建議改成可驗證的 transition：

```text
pending -> running(input_head) -> validated(output hashes) -> committed(commit_oid) -> complete
                                                     \-> needs_reconciliation
```

每次 state 更新要採同檔案系統的 temp file、flush、atomic replace；若產品承諾 power-loss recovery，還要定義並驗證 file 與 parent-directory fsync。stage 只有在 Git checkpoint 與 postconditions 都可驗證後才算 complete。plain text event log 可以保留作 audit，但不能單獨作為權威 state。

### H2. 沒有 run/workspace lock，fresh run 與 resume 都有競態

`RUN_ID` 只有秒級 timestamp（`adversarial-ai-coding.sh:90`），新 state 以它作唯一目錄；archive 的 `<id>-2` 選擇則是 check-then-create（`:367-379`）。兩個同秒 fresh runs，或同一 run 的兩個 resume process，可能同時：

- 讀到 stage 未完成並重複呼叫 AI
- append 同一 ledger
- 修改、commit 同一 worktree
- 共用 `$WF/review.md`、`verdict.json`、`ENGINE_OUT` 等 live files
- 競爭 archive sequence 與 branch

必須以 atomic exclusive create 配置唯一 run/attempt，加入 per-run lock 與 workspace-wide lock，並定義 PID／host／process-start token 與 stale-lock takeover。跨 Windows Git Bash 若不能依賴 `flock`，可用 atomic `mkdir` protocol。第二個 resume 必須在任何副作用前明確 fail busy。

### H3. `RESUME_RUN=last` 與 `finish` 沒有 lifecycle／idempotency

state 只有設定與 completed stages，沒有 `running/interrupted/completed` 狀態。`last` 只是挑最新目錄（原計畫 `:32`），因此可能選到已完成的 run。`finish` 又位於所有 guarded stages 之外（`adversarial-ai-coding.sh:1527-1535`），且可執行 push、`gh pr create` 與 notification（`:1335-1375`）。

若 push 或 PR 已成功、receipt 寫入前中斷，resume 會重做外部副作用；`gh pr create` 可能因 PR 已存在而讓 run 永遠無法完成。

manifest 應加入 `status`、attempt lineage 與 `last_failure`；`last` 只選 schema 有效、未完成且未被鎖住的 run。finish 至少拆成 `push_verified`、`pr_verified(number/url)`、`notification_attempted`、`run_complete`，先查詢外部狀態再執行，最後才 atomic 標記 completed。無法去重的通知要明列為 at-least-once。

### H4. 「所有中止路徑都有提示」目前無法成立

原計畫只列 rate limit、review/gate exhaustion 與 `ERR` trap（`:90-100`）。但現有多處是 explicit `exit 1`，例如 protected-test violation、human gate、dual-spec merge abort（`adversarial-ai-coding.sh:917-920,1148-1156,1258-1265`）；單純 `exit` 不會觸發 Bash `ERR` trap，計畫也沒有 `INT/TERM/HUP` handler。另一方面 `run_review` 會捕捉 `engine_call` failure 並把它轉成一般 review failure（`:1035-1040`），rate limit 或網路錯誤不一定能直接冒泡成 resumable abort，甚至可能觸發額外 repair call。

必須：

- state ready 後安裝單一、去重的 nonzero `EXIT` handler，另處理 `INT/TERM/HUP` 並保留原 exit code。
- 使用 typed abort reason／return code，使 quota、network 與真正 review rejection 不會混在一起。
- 只有 state 已成功建立、run 未 complete 時才印一次 hint。
- worktree hint 要包含 shell-quoted absolute worktree 與實際 script path，才能真的直接貼上。
- 文案改成「所有可捕捉、且 state 已建立的中止」；`SIGKILL`、強制終止與斷電不可能印提示。

### H5. persisted config 不完整，優先序與寫入時機也未定義清楚

原計畫的 state 欄位名稱使用 `SPEC_DIR/ENGINE_A/...`，讀取公式卻期待 `RESUMED_VAR`（`:20-28`）；若 source 原名稱會覆蓋顯式環境，若改用 namespaced 變數又必須接入現有 `AGENT_A`／legacy `ENGINE_A` alias 規則（`adversarial-ai-coding.sh:46-72`）。`${VAR:-...}` 也無法區分 unset 與 explicit empty。

清單還漏掉 custom-agent args、`HUMAN_GATE`、`TOOLS`、retry policy、`OPEN_PR` 等行為設定（`adversarial-ai-coding.sh:63-88`）。此外計畫要求在 archive 建立後、任何 AI 前寫 state（原計畫 `:104-107`），但 resolved `GATE_CMD`／`BUILD_GATE_CMD` 目前到 `main` 後段才 auto-detect（`adversarial-ai-coding.sh:1441-1442`），容易保存空值並在 resume 時重新偵測成不同命令。

計畫必須先列出完整的 immutable／persisted／resume-overridable 設定表，以「是否 set」而非 `:-` 實作 precedence；所有 auto-detection 完成後才凍結 trusted config snapshot。安全敏感或可能含秘密、刻意不保存的設定，也要明確寫出 resume 行為。

### H6. 驗證沒有跑過真正的 interrupt -> 第二個 process resume 流程

原計畫主要新增 helper tests，並以 source 後直接呼叫 `begin_stage` 模擬跳過（`:121-134`）；真正 AI E2E 被延後（`:135`）。現有 CI 也只有 syntax、helper tests 與 setup-only E2E（`.github/workflows/ci.yml:30-42`），而 setup-only 在呼叫 workflow 前就退出（`tests/e2e/run.sh:67-69`）。因此漏包 stage guard、`end_stage` 放錯位置、state 被初始化刪除、dual restore 順序錯誤等問題都可能全綠。

本功能不需要真實 AI 才能有完整整合測試。應新增 `tests/resume.test.sh` 或等價 suite，以兩個不同名稱的 fake agents、fake reviewer verdict、可控 gate 與 failpoint 跑完整 `main`：

- 比較 uninterrupted baseline 與 interrupt/resume 的 final tree、logical checkpoints、protected state。
- 已完成 stage/task 的 fake-agent call count 必須是 0；未完成者必須重跑或進入 reconciliation。
- 覆蓋 single／dual spec、`select-spec -> finalize-spec`、acceptance commit window、write-code task window、finalization、AUTO_BRANCH、USE_WORKTREE 與多次 resume。
- 在副作用前後、gate 前後、commit 前後、checkpoint 前後注入 `SIGKILL`。
- 測 corrupted／truncated／future-schema state、path traversal、symlink、missing artifact、HEAD reset、wrong branch、dirty worktree 與 concurrent resume。
- 逐一觸發 worker failure、quota、network、review/gate exhaustion、human abort、`INT/TERM`，驗證 exit code、abort reason 與 hint 恰好一次。

真實 AI E2E 可以保留為手動 rollout gate，但不能取代上述無配額、可在 CI 重複執行的 state-machine 測試，也不應把個人配額重置日期寫成唯一驗證時點。

## 其他需要補入計畫的項目

### Schema、相容性與升降版

v1 應明確宣告只支援由新版本建立有效 state 的 runs；既有 archive 沒有可靠 ledger，不能根據 artifact 猜測 completed stages。state 必須保存 schema、workflow/stage-graph version、script/prompt compatibility fingerprint；未知版本只能經明確 migration 或拒絕 resume。`RESUME_RUN=last` 要忽略 partial、corrupt、completed 與 incompatible state。

### 跨 attempt budget 與 observability

rate retry、gate count、review round 與 worker session 目前都只在記憶體中，resume 後會從頭計數（`adversarial-ai-coding.sh:844-878,1058-1076,1096-1105`）。反覆 resume 會繞過 `RETRY_MAX`／`MAX_ROUNDS` 並增加成本。計畫需決定 budget 是 per attempt 還是 per run；若是上限，必須 durable 累積。

每個 archive attempt 應記錄 `attempt_id`、`resumed_from`、starting／ending HEAD、started／ended time、exit reason、interrupted transition，以及 structured `stage_started/skipped/completed` events。finish 的 metrics 應能聚合所有 attempts，並以 fake-agent counters 驗證真正避免了多少重複呼叫。

### Worktree bootstrap 與 dirty state

目前 `setup_workspace` 先建立 branch/worktree 並 `cd`，計畫要到之後才寫 state（原計畫 `:104-107`；程式 `:1378-1390`）。若在兩者之間中斷，已產生 Git 副作用卻沒有 resumable state。若要涵蓋這個窗口，必須在 mutation 前於 Git common-dir 或外部 state root 寫 `PREPARING` intent，之後再 idempotently reconcile。

「保留 dirty changes、只印警告」也不足。`ensure_committed` 會 `git add -A` 提交所有變更（`adversarial-ai-coding.sh:1125-1131`）；resume 前必須確認 dirty fingerprint 與 in-flight transition 相符，否則人工或其他 process 的變更可能被一起提交。

## 建議的新實作順序與 commit 切分

原計畫第一個 commit 同時包含 state、ledger、stage guards、dual roles、branch restore 與測試，風險過大。建議改成下列可獨立驗證的 tasks；每一項在相關測試與完整 suite 通過後才 commit：

1. `feat(resume): add trusted versioned run state and locking`
   - data-only manifest、run ID/path validation、atomic state update、fresh/resume init、run/workspace locks。
   - 同 commit 加 parser、tamper、corruption、collision 與 concurrency tests。
2. `feat(resume): add validated stage and task checkpoints`
   - stage/task state machine、Git/artifact postconditions、checkbox reconciliation、fault-injection hooks。
   - 同 commit 加 commit/marker 前後的跨 process resume tests。
3. `feat(resume): preserve cross-stage state and restore dual spec runs`
   - protected state、suggestions、merge request、acceptance base、decision/roles 還原。
   - 同 commit 加 single/dual 與 acceptance crash-window integration tests。
4. `feat(resume): restore workspaces and finalize runs idempotently`
   - repo/worktree/branch/HEAD validation、dirty reconciliation、attempt lifecycle、`last`、push/PR receipts。
   - 同 commit 加 branch/worktree、completed-run 與 finish tests。
5. `feat(resume): report resumable aborts consistently`
   - typed aborts、`EXIT`／signal handlers、shell-quoted hints、cross-attempt budget／metadata。
   - 同 commit 加完整 abort matrix tests。
6. `docs: document interruption recovery guarantees`
   - 更新兩份 README，明列自動恢復、reconciliation 與不可恢復邊界。

每個 commit 都應執行：

```bash
bash -n adversarial-ai-coding.sh tests/helpers.test.sh tests/resume.test.sh tests/e2e/run.sh
bash tests/helpers.test.sh
bash tests/resume.test.sh
E2E_SETUP_ONLY=1 bash tests/e2e/run.sh
```

Windows Git Bash 是文件宣告的使用情境，但目前 CI 只有 Ubuntu（`.github/workflows/ci.yml:13`）。至少要再驗證 Windows Git Bash 的 state round-trip、atomic replace／locking、空白與 Unicode 路徑、branch/worktree resume 與 LF。

## 建議的 acceptance criteria

實作計畫應把以下結果列成可自動驗證的 release blockers：

1. 對每個 failpoint，resume 只能重跑未完成 transition、skip 已驗證 checkpoint，或在任何 AI／Git mutation 前進入 reconciliation。
2. interrupt/resume 與 uninterrupted baseline 產生等價 final tree、相同 logical task/stage completion，且 protected-test enforcement 全程存在。
3. 已完成 stage/task 在 resume attempt 的 fake-agent call count 為 0。
4. 任意 state 內容都不能透過 parser 執行 shell；path traversal、symlink、tamper 與 corrupt state 一律 fail closed。
5. 同一 run 的第二個 resume process 在任何副作用前 fail busy；兩個 fresh runs 不共用 state、archive 或 branch。
6. task、repo、worktree、branch、checkpoint HEAD、stage graph 或必要 artifact 不相符時，不得沿用 ledger。
7. `last` 不選 completed、locked、corrupt 或 incompatible runs；已存在的 push／PR 不會重複建立。
8. 所有可捕捉且 state-ready 的 abort 只印一次可執行 hint，並保留正確 exit code；不可捕捉中斷的限制寫入文件。
9. fresh run 在未設定 `RESUME_RUN` 時維持既有行為，完整 helper、offline resume integration 與 setup-only E2E 全綠。

## 計畫中值得保留的部分

- 沿用同一 `RUN_ID`，並讓每個 attempt archive 使用 `<id>-2/-3`，符合現有 `establish_run_archive` 的方向。
- 用 `if begin_stage; then ...; end_stage; fi` 包住線性 stage，是契合現有 main flow 的控制結構；`STAGE_LEDGER=""` 保持 source-based helper tests 相容也合理。
- 以 `spec-decision.md` 作為 dual-spec owner 的 durable receipt 是對的，只需把讀取時機、完整性與 merge request 一起補齊。
- resume 不重新 `git switch -c`、不直接 reset 中斷留下的 worktree，是正確方向；補上 identity/checkpoint/dirty validation 後可保留。
- 語法檢查、完整單測、雙語 README 與分 commit 的意識都正確，但 commit 1 需要再拆小，且必須增加不呼叫 AI 的完整 resume integration suite。
- v1 暫不提供任意 `RESUME_FROM=<stage>` 是合理範圍控制；但偵測到 checkpoint 損壞時仍要有 `needs_reconciliation` 或受控 invalidate 操作，不能直接相信 ledger。

總結而言，這不是在原計畫上補幾個 tests 就能安全落地；必須先把「權威 state、checkpoint transaction、run identity、外部副作用 reconciliation」四個核心契約寫清楚，再依新契約實作 stage guards。
