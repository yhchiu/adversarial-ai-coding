# Phased ATDD (PHASES=1) 收尾後續工作

- 建立日期：2026-07-17
- 狀態：F1-F4、F9 已完成（2026-07-18 合回 main@03526e1）；F5-F8 待辦
- 來源：[`docs/superpowers/plans/2026-07-17-phased-atdd.md`](../superpowers/plans/2026-07-17-phased-atdd.md)
- 實作分支：`feat/phased-atdd`（worktree `.worktrees/phased-atdd`，4fae39e..e893fe6）

## 目的

Tasks 1-9 的程式與測試已在 worktree 完成（558 tests 全綠），task 10 的
live E2E 第一次執行因 codex elevated sandbox 的 deny-ACL 檔案在 write-spec
round 2 失敗，已由 `e893fe6 fix(review)` 加入 reviewer 輸出檔的
fail-closed recovery。本文件追蹤 E2E 通過後的收尾與後續驗證工作。

## 必要收尾（依順序）

### F1. Live E2E 通過（task 10 完成條件）

- [x] 2026-07-18 通過（AGENT_B=claude MODEL_B=opus；codex reviewer 因其 Windows sandbox 故障改用 claude，見 F8）：
      `uv run pytest -m e2e -s tests/e2e/test_e2e.py::test_full_workflow_phased_e2e`
- [x] recovery 路徑已在 07-17/18 的 codex 失敗 run 中驗證發動
      （log 出現 `is unreadable; discarding it`）且 run 仍能完成。

### F2. 合回 main

- [x] main 無新 commit，直接 ff。
- [x] ff-merge 完成：main@03526e1（16 commits）。
- [x] main 全套件 560 passed。

### F3. 清理

- [x] phased-atdd worktree 與分支已移除。
- [x] custom-impl-agent-core-rc worktree 與分支已移除（cc2df4d 存檔保留）。
- [x] 已清理全部失敗與過期 workspaces。保留三個：`%TEMP%\wf-e2e-h_vxb0b4`（毒檔證據，F8 上報用）、`C:\tmp\wf-e2e-manual-20260717-113900`（乾淨 codex run 對照）、`C:\tmp\wf-e2e-ph-x1bb_fsk`（本次通過，檢視後可刪）。

### F4. 記錄

- [x] memory 已更新。

## 建議進行（Arthur 決定）

### F5. 跨廠牌 review

- [x] 2026-07-19 GPT review 完成:7 blockers + 2 suggestions 全數確認並修復,
      計畫與驗證見 `docs/superpowers/plans/2026-07-19-phased-atdd-gpt-review-fixes.md`。

### F6. 真實任務 dogfooding

- [ ] 找一個中型真實任務用 `PHASES=1` 跑完整流程。E2E fixture 太小，
      「強模型是否真的拆出垂直切片」「red check 在真實 gate 下的
      訊噪比」只有真實任務驗得到。
- [ ] 記錄 phase 拆分品質與 reviewer 行為,回饋到
      `write-implementation-plan-phased.md` 提示詞。

### F7. CI（有 remote 之後才適用）

- [ ] repo 推上 GitHub 後，確認 windows CI job 涵蓋新測試檔。

### F8. Codex Windows sandbox 對策（2026-07-18 新增）

背景：codex 0.144.5 的 Windows sandbox 三種組態對 headless workflow 全數
不可用——elevated+trusted 會寫出 deny-all ACL 毒檔、elevated+untrusted
是唯讀 sandbox（verdict 寫不出）、unelevated 連 workspace 都讀不到。
七次 live E2E 失敗全數源於此；workflow 端的 recovery（e893fe6）與
fail-closed sentinel 在每種模式下都正確運作。

- [ ] 評估為 codex slot 增加 opt-in 的 `danger-full-access` sandbox 設定
      （第一級設定、預設關、README 警語要求搭配 worktree/container；
      不開放走 CODEX_ARGS，維持 reserved-flags 契約）。
- [ ] 向 OpenAI 回報兩個 codex Windows sandbox bug：elevated broker 寫出
      母行程不可讀的檔案；unelevated token 連 workspace 都不可讀。

### F9. Live E2E 改用非 codex reviewer（task 10 的解套路徑）

- [x] `AGENT_B=claude MODEL_B=opus` 通過（2026-07-18，workspace x1bb_fsk）。

## 已知限制（spec 已接受，不需行動）

- Red check 無法區分「新測試紅」與「B 弄壞 build」；由 A 的測試審查與
  phase gate 兜底。
- Codex elevated sandbox 的 ACL 污染在單一 run 內不確定（同一 run 的
  round 1 正常、round 2 污染）；workflow 已能 fail-closed 恢復，但
  根因在 codex 端，無法從本 repo 消除。
