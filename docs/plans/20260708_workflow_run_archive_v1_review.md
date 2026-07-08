結論：方向大致正確，尤其「保留 live 工作檔、另外做 archive 快照」是對的，因為現有流程確實直接依賴 .workflow/
review.md、.workflow/verdict.json、ENGINE_OUT 這些固定路徑。但這份計畫還有幾個需要修正的問題，否則無法完全達成你的
目標。

主要問題

1. 仍可能有跨 run 干擾
   計畫保留 live 檔固定路徑，但沒有明確在新 run 開始時清空或初始化 .workflow/suggestions.md、protected-tests.txt、
   protected-base.sha、review.md、verdict.json、last-engine-output.txt。
   這會污染下一次 run：例如目前 check_protected 會讀舊的 protected 檔，finish 前也會讀舊的 suggestions.md。這和
   「避免不同 run 的資料互相干擾」衝突。
   建議新增 init_live_state：在 archive/run dir 建好後，清空或重建本次 run 會影響邏輯的 live 狀態檔。

2. task 檔案來源保存時機不夠精確
   現有程式在 setup_workspace 前就把 task 檔案讀成字串，之後原始路徑資訊會遺失或因 cd 到 worktree 而變得不可靠。見
   adversarial-ai-coding.sh:584。
   計畫說 main() 解析後呼叫 archive_task，但應明確保留 task_arg、task_source_kind、task_source_path、
   task_resolved_text，且檔案路徑最好轉成絕對路徑後再 setup_workspace。

3. 計畫內「移除序號」和「使用 ART_SEQ 三位數」文字矛盾
   docs/plans/workflow_run_archive.md:28 說移除序號，但 docs/plans/workflow_run_archive.md:45 又要求 artifact 使用
   NNN- 遞增序號。
   實際上應該是「移除 run id probe 序號，但保留 artifact 序號」。建議改清楚，避免實作時誤刪 artifact 排序機制。

4. 不是所有「資料與 log」都有生成者與時間
   write_meta 只涵蓋 archive artifacts；但 metrics.csv 計畫維持原欄位格式，沒有 timestamp、model、model_args。log
   也只有 section header，未保證所有 log 段落都被 attribution 包住。
   若要嚴格符合目標，建議：
    - metrics.csv 加 generated_at,model,model_args，或至少有 metrics.csv.meta.json
    - 001-run.log 也要有 .meta.json
    - startup、gate、retry、protected check、human gate、finish 都要有明確 section

5. engine retry 的失敗中間輸出可能仍會遺失
   計畫只在呼叫後 snapshot ENGINE_OUT。但 engine_call 可能重試；每次重試都會覆蓋 ENGINE_OUT。這代表 rate-limit 或
   失敗 attempt 的 raw output 可能只剩 log，不會成為獨立 artifact。
   若目標是「所有中間資料」，應在每次 attempt 後立即 archive attempt output，檔名帶 attempt-N 與 rc。

6. prompt 保存可能不是「實際送出的完整 prompt」
   work() 很容易存完整 prompt；但 reviewer prompt 由 r_claude、r_codex、r_agy 各自組合，codex/agy 還會附加
   verdict_file_instr。計畫應明確重構成「先組出 exact prompt，再拿同一份 prompt 去 archive 和呼叫引擎」，避免
   archive 的 prompt 與實際 prompt 不一致。

7. archive 不一定涵蓋專案檔案的中間狀態
   目前計畫保存 AI I/O、review/verdict、protected/pr-body/suggestions，但沒有保存每次 worker 修改後的 git status、
   git diff、spec/plan 草稿或程式碼中間狀態。
   如果你的「所有中間資料」包含每輪檔案狀態，應加上每次 work() 後 snapshot：
    - NNN-git-status.txt
    - NNN-git-diff.patch
    - 必要時 git diff --binary

整體判斷：這份計畫的架構可以採用，但我會先補上「run 開始時初始化 live state」、「精確保存 task source」、「每次
retry/attempt 都 archive」、「metrics/log metadata 完整化」這幾點，再開始實作。未修正前，它還不能完整保證 run 隔離
與全量保存。

