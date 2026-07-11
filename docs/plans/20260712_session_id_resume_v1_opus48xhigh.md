# codex / agy 改用 session id resume,解除 A/B 同 agent 限制

## Context

目前 `validate_agents()`(`src/adversarial_ai_coding/agents.py:70-87`)禁止 A/B 兩個 slot 使用同一個 agent(claude 除外),理由是 codex 用 `exec resume --last`、agy 用 `--continue`,兩者都指向「最近一次 session」。當 reviewer 用同一個 CLI 開新 session 時,worker 之後的 resume 會接到 reviewer 的 session,context 錯亂。

但兩個 CLI 其實都支援用 id 定址 session(已實測):

- `codex exec --json` 第一行輸出 `{"type":"thread.started","thread_id":"019f530c-..."}`;`codex exec resume <SESSION_ID>` 接受該 UUID,`resume` 子命令同樣支援 `--json`。
- agy 沒有結構化輸出,但 `--log-file <path>` 指定的 log 內含 `Created conversation <uuid>` 與 `Print mode: conversation=<uuid>`;`agy --conversation <id>` 可依 id 恢復。

改用 id 定址後,reviewer 開新 session 不再影響 worker,限制即可解除。

連帶要處理一個 blocker:整個 codebase 用「agent 名稱」當身分(`agent_model(name, settings)` 先比對 `agent_a`、再比對 `agent_b`)。一旦 A/B 同名,`MODEL_B` 與 slot args 會被靜默忽略 —— 而「同一個 CLI、兩個不同模型互審」正是本功能最主要的用途。因此先做 slot-aware 重構,再改 adapter。這也順手修掉現在 `AGENT_A=claude AGENT_B=claude` 時 `MODEL_B` 失效的既有問題。

預期成果:`AGENT_A=codex AGENT_B=codex MODEL_A=gpt-5.4 MODEL_B=gpt-5.5-codex` 能正常互審,agy 亦同。

## 設計決策

1. **身分改用 slot(A/B),不用名稱。** 新增 `AgentRef(slot, name)`,`agent_model` / `resolve_model_args` 改吃 `AgentRef`。`CLAUDE_ARGS` / `CODEX_ARGS` / `AGY_ARGS` 維持「按命令」共用的語意(不變);`AGENT_A_ARGS` / `AGENT_B_ARGS` 維持只作用於 custom agent(不擴大到內建,避免既有設定行為突變)。
2. **codex 改用 `--json` 並自行渲染輸出。** JSONL 逐行解析:`thread.started` 取 id;`item.completed` 的 `agent_message` 印出文字;**任何無法辨識的事件型別原樣落一行 JSON**。這條規則是關鍵 —— `ratelimit.py` 的 quota 偵測是對 `last-agent-output.txt` 做 regex,原樣落檔可保證 quota / rate-limit 訊息不會在渲染中被吃掉。stderr 仍併入 stdout,純文字行直接通過。
3. **session id 抓不到時的 fallback 要保守。** `AgentSession.worker_session` 可能是 `""`(無 session)、`<uuid>`(id 定址)或 sentinel `last` / `continue`(舊行為)。抓不到 id 時:A≠B → 退回 `--last` / `--continue`(維持現狀);**A==B → 不用 sentinel,改開新 session 並印警告**,寧可失去 context 也絕不接錯別人的 session。
4. **reviewer 永遠開新 session**(現狀),不需要 id;有了 id 定址,reviewer 的新 session 不再污染 worker。
5. agy 的 id 來源是 glog 訊息格式,agy 升版可能改字串 —— 靠決策 3 的 fallback 兜底,並在 README 標註此風險。

## Commit 1 — `refactor(agents): resolve model and args by slot, not agent name`

**`src/adversarial_ai_coding/agents.py`**
- 新增 `@dataclass(frozen=True) class AgentRef: slot: str; name: str` 與 `agent_ref(slot, settings)`(A → `settings.agent_a`,B → `settings.agent_b`)。
- `agent_model(ref, settings)`:非內建回 `""`;內建依 `ref.slot` 取 `model_a` / `model_b`。
- `generic_agent_args(ref, settings)` / `resolve_model_args(ref, settings)`:依 `ref.slot` 取 slot args;內建仍依 `ref.name` 取 `claude_args` / `codex_args` / `agy_args`。
- `run_worker(ref, ...)` / `run_reviewer(ref, ...)` 與各 `_worker_*` / `_reviewer_*` / `_run_generic` 改吃 `AgentRef`(dispatch 用 `ref.name`,取模型用 `ref.slot`)。

**呼叫點**(把名稱換成 ref,slot 在呼叫點都已知)
- `workflow.py`:`work(ctx, ref, instruction)`;`WorkflowContext` 加 `ref(slot)` helper;`SpecRoles` 已有 `owner_slot` / `reviewer_slot`,把 `owner_agent` / `reviewer_agent` 換成 `AgentRef`。`work()` 內 log / archive 用 `ref.name`。call sites:`workflow.py:259,320,542,578,608,682,700,720`。
- `review.py`:`review_loop(ctx, reviewer_ref, worker_ref, scope, gate_cmd)`、`review_once`、`compose_review_prompt(ref, ...)`(內部 `agent == "claude"` 改判 `ref.name`)。call sites:`review.py:203,217`。
- `dual_spec.py`:`agent_for_slot()` 回 `AgentRef`;call sites `362,378,390,409,420,426`。
- `archive.py`:`archive_agent_attempt` / run meta / summary(`archive.py:122-123,214-218,235-239,261-262,296-297`)改吃 `AgentRef`,meta 內仍寫 `agent_a` / `agent_b` 名稱字串,只是模型與 args 改由 slot 解析。

**測試**
- `tests/test_agents.py`:改用 `AgentRef`;新增「`AGENT_A=claude AGENT_B=claude MODEL_A/MODEL_B` 各自生效」的迴歸測試(這是本 commit 修掉的既有 bug)。
- `tests/test_work.py`、`test_review.py`、`test_dual_spec.py`、`test_archive_*.py`:更新簽名。行為應完全不變(A≠B 時 slot 與名稱一一對應)。

## Commit 2 — `feat(codex): resume by session id captured from the JSON event stream`

**`src/adversarial_ai_coding/agents.py`**
- 新增 `_run_codex_json(argv, io) -> tuple[int, str, str]`(rc、渲染後文字、thread_id):
  - 逐行讀 stdout(stderr 已併入),嘗試 `json.loads`;非 JSON 行原樣輸出。
  - `thread.started` → 記下 `thread_id`(不必印)。
  - `item.completed` 且 `item.type == "agent_message"` → 印 `item.text`。
  - 其他型別(含 `error` / `turn.failed` / 未知)→ 原樣輸出該行 compact JSON。
  - 與 `_run_streaming` 一樣:echo 到終端、寫入 `io.agent_out`。
- `_worker_codex`:
  - 全新:`codex exec --json --sandbox workspace-write <model_args> <prompt>`。
  - 有 uuid:`codex exec resume --json <uuid> -c sandbox_mode="workspace-write" <model_args> <prompt>`(`resume` 無 `--sandbox`,沿用現有 `-c` 覆寫)。
  - sentinel `last`(僅 A≠B 且先前沒抓到 id):維持 `resume --json --last ...`。
  - 收尾:抓到 id 就寫入 `session.worker_session`;沒抓到且 `settings.agent_a == settings.agent_b` → 保持 `""` 並 echo 一行警告;否則寫 sentinel `last`。
- `_reviewer_codex`:同樣改走 `--json`(輸出風格一致、確保 quota 文字落檔),不 resume。
- `validate_agents`:同名檢查改成只擋 custom agent(內建 claude / codex / agy 皆放行)。

**測試** `tests/test_agents.py`
- argv:首呼叫含 `exec --json --sandbox workspace-write`;第二次呼叫為 `resume --json <uuid>` 且帶 `sandbox_mode="workspace-write"`;prompt 仍是最後一個參數。
- 事件解析:`thread.started` 寫進 `session.worker_session`;`agent_message` 渲染成純文字;未知事件原樣落檔。
- quota 迴歸:餵一段含 `error` 事件、內文是 "You've hit your usage limit ... try again at ..." 的 JSONL,斷言 `ratelimit.is_rate_limited(agent_out)` 與 `parse_reset_wait()` 仍成立(這條是 `--json` 改動最大的風險點)。
- fallback:沒有 `thread.started` 時,A≠B → sentinel `last`;A==B → 維持 `""` 且輸出警告。
- `validate_agents`:`AGENT_A=codex AGENT_B=codex` 不再 raise;custom agent 同名仍 raise。

## Commit 3 — `feat(agy): resume by conversation id parsed from the run log`

**`src/adversarial_ai_coding/agents.py`**
- `AgentIO` 加欄位 `agent_log: Path`(`workflow.WorkflowContext.agent_io()` 給 `wf / "last-agy.log"`,每次呼叫覆寫)。
- 新增 `_parse_agy_conversation_id(log_path) -> str`:regex `conversation[= ]([0-9a-fA-F-]{36})`,取第一個命中;檔案不存在或無命中回 `""`。
- `_worker_agy`:一律加 `--log-file <io.agent_log>`;有 uuid → `--conversation <uuid>`;sentinel `continue`(僅 A≠B)→ `--continue`;跑完解析 log 取 id,套用與 codex 相同的 fallback 規則(A==B 且無 id → 開新 session + 警告)。
- `_reviewer_agy`:維持不 resume(可不帶 `--log-file`)。

**測試** `tests/test_agents.py`
- 用 fixture log(含 `Created conversation <uuid>` 那兩行)驗 `_parse_agy_conversation_id`。
- argv:首呼叫含 `--log-file`、不含 `--continue`;第二次呼叫含 `--conversation <uuid>`、不含 `--continue`。
- fallback:log 無 id 時 A≠B → `--continue`;A==B → 全新呼叫 + 警告。
- `validate_agents`:`AGENT_A=agy AGENT_B=agy` 不再 raise。

## Commit 4 — `docs: document same-agent A/B slots and id-based session resume`

- `README.md` / `README.zh-TW.md`:
  - 新增「同一個 agent 放在兩個 slot」用法與範例(`AGENT_A=codex AGENT_B=codex MODEL_A=... MODEL_B=...`)。
  - 說明 `MODEL_A` / `MODEL_B` 現在按 slot 解析(A/B 同名時兩者都生效)。
  - agy 的 conversation id 來自 log 解析,agy 升版可能失效;失效時 A==B 會退回「每輪新 session」並印警告。
  - custom agent 仍不支援自動 session resume,A/B 同名仍被拒(維持現有 wrapper 建議)。
- `docs/python-port-parity.md`:記錄與 bash 的刻意分歧(bash 用 `--last` / `--continue` 且禁止同 agent;Python port 改為 id 定址並放行內建 agent)。

## Verification

**自動化**
```powershell
uv run --locked pytest            # 全套(注意先清掉系統級 PYTHONHOME/PYTHONPATH)
uv run --locked pytest tests/test_agents.py -k "codex or agy or slot or model"
```
E2E(`tests/e2e/`)走 fake custom agent,不受影響,但仍需綠燈確認沒有回歸。

**手動 smoke test(必要 —— 上述單元測試都是 mock,真實 CLI 行為只有實跑才算數)**

1. codex id 定址:在暫存 repo 跑一次真的兩階段 worker 呼叫,確認第二次 argv 走 `resume --json <uuid>`,且 agent 記得第一次的 context;終端輸出仍可讀。
2. codex 同 slot:`AGENT_A=codex AGENT_B=codex MODEL_A=gpt-5.4 MODEL_B=gpt-5.5-codex` 跑一個小 task,確認 worker 與 reviewer 各自使用自己的模型(查 `.workflow/runs/<id>/` 的 meta 與 attempt log)、且 worker 跨輪 context 未被 reviewer 打斷。
3. agy id 定址:`AGENT_A=agy AGENT_B=agy` 跑一次,確認 `last-agy.log` 有 `conversation=<uuid>`、第二輪 argv 帶 `--conversation`。此步驟需要 `--dangerously-skip-permissions`,由你本人執行(我在此環境無權限跑該旗標)。
4. quota 路徑:人工把一段 codex `--json` 的 rate-limit 輸出寫進 `.workflow/last-agent-output.txt`,確認 `is_rate_limited()` / `parse_reset_wait()` 仍能命中(或直接靠 Commit 2 的單元測試涵蓋)。

## 風險

- **codex `--json` 的事件 schema 若變動**:未知事件原樣落檔的規則讓輸出與 quota 偵測不致崩壞,最壞情況只是可讀性變差、id 抓不到(退回 fallback)。
- **agy log 格式變動**:同上,靠 fallback 兜底,並在 README 明講。
- **子命令旗標位置**:`codex exec resume --json <ID>` 的 `--json` 放在 `resume` 之後(`codex exec resume --help` 有列出);實作時先用一次真實呼叫確認,再寫死。
