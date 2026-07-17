# Custom implementation agent v3 收尾後續工作

- 建立日期：2026-07-17
- 狀態：待規劃
- 來源：[`docs/plans/20260712_custom_impl_agent_v3_closure_gpt56sol-max.md`](../plans/20260712_custom_impl_agent_v3_closure_gpt56sol-max.md)
- 已完成基線：`main@47f1b0b0cb4f78eb776a20cfe33e91255e29653e`

## 目的

Custom implementation agent v3 的 core RC 已完成並合併至本地 `main`。本文件只追蹤收尾後仍未完成的安全架構、跨平台驗證與維運工作，不重新開啟已完成的 implementation-slot routing、session isolation、metadata、resume、built-in arguments validation 或 process-local protected-control enforcement。

目前 core RC 的 protected-control 保證只在單一 workflow process 內成立：acceptance stage 後保存 immutable snapshot，並在 active worker boundary 前後 fail closed。它不宣稱能抵禦同 UID concurrent/background actor 在 pathname syscall 間替換檔案或目錄，也不提供 stable-handle mutation、OS-level mutual exclusion、自動 restore/quarantine 或跨 process trust persistence。

## 執行順序

`H0 安全設計` → `H1/H2/H3 實作` → `H4 跨平台驗證` → `H5 文件與交付`

H0 完成前，不應直接把舊 hardening commit 的實作帶回 production。H1、H2 與 H3 可在設計核准後分開實作與提交，但 H4 必須涵蓋三者實際採用的所有 platform primitives。

## P0：安全設計決策

### H0. 定義擴大的 threat model 與 platform primitives

- [ ] 明確決定產品是否要防禦同 UID concurrent/background workspace actor。
- [ ] 定義需要保護的 invariants：authoritative state/control writes、ledger recovery、restore、quarantine cleanup、lock ownership 與 protected-test trust anchor。
- [ ] 建立 Windows/POSIX capability matrix，評估 stable directory/entry handles、handle-relative read/write/rename/unlink、share modes、reparse point/symlink policy 與 owner-death detection。
- [ ] 定義各平台無法提供必要 primitive 時的 fail-closed 行為，不以相鄰的 pathname identity checks 假裝提供 atomic guarantee。
- [ ] 以獨立 ADR/design plan 記錄選擇、替代方案、平台限制、migration strategy 與測試 seam。

完成條件：設計文件經 review，清楚說明 final syscall 如何綁定已驗證 object/parent，以及每個宣稱如何由 deterministic test 證明。

## P1：核心 hardening 實作

### H1. Authoritative mutation 與 cleanup 安全邊界

- [ ] 重新設計 authoritative state/control restore，避免 identity check 後再用 pathname `replace` 寫入 replacement hierarchy。
- [ ] 重新設計 quarantine cleanup，避免 identity check 後用 pathname `rmtree`/unlink 刪除 replacement hierarchy。
- [ ] 為 authoritative reads 提供 stable-read semantics，明確處理 UTF-8 decode、short read、I/O failure 與 platform error translation。
- [ ] 所有不確定狀態一律 fail closed，且保留原始 exception cause 供診斷。
- [ ] 加入在 final write/replace/delete syscall 邊界注入 replacement 的 deterministic regression tests。

完成條件：write/restore/cleanup 不會 write through 或 delete through 被替換的 hierarchy；保證由最終操作 seam 的測試證明，而非只增加 syscall 前後檢查。

### H2. OS-backed lock 與 fresh-state rollback

- [ ] 以 OS-backed held lock 或等價 stable-handle primitive 取代 pathname ownership check 加 `rmdir` 的釋放模式。
- [ ] 定義 acquire、contention、release、owner death、stale metadata 與 process crash 後的行為。
- [ ] 保證舊 owner 不會刪除或釋放較新 owner 的合法 lock。
- [ ] 修正 fresh `RunState.create()`：取得 lock 後若 initial `task.txt`、ledger 或其他初始化寫入失敗，必須可靠清理自己持有的 lock。
- [ ] 加入 acquire failure、partial initialization、owner death、double release 與 competing owner 的 deterministic tests。

完成條件：lock ownership 由持有中的 OS primitive 決定；所有初始化 failure path 都不遺留無 owner lock，也不會清除其他 owner 的 lock。

### H3. Cross-process protected-control trust 決策

- [ ] 決定 protected-control trust 是否需要跨 process/resume persistence；若不需要，將限制保留為正式產品契約。
- [ ] 若需要 persistence，設計 worker 無法自行改寫或重新建立的 trust anchor；不得把 snapshot 寫回 worker 可寫的 run state 後就視為可信。
- [ ] 定義 resume 時如何驗證 ledger、control files、protected paths 與 base 的來源及一致性。
- [ ] 定義 trust anchor 遺失、損毀、版本不相容與人工 recovery 的 fail-closed 流程。
- [ ] 加入 process restart、tampered state、missing anchor 與 stale base 的 integration tests。

完成條件：跨 process 保證有明確的信任來源與 failure semantics；若產品選擇維持 process-local scope，相關限制在 CLI 與文件中不可被描述成更強的安全承諾。

## P2：驗證、文件與交付

### H4. Deterministic security tests 與跨平台 CI

- [ ] 為每個 final mutating syscall 建立可重現的 replacement-race seam；不得只用 timing/sleep 型測試。
- [ ] 在 Windows 覆蓋 junction、reparse point、symlink privilege 與 share-mode 行為；需要權限的測試應有明確 CI job，不得把 security gate 靜默 skip。
- [ ] 在 POSIX 覆蓋 symlink、rename/unlink、directory handle 與 owner-death 行為。
- [ ] 設定 Git remote 與 Ubuntu CI，執行完整 offline suite；目前本專案尚未配置 remote，本次 core RC 也未執行 Ubuntu CI。
- [ ] hardening 完成後重跑 local Windows full suite，並在再次取得 quota 同意後只執行一次 bounded live-agent E2E。
- [ ] 保存 exact pass/skip/deselect counts、平台資訊、失敗注入點與 retained workspace evidence。

完成條件：Windows 與 Ubuntu 的必要 gates 全部通過；任何平台限制或 skip 都有精確原因，且不削弱已宣稱的 threat model。

### H5. 文件、migration 與 release 檢查

- [ ] 更新英文與繁中 README，區分 process-local core RC 保證與新增的 concurrent-actor 保證。
- [ ] 文件化 lock recovery、trust-anchor recovery、quarantine/restore failure 與人工操作順序。
- [ ] 提供舊 run state/ledger 的 compatibility 或 migration 決策，以及無法安全 migration 時的拒絕策略。
- [ ] 在 release notes 列出支援平台、已知限制、CI evidence 與仍未處理的風險。
- [ ] 對完整 hardening diff 做 bounded security review，review scope 必須包含 final syscall 與 error paths。

完成條件：實作、tests、threat model 與操作文件的保證一致，沒有把未測試或平台不支援的能力描述為已完成。

## 唯讀參考與禁止事項

- `feat/custom-impl-agent@cc2df4d96172f2ecb9edecad5acfbbc8c895b7c8` 可作為 findings 與測試想法的唯讀參考，但不得整個 cherry-pick、rebase 或視為可直接發布的 hardening 實作。
- 不以更多相鄰 `lstat`/identity checks 取代 stable-handle 或 OS-backed primitive。
- 在 H0 核准前，不擴大 core RC 的安全宣稱，也不自動啟用 restore、quarantine 或 destructive cleanup。
- 每個 implementation task 應先建立會失敗的 regression tests，通過 focused/full gates 後以獨立 Conventional Commit 提交。

## Repository housekeeping（非安全 blocker）

- [ ] 在不再需要 forensic/reference evidence 後，決定是否刪除舊 `feat/custom-impl-agent` branch 與其 worktree。
- [ ] 在確認本地 `main` 與所需 artifacts 均已備份後，清理 core-RC worktree/branch。
- [ ] 若要協作或發布，配置 remote、push `main`，並啟用上述 Ubuntu CI gates。
