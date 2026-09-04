# Artifact 契約 — A/B/I 之間如何交接

[English](artifact-contract.md) | 繁體中文

本文件把隱式的 agent 間協定顯性化。在 adversarial-ai-coding 中,工作者 slot `A`、審查者 slot `B` 與選用的實作 slot `I` 從不直接對話;它們之間的每個邊界都穿過一個由 workflow orchestrator 控管的檔案。這些檔案就是協定。本頁是它們的規範參考:每個 artifact 必須包含什麼、誰有權寫、workflow 如何驗證,以及內容錯誤時會發生什麼。

讀者對象:修改流程的維護者、撰寫 custom agent wrapper 的作者,以及任何要消費 run artifact 的工具開發者。Agent 本身則是透過 `AGENTS.md`(`resources/AGENTS.template.md`)與各 stage prompt 收到契約;本文件解釋這些規則為何長成這個形狀。

相關閱讀:[how-it-works.zh-TW.md](how-it-works.zh-TW.md) 瞭解逐 stage 流程、[import-format.md](import-format.md) 瞭解外部 spec/plan 匯入契約、[docs/adr/0001-single-aac-root-for-run-artifacts.md](adr/0001-single-aac-root-for-run-artifacts.md) 瞭解目錄布局的理由。

## 協定規則

五條規則貫穿所有跨 slot 邊界:

1. **一切由 orchestrator 中介。** Agent 只透過 `aac/` 底下的檔案交換資訊,沒有共用的聊天上下文:session 在交接邊界被刻意丟棄,每次呼叫指向一份完整、自足的 prompt 檔。
2. **結構化輸出 fail closed。** 必要的結構化 artifact(`verdict.json`、`phases.json`、`ledger.json`)缺失、不可讀或格式錯誤時,workflow 一律視為失敗或拒絕繼續。AI 自述的說法永遠不被採信,只看它寫進磁碟的內容。唯一刻意的例外是 `phased-suggestion.json`:它 fail open,因為它絕不能擋住 run。
3. **發現事項分級。** Blocker 讓審查迴圈重跑;suggestion 累積之後一次處理。這讓 reviewer 不會為了雞毛蒜皮擋關,也不會為了客套放水。
4. **控制真相在 workflow state,不在散文檔。** `plan.md` 是給人看的視圖;權威任務佇列是 `state/<RUN_ID>/tasks-remaining`。Run 開始後,持久化的 JSON state 才決定控制流。
5. **一切都帶溯源封存。** 每份 prompt、回覆、verdict、diff 快照與原始 CLI transcript 都落在 `aac/.run/archive/<RUN_ID>/`,並附 `.meta.json` sidecar 標明產生者、slot、model、stage 與 round。

## 交接單元:封存的 prompt 檔

每次 agent 呼叫都是同一個形狀,內建與 custom 指令皆然:

```text
$AGENT $AGENT_ARGS "Read the full workflow prompt from this repository \
file and follow it exactly: aac/.run/archive/<RUN_ID>/NNN-*-prompt.md"
```

- Prompt 檔由 `resources/prompts/*.md` 模板渲染(`{{PLACEHOLDER}}` 替換),並在呼叫前封存(`src/adversarial_ai_coding/prompts.py:64`)。它自足完整:角色、範圍、輸出路徑與限制都在裡面,不依賴任何保留的聊天歷史。
- 封存命名:執行方 worker 用 `{seq:03d}-worker-{stage}-r{round}-prompt.md`,reviewer 用 `{seq:03d}-reviewer-{stage}-r{round}-prompt.md`,伴隨同名 `-output.txt`、`-attempt-N-rc*.raw`、`-attempt-N-rc*.cli.raw`(`src/adversarial_ai_coding/workflow.py:368`、`src/adversarial_ai_coding/review.py:177`)。`{seq}` 是每 run 原子遞增的三位數計數。
- Custom agent 的全部義務是:讀那個檔案、照做、執行失敗時以非零退出。以下各節說明檔案會「要求什麼」。

## Agent 面向的 Artifact 契約

### C1 · `aac/.run/verdict.json` — 審查結果

寫入者:reviewer,必須用內建檔案編輯工具(絕不用 shell 轉向)。

Schema —— 恰好一行 JSON:

```json
{"approved": true|false, "blockers": ["must-fix issue"], "suggestions": ["non-blocking"]}
```

契約:

- Workflow 在每次 reviewer 呼叫前先把檔案預填成失敗 verdict(`src/adversarial_ai_coding/review.py:195`)。Reviewer 若沒寫這個檔案,預設就是輸——fail closed。
- 核准需要 `approved === true` **且** blockers 為空;JSON 解析失敗、型別錯誤或自相矛盾的 verdict 都算*未核准*(`src/adversarial_ai_coding/review.py:106`)。
- Blocker 是正確性 bug、違反 spec、弱化測試、安全問題。只有 blocker 會讓 review→fix 迴圈重跑;迴圈何時結束由 workflow 決定(`MAX_ROUNDS` 封頂,用盡即中止 run)。
- Suggestion 以 `## {stage}(round {n})` 分組附加到 `aac/.run/suggestions.md`,在最終 stage 一起處理(`src/adversarial_ai_coding/review.py:130`);它們永不擋核准。
- 每輪 verdict 封存為 `NNN-verdict-{stage}-r{n}.json`;live 檔永遠只描述當前這一輪。

### C2 · `aac/.run/review.md` — 發現與回覆

寫入者:reviewer(發現)與 worker(回覆)交替。

契約:

- Reviewer 逐條列出發現,指名檔案並附上 worker 可重現的證據:執行的指令與關鍵輸出行,或被違反的 spec/plan 原文。新一輪**覆寫**舊內容,但保留 worker 尚未回覆的項目。
- Worker 在每條發現底下回覆:修好後寫 `Fixed: <summary>`,不同意寫 `Disagree: <reason>`。默默無視任何一條都違反契約。
- Reviewer 下一輪先逐條驗證回覆,再找新問題。
- Reviewer 只能改 `review.md` 與 `verdict.json` 兩個檔(`resources/prompts/review.md`);可以跑測試,不可以改 branch 上的其他東西。
- 每輪封存為 `NNN-review-{stage}-r{n}.md`(reviewer)與 `NNN-review-{stage}-r{n}-worker.md`(worker 回覆輪)。
- 不可讀的 `review.md`(sandbox 可能用壞掉的 ACL 寫出)會被丟棄,該輪視為失敗(`src/adversarial_ai_coding/review.py:38`)。

### C3 · `aac/.run/phased-suggestion.json` — 附帶判斷

寫入者:spec reviewer,僅在機制啟用時(未明確設定 `PHASES`、未匯入 plan)。

Schema —— 一行:`{"phased": true|false, "reason": "..."}`
(預設 `{"phased": false, "reason": ""}`)。

契約:

- 它是獨立於 verdict 的判斷:絕不放進 `verdict.json`,絕不影響 `approved` 或 blockers。
- 它 fail open:任何缺失、格式錯誤或型別錯誤都降級為「無建議」並警告,絕不變成失敗 stage(`src/adversarial_ai_coding/phased_suggestion.py:45`)。

### C4 · `aac/docs/<RUN_ID>/spec.md` — 規格

寫入者:owner slot(或經 `IMPORT_SPEC` 匯入)。

必要章節:功能描述、可測試的驗收條件、邊界情況、範圍外項目,以及**誠實列出所有未問人類就做的假設**的「Assumptions and Open Questions」章節(headless agent 不能問人,禁止默默腦補)。

雙 spec 模式(`DUAL_SPEC=1`)追加:

- `spec-a.md` / `spec-b.md` 是獨立候選。被指定寫某份候選的 slot 不得讀另一份候選、其 review 或比較檔——隔離本身就是契約的一部分。
- 交叉 review 落在 `spec-{a,b}.review-by-{b,a}.md`,verdict 在 `spec-{a,b}.verdict-by-{b,a}.json`(schema 同 C1,但僅供參考:候選 review 不擋關,只提供給人類決策)。
- 各 slot 各寫一份比較表 `spec-comparison-{a,b}.md`;workflow 寫 `spec-comparison.md` 索引;人類選 `a`/`b`/`ma`/`mb`,`spec-decision.md` 記錄選定的 owner。
- 合併(`ma`/`mb`)時,人類編輯 `aac/.run/spec-merge-request.md`;owner 必須把指定項目採納進 `spec.md`,reviewer 驗證項目完好且未被扭曲。

### C5 · `aac/docs/<RUN_ID>/plan.md` — 任務佇列視圖

寫入者:owner slot(或經 `IMPORT_PLAN` 匯入)。

預設模式契約:

- 任務清單是 `- [ ]` checkbox 行;每一項恰好對應一個 commit,且必須能獨立實作與驗證。
- 實作者把完成項翻成 `- [x]`(commit 之後 workflow 也會權威性地翻一次,`src/adversarial_ai_coding/runstate.py:583`)。

分階段模式(`PHASES=1`)追加結構需求,在任何實作開始前由 workflow 確定性驗證(`src/adversarial_ai_coding/phases.py:33`):

- 章節用 `## Phase N: <title>` 標題;編號自 Phase 1 起連續。
- 每個 phase 要有一行非空的 `Acceptance:`(穩定邊界上的可觀察行為)與至少一項非空的 `- [ ] ` 任務。
- Phase 外的任務、編號跳號或重複、空標題都會被退回 owner 修正。
- 標題以 `(regression-guard)` 結尾會反轉 red check 的預期:那些測試必須立刻通過。
- 驗證通過後,解析出的 phase 圖持久化到 `state/<RUN_ID>/phases.json`(schema 1)並成為控制真相;`plan.md` 此後降級為 UI。

### C6 · 驗收測試與受保護控制

寫入者:**reviewer**(此階段角色互換;owner 只負責審測試)。這個分離是刻意的——沒有任何 slot 對自己出的考題應考。

- 測試檔放在專案慣常的測試位置;紅燈是預期的(TDD red phase)。寫測試者不得寫產品碼,也不得改 `aac/docs/` 底下的檔案。
- Stage 結束後,workflow 把測試路徑記進 `aac/.run/protected-tests.txt`,基準 commit 記進 `protected-base.sha`。此後實作者不得編輯、刪除或 skip 這些檔案(`resources/prompts/implement-plan-task.md`);workflow 在每次 worker 動作前後以位元組比對硬查,違規即強制還原。
- Agent 認為測試本身錯了也絕不動手改:把異議記在 spec 的 Assumptions and Open Questions。只有人類能改保護清單或推進 `protected-base.sha`。

## Workflow 持有的 State(Agent 唯讀)

位於 `aac/.run/state/<RUN_ID>/`。只由 workflow 寫入;只當資料解析、絕不執行;未知 schema 一律拒絕。

| 檔案 | Schema | 內容 |
|---|---|---|
| `settings.json` | `{"schema": 2, ...}` | 解析後的設定快照;未知 key 會讓 resume 被拒(`src/adversarial_ai_coding/runstate.py:146`) |
| `ledger.json` | `{"schema": 1, "stages": [...]}` | Append-only 的已完成 stage 清單;resume 跳過這些 |
| `task.txt` | 文字 | 不可變的請求快照 |
| `tasks-remaining` / `tasks-remaining-phase-NN` | 文字,一行一任務 | 權威任務佇列;空檔案代表所有任務都已 commit |
| `phases.json` | `{"schema": 1, "phases": [{number, title, regression_guard, tasks}]}` | 持久化的 phase 圖(`PHASES=1` 的控制真相) |
| `last-head`、`acceptance-test-base`、`run-base` | 文字(SHA) | 跨 stage 的 git 基準,resume 時還原 |
| `imported-{kind}-archive-path` | 文字(路徑) | 匯入的 spec/plan 被封存的位置 |
| `lock/` | 目錄 mutex | 每 run id 同時只允許一個嘗試 |
| `completed` | 標記 | Run 完成時出現;已完成的 run 拒絕 resume |

`aac/.run/` 直下的暫存檔只描述當前這一輪(`review.md`、`verdict.json`、`last-agent-output.txt`、`pr-body.md` 每次啟動都清掉;耐久的控制檔在 resume 時保留,`src/adversarial_ai_coding/runstate.py:595`)。

### 進版控的 manifest:`aac/docs/<RUN_ID>/run.json`

有一個 workflow 持有的檔案不放在 `aac/.run/`,而放在進版控的那一半。上表所有東西對 git 都是隱形的,所以「這次 run 在做什麼」clone 之後什麼都不剩,之後回頭看的人手上只有一個時間戳目錄名。這個檔把那些事實帶過去。

| 欄位 | 內容 |
|---|---|
| `schema` | `1`;未知值只會讓列表降級,絕不會擋掉 run |
| `run_id` | 這個目錄所屬的 run |
| `request` | 解析後的請求文字,與 `task.txt` 同一份快照 |
| `started_at` | 含時區偏移的本地時間,`%Y-%m-%dT%H:%M:%S%z` |
| `branch` | Run 啟動時所在的分支 |
| `agent_a` / `agent_b` | 這次 run 啟動時的 slot 指令 |
| `dual_spec` / `phases` / `imported_spec` | 決定這次 run 形狀的模式旗標 |

契約:

- **沒有任何 agent 會寫它。** 沒有 prompt 要求產出它,所以沒有任何審查或修訂輪次能弄丟它,reviewer 也不會把它當成 spec 內容。Agent 對它的待遇與上表的 state 檔完全相同:唯讀。
- **只寫一次。** Resume 沿用同一個 run id,並保留既有的 manifest 不動,所以 `started_at` 永遠是最初的啟動時間。
- **絕不是控制流。** 沒有任何東西解析它來做決定,裡面也不存 run 狀態:`adversarial-ai-coding list-runs` 改從 `state/<RUN_ID>/completed` 推導,因為啟動時寫下的狀態,在 run 被中斷的那一刻就是錯的。
- 在第一個 stage 之前就寫入,好讓 `commit-spec` stage 以 `git add -A` 語意把它帶進分支(`src/adversarial_ai_coding/runindex.py:58`)。
- 它跟 spec 放在一起,所以 `SPEC_DIR` 也會把它一起搬走。`list-runs` 仍然找得到:它聯集了預設位置、每份設定快照裡記錄的 `spec_dir`,以及 git 追蹤到的 manifest(`src/adversarial_ai_coding/runindex.py:200`)。

## 溯源:`.meta.json` Sidecar 與 `metrics.csv`

每個封存 artifact 都有原子寫入的 `<name>.meta.json` 兄弟檔(`src/adversarial_ai_coding/archive.py:114`):

| 欄位 | 意義 |
|---|---|
| `generated_at` | 含時區偏移的本地時間,`%Y-%m-%dT%H:%M:%S%z` |
| `generator_role` | 產出者的功能角色:`worker`、`reviewer` 或 `workflow` |
| `agent` | 解析後的 agent 指令/runtime(如 `claude`、custom wrapper 名稱、`workflow`) |
| `agent_slot` | Slot 身分:`A`、`B`、`I` 或 `workflow` |
| `model` / `model_args` | 解析後的 model 覆寫與 model 參數(未設定則空) |
| `stage` / `round` | Stage slug 與審查輪次(`round` 序列化為字串) |
| `run_id` / `artifact` | Run 身分與 artifact 路徑 |

注意這一對欄位編碼的區別:驗收測試 stage 中,B slot *撰寫*測試,所以它的 artifact 帶 `generator_role: "worker"` 而 `agent_slot: "B"`——role 是「做了什麼」,slot 是「它是誰」。

`metrics.csv` 欄位:`run_id, stage, role, agent, round, duration_s,
cost_usd, model, model_args, generated_at, agent_slot`。

## Stage 名稱登錄表

記錄在 `ledger.json`、也用於封存 slug 的名稱:

- 預設流程:`write-spec`、`commit-spec`、`write-implementation-plan`、`write-acceptance-tests`、`write-code`、`final-review-and-fixes`。
- 雙 spec 取代 spec stage:`write-spec-a`、`write-spec-b`、`review-spec-a`、`review-spec-b`、`compare-specs-a`、`compare-specs-b`、`select-spec`、`finalize-spec`。
- 分階段模式以逐 phase 成對取代 stage 4–5:`phase-{NN}-write-tests`、`phase-{NN}-implement`(兩位數補零的 phase 編號)。

## 不變式總表

| # | 不變式 | 強制點 |
|---|---|---|
| 1 | `approved: true` ⟺ 零 blocker;缺失/無效 verdict ⇒ 未核准 | `src/adversarial_ai_coding/review.py:106` |
| 2 | 每次 reviewer 呼叫前 verdict 先預填為失敗 | `src/adversarial_ai_coding/review.py:195` |
| 3 | Suggestion 永不擋關;累積到 `suggestions.md` | `src/adversarial_ai_coding/review.py:130` |
| 4 | Reviewer 只能改 `review.md` + `verdict.json` | `resources/prompts/review.md` |
| 5 | Worker 逐條回覆每項發現(`Fixed:` / `Disagree:`) | `resources/AGENTS.template.md` |
| 6 | 沒有 slot 同時寫 spec 與驗收測試 | 流程設計(`docs/how-it-works.zh-TW.md`) |
| 7 | 實作者碰不到受保護測試 | `protected-tests.txt` + 位元組複查 |
| 8 | 任務佇列真相在 workflow state,`plan.md` 只是 UI | `src/adversarial_ai_coding/runstate.py:541` |
| 9 | 分階段 plan 結構在實作前先驗證 | `src/adversarial_ai_coding/phases.py:33` |
| 10 | 持久化 state 拒絕未知 schema 與衝突的 resume | `src/adversarial_ai_coding/runstate.py:157,203` |
| 11 | Run manifest 由 workflow 持有且只寫一次;沒有 agent 產出它 | `src/adversarial_ai_coding/runindex.py:58` |
