# Workflow Run Archive Plan（修訂版）

> 本檔即為 `C:\Project\auto-coding\plan.md` 的修訂內容；核准後原樣寫入 plan.md。
> 相對於前一版,本版依 code review findings 全面修正,核心改動是**改採「live 工作檔不動、快照進 archive」的設計**,以避免弄壞既有互審迴圈。

## Context（為什麼要改）

現行 `auto-workflow.sh` 把跨 run 的中間狀態都寫在 `.workflow/` 的固定檔名(`review.md`、`verdict.json`、`last-engine-output.txt`、`pr-body.md`…),每輪/每次呼叫直接覆蓋,所以:

- 一個 run 內的歷輪 review/verdict/engine output 會被後續覆蓋,事後無法完整重建過程。
- `$task` 原文(literal 或檔案)從未落檔。
- 沒有「生成者(engine/model/參數)+ 時間」的標註。
- log 缺乏分段,可讀性差。

目標:每個 run 保留完整、可依檔名排序、且標註生成者與時間的中間資料,且不同 run 互不干擾。**前一版計畫的方向對,但把幾個承重的既有機制當成不存在**(見下方 findings),照那版做會弄壞流程。本版修正之。

## 設計原則(關鍵決策)

**live 工作檔維持現有固定路徑不動;archive 以「快照(cp)+ 遞增序號檔名 + sidecar metadata」另存。**

理由:`review.md` / `verdict.json` / `protected-*` / `ENGINE_OUT` 都被 script 邏輯與**送給 AI 的 prompt 文字**直接引用。若把它們改成 per-round 專屬檔名(前一版的 `014-review-spec-r2.md` 寫法),就得把所有 prompt 與下游 reader 重新接線,且會切斷「工作者與審查者在同一 stage 內共用同一份 review.md 對話」的交接。快照法達到「全保存 + 可排序 + 帶生成資訊」三個目標,又完全不動互審迴圈與 prompt。

## Findings → 對應處置(全數納入)

| # | Finding | 本版處置 |
|---|---------|---------|
| 1 | `RUN_ID` 已存在且驅動 SPEC_DIR / branch / worktree / log 名 | **沿用現有 `RUN_ID`(秒級、不加序號)**;archive 目錄為 `$RUNS_DIR/$RUN_ID`。branch/spec 命名完全不動。 |
| 2 | `review.md`/`verdict.json` 是跨輪演進對話 | 維持固定工作路徑;每次 writer 寫完就快照一份帶序號的進 archive(見「快照時機」)。 |
| 3 | worktree 下序號 probe 的落地時機死結 | 因為不再有序號 probe,且 archive 目錄在 `setup_workspace` 之後才 `mkdir`(與現有 `mkdir -p "$LOGS" "$SPEC_DIR"` 同處),自然落在正確的樹。死結消失。 |
| 4 | `ENGINE_OUT` 是限額偵測的共享讀寫契約;claude 只在失敗時落檔 | `ENGINE_OUT` 保持單一固定檔(限額 reader 不動);另令 `w_claude`/`r_claude` **成功時也把原始 JSON 寫入 `ENGINE_OUT`**,再由呼叫端快照成 `-output-` 產物,claude 成功輸出才有 archive。 |
| 5 | 三位數序號過度工程 | **移除序號**。撞號(同 `$RUN_ID` 目錄已存在)才 fallback 加 `-2`、`-3`… 後綴,不中止。 |
| 6 | metrics.csv 跨 run 語義 + E2E 依賴 | metrics 移入 `$WF_RUN/metrics.csv`(每 run 自足);欄位格式不變,`finish()` 與 E2E awk 只改路徑。 |
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

### 每份資料 + log 都帶生成資訊
- `write_meta <artifact> <role> <engine> <model> <model_args> <stage> <round>` 寫 sidecar `<artifact>.meta.json`,欄位:`generated_at`(本機 local ISO 時間)、`generator_role`、`engine`、`model`(經 `engine_model` 解析後的實際值)、`model_args`、`stage`、`round`、`run_id`。
- run 起始寫 `NNN-run-metadata.json`:完整設定 + A/B 解析後 engine/model/args。
- `model_args` 由新純函式 `resolve_model_args <engine>` 依引擎回傳對應 `CLAUDE_ARGS`/`CODEX_ARGS`/`AGY_ARGS`。
- log:新增 `log_section` 於每次 AI call / review / gate / retry / commit / finish 前輸出「空行 + 分隔線 + 標頭(時間 + role/engine/model/args/stage/round)」。

### 保存 `$task`
- `main()` 解析 task 後,經新函式 `archive_task`:
  - `NNN-task-source.md`:來源(檔案 → 記原始路徑 + 內容;literal → 標注為命令列字串)。
  - `NNN-task.txt`:實際使用的 resolved task 字串。

### 每次 AI 呼叫的 prompt / output 落檔
- `work()` 與 `run_review()` 於呼叫前:`art_path` 產出 `NNN-<role>-prompt-<stage>-r<round>.md`(prompt 全文)+ meta。
- 呼叫時把 `engine_call` 的 stdout 同時 tee 進 LOG 與 `NNN-<role>-output-<stage>-r<round>.txt`(所有引擎一致擷取串流輸出)。
- 呼叫後快照 `ENGINE_OUT`(原始輸出,含 claude 成功 JSON)為同序號的 `.raw` 產物。

### 快照時機(review.md / verdict.json,確保不漏 state)
- `run_review` 末(審查者寫完):快照 `review.md` → `NNN-review-<stage>-r<round>.md`;`verdict.json` → `NNN-verdict-<stage>-r<round>.json`。
- 修正輪 `work()` 若動到 `review.md`(工作者補「已修正」回覆):在下一輪審查者覆寫前快照 `NNN-review-<stage>-r<round>-worker.md`。
- `protected-tests.txt` / `protected-base.sha` 設定當下各快照一份。
- `finish()`:快照 `pr-body.md`、`suggestions.md`。

## 逐檔改動

- **`auto-workflow.sh`**
  - 設定區(:62–68 附近):新增 `RUNS_DIR`;`WF_RUN` 與撞號 fallback;`LOG`/`METRICS` 指向 `$WF_RUN`;保留 `ENGINE_OUT` 為固定檔。
  - 新增純函式:`art_path`、`write_meta`、`resolve_model_args`、`archive_task`、`archive_snapshot`(cp + write_meta)、`log_section`。
  - `w_claude`/`r_claude`(:207, :344):成功時亦把 `$out` 寫入 `ENGINE_OUT`。
  - `work`(:282)/`run_review`(:403):加 prompt/output 落檔與 verdict/review 快照;`begin_stage`/`gate_loop`/`engine_call` retry/`commit_work`/`finish` 加 `log_section`。
  - `main()`(:576):`archive_task`;`mkdir -p "$WF_RUN/logs"` 與寫 `NNN-run-metadata.json`、`latest-run.txt`(在 `setup_workspace` 之後)。
  - 標頭環境變數文件補上 `RUNS_DIR`。
  - **live 工作檔路徑一律不動**(`$WF/review.md`、`$WF/verdict.json`、`$WF/suggestions.md`、`$WF/protected-*`、`$WF/pr-body.md`);故所有送 AI 的 prompt 文字與 `verdict_approved`/`collect_suggestions`/`show_blockers`/`check_protected` **完全不改**。

- **`tests/helpers.test.sh`**
  - `metric` 測試(:121):改指向 `$WF_RUN` 下的 metrics 路徑。
  - 新增:`art_path` 前綴遞增且排序 = 生成序;`write_meta` 產出含 engine/model/model_args/role/stage/round/run_id/generated_at(時鐘注入);`resolve_model_args` 各引擎正確;`archive_task` 對 literal 與檔案兩種來源都保存;快照連兩輪不覆蓋(兩檔並存)。

- **`tests/e2e/run.sh`**
  - 以 `.workflow/latest-run.txt` 解析 run 目錄;metrics/log 斷言改指該目錄。
  - 新增斷言:run 目錄存在、`task-source`/`task` 已保存、prompt/output 產物存在且序號遞增、metadata 齊備、`latest-run.txt` 指向本次 run。
  - **live `protected-*` 檢查維持在 `.workflow/`**(工作檔不動),故現有 `protected_violations` 相關斷言不改。

- **`README.md`**:更新 `.workflow` / archive 結構說明與 `RUNS_DIR` 變數。

## Tests

- `bash -n auto-workflow.sh tests/helpers.test.sh tests/e2e/run.sh`
- `bash tests/helpers.test.sh`(新增與既有純函式測試)
- `E2E_SETUP_ONLY=1 bash tests/e2e/run.sh`(不呼叫 AI)
- 完整 E2E(`bash tests/e2e/run.sh`)維持手動,因會呼叫真實 AI 並消耗配額。

## Commit Plan

- **Commit 1** `feat(workflow): archive per-run intermediate artifacts`
  - `RUNS_DIR`/`WF_RUN`、`art_path`/`write_meta`/`resolve_model_args`/`archive_task`/`archive_snapshot`、run-metadata、task 保存、prompt/output 落檔、review/verdict/protected/pr-body/suggestions 快照、claude 成功輸出落檔。
  - 更新 helper tests。
- **Commit 2** `feat(workflow): segment run log with sections`
  - `log_section` 與各處套用(可讀性)。
- **Commit 3** `test(e2e): validate run-local workflow archive`
  - E2E 斷言 + README 結構說明。

## 驗證(Verification)

1. `bash tests/helpers.test.sh` 全綠(涵蓋新純函式)。
2. `E2E_SETUP_ONLY=1 bash tests/e2e/run.sh` 通過基線,確認 script 語法與早期路徑無誤。
3. 手動小型 run(可設 `RUNS_DIR=/tmp/wf-archive` 驗證可設定性),完成後檢查:
   - `$RUNS_DIR/<RUN_ID>/` 內產物以 `NNN-` 遞增、`ls` 排序即生成序。
   - 每份產物有對應 `.meta.json`,欄位含 engine/model/model_args/role/stage/round/time。
   - `task-source` 與 `task` 皆存在;literal 與檔案兩種輸入各測一次。
   - 同一 stage 多輪時,`review`/`verdict` 各輪快照並存、未互相覆蓋。
   - `$WF/latest-run.txt` 指向本次 run 目錄。
   - log 有分段分隔線與帶時間/生成者的區段標頭。
4. worktree 模式(`USE_WORKTREE=1`)下確認 archive 目錄落在 worktree 內,而非原 repo。

## Assumptions / Non-goals

- 日期時間用本機 date 的 local time。
- `RUNS_DIR` 預設 `.workflow/runs`(被 `.workflow/.gitignore` 的 `*` 忽略);若指到 `.workflow` 之外,需自行處理版控忽略。
- `RUN_ID` 沿用現有秒級格式,不加序號;撞號(極罕見)才加數字後綴。
- 不搬移或刪除既有 `.workflow` 檔;新程式只新增 archive 與快照,不改 live 工作檔路徑。
- **本次不實作 archive 保留/成長上限**(使用者指定),列為後續工作。
