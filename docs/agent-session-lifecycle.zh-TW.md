# Agent Session 生命週期

[English](agent-session-lifecycle.md) | 繁體中文

本文件說明 workflow 中每個 agent 何時開始全新對話、何時續接既有對話，
涵蓋預設流程、Phased ATDD、Dual Spec、匯入產物與 `RESUME_RUN`。

## Process 與對話是兩件事

每次 agent 呼叫都會啟動新的 CLI process。所謂續接 worker，仍然是新的
process，只是 CLI 會收到精確的 session、thread 或 conversation ID，讓這個
process 接回先前的對話 context。

本文使用以下名詞：

- **全新（fresh）**：呼叫時沒有收到 workflow 管理的對話 ID。
- **續接（resume）**：呼叫時收到目前 active worker 已捕捉的 ID。
- **O**：選定的 owner；預設流程中是 slot A。
- **R**：另一個擔任 reviewer 的 slot；預設流程中是 slot B。
- **I**：設定任一 `IMPL_*` 客製化後建立的實作 ref。三個 `IMPL_*` 都未設定
  時，實作者就是 O 本身，不會建立獨立的 I ref。

## 核心規則

1. **每個 stage 都從沒有 active worker session 開始。** 進入新 stage 時，
   即使工作者與上一個 stage 是同一 slot，也會清除已捕捉的 ID 與 owner。
2. **整個 workflow process 只有一個 active worker session。** A、B、I 並不
   會各自保存一份可稍後取回的 session。
3. **只有完整 worker ref 相同才能續接。** Ref 包含 slot、command 名稱與實作
   base-slot identity。切換 ref 會丟棄 active ID。只更換模型不會改變 ref，
   因此不會單獨觸發清除。
4. **每次 reviewer 呼叫都是全新 session。** 同一 review loop 的後續輪次也
   一樣。Reviewer 不讀取也不取代 active worker session，所以 worker 收到
   review 意見後仍可續接。
5. **`RESUME_RUN` 續接的是 workflow 狀態，不是 agent context。** 新的
   workflow process 會建立新的記憶體內 session holder；agent ID 不會寫入
   run state。
6. **Prompt 不依賴聊天歷史。** 每次呼叫都會指向一份內容完整的 archive
   prompt，因此全新接手的 agent 可從檔案與 repository 狀態還原任務。

一般 review loop 的 session 流向如下：

```text
worker 全新或續接
  -> reviewer 全新
  -> 發現 blocker
  -> 同一 worker 續接
  -> reviewer 再開全新 session
```

## 內建 adapter 行為

自動 session 生命週期適用於三個內建 agent：

| Agent | 全新 worker | 續接 worker | Reviewer |
| --- | --- | --- | --- |
| Claude | `claude -p ...` | `claude -p ... --resume <session-id>` | 永遠全新，不傳 `--resume` |
| Codex | `codex exec --json ...` | `codex exec resume --json ... <thread-id> <prompt>` | 永遠全新，使用一般 `codex exec` |
| Agy | `agy --print ...` | `agy --print ... --conversation <conversation-id>` | 永遠全新，不傳 `--conversation` |

若全新 worker 呼叫沒有回報 ID，下一次 worker 呼叫仍然全新。若已建立的
worker session 某次沒有再回報 ID，則保留最後一個已知 ID。Workflow 絕不以
Codex `--last` 或 Agy `--continue` 猜測要接哪個對話。

自訂 agent command 沒有自動 session 管理。從 workflow 角度看，每次自訂
command 呼叫都互相獨立。Wrapper 可以自行實作 continuity，但該行為不在本文
所述的 workflow 保證範圍內。

## 預設流程矩陣

除非 Dual Spec 選出不同角色，以下 O=A、R=B。

| Stage | Worker 生命週期 | Reviewer 生命週期 |
| --- | --- | --- |
| `write-spec` | O 第一次呼叫為全新；review 發現 blocker 時，O 續接修正。使用 `IMPORT_SPEC` 時沒有最初的 O 呼叫。 | R 的每一輪 review 都是全新。 |
| `commit-spec` | O 為全新，因為這是另一個 stage，即使 spec 也是 O 寫的。 | 沒有 reviewer 呼叫。 |
| `write-implementation-plan` | O 第一次呼叫為全新；之後的 plan 修正、phased 格式修正與 plan commit 都在同一 stage 續接 O。使用 `IMPORT_PLAN` 時沒有撰寫呼叫，因此 O 的第一次呼叫可能是修正或 commit；該呼叫仍為全新。 | R 的每一輪 review 都是全新。 |
| `write-acceptance-tests` | 角色互換：R 以全新 session 撰寫測試；測試修正與 acceptance-test commit 續接 R。 | O 的每一輪 review 都是全新。 |
| `write-code` | 實作者第一次呼叫為全新，並在 task 實作、build-gate 修正與 task commit 間續接。後續完整 gate 與 branch-review 修正是否續接，取決於實作者是 O 還是獨立 I，詳見下節。 | R 的每一輪 branch review 都是全新。 |
| `final-review-and-fixes` | O 以全新 session 執行 self-review；gate 修正、review 修正與最後 commit 都續接 O。 | R 的每一輪 final acceptance 都是全新。 |
| `finish` | 沒有 agent 呼叫。 | 沒有 agent 呼叫。 |

### `write-code` 內的 O 與 I 交接

若未設定 `IMPL_AGENT`、`IMPL_MODEL`、`IMPL_ARGS`，implementation ref 就是
O。因此 `write-code` 內所有 worker 呼叫使用同一 ref，可以續接同一個對話。

只要設定任一 `IMPL_*`，即使 I 與 O 使用相同 CLI command，也會建立不同的
I ref。I 以全新 session 進入逐任務實作迴圈，並在所有 task 間續接。完整品質
關卡由 workflow 自己執行，只有需要修正時才呼叫 O；branch review 也只有出現
blocker 時才呼叫 O。第一次 O 呼叫會切換 active worker ref、丟棄 I 的 ID，並
讓 O 從全新 session 開始；同一 stage 中後續的 O 修正則續接這個新 session。
先前 stage 的舊 O session 不會恢復。

若完整 gate 與 branch review 都直接通過，這個 stage 不會呼叫 O worker；目前
的 I session 只會在下一個 stage 開始時被清除。

## Phased ATDD 矩陣

Spec、`commit-spec` 與 plan stage 依照預設矩陣。之後每個 phase 都會建立兩個
獨立 stage：

| Stage | Worker 生命週期 | Reviewer 生命週期 |
| --- | --- | --- |
| `phase-N-write-tests` | R 以全新 session 撰寫測試；review 修正、red-check 修正與測試 commit 都在此 stage 續接 R。 | O 的每一次 acceptance-test review 都是全新。 |
| `phase-N-implement` | I，或沒有獨立 I 時的 O，在這個 phase 以全新 session 開始；該 phase 的所有 task、gate 修正與 commit 都續接同一 worker。 | 設定 `PHASE_REVIEW=1` 時，R 的每一輪 phase review 都是全新。 |

兩個 stage 之間的邊界會在開始實作前清除 R 的測試撰寫 session。下一個 phase
又會經過兩次新 stage 邊界，因此不會沿用上一個 phase 的測試或實作 session：

```text
phase-01-write-tests: R 全新 -> R 視需要續接
phase-01-implement:   I 全新 -> I 視需要續接
phase-02-write-tests: R 全新 -> R 視需要續接
phase-02-implement:   I 全新 -> I 視需要續接
```

所有 phase 完成後，仍會進入一般的 `write-code` stage 執行完整品質關卡與
branch review；phased 模式在此不會再跑逐任務實作迴圈。Stage 邊界已清除最後
一個 phase session，所以第一次 O 修正（如果需要）會是全新 session。後續的
`final-review-and-fixes` 又會再次從全新 session 開始。

## Dual Spec 矩陣

Dual Spec 以以下 stage 取代 `write-spec`：

| Stage | Session 行為 |
| --- | --- |
| `write-spec-a` | A 啟動全新 worker session。 |
| `write-spec-b` | B 啟動全新 worker session。 |
| `review-spec-a` | B 執行一次全新 reviewer 呼叫。 |
| `review-spec-b` | A 執行一次全新 reviewer 呼叫。 |
| `compare-specs-a` | A 啟動全新 worker session。 |
| `compare-specs-b` | B 啟動全新 worker session。 |
| `select-spec` | 沒有 agent 呼叫，由人選擇 owner。 |
| `finalize-spec` | Merge 決策時，O 以全新 session 合併候選；R 的每輪 review 都是全新，O 則續接修正 blocker。非 merge 決策沒有最初的 O 呼叫；R 仍以全新 session review，第一個 blocker 修正才讓 O 啟動全新 session。 |

接下來的 `commit-spec` 又會讓 O 從全新 session 開始。若人選擇 B，後續所有
stage 就是 O=B、R=A；其餘生命週期規則不變。

## 匯入產物

匯入 spec 或 plan 只會移除最初的撰寫呼叫：

- `IMPORT_REVIEW=1` 時 reviewer 仍以全新 session 開始。若發現 blocker，
  artifact owner 的第一次修正呼叫為全新，該 stage 的後續修正再續接。
- `IMPORT_REVIEW=0` 時 import stage 沒有 agent 呼叫。`commit-spec` 或 plan
  commit 仍位於自己的 stage，因此其 worker 呼叫會是全新。

## Workflow resume 與 retry

`RESUME_RUN` 會還原已完成 stage、phase 與 task queue、snapshot、base commit
等持久 workflow 狀態，但每次 CLI 啟動仍會建立全新的空 `AgentSession`。已完成
stage 會跳過；未完成 stage 的第一個 worker 呼叫則從全新 session 開始：

```text
第一次 process：write-code task 1 使用 I session abc -> process 中止
RESUME_RUN：     task 1 維持完成；task 2 使用全新 I session 開始
```

這與同一 workflow process 內重試 worker 呼叫不同。若 adapter 已捕捉到精確
ID，同一 process 的 retry 可以續接該 ID；reviewer attempt 仍然全新。

## 實作位置

- [`agents.py`](../src/adversarial_ai_coding/agents.py) 定義 `AgentSession`、worker
  owner 切換、內建 resume 參數與全新 reviewer 呼叫。
- [`workflow.py`](../src/adversarial_ai_coding/workflow.py) 在 `begin_stage` 清除
  active worker session，並定義預設 stage graph。
- [`phaseflow.py`](../src/adversarial_ai_coding/phaseflow.py) 定義每個 Phased ATDD
  phase 所建立的兩個 stage。
- [`dual_spec.py`](../src/adversarial_ai_coding/dual_spec.py) 定義 Dual Spec stage
  graph 與 owner 選擇。
- [`cli.py`](../src/adversarial_ai_coding/cli.py) 在每個 workflow process 建立新的
  記憶體內 `AgentSession`。
