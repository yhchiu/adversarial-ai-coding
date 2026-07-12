# 強模型規劃、便宜模型實作：把「實作」變成第三個 agent slot

## Context

這個 workflow 的價值分布很不平均：寫 spec、寫 plan、寫 acceptance test、對抗式 review 需要強
模型；而 stage 5 的 checkbox task 實作是照著已審核過的 plan 逐條做，是最機械、呼叫次數最多、
也最燒錢的部分。目標是讓 stage 5 的 per-task 迴圈跑在獨立設定的 agent／模型上，其餘階段不變。

**已拍板的三個決策：**

1. 可換整個 agent，不只是模型（`IMPL_AGENT` / `IMPL_MODEL` / `IMPL_ARGS`）。
2. 生效範圍是整個 per-task 迴圈：`implement-plan-task` + `BUILD_GATE_CMD` 修錯迴圈 + 該 task
   的 commit。跑完所有 task 之後的完整 `GATE_CMD`、branch review 修正、final review 全部回到
   owner agent —— acceptance test 必須轉綠的硬骨頭留給強模型收尾。
3. 命名用 `IMPL_*` 前綴。

### 這份計畫已依 b8f7a36..e05cbe7 重寫

計畫初稿寫完後，session-id resume 那條線落地了（`0ecbcaa` → `e05cbe7`），它改掉了本計畫依賴的
每一個地基：

- **agent 身分從「名稱」改成 `AgentRef(slot, name)`**（`agents.py:45-56`）。模型完全按 slot
  解析（`agent_model`,`agents.py:59-67`），custom agent 的 args 也按 slot（`generic_agent_args`,
  `agents.py:80-85`）。
- **archive 整條改吃 `AgentRef`**（`archive.py:113,122-124,285,304-309`）：metrics.csv 的
  `model` / `model_args` 欄與 `.meta.json` 都是從 ref 現算的。
- **args 改用 `shlex.split`**，並新增保留參數檢查 `_validate_reserved_args`（`agents.py:111-157`）：
  `CODEX_ARGS` 不得含 `--json` / `resume` / `--sandbox`，`AGY_ARGS` 不得含 `--log-file` /
  `--continue` / `--conversation` —— 這些旗標是 session 定址的基礎設施，由 workflow 獨佔。
- **`session.worker_session` 現在存真正的 session ID**（claude session_id / codex thread_id /
  agy conversation UUID），不再是 `last` / `continue` sentinel。

結果是：初稿裡那個「引入 `AgentProfile` 並貫穿 archive」的重構 commit **整個作廢** —— 它要解的
問題（archive 用名字反查 slot 會把 B slot 的模型記成 A slot）已經被 slot 化修掉了。反過來說，
加第三個 agent 現在變得乾淨很多：**把「實作」做成第三個 slot `I`**，archive / metrics / meta
全部自動正確，那些檔案一行都不用改。

## 語意定義

三個變數全部省略時，行為與現在完全相同（`impl_ref()` 直接回傳 owner ref）。

| 變數 | 預設 | 意義 |
|---|---|---|
| `IMPL_AGENT` | owner agent 的指令 | per-task 實作迴圈使用的 CLI。可為 `claude` / `codex` / `agy` 或自訂指令，**允許**與 `AGENT_A` / `AGENT_B` 同名。 |
| `IMPL_MODEL` | 見下方繼承規則 | 實作 slot 的模型。custom agent 沒有模型概念，會被忽略並發警告。 |
| `IMPL_ARGS` | 空 | 實作 slot 的額外 CLI 參數。內建 agent 附加在該指令的 `CLAUDE_ARGS` / `CODEX_ARGS` / `AGY_ARGS` 之後；custom agent 則取代 slot args（custom agent 本來就只吃 slot args）。 |

**模型繼承規則**：`IMPL_MODEL` 沒設時，只有在「實作 slot 的指令與 owner 相同」時才繼承 owner
slot 的 `MODEL_A` / `MODEL_B`；換了指令就不繼承（把 claude 的模型名餵給 codex 是錯的）。這條規則
用 `AgentRef` 上一個新的 `base_slot` 欄位表達，見下。

用法範例：

```bash
# 同一個 CLI,只降模型(最常見)
AGENT_A=claude MODEL_A=opus IMPL_MODEL=sonnet \
  adversarial-ai-coding task.md

# claude 規劃 + codex 便宜實作
AGENT_A=claude MODEL_A=opus \
AGENT_B=codex  MODEL_B=gpt-5.5 \
IMPL_AGENT=codex IMPL_MODEL=gpt-5-codex IMPL_ARGS='-c model_reasoning_effort="low"' \
  adversarial-ai-coding task.md
```

自訂 `IMPL_AGENT` 的指令契約與現有 custom agent 完全相同
（`$IMPL_AGENT $IMPL_ARGS "Read the full workflow prompt from ..."`），模型旗標請放進 `IMPL_ARGS`。

`IMPL_AGENT` 允許與 A 或 B 同名，因為 per-task 迴圈裡只有它自己在跑，不與 reviewer 交錯，
不會踩到 `validate_agents` 那條「同名 custom agent 的 session 語意未知」的限制。

## 設計：第三個 slot

`AgentRef` 現在是 `(slot, name)`，slot 決定模型與 custom args。實作 agent 就是一個新的
slot `"I"`：

```python
# agents.py
@dataclass(frozen=True)
class AgentRef:
    slot: str
    name: str
    # 只有 slot "I" 會用:IMPL_MODEL 沒設時,從這個 slot 繼承模型。
    # 換了指令就留空 —— 不把 A 的模型名餵給不同的 CLI。
    base_slot: str = ""


def impl_ref(owner: AgentRef, settings: Settings) -> AgentRef:
    """Per-task implementation ref: the owner ref unless IMPL_* asks for something else."""
    if not (settings.impl_agent or settings.impl_model or settings.impl_args):
        return owner
    name = settings.impl_agent or owner.name
    base = owner.slot if name == owner.name else ""
    return AgentRef(slot="I", name=name, base_slot=base)
```

`base_slot` 有預設值，現有的 `AgentRef("A", "claude")` 建構與相等性都不受影響。

因為 archive / metrics / meta 已經全部從 ref 解析模型與 args，slot `I` 一旦被
`agent_model` / `resolve_model_args` 認得，metrics.csv 的實作階段列就會自動記成
`IMPL_MODEL` / `IMPL_ARGS` —— **`archive.py` 不用改**。

## 要改的檔案

### `src/adversarial_ai_coding/config.py`
`Settings` 加三個欄位 `impl_agent` / `impl_model` / `impl_args`，`from_env` 用既有的
`persisted()`（env → resume snapshot → 預設）讀，預設空字串。

### `src/adversarial_ai_coding/runstate.py`
`SNAPSHOT_KEYS`（:21）與 `snapshot_values()`（:60）各加三個 key。`load_snapshot` 會拒絕未知 key
（:128），漏掉會讓新 run 存下的 snapshot 無法被讀回；不列入 `IMMUTABLE_KEYS`，所以 resume 時可以
改實作模型。

### `src/adversarial_ai_coding/agents.py`
- `AgentRef` 加 `base_slot: str = ""`；新增 `impl_ref(owner, settings)`（如上）。
- `agent_model`（:59）：slot `"I"` → `settings.impl_model`；沒設且 `base_slot` 非空 → 取該 slot 的
  `model_a` / `model_b`；custom agent 一律 `""`（維持現有規則）。
- `generic_agent_args`（:80）：slot `"I"` → `settings.impl_args`。
- `resolve_model_args`（:70）：內建 agent 仍按指令取 `CLAUDE_ARGS` / `CODEX_ARGS` / `AGY_ARGS`，
  **slot `"I"` 時再附加 `IMPL_ARGS`**。這是 `IMPL_ARGS` 與 `AGENT_A_ARGS` 的刻意分歧：後者只作用於
  custom agent，前者對內建與自訂都作用 —— 因為「便宜階段順便調低 reasoning effort」正是這個功能的
  主要用途之一。
- `_run_generic`（:601）：錯誤訊息目前用 `f"AGENT_{ref.slot}_ARGS"`，slot `I` 會印出不存在的
  `AGENT_I_ARGS`。加一個 slot → 環境變數名的小 mapping（A/B → `AGENT_?_ARGS`，I → `IMPL_ARGS`）。
- `_validate_reserved_args`（:120）：**必須把 `IMPL_ARGS` 納入檢查**。實作 slot 的指令在驗證時
  可能還不確定（`IMPL_AGENT` 沒設時要看 dual-spec 選誰當 owner），所以 `IMPL_ARGS` 一律用
  codex ∪ agy 的保留字集合檢查：`--json` / `resume` / `--sandbox` / `-s` / `sandbox_mode=` /
  `--log-file` / `--continue` / `--conversation`。少了這道，使用者能用
  `IMPL_ARGS='--conversation <id>'` 靜默奪走 session ownership，讓剛落地的 id 定址失效。
  順便走 `_split_cli_args` 讓引號錯誤在啟動時就報錯。
- `validate_agents`（:88）：`IMPL_AGENT` 有設就必須在 PATH 上（沿用
  `Missing required command:<name>` 訊息）。**不要**把它加進同名檢查。
- **`AgentSession` 加 `owner: AgentRef | None = None`，並在 `run_worker`（:615）開頭加守衛**：

  ```python
  if session.owner != ref:
      session.worker_session = ""
      session.owner = ref
  ```

  這是本計畫最關鍵的正確性要求。`worker_session` 現在存的是**真的 session ID**
  （codex thread UUID / agy conversation UUID / claude session id），而 `IMPL_AGENT` 是這個
  codebase 第一個「同一個 stage 內換 agent」的情境 —— 沒有這道守衛，codex 的 thread UUID 會被
  `--resume` 餵給 claude，或 claude 的 session id 被 `--conversation` 餵給 agy。守衛放在
  `run_worker` 而不是迴圈邊界，是為了讓它成為 agents 層的不變式（「一個 worker session 只屬於
  一個 ref」），而不是 stage 5 的特例。

  副作用（可接受，需寫進文件）：實作 slot 與 owner slot 各自持有獨立 session。進入 per-task 迴圈
  時開新 session，離開迴圈跑完整 gate 時強模型也開新 session。prompt 本來就是自足的
  （`prompt_file_instruction` 指向 artifact 檔），不依賴 session context。

### `src/adversarial_ai_coding/workflow.py`
- `write-code` stage（:691-727）：進 while 迴圈前建一次
  `impl = impl_ref(ctx.spec_roles.owner_agent, ctx.settings)`，迴圈內三處全部改用它：
  - `work(ctx, impl, render_prompt(...implement-plan-task...))`
  - `gate_loop_ref(..., do_work=lambda p: work(ctx, impl, p))`
  - `commit_work(ctx, impl, f'Task "{task_line}"')`

  迴圈**之後**的完整 gate、branch review、`commit_if_dirty` 與 `final-review-and-fixes` stage
  全部不動，繼續用 `ctx.spec_roles.owner_agent`。
- `check_protected` 已經吃 ref 並用傳進來的 agent 遞迴呼叫 `work()`，所以 protected-test 修復會
  自動留在實作 agent 上（session 不會錯亂），不用改。
- custom 實作 agent 配 `IMPL_MODEL` 時，在建 ref 處發一次 stderr 警告（`ctx.echo_err`）：模型被
  忽略，請改用 `IMPL_ARGS`。放這裡而不是 cli.py，因為 `IMPL_AGENT` 沒設時 owner 要等 dual-spec
  選完才確定。

### `src/adversarial_ai_coding/cli.py`
啟動設定行（:158-162）在任一 `IMPL_*` 有值時附加 `IMPL=<agent>/<model>`；沒設就完全不印，
不影響現有輸出與既有測試。

### `src/adversarial_ai_coding/archive.py`
metrics / meta / log banner **不用改**（已經是 ref 驅動）。只在 `write_run_metadata`（:214-227）
加三個欄位 `impl_agent` / `impl_model` / `impl_args`（直接寫 settings 的原始字串即可，
不需要 owner slot）。

### 文件
- `README.md` / `README.zh-TW.md`：Configuration 表格加三列；新增
  "Strong model plans, cheap model implements" 用法段落（放在 "Custom Agent Commands" 附近）；
  stage 5 的說明補一句可換 agent／模型；custom agent 契約段落補上 `IMPL_AGENT` 適用同一份契約；
  明講「實作 slot 與規劃 slot 各自持有獨立 session」與 `IMPL_ARGS` 的保留參數限制。
- `docs/python-port-parity.md`：記一行 —— bash 沒有實作 slot 這個概念，是 Python port 新增功能。

## 測試

- **`tests/test_config.py`**：三個新變數的預設值、從 env 讀、從 snapshot 讀。
- **`tests/test_runstate_snapshot.py`**：key set 斷言更新（現有測試會紅，這是預期的）。
- **`tests/test_agents.py`**：
  - `impl_ref`：三個變數全空 → 原封不動回傳 owner ref；只設 `IMPL_MODEL` → slot `I`、名稱同 owner、
    `base_slot` = owner slot；只設 `IMPL_ARGS` → 模型從 `base_slot` 繼承到 `MODEL_A`；
    `IMPL_AGENT` 換了指令 → `base_slot` 為空、不繼承模型；`IMPL_AGENT` 與 owner 同名 → 仍繼承。
  - `agent_model` / `resolve_model_args` 對 slot `I`：內建 → `IMPL_MODEL` + `CODEX_ARGS` + `IMPL_ARGS`；
    custom → 模型 `""`、args 只有 `IMPL_ARGS`。
  - `validate_agents`：`IMPL_AGENT` 不在 PATH → raise；`IMPL_AGENT` 與 `AGENT_B` 同名 → **不** raise。
  - `_validate_reserved_args`：`IMPL_ARGS` 含 `--json` / `resume` / `--sandbox` / `--log-file` /
    `--continue` / `--conversation` → raise；引號不合法 → raise。
  - `_run_generic` 的引號錯誤訊息對 slot `I` 印的是 `IMPL_ARGS`，不是 `AGENT_I_ARGS`。
- **`tests/test_session_resume.py`**（新 commit 建立的檔案，沿用其風格）：
  **ref 換手時 `worker_session` 被清空** —— 用 ref A（codex）跑一次拿到 thread UUID，再用 slot I
  的 ref 跑，斷言 argv 是全新的 `codex exec`（不含 `resume`），且不會把 A 的 UUID 傳出去；換回
  ref A 時同樣重新開始。這是整個功能最重要的迴歸測試。
- **`tests/test_stageflow.py`**：per-task 迴圈的三種呼叫（implement / build-gate 修錯 / commit）
  都用 slot `I` 的 ref；迴圈後的完整 gate 與 branch review 回到 owner ref。
- **`tests/test_archive_io.py`**：slot `I` 的 ref 寫出的 metrics 列與 `.meta.json`，`model` /
  `model_args` 是 `IMPL_MODEL` / `IMPL_ARGS`（驗證「archive 不用改就會對」這個前提）。
- **`tests/test_resume_integration.py`**：新增一個 scenario。假 agent harness 現成好用
  （`_make_wrapper(work, role)` 產生 `--role fake-<role>` 的 wrapper，`fake_agent.py` 把
  `<role> <kind>` 寫進 calls.log），只要多做一個 `fake-impl` wrapper、設 `IMPL_AGENT=<path>`，
  跑完整 workflow 後斷言：`calls("fake-impl implement") == 2`（假 plan 有兩個 checkbox）、
  `calls("fake-worker implement") == 0`、`calls("fake-impl commit") == 2`、
  `calls("fake-worker final-review") == 1`（收尾仍是 owner）。這一條是整個功能的端到端證明。

`tests/e2e/`（真實 agent、marker-gated、燒 quota）維持不動。

## Commits

1. `feat(config): add IMPL_AGENT, IMPL_MODEL and IMPL_ARGS settings`
   — config.py + runstate.py snapshot 持久化 + test_config / test_runstate_snapshot。
2. `feat(agents): add the implementation agent slot`
   — `AgentRef.base_slot`、`impl_ref()`、slot `I` 的模型／args 解析、`_run_generic` 的變數名 mapping、
   `IMPL_ARGS` 的保留參數驗證、`IMPL_AGENT` 的 PATH 驗證 + test_agents。
3. `fix(agents): reset the worker session when the agent ref changes`
   — `AgentSession.owner` 守衛 + test_session_resume。獨立成一個 commit：它本身是無害的強化，且必須
   在 commit 4 之前落地，否則 commit 4 單獨 checkout 時會把 session ID 餵給錯的 CLI。
4. `feat(workflow): run the per-task loop on the implementation slot`
   — write-code 迴圈接上 impl ref、custom agent 的 `IMPL_MODEL` 警告、`write_run_metadata` 的三個
   欄位 + test_stageflow / test_archive_io。
5. `test(resume): cover IMPL_AGENT end to end with a third fake agent`
   — test_resume_integration。
6. `docs: document the implementation agent slot`
   — README + README.zh-TW + python-port-parity + cli.py 的啟動設定行。

## Verification

```bash
# 系統層的 PYTHONHOME/PYTHONPATH(Atrust Py2.7)會壞掉 .venv,跑 uv 前先清
unset PYTHONHOME PYTHONPATH

# 每個 commit 先跑 targeted,再跑全套,綠了才 commit
uv run --locked pytest tests/test_agents.py tests/test_session_resume.py tests/test_stageflow.py -q
uv run --locked pytest -q
```

端到端的人工確認（不燒 quota，用假 agent）：跑 `pytest tests/test_resume_integration.py -k impl` 後
檢查它建立的 repo —— `.workflow/runs/<run-id>/metrics.csv` 裡 `write-code` stage 的實作列，`agent`
欄應該是 `fake-impl`、`model` / `model_args` 欄是 `IMPL_MODEL` / `IMPL_ARGS` 的值；spec / plan /
review 的列仍是 `fake-worker` / `fake-reviewer` 配它們原本的模型。

真實 agent 抽驗（可選，會燒 quota）：在小 repo 上
`AGENT_A=claude MODEL_A=opus IMPL_MODEL=sonnet adversarial-ai-coding "小任務"`，
確認 metrics.csv 的 `write-code` 列 model 是 sonnet、其餘階段是 opus，且 worker 沒有拿 sonnet 的
session id 去 resume opus 的 session。
