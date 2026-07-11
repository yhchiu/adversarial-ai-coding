# codex / agy 改用 session id resume,解除 A/B 同 agent 限制(v2,已納入 review)

## Context

`validate_agents()`(`src/adversarial_ai_coding/agents.py:70-87`)禁止 A/B 兩個 slot 用同一個 agent(claude 除外),因為 codex 用 `exec resume --last`、agy 用 `--continue`,兩者都指向「最近一次 session」;reviewer 開新 session 後,worker 的下一輪 resume 會接錯。

實測(codex-cli 0.144.1 / 本機 agy)確認兩者都能用 id 定址:

- `codex exec --json` 首行輸出 `{"type":"thread.started","thread_id":"<uuid>"}`;`codex exec resume --json -c sandbox_mode="..." <uuid> <prompt>` 可恢復,且 **resume 時仍會再發一次 `thread.started`**(同 id),context 確實續上。`resume` 子命令不吃 `--sandbox`(需用 `-c` 覆寫),flags 要放在 `<SESSION_ID>` 之前。
- agy 無結構化輸出,但 `--log-file <path>` 的 log 內含 `Created conversation <uuid>` / `Print mode: conversation=<uuid>`;`--conversation <id>` 可依 id 恢復。實測 `--log-file` 是 **truncate 開檔**(連跑兩次,檔案變小且只剩新 id)。

改用 id 定址後 reviewer 不再污染 worker,限制可解除。

連帶必須先修一個 blocker:整個 codebase 用「agent 名稱」當身分(`agent_model(name, settings)` 先比對 `agent_a` 再比對 `agent_b`),A/B 同名時 `MODEL_B` 與 slot args 被靜默忽略 —— 而「同一個 CLI、兩個不同模型互審」正是本功能的主要用途。順帶修掉現在 `AGENT_A=claude AGENT_B=claude` 時 `MODEL_B` 失效的既有問題。

目標:`AGENT_A=codex AGENT_B=codex MODEL_A=gpt-5.4 MODEL_B=gpt-5.5-codex` 能正常互審,agy 亦同。

## 不可違反的 invariants

1. **已知 id 只升級、不降級。** 一旦 worker 拿到 uuid,之後某輪沒解析到 id 也**保留原 uuid**,絕不清空、絕不改用全域 resume。
2. **不使用全域 `--last` / `--continue`。** 採 id-only 兩態(`""` = 尚無 session、`<uuid>` = id 定址)。抓不到 id 就開新 session 並印警告 —— sentinel 三態不留(全域 resume 本身對其他 process 有競態,且本機 CLI 都能給 id)。
3. **slot 身分不得在 archive/log 邊界退化成名稱。** 任何要解析 model/args 的 API 都吃 `AgentRef`,序列化時才取 `.name`。
4. **`agent_out`(`ratelimit.py` 唯一的輸入)必須是 decode 後的可讀文字。** 原始 JSONL 另存 artifact。理由:`parse_reset_wait()` 只正規化真實換行,raw JSON 裡的 `\n` 是字面反斜線 n,跨行的 quota 訊息會比對不到。
5. **每個 commit 單獨 checkout 都安全。** validation 只在對應 adapter 完成的同一個 commit 內放行該 agent。

## Session state machine(codex 與 agy 共用)

| 呼叫前 | 本輪 argv | 解析結果 | 呼叫後 |
|---|---|---|---|
| `""` | fresh | 有 id | `<uuid>` |
| `""` | fresh | 無 id | `""` + 警告(下輪仍 fresh) |
| `<uuid>` | resume by id | 有 id | 解析到的 id |
| `<uuid>` | resume by id | 無 id | **保留原 `<uuid>`** |

agy 只在 fresh call 從 log 建立新 id;resume call 缺 match 時保留傳入的 uuid。
retry(`ratelimit.agent_call` 重跑 `attempt()`)沿用同一張表:第一次失敗前若已拿到 id,retry 必須 resume 該 id。

## Commit 1 — `refactor(agents): resolve model and args by slot, not agent name`

**`agents.py`**
- 新增 `@dataclass(frozen=True) class AgentRef: slot: str; name: str` 與 `agent_ref(slot, settings)`。
- `agent_model(ref, settings)`:非內建回 `""`;內建依 `ref.slot` 取 `model_a` / `model_b`。
- `resolve_model_args(ref, settings)` / `generic_agent_args(ref, settings)`:內建仍依 `ref.name` 取 `claude_args` / `codex_args` / `agy_args`(維持「按命令共用」語意);custom 依 `ref.slot` 取 slot args。
- `run_worker(ref, ...)` / `run_reviewer(ref, ...)` 及所有 `_worker_*` / `_reviewer_*` / `_run_generic` 改吃 `AgentRef`。
- 同時補既有 bug:`_run_captured` / `_run_streaming` 加 `stdin=subprocess.DEVNULL`(實測 codex 在 stdin 為 pipe 時會等 stdin 輸入)。

**`archive.py` —— review C3 的重點,全部改吃 `AgentRef | None`**(`None` 代表 workflow 自己產生的 artifact,不要偽造 AgentRef):
`write_meta`(:109-130,`agent_model(agent, …)` 的源頭)、`archive_snapshot`(:132-147)、`archive_text`(:149-162)、`archive_agent_attempt`(:178-197)、`log_section`(:245-268)、`metric`(:270-300)、`archive_git_state`(:302-347)、run metadata(:199-243)。序列化時寫 `ref.name`。

**呼叫點**(slot 在呼叫點都已知)
- `workflow.py`:`work(ctx, ref, instruction)`;`SpecRoles.owner_agent` / `reviewer_agent` 改存 `AgentRef`;`WorkflowContext` 加 `ref(slot)` helper。**人類可讀輸出必須顯式 `.name`**:`workflow.py:450-451`(PR body)。call sites:`259,320,542-628,682-775`。
- `review.py`:`review_loop(ctx, reviewer_ref, worker_ref, …)`、`run_review(ctx, ref, scope)`(**不是** review_once,原計畫寫錯)、`compose_review_prompt(ref, …)`(內部 `agent == "claude"` 改判 `ref.name`)。
- `dual_spec.py`:`agent_for_slot()` 回 `AgentRef`;`:219`(decision file)與 `:331-334`(restore 訊息)必須用 `.name`。
- 用 `rg` 重新盤點所有 call site,不要沿用手寫行號。

**測試**
- `test_agents.py`:`AgentRef` 版的 model/args 解析。
- 同名 slot regression:run metadata 同時保有不同的 `model_a` / `model_b`;B slot 的 artifact meta、attempt meta、log banner、metrics 都用 `MODEL_B`;**角色反轉**(B 當 worker、A 當 reviewer,acceptance 階段)同樣正確。
- PR body 與 dual-spec decision file 只印 agent 名稱,不得出現 `AgentRef(...)` repr(`test_stageflow.py` 等字串斷言一併更新)。
- 本 commit **不動** codex/agy validation。

## Commit 2 — `feat(codex): resume workers by captured thread id`

**`agents.py`**
- `_run_codex_json(argv, io) -> (rc, rendered_text, thread_id)`,雙 channel:
  - **raw**:每一行原始輸出(含併入的 stderr)寫進 `io.raw_out`(新欄位,per-attempt artifact,見下)。
  - **rendered**:`item.completed` 的 `agent_message` → 純文字;`error` / `turn.failed` → decode 後的訊息文字;非 JSON 行 → 原樣;其他/未知事件 → decode 後寫入 rendered(**不 echo 到終端**,避免 telemetry 洪流)。rendered 同時寫入 `io.agent_out` 並回傳成 `AgentResult.text`。
  - `thread.started` → `thread_id`。
- argv(已實測):
  - fresh:`codex exec --json --sandbox workspace-write <model_args> <prompt>`
  - resume:`codex exec resume --json -c sandbox_mode="workspace-write" <uuid> <prompt>`(flags 在 SESSION_ID 之前;`resume` 不吃 `--sandbox`)
- session 更新:照上面的 state machine(已知 uuid 不降級)。
- `_reviewer_codex`:同樣走 `--json` 雙 channel,永不 resume。
- `AgentIO` 加 `raw_out: Path`;`workflow.WorkflowContext.agent_io()` 提供路徑,`archive_agent_attempt` 一併把 raw 存進 attempt artifact(parser 壞掉時才有得查)。
- `validate_agents`:**只放行同名 codex**(claude 本來就放行);同名 agy 與同名 custom agent 仍拒絕。
- preflight:`CODEX_ARGS` 內出現 `--json` / `resume` / `-c sandbox_mode` 等會奪走控制權的 flag 時報錯。
- 成本:`turn.completed.usage` 只有 token 數、沒有 USD,`last_cost` 維持空字串(不假造成本)。

**測試**
- argv:fresh / resume 兩種形狀;prompt 永遠是最後一個參數。
- state machine 四條路徑各一測(含「已有 uuid、本輪無 id → 保留 uuid」)。
- retry:第一輪 rate-limit 失敗但已拿到 id → 第二次 attempt 必須 resume 該 id。
- **quota 迴歸(最大風險點)**:用一份真實 `codex exec --json` 去敏輸出當 fixture(含 stderr 純文字行、malformed JSON、未知事件、含 escaped newline 的 quota 訊息),斷言 `is_rate_limited(agent_out)` 與 `parse_reset_wait(agent_out)` 仍命中。
- **offline 整合測試**:在暫時 PATH 放一支叫 `codex` 的 fake executable(沿用 `tests/fake_agent.py` 的模式),記錄 argv、吐 JSONL;跑「worker fresh → reviewer fresh → worker resume by exact id」序列,驗證 worker 沒接到 reviewer 的 session、raw/rendered 分流、A/B 同名但模型不同時 argv 與所有 archive metadata 都按 slot。
- `validate_agents`:同名 codex 放行;同名 agy 此時仍 raise。

## Commit 3 — `feat(agy): resume workers by captured conversation id`

**`agents.py`**
- log 生命週期:**每個 attempt 一個唯一 log 路徑**(而非固定的 `last-agy.log`),呼叫前 `unlink(missing_ok=True)`,只解析本次產生的檔案,結束後把 log 存進 attempt artifact。(agy 實測是 truncate 開檔,但 crash 殘檔與未來行為改變仍需這層保險。)
- `_parse_agy_conversation_id(log)`:嚴格 UUID regex(`[0-9a-f]{8}-[0-9a-f]{4}-...`,不是寬鬆的 `[0-9a-fA-F-]{36}`),大小寫不敏感。
- `_worker_agy`:一律 `--log-file <attempt log>`;有 uuid → `--conversation <uuid>`,否則 fresh;只在 fresh call 建立新 id,resume 缺 match 時保留原 uuid。
- preflight:`AGY_ARGS` 含 `--log-file` / `--continue` / `--conversation` 時報錯(使用者 args 接在我們的 flag 之後會覆蓋,靜默奪走 session ownership)。
- `validate_agents`:此時才放行同名 agy。

**測試**:stale log、同檔多個 id、partial UUID、大小寫、檔案不存在、resume log 無 match(保留原 id)、retry 後的 log 生命週期;argv 形狀;fake-`agy` 的 offline 整合測試(同 Commit 2 模式);同名 agy 放行。

## Commit 4 — `docs: document same-agent slots and session guarantees`

- `README.md` / `README.zh-TW.md`:同 slot 用法與範例(`AGENT_A=codex AGENT_B=codex MODEL_A=… MODEL_B=…`);`MODEL_A` / `MODEL_B` 改為按 slot 解析;id 抓不到時的精確行為(開新 session + 警告,永不使用全域 resume);agy conversation id 來自 log 解析、agy 升版可能失效;`CODEX_ARGS` / `AGY_ARGS` 的保留 flag;custom agent 仍不支援自動 resume、同名仍拒絕。
- `docs/python-port-parity.md`:記錄與 bash 的刻意分歧(bash 用 `--last` / `--continue` 且禁止同 agent)。

## Verification

每個 commit 都先跑 targeted tests,再跑全套,綠燈才 commit:

```bash
unset PYTHONHOME PYTHONPATH   # 系統級 Atrust Py2.7 變數會壞掉 .venv
uv run --locked pytest tests/test_agents.py tests/test_archive_io.py tests/test_archive_git.py \
  tests/test_work.py tests/test_review.py tests/test_dual_spec.py tests/test_stageflow.py -q
uv run --locked pytest -q
```

**真實 CLI smoke test(rollout gate,單元測試全是 mock,證明不了 CLI 真實行為)**

1. codex 同 slot:`AGENT_A=codex AGENT_B=codex MODEL_A=… MODEL_B=…` 跑一個小 task。驗法不是看 `.meta.json`(那只是 settings 推導值),而是看 attempt artifact 裡的實際 argv/CLI trace;context 串線用「第一輪要 worker 記住一個隨機 token → reviewer 插入中間 → 第二輪要 worker 複述該 token」來驗。
2. agy 同 slot:確認 attempt log 有 `conversation=<uuid>`、第二輪 argv 帶 `--conversation`。此步驟需 `--dangerously-skip-permissions`,由你本人執行(我在此環境無權跑該旗標)。
3. 非 TTY:用 pipe 餵 stdin 跑一次,確認加了 `stdin=DEVNULL` 之後 codex 不再等 stdin。

## 風險

- codex `--json` 事件 schema 變動 → 未知事件仍 decode 落檔,quota 偵測與 debug 不受影響;最壞只是 id 抓不到 → 退回 fresh session + 警告。
- agy log 訊息格式變動 → 同上,退回 fresh + 警告(絕不接錯 session)。
- `AgentRef` 重構面積不小(archive/log/metrics 全線),但都是機械式改動,且有既有測試護欄。
