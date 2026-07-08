# Workflow Run Archive Plan（修訂版）

## Context（為什麼要改）

現行 `auto-workflow.sh` 把跨 run 的中間狀態都寫在 `.workflow/` 的固定檔名(`review.md`、`verdict.json`、`last-engine-output.txt`、`pr-body.md`…),每輪/每次呼叫直接覆蓋,所以:

- 一個 run 內的歷輪 review/verdict/engine output 會被後續覆蓋,事後無法完整重建過程。
- `$task` 原文(literal 或檔案)從未落檔。
- 沒有「生成者(engine/model/參數)+ 時間」的標註。
- log 缺乏分段,可讀性差。

目標:每個 run 保留完整、可依檔名排序、且標註生成者與時間的中間資料,且不同 run 互不干擾。**前一版計畫的方向對,但把幾個承重的既有機制當成不存在**(見下方 findings),照那版做會弄壞流程。本版修正之。

## 設計原則(關鍵決策)

**live 工作檔維持現有固定路徑不動;archive 以「快照(cp)+ 遞增序號檔名 + sidecar metadata」另存。**

理由:`review.md` / `verdict.json` / `protected-*` / `ENGINE_OUT` 都被 script 邏輯與**送給 AI 的 prompt 文字**直接引用。若把它們改成 per-round 專屬檔名(前一版的 `014-review-spec-r2.md` 寫法),就得把所有 prompt 與下游 reader 重新接線,且會切斷「工作者與審查者在同一 stage 內共用同一份 review.md 對話」的交接。快照法達到「全保存 + 可排序 + 帶生成資訊」三個目標,又不動互審迴圈與這些 live 檔的路徑。

> 兩個刻意的例外(源自第二輪 review,送出**內容**不變、只調整程式結構):(a) reviewer prompt 的組裝集中到 `compose_review_prompt`,好讓 archive 的 prompt 等於實際送出(review #6);(b) 每個 run 起始由 `init_live_state` 重置會跨 run 污染的 live 檔,以真正達成 run 隔離(review #1)。

## Findings → 對應處置(全數納入)

| # | Finding | 本版處置 |
|---|---------|---------|
| 1 | `RUN_ID` 已存在且驅動 SPEC_DIR / branch / worktree / log 名 | **沿用現有 `RUN_ID`(秒級、不加序號)**;archive 目錄為 `$RUNS_DIR/$RUN_ID`。branch/spec 命名完全不動。 |
| 2 | `review.md`/`verdict.json` 是跨輪演進對話 | 維持固定工作路徑;每次 writer 寫完就快照一份帶序號的進 archive(見「快照時機」)。 |
| 3 | worktree 下序號 probe 的落地時機死結 | 因為不再有序號 probe,且 archive 目錄在 `setup_workspace` 之後才 `mkdir`(與現有 `mkdir -p "$LOGS" "$SPEC_DIR"` 同處),自然落在正確的樹。死結消失。 |
| 4 | `ENGINE_OUT` 是限額偵測的共享讀寫契約;claude 只在失敗時落檔 | `ENGINE_OUT` 保持單一固定檔(限額 reader 不動);另令 `w_claude`/`r_claude` **成功時也把原始 JSON 寫入 `ENGINE_OUT`**,再由呼叫端快照成 `-output-` 產物,claude 成功輸出才有 archive。 |
| 5 | 三位數序號過度工程 | **移除序號**。撞號(同 `$RUN_ID` 目錄已存在)才 fallback 加 `-2`、`-3`… 後綴,不中止。 |
| 6 | metrics.csv 跨 run 語義 + E2E 依賴 | metrics 移入 `$WF_RUN/metrics.csv`(每 run 自足);**尾端追加 `model,model_args,generated_at` 三欄**(見 review #4),前 7 欄位置不變,`finish()` 與 E2E awk 只改路徑。 |
| 7 | prompt 從未落檔 | 每次 AI 呼叫前把 prompt 快照成 archive 產物(新增擷取)。 |
| 8 | 測試可行性(現況為純函式測試) | 抽出 `art_path` / `write_meta` / `resolve_model_args` / `archive_task` 等可注入(WF_RUN、seq、時鐘)的純函式再測。 |
| 9 | archive 無限成長 | **本次不做保留上限**(使用者指定);於 plan 的 Non-goals 標明為後續工作。 |
| 10 | log 交錯(async process substitution) | 加分隔線與區段標頭;文件註明 async tee 仍可能微幅交錯,不承諾完美圍欄。 |
| 11 | `.workflow/.gitignore = *` 使 archive 不進版控 | 維持;`RUNS_DIR` 預設在 `.workflow/` 下故自動被忽略。若使用者把 `RUNS_DIR` 指到 `.workflow` 之外,文件提醒需自理 gitignore。 |
| 12 | `latest-run.txt` 便利入口 | 保留於 `$WF/latest-run.txt`,在 id 定案(cd 後)寫入,指向本次 run 目錄。 |

## Key Changes

### 可設定的 archive 目錄
- 新增環境變數 `RUNS_DIR`,預設 `.workflow/runs`。
- `WF_RUN="$RUNS_DIR/$RUN_ID"`;若已存在則加數字後綴(撞號 fallback)。
- 相對路徑,在 `setup_workspace`(可能 `cd` 進 worktree)之後才實際 `mkdir`,確保落在正確的樹。
- log 與 metrics 移入本 run 目錄:`LOG="$WF_RUN/logs/001-run.log"`、`METRICS="$WF_RUN/metrics.csv"`。

### 遞增序號檔名(排序即生成序)
- 全域單調計數器 `ART_SEQ`;`art_path <name>` 回傳 `$WF_RUN/$(printf '%03d' ++ART_SEQ)-<name>`。
- 所有 archive 產物一律經 `art_path`,故三位數前綴反映整個 run 的真實生成順序。
- **消歧(見 review #3)**:此處的 **artifact 序號(`ART_SEQ`)** 與 Finding #5 所「移除」的序號是**兩回事**——移除的是 **RUN_ID 目錄的 `-NNN`**(改用秒級 RUN_ID),保留的是 **archive 檔名前綴**。實作時勿把兩者混為一談、勿誤刪 artifact 排序。

### 每份資料 + log 都帶生成資訊
- `write_meta <artifact> <role> <engine> <model> <model_args> <stage> <round>` 寫 sidecar `<artifact>.meta.json`,欄位:`generated_at`(本機 local ISO 時間)、`generator_role`、`engine`、`model`(經 `engine_model` 解析後的實際值)、`model_args`、`stage`、`round`、`run_id`。
- run 起始寫 `NNN-run-metadata.json`:完整設定 + A/B 解析後 engine/model/args。
- `model_args` 由新純函式 `resolve_model_args <engine>` 依引擎回傳對應 `CLAUDE_ARGS`/`CODEX_ARGS`/`AGY_ARGS`。
- **metrics.csv 也帶生成資訊(見 review #4)**:在現有欄位**尾端追加** `model,model_args,generated_at` 三欄。**務必附加在最後**,保持 `$1–$7`(run_id…cost_usd)欄位位置不變,`finish()` 與 `tests/e2e/run.sh` 既有的 `awk`(用 `$2/$6/$7`)才不會錯位。
- log:新增 `log_section`,於**所有階段**前輸出「空行 + 分隔線 + 標頭(時間 + role/engine/model/args/stage/round)」。套用點補齊為:**startup(設定摘要)**、每次 AI call、review、gate、retry、**protected check**、**human gate**、commit、finish(見 review #4,原本漏了 startup/protected/human-gate)。

### 保存 `$task`(精確擷取來源,見 review #2)
- **在 `main()` 把 task 檔讀成字串前(`:586` 的 `task="$(cat "$task")"` 覆寫前)先擷取來源中繼資料**,否則原始路徑會遺失:
  - `TASK_ARG`:原始命令列參數。
  - `TASK_SOURCE_KIND`:`file` 或 `literal`。
  - `TASK_SOURCE_PATH`:檔案來源時,**在 `setup_workspace` 的 `cd`(worktree)之前**用 `realpath` 轉絕對路徑,避免 cd 後相對路徑失效。
  - `TASK_RESOLVED_TEXT`:實際使用的內容(檔案內容或 literal 字串)。
- `WF_RUN` 建好後(`setup_workspace` 之後)由 `archive_task` 寫出:
  - `NNN-task-source.md`:`kind` + 絕對路徑(或標注 literal)+ 原文內容。
  - `NNN-task.txt`:resolved task 字串。

### 每次 AI 呼叫的 prompt / output 落檔
- **archive 的 prompt 必須等於實際送出的 prompt(見 review #6)**:reviewer prompt 目前由 `r_codex`/`r_agy` 各自在 `review_prompt` 後附 `verdict_file_instr`、`r_claude` 不附(改用 `--json-schema`)。因此:
  - 新增 `compose_review_prompt <engine> <scope>`,回傳「該引擎**實際會送出**的完整字串」;`r_*` 改為**接收已組好的 prompt**、不再自行組裝。
  - `run_review()` 先呼叫 `compose_review_prompt` 組出 exact 字串 → `art_path` 落 `NNN-<role>-prompt-<stage>-r<round>.md`(+meta)→ 再把同一份字串傳給 `r_<engine>`。
  - worker prompt 本就是 `work()` 的 `$2`(已是 exact),直接落檔。
- 輸出擷取:把 `engine_call` 的 stdout 同時 tee 進 LOG 與 `NNN-<role>-output-<stage>-r<round>.txt`(所有引擎一致)。
- **每個 retry attempt 都各自落檔(見 review #5)**:`engine_call` 每次呼叫引擎後,立即把當次 `ENGINE_OUT` 快照成 `NNN-<role>-attempt-<n>-rc<rc>.raw`,不讓限額/失敗 attempt 的原始輸出被下一次覆寫;最終成功輸出另存 `.raw`。`w_claude`/`r_claude` 成功時 `$out` 也寫入 `ENGINE_OUT` 以供擷取。

### 快照時機(review.md / verdict.json,確保不漏 state)
- `run_review` 末(審查者寫完):快照 `review.md` → `NNN-review-<stage>-r<round>.md`;`verdict.json` → `NNN-verdict-<stage>-r<round>.json`。
- 修正輪 `work()` 若動到 `review.md`(工作者補「已修正」回覆):在下一輪審查者覆寫前快照 `NNN-review-<stage>-r<round>-worker.md`。
- `protected-tests.txt` / `protected-base.sha` 設定當下各快照一份。
- `finish()`:快照 `pr-body.md`、`suggestions.md`。

### Run 起始初始化 live 狀態(避免跨 run 干擾,見 review #1)
「live 檔不動」會**繼承既有的跨 run 污染 latent bug**,而本計畫目標正是 run 隔離,故必補:
- 新增 `init_live_state()`,在 `WF_RUN` 建好後、任何 stage 之前執行,清掉會跨 run 污染邏輯的 live 檔:
  - `suggestions.md`:以 `>>` 追加(`:394`)、收尾 `work()` 會讀(`:678`)→ 不清會把**上一個 run 的建議**帶進本次。
  - `protected-tests.txt` / `protected-base.sha`:`check_protected` 在**第一個 stage(訂規格)**就會被呼叫(`work()` `:291`),此時本 run 尚未寫這兩檔;若殘留上一個 run 的非空清單 + 舊 base SHA,會拿舊 SHA `git diff` 誤報違規 → 逼工作者 `git checkout 舊SHA -- 檔案`,**可能破壞性地回退無關檔案**、兩次未果即 `exit 1`。這是最危險的殘留。
- `review.md` / `verdict.json` / `last-engine-output.txt` 因每次使用前必被覆寫(verdict 每輪預寫哨兵 `:409`、review 由審查者覆寫、ENGINE_OUT 先寫後讀),屬**自癒**;可一併清理求乾淨,但非必要。

### 每輪專案檔案中間狀態(git 快照,見 review #7)
- 新增 `archive_git_state()`,在**每次 `work()` 之後、commit 之前**存:
  - `NNN-git-status.txt`:`git status --porcelain`。
  - `NNN-git-diff.patch`:工作者本輪的變更(用 `git add -N` 納入未追蹤後 `git diff`,或 `git diff HEAD`;必要時 `--binary`)。
- 序號與其他 artifact 共用 `ART_SEQ`,故 git 快照在時序上與同輪的 prompt/output/review 正確交錯。可完整重建每輪 commit 前的檔案狀態。

## 逐檔改動

- **`auto-workflow.sh`**
  - 設定區(:62–68 附近):新增 `RUNS_DIR`;`WF_RUN` 與撞號 fallback;`LOG`/`METRICS` 指向 `$WF_RUN`;保留 `ENGINE_OUT` 為固定檔。
  - 新增純函式:`art_path`、`write_meta`、`resolve_model_args`、`archive_task`、`archive_snapshot`(cp + write_meta)、`log_section`、**`compose_review_prompt`(組出各引擎 exact reviewer prompt)**。
  - 新增流程函式:**`init_live_state`(清跨 run 污染的 live 檔)**、**`archive_git_state`(每輪 git status/diff 快照)**。
  - `metric()`(:86):CSV 標頭與每列**尾端追加** `model,model_args,generated_at`(保持前 7 欄位置不變)。
  - `verdict_file_instr` / `review_prompt`:改由 `compose_review_prompt` 統一組裝;`r_claude`/`r_codex`/`r_agy`(:344/:365/:375)**改為接收已組好的完整 prompt**,不再各自 `review_prompt`/`verdict_file_instr`。
  - `w_claude`/`r_claude`(:207, :344):成功時亦把 `$out` 寫入 `ENGINE_OUT`。
  - `engine_call`(:257):每個 attempt 呼叫後即刻 `archive_snapshot` 當次 `ENGINE_OUT`(檔名帶 `attempt-<n>-rc<rc>`);retry 前後加 `log_section`。
  - `work`(:282):呼叫前落 worker prompt;呼叫後 `archive_git_state` + output/最終 raw 快照。
  - `run_review`(:403):`compose_review_prompt` → 落 prompt → 傳入 `r_*`;審查後快照 `review.md`/`verdict.json`。
  - `begin_stage`/`gate_loop`/`commit_work`/`finish`/**startup 設定摘要**/**`check_protected`**/**`human_gate_spec`** 加 `log_section`。
  - `main()`(:576):**先擷取 task 來源中繼資料(在 `:586` 覆寫前、`setup_workspace` cd 前解析絕對路徑)**;`setup_workspace` 後 `mkdir -p "$WF_RUN/logs"` → **`init_live_state`** → 寫 `NNN-run-metadata.json`、`archive_task`、`latest-run.txt`。
  - 標頭環境變數文件補上 `RUNS_DIR`。
  - **live 工作檔路徑一律不動**(`$WF/review.md`、`$WF/verdict.json`、`$WF/suggestions.md`、`$WF/protected-*`、`$WF/pr-body.md`);`verdict_approved`/`collect_suggestions`/`show_blockers`/`check_protected` 讀寫路徑不改(僅由 `init_live_state` 在 run 起始重置)。

- **`tests/helpers.test.sh`**
  - `metric` 測試(:121):改指向 `$WF_RUN` 下的 metrics 路徑;**斷言新標頭尾端含 `model,model_args,generated_at` 且前 7 欄位置不變**。
  - 新增:`art_path` 前綴遞增且排序 = 生成序;`write_meta` 產出含 engine/model/model_args/role/stage/round/run_id/generated_at(時鐘注入);`resolve_model_args` 各引擎正確;`archive_task` 對 literal 與檔案兩種來源都保存(含絕對路徑);快照連兩輪不覆蓋(兩檔並存)。
  - `compose_review_prompt`:**codex/agy 含 `verdict_file_instr`、claude 不含**(對應 review #6)。
  - `init_live_state`:預置殘留的 `suggestions.md`/`protected-tests.txt`/`protected-base.sha`,執行後這些被清除(對應 review #1)。

- **`tests/e2e/run.sh`**
  - 以 `.workflow/latest-run.txt` 解析 run 目錄;metrics/log 斷言改指該目錄。
  - 新增斷言:run 目錄存在、`task-source`/`task` 已保存、prompt/output 產物存在且序號遞增、metadata 齊備、`latest-run.txt` 指向本次 run、**每輪 `git-status`/`git-diff` 快照存在**、**metrics 新欄位齊備**。
  - **live `protected-*` 檢查維持在 `.workflow/`**(工作檔不動),故現有 `protected_violations` 相關斷言不改。

- **`README.md`**:更新 `.workflow` / archive 結構說明與 `RUNS_DIR` 變數。

## Tests

- `bash -n auto-workflow.sh tests/helpers.test.sh tests/e2e/run.sh`
- `bash tests/helpers.test.sh`(新增與既有純函式測試)
- `E2E_SETUP_ONLY=1 bash tests/e2e/run.sh`(不呼叫 AI)
- 完整 E2E(`bash tests/e2e/run.sh`)維持手動,因會呼叫真實 AI 並消耗配額。

## Commit Plan

- **Commit 1** `fix(workflow): reset per-run live state at start`
  - `init_live_state`(清 `suggestions.md`/`protected-*`)+ 於 `main()` 呼叫;helper test。**先落地此隔離修正**(review #1,最高風險)。
- **Commit 2** `feat(workflow): archive per-run intermediate artifacts`
  - `RUNS_DIR`/`WF_RUN`、`art_path`/`write_meta`/`resolve_model_args`/`archive_task`(精確 task 來源)/`archive_snapshot`、run-metadata、prompt/output 落檔、每 attempt raw、review/verdict/protected/pr-body/suggestions 快照、claude 成功輸出落檔。
  - `compose_review_prompt` 重構(exact reviewer prompt);`metric` 追加 model/model_args/generated_at 欄。
  - 更新 helper tests。
- **Commit 3** `feat(workflow): snapshot per-round git state`
  - `archive_git_state`(每輪 `git-status`/`git-diff`)+ 於 `work()` 後呼叫;helper/e2e 斷言。
- **Commit 4** `feat(workflow): segment run log with sections`
  - `log_section` 與各處套用(含 startup/protected/human-gate,可讀性)。
- **Commit 5** `test(e2e): validate run-local workflow archive`
  - E2E 斷言 + README 結構說明。

## 驗證(Verification)

1. `bash tests/helpers.test.sh` 全綠(涵蓋新純函式)。
2. `E2E_SETUP_ONLY=1 bash tests/e2e/run.sh` 通過基線,確認 script 語法與早期路徑無誤。
3. 手動小型 run(可設 `RUNS_DIR=/tmp/wf-archive` 驗證可設定性),完成後檢查:
   - `$RUNS_DIR/<RUN_ID>/` 內產物以 `NNN-` 遞增、`ls` 排序即生成序。
   - 每份產物有對應 `.meta.json`,欄位含 engine/model/model_args/role/stage/round/time;`metrics.csv` 尾端含 `model,model_args,generated_at`。
   - `task-source` 與 `task` 皆存在;literal 與檔案兩種輸入各測一次(檔案來源記絕對路徑)。
   - 同一 stage 多輪時,`review`/`verdict` 各輪快照並存、未互相覆蓋;每輪有 `git-status`/`git-diff` 快照。
   - archived reviewer prompt 與實際送出一致(codex/agy 含 `verdict_file_instr`);多次 retry 時每個 attempt 各有 `.raw`。
   - `$WF/latest-run.txt` 指向本次 run 目錄;log 有分段分隔線與帶時間/生成者的區段標頭。
4. worktree 模式(`USE_WORKTREE=1`)下確認 archive 目錄落在 worktree 內,而非原 repo。
5. **跨 run 隔離(review #1)**:在同一 repo 連跑兩次(或預置殘留 `suggestions.md`/`protected-*`),確認第二次 run **不受上次殘留影響**——`suggestions` 不含舊 run 內容、第一個 stage 不誤觸 protected 回退。

## Assumptions / Non-goals

- 日期時間用本機 date 的 local time。
- `RUNS_DIR` 預設 `.workflow/runs`(被 `.workflow/.gitignore` 的 `*` 忽略);若指到 `.workflow` 之外,需自行處理版控忽略。
- `RUN_ID` 沿用現有秒級格式,不加序號;撞號(極罕見)才加數字後綴。**注意此「RUN_ID 序號」與 archive 檔名的 `ART_SEQ` 序號是兩回事**(見 review #3)。
- 不改 live 工作檔的**路徑**;但 `init_live_state` 會在**每個 run 起始重置**會跨 run 污染的 live 檔(`suggestions.md`/`protected-*`),這是達成 run 隔離的必要動作(review #1)。
- metrics 新欄位一律**附加在尾端**,以免打亂既有 `awk` 的欄位索引(review #4)。
- **本次不實作 archive 保留/成長上限**(使用者指定),列為後續工作。
