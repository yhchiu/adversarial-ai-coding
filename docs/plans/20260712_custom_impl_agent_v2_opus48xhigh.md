# 強模型規劃、便宜模型實作：把「實作」變成第三個 agent slot

## Context

這個 workflow 的價值分布很不平均：寫 spec、寫 plan、寫 acceptance test、對抗式 review 需要強
模型；而 stage 5 的 checkbox task 實作是照著已審核過的 plan 逐條做，是最機械、呼叫次數最多、
也最燒錢的部分。目標是讓 stage 5 的 per-task 迴圈跑在獨立設定的 agent／模型上，其餘階段不變。

**已拍板的決策：**

1. 可換整個 agent，不只是模型（`IMPL_AGENT` / `IMPL_MODEL` / `IMPL_ARGS`）。
2. 生效範圍是整個 per-task 迴圈：`implement-plan-task` + `BUILD_GATE_CMD` 修錯迴圈 +
   protected-test 修復 + 該 task 的 commit。跑完所有 task 之後的完整 `GATE_CMD`、branch review
   修正、final review 全部回到 owner agent —— acceptance test 必須轉綠的硬骨頭留給強模型收尾。
3. 命名用 `IMPL_*` 前綴。
4. 順手修掉 `CLAUDE_ARGS` 沒有 token 驗證的既有漏洞（見 Commit 1）。
5. `IMPL_*` 的 resume 語意不破例：可覆寫、不可用空字串清空，寫進文件。

### 這份計畫已依 `e05cbe7` 基線與 review 回饋重寫

初稿寫完後 session-id resume 落地（`0ecbcaa`→`e05cbe7`），改掉了本計畫依賴的地基：agent 身分
變成 `AgentRef(slot, name)`、模型按 slot 解析、archive 整條吃 `AgentRef`、args 改 `shlex.split`
並新增保留參數驗證、`session.worker_session` 改存真正的 session ID。初稿那個「引入
`AgentProfile` 並貫穿 archive」的重構 commit 因此作廢 —— 它要解的問題已經被 slot 化修掉。

隨後的 review（`docs/plans/20260712_custom_impl_agent_v1_review_gpt56sol-max.md`）抓到三個真的
會出事的問題，全部已驗證屬實並併入本版：

- **`resolve_model_args()` 根本不在內建 agent 的 argv 路徑上。** 三個內建 adapter 各自直接 split
  settings 欄位（`agents.py:326,436,509`），`resolve_model_args` 的消費者**只有 `archive.py`**。
  初稿的做法會讓 `IMPL_ARGS` 永遠進不了 CLI，卻在 metrics.csv 和 `.meta.json` 裡顯示已生效 ——
  一個會自我確認的假證據。**argv 與 metadata 必須共用同一個 args 來源。**
- **同名 custom 實作指令仍有未知 session 競態。** 初稿的理由（迴圈內沒有 reviewer 交錯）只覆蓋
  我們自己的 session state；`_run_generic` 本來就不傳任何 session 旗標，危險完全來自 wrapper 的
  內部狀態 —— 這正是現有「A/B 不得同名 custom」規則（`agents.py:95-108`）拒絕去猜的東西。
- **保留參數不能用固定聯集驗證。** 已用 `claude --help` 實測：`-c/--continue`、`-r/--resume`、
  `--session-id`、`--fork-session`、`--output-format` 全部存在。`-c` 在 codex 是 config、在 claude
  是 continue —— 同一個 token 兩種語意，固定聯集在原理上就不可能對。

## 語意定義

三個變數全部省略時，行為與現在完全相同（`impl_ref()` 原封不動回傳 owner ref）。

| 變數 | 預設 | 意義 |
|---|---|---|
| `IMPL_AGENT` | owner agent 的指令 | per-task 迴圈使用的 CLI。內建 agent 可與 `AGENT_A` / `AGENT_B` 同名；custom 指令**必須**與 A/B 不同名（見下）。 |
| `IMPL_MODEL` | 見繼承規則 | 實作 slot 的模型。custom agent 沒有模型概念，會被忽略並在 run log 留下警告。 |
| `IMPL_ARGS` | 空 | 實作 slot 的額外 CLI 參數。內建 agent 附加在該指令的 `CLAUDE_ARGS` / `CODEX_ARGS` / `AGY_ARGS` 之後；custom agent 則取代 slot args。 |

**模型繼承**：`IMPL_MODEL` 沒設時，只有「實作指令與 owner 相同」才繼承 owner slot 的
`MODEL_A` / `MODEL_B`；換了指令就不繼承（把 claude 的模型名餵給 codex 是錯的）。這條用
`AgentRef.base_slot` 表達，並在 `agent_model()` 內複驗，不靠呼叫端自律。

**custom 實作指令必須與 A/B 不同名**：workflow 無法得知 custom wrapper 是否會隱式沿用上一個
session（現有規則正是為此禁止 A/B 同名 custom）。實作 slot 是第三個角色，且與 owner／reviewer
在同一個 `write-code` stage 內先後呼叫同一個 wrapper，風險相同。因此：內建 agent 可跨 slot 同名
（有 exact-ID 隔離），custom 指令不行 —— 請用第二個 wrapper 名稱。這也表示 **owner 是 custom
agent 時，必須明確設 `IMPL_AGENT` 指向另一個 wrapper 才能用實作 slot**。

**resume 語意**：`IMPL_*` 是 persisted key，`persisted()` 是 `env or snap or default`
（`config.py:83-84`，且 `test_config.py:91` 已鎖死此行為）。所以 resume 時可以用非空值覆寫，
但**不能用空字串清空**（與 `CLAUDE_ARGS`、`MODEL_A` 等所有既有 key 一致）。要清空就直接編輯
`.workflow/state/<run>/settings.json`（snapshot 是純資料，載入時會驗證）。文件必須寫明。

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

## 設計

### 第三個 slot

```python
# agents.py
@dataclass(frozen=True)
class AgentRef:
    slot: str
    name: str
    # slot "I" 專用:IMPL_MODEL 沒設時從這個 slot 繼承模型。
    # 換了指令就留空 —— 不把 A 的模型名餵給不同的 CLI。
    base_slot: str = ""


def impl_ref(owner: AgentRef, settings: Settings) -> AgentRef:
    if not (settings.impl_agent or settings.impl_model or settings.impl_args):
        return owner                      # 零行為變化
    name = settings.impl_agent or owner.name
    base = owner.slot if name == owner.name else ""
    return AgentRef(slot="I", name=name, base_slot=base)
```

`base_slot` 有預設值，現有的 `AgentRef("A", "claude")` 建構與相等性不受影響。
`agent_model()` 繼承前必須**複驗** `ref.name == agent_ref(ref.base_slot, settings).name`，
否則手工造出的 `AgentRef("I", "codex", base_slot="A")` 會把 claude 的模型名餵給 codex。

### 單一 args 來源（修掉 review C1）

argv 路徑與 metadata 路徑必須從同一份來源展開，否則會漂移成「metrics 說生效、CLI 沒收到」：

```python
_ARGS_VAR = {"A": "AGENT_A_ARGS", "B": "AGENT_B_ARGS", "I": "IMPL_ARGS"}

def _arg_sources(ref, settings) -> list[tuple[str, str]]:
    """(環境變數名, 原始字串),依 argv 順序。"""
    sources = []
    if ref.name == "claude":
        sources.append(("CLAUDE_ARGS", settings.claude_args))
    elif ref.name == "codex":
        sources.append(("CODEX_ARGS", settings.codex_args))
    elif ref.name == "agy":
        sources.append(("AGY_ARGS", settings.agy_args))
    else:                                  # custom:slot args(slot I 即 IMPL_ARGS)
        sources.append((_ARGS_VAR[ref.slot], generic_agent_args(ref, settings)))
    if ref.slot == "I" and is_builtin_agent(ref.name):
        sources.append(("IMPL_ARGS", settings.impl_args))
    return [(var, raw) for var, raw in sources if raw]


def agent_args(ref, settings) -> list[str]:        # argv 路徑
    return [t for var, raw in _arg_sources(ref, settings)
              for t in _split_cli_args(var, raw)]  # 各自 split,保留正確的變數名錯誤訊息


def resolve_model_args(ref, settings) -> str:      # metadata / log banner 路徑
    return " ".join(raw for _, raw in _arg_sources(ref, settings))
```

三個內建 adapter（`_claude_common_args`:321、`_codex_model_args`:431、`_agy_model_args`:504）
與 `_run_generic`（:601）全部改呼叫 `agent_args(ref, settings)`，不再各自 split settings 欄位。
custom + slot I 只有一個來源（`generic_agent_args` 回 `impl_args`），不會重複。

### 保留參數依 adapter 驗證（修掉 review C3）

每個內建 CLI 各自擁有一組「workflow 獨佔」的旗標，`_validate_reserved_args`（:120）改成
依 adapter 套規則：

| 變數 | 套用規則 |
|---|---|
| `CLAUDE_ARGS` | claude 規則（**新增,見 Commit 1**） |
| `CODEX_ARGS` | codex 規則（現有） |
| `AGY_ARGS` | agy 規則（現有） |
| `AGENT_A_ARGS` / `AGENT_B_ARGS` | 只檢查引號（custom 契約是自由參數，現有行為） |
| `IMPL_ARGS` | 一律檢查引號；token 規則依**實際會餵到的 adapter**：`IMPL_AGENT` 有設 → 該 adapter（custom → 不套 token 規則）；沒設 → owner 候選（`AGENT_A`，`DUAL_SPEC=1` 時再加 `AGENT_B`），對其中的內建候選各套一次 |

- claude 規則（已用 `claude --help` 實測存在）：`-c` / `--continue`、`-r` / `--resume`、
  `--session-id`、`--fork-session`、`--output-format`、`--json-schema`。
- codex 規則（現有）：`--json`、`resume`、`--sandbox` / `-s`、`-c sandbox_mode=`。
- agy 規則（現有）：`--log-file`、`--continue`、`--conversation`。

`impl_ref()` 解析出實際 ref 後再跑一次同一組檢查（dual-spec 兩個候選在啟動時都已檢查過，
這道理論上不會觸發，只是防止 startup 候選邏輯與 runtime 解析漂移）。

### session 換手守衛（review M1 修正過的語意）

`AgentSession` 加 `owner: AgentRef | None = None`，`run_worker`（:615）開頭：

```python
if session.owner != ref:
    session.worker_session = ""
    session.owner = ref
```

`worker_session` 現在存的是真的 session ID（codex thread UUID / agy conversation UUID /
claude session id），而 `IMPL_AGENT` 是這個 codebase 第一個「同一 stage 內換 agent」的情境 ——
沒有守衛，codex 的 thread UUID 會被 `--resume` 餵給 claude。守衛放在 `run_worker` 而不是迴圈
邊界，是要讓它成為 agents 層的不變式，而不是 stage 5 的特例。`begin_stage` 同時把
`worker_session` 清空並把 `owner` 重設為 `None`，讓 stage 邊界的不變式一眼可讀。

**正確語意是「換手即丟棄」，不是「兩個 slot 各自保存 session」**：全程只有一個
`worker_session`。進入 per-task 迴圈時開新 session、迴圈內累積；切回 owner 跑完整 gate 時丟棄
並重新開始。prompt 本來就自足（`prompt_file_instruction` 指向 artifact 檔），不依賴 session
context。

**測試遷移地雷**：現有測試直接建 `AgentSession(worker_session="keep-me")` 再餵給 `run_worker`
（`test_agents.py:299,331,342,359,389` 等，用 `grep -n "AgentSession(worker_session=" tests/`
找齊），守衛會把它們全部清空 → 這些測試會以很難懂的方式紅掉。必須同步補上 `owner=<該 ref>`。

## 要改的檔案

- **`config.py`**：`Settings` 加 `impl_agent` / `impl_model` / `impl_args`，`from_env` 用既有的
  `persisted()` 讀，預設空字串。
- **`runstate.py`**：`SNAPSHOT_KEYS`(:21) 與 `snapshot_values()`(:60) 各加三個 key；不列入
  `IMMUTABLE_KEYS`。
- **`agents.py`**：`AgentRef.base_slot`；`impl_ref()`；`agent_model()` 支援 slot `I` 並複驗
  `base_slot`；`_arg_sources()` / `agent_args()` / `resolve_model_args()`；三個內建 adapter 與
  `_run_generic` 改用 `agent_args()`；`_validate_reserved_args` 改成依 adapter 套規則並涵蓋
  `CLAUDE_ARGS` 與 `IMPL_ARGS`；`validate_agents` 檢查 `IMPL_AGENT` 在 PATH 上、並拒絕與 A/B
  同名的 custom 實作指令；`AgentSession.owner` + `run_worker` 守衛。
- **`workflow.py`**：`write-code` stage(:691-727) 進迴圈前建一次
  `impl = impl_ref(ctx.spec_roles.owner_agent, ctx.settings)`，迴圈內三處（implement、
  build-gate 的 `do_work` closure、`commit_work`）改用 `impl`；迴圈後的完整 gate、branch review、
  `commit_if_dirty` 與 `final-review-and-fixes` 全部維持 owner。`check_protected` 已經沿用傳入的
  ref 遞迴呼叫 `work()`，protected-test 修復會自動留在實作 agent 上，不用改。
  進迴圈前用 `ctx.log()` 印一行 **resolved** 實作設定（agent / model / args）——
  這是 durable 的（進 run.log），且發生在 dual-spec 選完 owner 之後；custom agent 配 `IMPL_MODEL`
  的警告也走 `ctx.log()`。
- **`archive.py`**：`write_meta` 的 payload 加 `agent_slot`；`METRICS_HEADER` 尾端追加
  `agent_slot` 欄（**追加在最後**，才不會動到 `metrics_summary` 的既有欄位索引）。這讓「實作 slot
  真的有跑」可以被稽核，即使它與 owner 同指令同模型。
- **`cli.py`**：**不動**。啟動時 dual-spec 還沒選 owner，印不出正確的 effective 值（review H3）；
  改由 workflow 在解析完之後印。
- **文件**：`README.md` / `README.zh-TW.md` 加設定表格三列、"Strong model plans, cheap model
  implements" 用法段落、stage 5 說明、custom 指令必須不同名的限制、`IMPL_ARGS` 的保留旗標政策、
  「換手即丟棄」的 session 語意、resume 可覆寫不可清空與清空的操作方式。
  `docs/python-port-parity.md` 記一行：實作 slot 是 Python port 新增、bash 沒有的概念。

## Commits

每個 commit 先跑自己的 targeted tests，再跑完整 suite，綠了才 commit。

1. **`fix(agents): reserve Claude's session and output flags`**
   把 `_validate_reserved_args` 改成依 adapter 套規則，補上 claude 的保留旗標。這是既有漏洞：
   `CLAUDE_ARGS='--continue'` 今天就能架空剛落地的 exact-ID resume。獨立 commit，本身就有價值，
   也是 `IMPL_ARGS` 依 adapter 驗證的前提。
   targeted：`test_agents.py`
2. **`feat(config): add IMPL_AGENT, IMPL_MODEL and IMPL_ARGS settings`**
   targeted：`test_config.py`、`test_runstate_snapshot.py`
3. **`refactor(agents): resolve agent args from a single source`**
   `_arg_sources()` / `agent_args()`，三個內建 adapter 與 `_run_generic` 改用它，
   `resolve_model_args()` 改由同一來源渲染。純重構，行為不變，但它是 C1 的結構性修復。
   targeted：`test_agents.py`、`test_archive_io.py`
4. **`feat(agents): add the implementation agent slot`**
   `AgentRef.base_slot`、`impl_ref()`、`agent_model()` 的 slot `I` 與繼承複驗、
   `IMPL_ARGS` 進 `_arg_sources`、依 adapter 驗證 `IMPL_ARGS`、`IMPL_AGENT` 的 PATH 檢查與
   custom 同名拒絕。
   targeted：`test_agents.py`
5. **`fix(agents): isolate worker sessions by agent ref`**
   `AgentSession.owner` + `run_worker` 守衛 + `begin_stage` 重設 + 既有測試補 `owner=`。
   必須在 commit 6 之前落地，否則 commit 6 單獨 checkout 時會把 session ID 餵給錯的 CLI。
   targeted：`test_agents.py`、`test_session_resume.py`、`test_stageflow.py`
6. **`feat(workflow): route plan tasks through the implementation slot`**
   write-code 迴圈接上 impl ref、resolved 設定 log 行、custom `IMPL_MODEL` 警告、
   `agent_slot` 進 meta 與 metrics。
   targeted：`test_stageflow.py`、`test_archive_io.py`、`test_e2e.py::test_fixture_baseline`
7. **`test: cover the implementation slot end to end`**
   offline fake-builtin（codex）integration + custom fake-agent routing + mid-queue resume 換模型。
   targeted：`test_resume_integration.py` 與新增的 adapter integration
8. **`docs: document the implementation agent slot`**
   README、README.zh-TW、python-port-parity。

## 測試

### 單元

- **`test_config.py`**：三個新變數的預設／env／snapshot 讀取。
- **`test_runstate_snapshot.py`**：key set 斷言更新（既有測試會紅，是預期的）。
- **`test_agents.py`**
  - `impl_ref`：三個變數全空 → 原封不動回傳 owner ref；只設 `IMPL_MODEL` → slot `I`、
    `base_slot` = owner slot；只設 `IMPL_ARGS` → 模型從 `base_slot` 繼承到 `MODEL_A`；
    `IMPL_AGENT` 換了指令 → `base_slot` 空、不繼承；`IMPL_AGENT` 與 owner 同名 → 仍繼承。
  - **malformed ref 不繼承**：手工造 `AgentRef("I", "codex", base_slot="A")` 而 A 是 claude →
    `agent_model` 回 `""`（M2）。
  - **實際 argv 斷言（C1 的核心防線）**：claude / codex / agy 各自的 fresh 與 resume 呼叫，
    argv 依序含 effective model → 該 CLI 的 base args → `IMPL_ARGS`；custom path 同理。
    不能只斷言 `resolve_model_args()` 的回傳字串。
  - metadata 的 rendered args 與實際 argv 來自同一來源（改一邊、另一邊跟著動）。
  - 保留參數：`CLAUDE_ARGS='--continue'` / `--output-format text` → raise；
    `CODEX_ARGS='-c model_reasoning_effort=low'` → 放行；`IMPL_ARGS` 依 `IMPL_AGENT` 指向的
    adapter 判定（custom agent 用到碰巧同名的 `resume` / `--json` **不得**被誤擋）。
  - `validate_agents`：`IMPL_AGENT` 不在 PATH → raise；`IMPL_AGENT=codex` 與 `AGENT_B=codex`
    同名 → 放行；custom `IMPL_AGENT` 與 `AGENT_A` 同名 → raise；三個變數全空 + custom A/B →
    不得誤擋。
- **`test_session_resume.py`**：ref 換手時 `worker_session` 被清空 —— ref A（codex）跑一次拿到
  thread UUID，換 slot `I` 的 ref 跑，斷言 argv 是全新的 `codex exec`（不含 `resume`、不含舊
  UUID）；換回 A 同樣重新開始。同一 ref 的 rate-limit retry 仍用 exact ID resume。
- **`test_stageflow.py`**：**主動讓 gate / review 失敗**，逼出三個 repair closure，分別斷言
  build-gate repair 用 slot `I`、full-gate repair 與 branch-review repair 用 owner；
  protected-test 修復留在 `I`。
- **`test_archive_io.py`**：slot `I` 的 ref 寫出的 metrics 列與 `.meta.json`，`model` /
  `model_args` / `agent_slot` 正確。

### 整合（離線，不燒 quota）

- **offline fake built-in（新）**：在暫存目錄放一個名為 `codex` 的假執行檔並塞進 PATH
  （`_resolve_argv0` 走 `shutil.which`，所以 shim 會被撿到），讓它記錄 argv 並吐 JSONL。
  跑真正的 `run_worker`，證明 `IMPL_MODEL` / `IMPL_ARGS` **真的進了 argv**、且 I → owner 換手
  不帶舊 thread ID。這是 C1 與 session 守衛的端到端證明，custom fake wrapper 做不到。
- **`test_resume_integration.py`**：
  - custom routing scenario：多做一個 `fake-impl` wrapper（harness 現成：`_make_wrapper` 產生
    `--role fake-<role>` 的 wrapper，`fake_agent.py` 把 `<role> <kind>` 寫進 calls.log），
    設 `IMPL_AGENT=<path>`，斷言 `fake-impl implement`×2、`fake-worker implement`×0、
    `fake-impl commit`×2、`fake-worker final-review`×1。
  - mid-queue resume：第一個 task 後中止，resume 時換掉 `IMPL_MODEL`，斷言只有剩下的 task 用新
    設定、已完成的 task 不重跑。

`tests/e2e/`（真實 agent、marker-gated）維持不動，但 `METRICS_HEADER` 加欄後要更新它的 header 斷言
（`test_e2e.py:176-187`）。

## Verification

```bash
# 系統級 PYTHONHOME/PYTHONPATH(Atrust Py2.7)會壞掉 .venv,跑 uv 前先清
unset PYTHONHOME PYTHONPATH

uv run --locked pytest <該 commit 的 targeted tests> -q
uv run --locked pytest -q
```

人工端到端（不燒 quota）：跑 offline fake-builtin integration 後，檢查它記錄的 argv 檔 ——
實作階段的 `codex exec` 必須帶 `-c model="<IMPL_MODEL>"` 與 `IMPL_ARGS` 的 token，且第一次呼叫
不含 `resume`、不含 owner 的 thread ID。再檢查 `.workflow/runs/<id>/metrics.csv`：`write-code`
stage 的實作列 `agent_slot=I`、`model` / `model_args` 為 `IMPL_*` 的值；spec / plan / review 的列
仍是 owner / reviewer 的 slot 與模型。

真實 agent 抽驗（可選，會燒 quota）：
`AGENT_A=claude MODEL_A=opus IMPL_MODEL=sonnet adversarial-ai-coding "小任務"`，
確認 metrics.csv 的 `write-code` 實作列是 sonnet、其餘階段是 opus，且沒有把 sonnet 的 session id
拿去 resume opus 的 session。
