# Custom Implementation Agent v3 核心 RC 收尾 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 若執行環境與使用者明確允許逐任務委派，也可改用 `superpowers:subagent-driven-development`；不得自行展開無上限的 review/fix 迴圈。

**Goal:** 在保留既有 custom implementation agent 核心功能的前提下，只補齊兩個已確認的 correctness 缺口，產出一條可審、可測、可交付的 core RC 分支。

**Architecture:** 從已完成核心功能與文件的 `0ad4ce7` 建立乾淨 RC，而不是延續含 4,046 行 hardening 的 `cc2df4d`。RC 只加入 adapter-aware 參數驗證補強，以及 process-local 的 protected-control fail-closed 邊界；OS-backed lock、handle-relative mutation、quarantine/restore 與完整 topology hardening 全部留待另一份 hardening 計畫。

**Tech Stack:** Python 3.12+、標準函式庫、pytest、uv、Git；Windows 為本次必要驗證平台，Git Bash 為 repository/development 命令的優先 shell。

## Global Constraints

- 本計畫採用已核定的「策略一：核心功能與 hardening 拆分」。
- 核心 RC branch 固定為 `feat/custom-impl-agent-core-rc`，起點固定為 `0ad4ce71375dc5c5c54c0d7b18ac4d16ae1b53de`。
- 舊 branch `feat/custom-impl-agent` 必須保持在 `cc2df4d96172f2ecb9edecad5acfbbc8c895b7c8`，不得 reset、rebase、amend 或追加 commit。
- 不得 cherry-pick `cc2df4d` 整個 commit；它可以作為唯讀參考，但不得把 `runstate.py`、`cli.py`、`trusted_paths.py`、lock、restore、quarantine 或 topology hardening 帶進 core RC。
- 本輪 production scope 只有兩項：extended built-in args validation，以及 process-local protected-control fail-closed。
- 嚴格遵守 TDD：先加入會失敗的 regression tests，確認 RED，再做最小 production change，確認 GREEN。
- 每個 implementation task 的 focused tests 與完整 local Windows suite 通過後，立即建立自己的 commit，才可進下一個 task。
- Commit 必須使用 Conventional Commit、簡單英文 subject、詳細英文 body，且不得加入 `Co-Authored-By`。
- 執行 uv 前清除 `PYTHONHOME` / `PYTHONPATH`；`UV_CACHE_DIR` 與 `GOCACHE` 必須位於 workspace 內且被忽略。新 worktree 不得先把 cache 建在尚不存在的 `.venv/` 裡；本計畫統一使用 `.pytest_cache/`。
- repository 沒有 Git remote。本輪以 local Windows full suite 作為必要 gate；Ubuntu CI 不列為 blocker，但交付說明必須明載未執行 Ubuntu CI。
- 真實 agent E2E 會消耗 quota，執行前必須再次取得使用者明確同意。使用者若 waiver，仍可 offline-only closure，但交付說明必須逐字記錄 waiver。
- 不自動 merge、push、刪除 worktree 或刪除舊 branch。完成時使用 `superpowers:finishing-a-development-branch` 提供選項。

---

## 1. 決策摘要：為什麼從 `0ad4ce7` 收尾

`0ad4ce7` 已包含原始計畫的八個可獨立理解、已測試且有文件的核心 commits。其後的 `cc2df4d` 原本是一次 final-review fix wave，但同時擴張成跨 run state、filesystem topology、restore/quarantine 與 directory lock 的安全架構；最新審查證明它仍有同一類 compare-then-act 缺口。繼續在該 commit 上疊加相鄰 `lstat` 檢查不會解決最終 syscall 與 pathname replacement 之間的競態。

因此，本計畫不把「hardening 還沒完成」誤判成「implementation slot 核心功能沒完成」。正確做法是：

1. 保留 `0ad4ce7` 的完整 implementation-slot 功能。
2. 從首次 final review 中抽出兩個會直接破壞核心契約的 bounded correctness fixes。
3. 把其餘安全強化留在舊 branch／後續 hardening backlog，不讓 core RC 無限擴張。

## 2. Repository 與 branch 現況（2026-07-14）

### 2.1 工作區

| 用途 | 路徑 | Branch / HEAD | 狀態與限制 |
|---|---|---|---|
| 主 checkout | `C:\Project\adversarial-ai-coding` | `main@ee2ae5b80a42e5f672925c8cc2ceee3a005b7092` | 建立 RC 時為 clean；不在這裡實作 |
| 舊功能／hardening worktree | `C:\Project\adversarial-ai-coding\.worktrees\custom-impl-agent` | `feat/custom-impl-agent@cc2df4d96172f2ecb9edecad5acfbbc8c895b7c8` | 唯讀保存，不得改動 |
| 核心 RC worktree | `C:\Project\adversarial-ai-coding\.worktrees\custom-impl-agent-core-rc` | `feat/custom-impl-agent-core-rc`，由 `0ad4ce7` 建立 | 本文件與後續兩個 fixes 的唯一工作區 |

`.worktrees/` 已由 root `.gitignore` 忽略。建立 RC 前沒有同名 branch、目錄或目標文件。

### 2.2 已完成的八個核心 commits

以下 commits 都位於 `main..0ad4ce7`，順序不可改：

1. `4c869ba` — `fix(agents): reserve workflow-owned flags in built-in agent args`
2. `03176f8` — `feat(config): add IMPL_AGENT, IMPL_MODEL and IMPL_ARGS settings`
3. `a4c66b9` — `refactor(agents): resolve agent args from a single source`
4. `ef74c4d` — `feat(agents): add the implementation agent slot`
5. `6fd7eca` — `fix(agents): isolate worker sessions by agent ref`
6. `09e2f32` — `feat(workflow): route plan tasks through the implementation slot`
7. `9cd7b6c` — `test: cover the implementation slot end to end`
8. `0ad4ce7` — `docs: document the implementation agent slot`

它們已完成的核心能力如下：

- `IMPL_AGENT`、`IMPL_MODEL`、`IMPL_ARGS` 的 config、snapshot 與 resume 語意。
- argv 與 metadata 共用單一 args source，避免「archive 說已套用、CLI 實際沒收到」。
- slot I 的 model inheritance、custom wrapper 同名限制與 adapter-specific args 規則。
- `AgentRef` 換手時丟棄 active worker session，避免把一個 agent 的 exact session ID 餵給另一個 agent／slot。
- 每個 plan task 的 implement、build-gate repair、protected-test repair、task commit 走 slot I。
- 完整 gate、branch review/fixes、final review/fixes 回到 owner。
- `agent_slot`、requested/resolved implementation settings、metrics、bilingual docs 與 offline integrations。

目前沒有已知的 implementation-slot routing、session isolation、metadata 或 resume 核心缺口。

### 2.3 不採用的第九個 commit

`cc2df4d` — `fix(workflow): harden implementation safety boundaries`

- 相對 `0ad4ce7`：17 files、4,046 insertions、203 deletions。
- 大量改動集中在 `runstate.py`、`workflow.py`，並新增 `trusted_paths.py` 與 topology tests。
- 最新完整 offline evidence：`624 passed, 4 skipped, 1 deselected`；四個 skip 都是 Windows `WinError 1314` 無 symlink privilege，唯一 deselection 是 live-agent E2E。
- 測試綠不代表其安全主張成立；第八次 review 以 deterministic syscall seam 仍重現 Critical issues。

## 3. 已知問題分類

### 3.1 本次必修：核心 correctness

#### A. Codex／built-in args validation 漏洞

`src/adversarial_ai_coding/agents.py::_validate_builtin_arg_tokens()` 目前只辨識 `-m value` 與 `-m=value`，沒有辨識 `-mMODEL`；`-sVALUE`、`-cVALUE` 也未被正確解析。Codex 的以下 aliases 也仍可由 `CODEX_ARGS` 或 Codex-targeted `IMPL_ARGS` 注入：

- `--dangerously-bypass-approvals-and-sandbox`
- `--yolo`
- `--ephemeral`

前兩者可關閉 workflow 預期的 sandbox，`--ephemeral` 破壞 exact-ID continuation 所需的 persistence，attached model form 可讓實際模型與 archive metadata 分歧。這是 implementation slot 成本歸因與 session 契約的一部分，不能延後。

#### B. Protected-test enforcement fail-open

目前 `src/adversarial_ai_coding/gitops.py::protected_violations()` 在 `git diff` 失敗時回傳 `[]`；`workflow.py::check_protected()` 又每次重新讀 worker 可寫的 `.workflow/protected-tests.txt` 與 `.workflow/protected-base.sha`。因此 slot I 或後續 owner worker 可以清空、刪除或改寫控制檔，讓 workflow 把竄改當成 clean。

原始計畫明確把 protected-test repair 納入 slot I per-task loop；所以「worker 不能重新定義 protected paths/base」是核心 correctness，不只是額外 hardening。

### 3.2 本次明確延後：hardening backlog

以下是 `cc2df4d` 第八次 review 的真實 findings，但不屬於 core RC：

1. authoritative state/control restore 與 quarantine cleanup 在 identity check 後仍以 pathname 執行 `replace` / `rmtree`，可對 replacement hierarchy 寫入或刪除。
2. directory lock 在 identity verify 後以 pathname `rmdir`，可能刪掉較新的合法 owner lock；需要 OS-backed held lock／stable handle，而不是更多相鄰檢查。
3. fresh `RunState.create()` 在取得 lock 後若 initial `task.txt` 或 ledger write 失敗，CLI 尚未拿到 state object，可能遺留無 owner lock。
4. 若未來重新採用 topology hardening，stable read、UTF-8/I/O translation 與 final-operation deterministic tests 必須一併設計。

Core RC 不宣稱可抵禦同 UID 的 concurrent/background actor 在 pathname syscall 間替換 hierarchy。這些 finding 必須保留在交付風險說明，不能因為不擋 core RC 就說不存在。

## 4. Core RC 的明確 threat model

本計畫的 protected-control 邊界只承諾：

- workflow process 在 acceptance stage 結束後，把兩個控制檔的 exact bytes、解析後 paths 與 base 保存在 immutable process-local snapshot。
- 之後每次 active worker call 前、以及 worker + archive + metric + protected repair 整段完成／失敗後，重新確認兩個 direct entries 仍是 regular files 且 exact bytes 不變。
- active process 內的 worker 無法藉由修改、刪除、清空、改成 directory/symlink 或換掉 base/list 來關閉保護。
- `git diff` 或 control read/decode 失敗一律停止 workflow，不得回傳 clean。
- process 啟動／resume 時，磁碟上的 ledger 與 control files 視為使用者已信任的起始狀態；snapshot 只在該 process 內有效。

本計畫不承諾：

- 防禦同 UID concurrent/background process 在 `lstat` 與 `read_bytes`／write syscall 之間做 TOCTOU replacement。
- 自動 restore、quarantine、unlink/rmtree、跨 process trust persistence 或 OS-level mutual exclusion。
- 把 control snapshot 寫入 worker 可寫的 run state 再稱為 trust anchor。

這個限制是刻意的產品切分，不可在實作途中悄悄擴張。

## 5. File map 與責任邊界

| File | 本輪責任 | 不得順手加入的內容 |
|---|---|---|
| `src/adversarial_ai_coding/agents.py` | attached short parsing、Codex aliases、config key 精確判定 | adapter 規則聯集、custom args validation、argv 重構 |
| `tests/test_agents.py` | table-driven RED/GREEN for `CODEX_ARGS` 與 Codex-targeted `IMPL_ARGS` | live agent invocation |
| `README.md` / `README.zh-TW.md` | reserved args 與 manual protected-test recovery 對齊 | hardening 保證或 OS-lock 宣稱 |
| `src/adversarial_ai_coding/gitops.py` | `protected_violations()` 改吃 trusted paths，`git diff` 透過 `git_out()` fail closed | filesystem restore／topology API |
| `src/adversarial_ai_coding/workflow.py` | immutable process-local snapshot、safe activation、worker pre/post boundary、typed abort | quarantine、auto restore、lock、runstate rewrite |
| `tests/test_gitops.py` | invalid base／diff failure fail-closed | topology tests |
| `tests/test_work.py` | control tampering、empty list active、exception chaining、same-agent repair | OS-race simulation |
| `tests/test_stageflow.py` | acceptance 後／resume skip 後 activation，既有 slot-I repair routing 不退化 | full CLI resume architecture |
| `tests/e2e/test_e2e.py` | 適配 `protected_violations(paths, base, cwd)` 新介面；offline fixture 保持綠 | 預設執行 live E2E |
| `tests/test_documentation.py`（若不存在則新增） | 鎖定雙語 aliases 與 English recovery 關鍵語意 | 逐字綁死整段 README 文案 |

## 6. Handoff 與基線證據

建立本文件的 session 已完成：

- 從 `0ad4ce7` 建立 `feat/custom-impl-agent-core-rc` 與獨立 worktree。
- `uv sync --frozen` 成功；第一次 sandbox run 因不能連 PyPI 失敗，經明確授權後安裝 lockfile 指定套件。
- 一開始把 `UV_CACHE_DIR` 放進尚未建立的 `.venv/`，uv 先建立 cache parent，導致它被判成 invalid venv；該本次生成、僅含 cache 的 `.venv` 經路徑驗證後清除，之後固定改用 `.pytest_cache/.codex-uv-cache`。
- Go fixture 的預設 user cache 位於 sandbox 外而失敗；改用 `.pytest_cache/go-build` 後，`tests/e2e/test_e2e.py::test_fixture_baseline` 為 `1 passed`。
- full suite 收集 `458 selected, 1 deselected`，在 300 秒外部 timeout 時約 95% 皆為 pass；依 collection 尾端補跑 `test_runstate_snapshot.py`、`test_session_resume.py`、`test_stageflow.py`、`test_work.py` 為 `48 passed`。未觀察到 test failure，但這不是一個完整結束的 full-suite command，所以接手 session 仍須執行下列完整 baseline。

接手後第一個必要 gate：

```bash
cd /c/Project/adversarial-ai-coding/.worktrees/custom-impl-agent-core-rc
unset PYTHONHOME PYTHONPATH
export UV_CACHE_DIR="$PWD/.pytest_cache/.codex-uv-cache"
export GOCACHE="$PWD/.pytest_cache/go-build"
export GOTELEMETRY=off
uv sync --frozen
uv run --locked pytest -q -p no:cacheprovider
```

預期：458 selected tests 全部 PASS、1 個 live-agent E2E deselected。不要使用 300 秒硬 timeout；依歷史 Windows 執行時間保留至少 900 秒。若 baseline 有真正 failure，停止 implementation，先用 `superpowers:systematic-debugging` 判斷是 baseline regression 或環境問題。

---

### Task 0: 驗證 handoff branch 與 scope guard

**Files:**

- Read: `docs/plans/20260712_custom_impl_agent_v3_closure_gpt56sol-max.md`
- Read: `docs/plans/20260712_custom_impl_agent_v3_opus48xhigh.md`
- No production changes

**Interfaces:**

- Consumes: `main@ee2ae5b...`、core base `0ad4ce7...`、preserved branch `cc2df4d...`
- Produces: 一個已確認可開始 Task 1 的 clean worktree

- [ ] **Step 1: 確認 branch、HEAD、舊 branch 與 worktree**

```bash
git branch --show-current
git status --short
git merge-base --is-ancestor 0ad4ce7 HEAD
git rev-parse feat/custom-impl-agent
git worktree list --porcelain
```

Expected:

- current branch 是 `feat/custom-impl-agent-core-rc`。
- status clean。
- merge-base command exit 0。
- preserved branch 仍解析為 `cc2df4d96172f2ecb9edecad5acfbbc8c895b7c8`。

- [ ] **Step 2: 跑第 6 節的完整 baseline**

Expected: command 完整結束且沒有 failed tests。若只因 Windows symlink privilege skip，記下 exact node IDs；不得把新的 skip 當成通過。

- [ ] **Step 3: 確認目前 `0ad4ce7..HEAD` 只有本移交文件 commit**

```bash
git log --oneline --reverse 0ad4ce7..HEAD
git diff --stat 0ad4ce7..HEAD
```

Expected: 只有 `docs: add custom implementation agent closure plan`，production code 尚未變動。

---

### Task 1: 補齊 attached args 與 Codex safety aliases

**Files:**

- Modify: `src/adversarial_ai_coding/agents.py`（目前 `_matches_option` 約 line 191、`_validate_builtin_arg_tokens` 約 line 202）
- Modify: `tests/test_agents.py`（reserved-argument tests 約 line 387-603）
- Modify: `README.md`（Built-in session continuity 約 line 514-523）
- Modify: `README.zh-TW.md`（內建 session／args 規則約 line 320-321）
- Create or Modify: `tests/test_documentation.py`

**Interfaces:**

- Consumes: `_matches_option(token: str, option: str) -> bool`、`_validate_builtin_arg_tokens(variable: str, adapter: str, tokens: list[str]) -> None`
- Produces: `_matches_short_option(token: str, option: str) -> bool`
- Produces: `_option_value(tokens: list[str], index: int, short: str, long: str) -> str`
- Preserves: custom args 原樣通過；每個 adapter 只套自己的規則；`model_reasoning_effort` 必須放行

- [ ] **Step 1: 先加入 attached short 與 Codex alias 的 failing tests**

把以下測試併入 `tests/test_agents.py` 現有 reserved-argument 區塊；沿用檔案既有的 `make()` 與 `validate_agents()`：

```python
@pytest.mark.parametrize(
    "value",
    [
        "--dangerously-bypass-approvals-and-sandbox",
        "--dangerously-bypass-approvals-and-sandbox=true",
        "--yolo",
        "--yolo=true",
        "--ephemeral",
        "--ephemeral=true",
        "-sworkspace-write",
        "-cmodel=gpt-5",
        "-c model = gpt-5",
        "-csandbox_mode=workspace-write",
        "-c sandbox_mode = workspace-write",
    ],
)
def test_validate_agents_rejects_extended_codex_workflow_owned_args(value):
    settings = make({"CODEX_ARGS": value})

    with pytest.raises(SettingsError, match="CODEX_ARGS"):
        validate_agents(settings, which=lambda name: "C:/fake/" + name)


@pytest.mark.parametrize("value", ["-mgpt-5", "-m=gpt-5"])
@pytest.mark.parametrize(
    ("key", "agent_env"),
    [
        ("CLAUDE_ARGS", {}),
        ("CODEX_ARGS", {}),
        ("AGY_ARGS", {"AGENT_A": "agy"}),
    ],
)
def test_validate_agents_rejects_attached_builtin_model_args(
    key, agent_env, value
):
    settings = make({**agent_env, key: value})

    with pytest.raises(SettingsError) as exc_info:
        validate_agents(settings, which=lambda name: "C:/fake/" + name)

    assert str(exc_info.value) == (
        f"{key} cannot set the model; "
        "use MODEL_A / MODEL_B / IMPL_MODEL instead"
    )


@pytest.mark.parametrize(
    "value",
    [
        "--yolo",
        "--ephemeral",
        "-sworkspace-write",
        "-cmodel=gpt-5",
        "-csandbox_mode=workspace-write",
        "-mgpt-5",
    ],
)
def test_validate_agents_rejects_extended_codex_impl_args(value):
    settings = make({"IMPL_AGENT": "codex", "IMPL_ARGS": value})

    with pytest.raises(SettingsError, match="IMPL_ARGS"):
        validate_agents(settings, which=lambda name: "C:/fake/" + name)


@pytest.mark.parametrize(
    "value",
    [
        "-cmodel_reasoning_effort=low",
        "-c model_reasoning_effort = low",
        "--config 'model_reasoning_effort = low'",
    ],
)
def test_validate_agents_allows_spaced_or_attached_reasoning_config(value):
    settings = make({"CODEX_ARGS": value})

    validate_agents(settings, which=lambda name: "C:/fake/" + name)


def test_validate_agents_keeps_extended_codex_rules_adapter_specific():
    settings = make({"IMPL_AGENT": "claude", "IMPL_ARGS": "--yolo --ephemeral"})

    validate_agents(settings, which=lambda name: "C:/fake/" + name)


def test_validate_agents_keeps_custom_impl_args_unmodified():
    settings = make(
        {
            "IMPL_AGENT": "impl-wrapper",
            "IMPL_ARGS": "-mgpt-5 -snone -cmodel=x --yolo --ephemeral",
        }
    )

    validate_agents(settings, which=lambda name: "C:/fake/" + name)
```

- [ ] **Step 2: 跑 RED，確認 failures 正好對應現有漏網形式**

```bash
uv run --locked pytest tests/test_agents.py -q -p no:cacheprovider
```

Expected: 新增的 attached／alias tests FAIL；既有 adapter-specific、custom pass-through 與 reasoning config tests 仍 PASS。若既有測試也壞，先修正 test data，不得改 production code 來迎合錯誤測試。

- [ ] **Step 3: 實作最小 token helpers 與 Codex 規則**

在 `_matches_option()` 後加入以下 helpers：

```python
def _matches_short_option(token: str, option: str) -> bool:
    return token == option or (
        token.startswith(option) and len(token) > len(option)
    )


def _option_value(
    tokens: list[str], index: int, short: str, long: str
) -> str:
    token = tokens[index]
    if token in {short, long}:
        return tokens[index + 1] if index + 1 < len(tokens) else ""
    if token.startswith(f"{long}="):
        return token.removeprefix(f"{long}=")
    if token.startswith(short) and token != short:
        return token.removeprefix(short).removeprefix("=")
    return ""
```

把 `_validate_builtin_arg_tokens()` 的 model 判斷改成：

```python
if _matches_option(token, "--model") or _matches_short_option(token, "-m"):
    raise _model_conflict(variable)
```

Codex block 必須使用下列等價邏輯；不可把 aliases 加到所有 adapters 的聯集：

```python
if adapter == "codex":
    if (
        _matches_option(token, "--json")
        or token == "resume"
        or _matches_option(token, "--sandbox")
        or _matches_short_option(token, "-s")
        or any(
            _matches_option(token, option)
            for option in {
                "--dangerously-bypass-approvals-and-sandbox",
                "--yolo",
                "--ephemeral",
            }
        )
    ):
        raise SettingsError(
            f"{variable} cannot contain session-control argument:{token}"
        )

    value = _option_value(tokens, index, "-c", "--config")
    key = value.split("=", 1)[0].strip()
    if key == "model":
        raise _model_conflict(variable)
    if key == "sandbox_mode":
        raise SettingsError(
            f"{variable} cannot override sandbox_mode; the workflow owns it"
        )
```

重點：`split("=", 1)` 後只比對完整、trimmed key；不得用 `startswith("model")`，否則會誤擋 `model_reasoning_effort`。

- [ ] **Step 4: 跑 GREEN 與既有 argv/session focused tests**

```bash
uv run --locked pytest tests/test_agents.py tests/test_session_resume.py -q -p no:cacheprovider
```

Expected: 全部 PASS。確認 `test_fake_codex_impl_model_args_and_handoffs_use_real_argv` 仍證明實際 argv 與 session handoff。

- [ ] **Step 5: 同步更新雙語文件與精簡 documentation regression**

`README.md` 的 Codex reserved paragraph 必須明列三個 aliases，並說明 `-mMODEL`、`-sVALUE` 與 attached `-cVALUE` 也會依相同規則解析。`README.zh-TW.md` 表達相同語意。不得宣稱 custom args 受限。

若 `tests/test_documentation.py` 不存在，新增以下精簡測試；若已存在，合併而不要建立重複 anchors：

```python
from pathlib import Path


ROOT = Path(__file__).parents[1]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_codex_reserved_aliases_are_documented_bilingually():
    for readme in (_read("README.md"), _read("README.zh-TW.md")):
        assert "--dangerously-bypass-approvals-and-sandbox" in readme
        assert "--yolo" in readme
        assert "--ephemeral" in readme
        assert "-mMODEL" in readme
```

- [ ] **Step 6: 跑 Task 1 完整 gate**

```bash
uv run --locked pytest tests/test_agents.py tests/test_session_resume.py tests/test_documentation.py -q -p no:cacheprovider
uv run --locked pytest -q -p no:cacheprovider
git diff --check
```

Expected: focused 與 full suite PASS；live-agent E2E 維持 deselected；`git diff --check` 無輸出。

- [ ] **Step 7: Commit Task 1**

```bash
git add src/adversarial_ai_coding/agents.py tests/test_agents.py \
  tests/test_documentation.py README.md README.zh-TW.md
git commit \
  -m "fix(agents): validate attached implementation arguments" \
  -m "Recognize attached short values for workflow-owned model, sandbox, and Codex config options." \
  -m "Reject Codex sandbox-bypass and ephemeral aliases for CODEX_ARGS and Codex-targeted IMPL_ARGS while preserving adapter-specific and custom argument behavior." \
  -m "Document the extended reserved-option contract in both READMEs and cover it with regression tests."
```

Expected: commit only contains Task 1 files；無 `Co-Authored-By`。

---

### Task 2: 建立 process-local protected-control fail-closed 邊界

**Files:**

- Modify: `src/adversarial_ai_coding/gitops.py`（`protected_violations` 約 line 53）
- Modify: `src/adversarial_ai_coding/workflow.py`（`WorkflowContext`、`work`、`check_protected`、acceptance stage）
- Modify: `tests/test_gitops.py`
- Modify: `tests/test_work.py`
- Modify: `tests/test_stageflow.py`
- Modify: `tests/e2e/test_e2e.py`
- Modify: `README.md`
- Modify: `README.zh-TW.md`
- Modify: `tests/test_documentation.py`

**Interfaces:**

- Produces: immutable `ProtectedControlsSnapshot`
- Produces: `WorkflowContext.protected_controls: ProtectedControlsSnapshot | None`
- Produces: `_activate_protected_controls(ctx: WorkflowContext) -> None`
- Produces: `_verify_protected_controls(ctx: WorkflowContext) -> None`
- Changes: `protected_violations(protected: Collection[str], base: str, cwd: Path) -> list[str]`
- Preserves: `check_protected(ctx, agent)` 的同 agent repair recursion；slot I protected repair routing 不變

- [ ] **Step 1: 先把 git diff fail-open 測試改成 RED**

將 `tests/test_gitops.py::test_protected_violations_lifecycle` 改為直接傳 trusted path set，而不是傳 control file：

```python
def test_protected_violations_lifecycle(new_repo):
    (new_repo / "acc_test.go").write_text("func TestAcc\n", encoding="utf-8")
    git(new_repo, "add", "-A")
    git(new_repo, "commit", "-qm", "tests")
    base = head_sha(new_repo)
    protected = frozenset({"acc_test.go"})

    assert protected_violations(protected, base, new_repo) == []
    (new_repo / "acc_test.go").write_text("weakened\n", encoding="utf-8")
    assert protected_violations(protected, base, new_repo) == ["acc_test.go"]
    git(new_repo, "add", "-A")
    git(new_repo, "commit", "-qm", "hack")
    assert protected_violations(protected, base, new_repo) == ["acc_test.go"]
    assert protected_violations(frozenset(), base, new_repo) == []


def test_protected_violations_fails_closed_when_git_diff_fails(new_repo):
    with pytest.raises(subprocess.CalledProcessError):
        protected_violations(
            frozenset({"acc_test.go"}), "not-a-valid-base", new_repo
        )
```

Run:

```bash
uv run --locked pytest tests/test_gitops.py -q -p no:cacheprovider
```

Expected: 新介面／invalid-base test FAIL。

- [ ] **Step 2: 讓 `protected_violations()` 只吃 trusted paths 並透過 `git_out()` fail closed**

在 `gitops.py` 從 `collections.abc` import `Collection`，將函式替換為：

```python
def protected_violations(
    protected: Collection[str], base: str, cwd: Path
) -> list[str]:
    if not protected:
        return []
    changed = [
        line
        for line in git_out(["diff", "--name-only", base, "--"], cwd).splitlines()
        if line
    ]
    return [name for name in changed if name in protected]
```

不得 catch `CalledProcessError` 並回傳 `[]`。空 path set 可以不呼叫 git，但這不代表控制檔不受保護；控制檔完整性由 workflow snapshot boundary 負責。

- [ ] **Step 3: 加入 protected-control snapshot 與 boundary 的 failing tests**

在 `tests/test_work.py` import `_activate_protected_controls`，並從 `adversarial_ai_coding.gitops` import `head_sha`，加入下列 tests。為避免 Windows symlink privilege 造成核心 coverage skip，以 modify、delete、empty、directory replacement、invalid UTF-8 覆蓋 direct-entry failure；symlink 可另加 capability-gated test，但不是唯一證據。

```python
def _write_controls(ctx, paths="acc_test.go\n", base=None):
    base_text = f"{head_sha(ctx.workspace)}\n" if base is None else base
    (ctx.wf / "protected-tests.txt").write_text(paths, encoding="utf-8")
    (ctx.wf / "protected-base.sha").write_text(base_text, encoding="utf-8")
    wf_mod._activate_protected_controls(ctx)


@pytest.mark.parametrize(
    ("target", "action"),
    [
        ("protected-tests.txt", "modify"),
        ("protected-tests.txt", "empty"),
        ("protected-tests.txt", "delete"),
        ("protected-base.sha", "modify"),
        ("protected-base.sha", "empty"),
        ("protected-base.sha", "directory"),
        ("protected-tests.txt", "invalid-utf8"),
    ],
)
def test_work_aborts_when_worker_tampers_with_protected_controls(
    make_ctx, monkeypatch, target, action
):
    ctx = make_ctx()
    _write_controls(ctx)
    path = ctx.wf / target

    def fake_worker(agent, prompt, settings, session, io):
        io.agent_out.write_text("worker output\n", encoding="utf-8")
        if action == "modify":
            path.write_text("forged\n", encoding="utf-8")
        elif action == "empty":
            path.write_bytes(b"")
        elif action == "delete":
            path.unlink()
        elif action == "directory":
            path.unlink()
            path.mkdir()
        else:
            path.write_bytes(b"\xff")
        return AgentResult(0, "worker output")

    monkeypatch.setattr(wf_mod, "run_worker", fake_worker)

    with pytest.raises(WorkflowAbort, match="protected control"):
        work(ctx, ctx.ref("A"), "prompt")


def test_work_rejects_preexisting_control_tampering_before_agent_call(
    make_ctx, monkeypatch
):
    ctx = make_ctx()
    _write_controls(ctx)
    (ctx.wf / "protected-base.sha").write_text("forged\n", encoding="utf-8")
    called = False

    def fake_worker(*args, **kwargs):
        nonlocal called
        called = True
        return AgentResult(0, "unexpected")

    monkeypatch.setattr(wf_mod, "run_worker", fake_worker)

    with pytest.raises(WorkflowAbort, match="protected control"):
        work(ctx, ctx.ref("A"), "prompt")
    assert called is False


def test_empty_protected_list_still_activates_control_integrity(
    make_ctx, monkeypatch
):
    ctx = make_ctx()
    _write_controls(ctx, paths="")
    assert ctx.protected_controls is not None
    assert ctx.protected_controls.paths == frozenset()

    def fake_worker(agent, prompt, settings, session, io):
        io.agent_out.write_text("worker output\n", encoding="utf-8")
        (ctx.wf / "protected-tests.txt").write_text(
            "new-test.py\n", encoding="utf-8"
        )
        return AgentResult(0, "worker output")

    monkeypatch.setattr(wf_mod, "run_worker", fake_worker)
    with pytest.raises(WorkflowAbort, match="protected control"):
        work(ctx, ctx.ref("A"), "prompt")


def test_tampering_error_keeps_archive_failure_as_cause(make_ctx, monkeypatch):
    ctx = make_ctx()
    _write_controls(ctx)

    def fake_worker(agent, prompt, settings, session, io):
        io.agent_out.write_text("worker output\n", encoding="utf-8")
        return AgentResult(0, "worker output")

    def fail_archive(*args, **kwargs):
        (ctx.wf / "protected-base.sha").write_text("forged\n", encoding="utf-8")
        raise OSError("archive failed")

    monkeypatch.setattr(wf_mod, "run_worker", fake_worker)
    monkeypatch.setattr(ctx.archive, "archive_git_state", fail_archive)

    with pytest.raises(WorkflowAbort, match="protected control") as exc_info:
        work(ctx, ctx.ref("A"), "prompt")
    assert isinstance(exc_info.value.__cause__, OSError)
    assert str(exc_info.value.__cause__) == "archive failed"


@pytest.mark.parametrize(
    ("target", "action"),
    [
        ("protected-tests.txt", "missing"),
        ("protected-tests.txt", "invalid-utf8"),
        ("protected-base.sha", "empty"),
        ("protected-base.sha", "invalid-utf8"),
        ("protected-base.sha", "directory"),
    ],
)
def test_activate_protected_controls_rejects_invalid_inputs(
    make_ctx, target, action
):
    ctx = make_ctx()
    protected = ctx.wf / "protected-tests.txt"
    base = ctx.wf / "protected-base.sha"
    protected.write_text("acc_test.go\n", encoding="utf-8")
    base.write_text(f"{head_sha(ctx.workspace)}\n", encoding="utf-8")
    path = ctx.wf / target
    if action == "missing":
        path.unlink()
    elif action == "empty":
        path.write_bytes(b"")
    elif action == "invalid-utf8":
        path.write_bytes(b"\xff")
    else:
        path.unlink()
        path.mkdir()

    with pytest.raises(WorkflowAbort, match="protected control"):
        wf_mod._activate_protected_controls(ctx)


def test_acceptance_control_target_allows_missing_and_regular_but_not_directory(
    make_ctx
):
    ctx = make_ctx()
    path = ctx.wf / "protected-tests.txt"
    wf_mod._require_regular_or_missing_control(path)
    path.write_text("old\n", encoding="utf-8")
    wf_mod._require_regular_or_missing_control(path)
    path.unlink()
    path.mkdir()
    with pytest.raises(WorkflowAbort, match="non-regular protected control"):
        wf_mod._require_regular_or_missing_control(path)
```

另把既有 `test_check_protected_repairs_then_stops` 在寫完兩個 controls 後呼叫 `_activate_protected_controls(ctx)`，並把 monkeypatch signature 改成 `(protected, base, cwd)`。新增一個 capture test，證明 `check_protected()` 傳入 snapshot 的 `frozenset` 與 base，而不是重新讀目前 pathname。

Run:

```bash
uv run --locked pytest tests/test_work.py tests/test_gitops.py -q -p no:cacheprovider
```

Expected: 新 tests RED；既有 protected repair test 不應因 slot／agent 參數改變。

- [ ] **Step 4: 實作 immutable snapshot 與 typed direct-entry helpers**

在 `workflow.py` 加 `import stat`、`import subprocess`，並在 `WorkflowContext` 前加入：

```python
@dataclass(frozen=True)
class ProtectedControlsSnapshot:
    protected_bytes: bytes
    base_bytes: bytes
    paths: frozenset[str]
    base: str
```

在 `WorkflowContext` 加：

```python
protected_controls: ProtectedControlsSnapshot | None = None
```

加入下列 helpers；錯誤訊息可在不改變語意下微調，但必須包含 `protected control`，並用 `WorkflowAbort` 包住 OSError／UnicodeError：

```python
def _read_regular_control(path: Path) -> bytes:
    try:
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode):
            raise WorkflowAbort(
                f"!! Protected control is not a regular file:{path}"
            )
        return path.read_bytes()
    except WorkflowAbort:
        raise
    except OSError as exc:
        raise WorkflowAbort(
            f"!! Unable to read protected control:{path}: {exc}"
        ) from exc


def _snapshot_protected_controls(ctx: WorkflowContext) -> ProtectedControlsSnapshot:
    protected_path = ctx.wf / "protected-tests.txt"
    base_path = ctx.wf / "protected-base.sha"
    protected_bytes = _read_regular_control(protected_path)
    base_bytes = _read_regular_control(base_path)
    try:
        protected_text = protected_bytes.decode("utf-8")
        base = base_bytes.decode("utf-8").strip()
    except UnicodeError as exc:
        raise WorkflowAbort(
            f"!! Protected control is not valid UTF-8: {exc}"
        ) from exc
    if not base:
        raise WorkflowAbort("!! Protected control base is empty")
    return ProtectedControlsSnapshot(
        protected_bytes=protected_bytes,
        base_bytes=base_bytes,
        paths=frozenset(line for line in protected_text.splitlines() if line),
        base=base,
    )


def _activate_protected_controls(ctx: WorkflowContext) -> None:
    ctx.protected_controls = _snapshot_protected_controls(ctx)


def _verify_protected_controls(ctx: WorkflowContext) -> None:
    snapshot = ctx.protected_controls
    if snapshot is None:
        return
    protected_path = ctx.wf / "protected-tests.txt"
    base_path = ctx.wf / "protected-base.sha"
    if _read_regular_control(protected_path) != snapshot.protected_bytes:
        raise WorkflowAbort(
            f"!! Protected control changed during worker execution:{protected_path}"
        )
    if _read_regular_control(base_path) != snapshot.base_bytes:
        raise WorkflowAbort(
            f"!! Protected control changed during worker execution:{base_path}"
        )


def _require_regular_or_missing_control(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    except OSError as exc:
        raise WorkflowAbort(
            f"!! Unable to inspect protected control:{path}: {exc}"
        ) from exc
    if not stat.S_ISREG(mode):
        raise WorkflowAbort(
            f"!! Refusing to overwrite non-regular protected control:{path}"
        )
```

這些 helpers 刻意不 restore、不 quarantine、不 delete replacement。`lstat` 到 `read_bytes` 的 concurrent TOCTOU 不在本 RC threat model 內，文件不得描述成 stable-handle guarantee。

- [ ] **Step 5: 用 wrapper 把整個 worker/archive/metric/check sequence 包進 pre/post verification**

把目前 `work()` 改成公開 boundary，並把原 body 機械式改名為 `_work_body()`。完成後兩個函式應是：

```python
def work(ctx: WorkflowContext, agent: AgentRef, instruction: str) -> None:
    _verify_protected_controls(ctx)
    try:
        _work_body(ctx, agent, instruction)
    except BaseException as exc:
        try:
            _verify_protected_controls(ctx)
        except WorkflowAbort as tampering:
            raise tampering from exc
        raise
    _verify_protected_controls(ctx)


def _work_body(ctx: WorkflowContext, agent: AgentRef, instruction: str) -> None:
    started = time.monotonic()
    ctx.session.last_cost = ""
    ctx.archive.log_section(
        "AI call",
        "worker",
        agent,
        ctx.cur_stage,
        ctx.cur_round,
        echo=ctx.echo,
    )
    ctx.echo(f">>> Worker({agent.name}) is running...")
    slug = f"worker-{safe_slug(ctx.cur_stage or 'startup')}-r{ctx.cur_round}"
    prompt_artifact = ctx.archive.archive_text(
        f"{slug}-prompt.md",
        instruction,
        "worker",
        agent,
        ctx.cur_stage,
        ctx.cur_round,
    )
    short_prompt = prompt_file_instruction(str(prompt_artifact))
    io = ctx.agent_io()
    result = agent_call(
        lambda: run_worker(agent, short_prompt, ctx.settings, ctx.session, io),
        agent_out=ctx.agent_out,
        settings=ctx.settings,
        events=_retry_events(ctx, "worker", agent, slug, io),
    )
    output_artifact = ctx.archive.art_path(f"{slug}-output.txt")
    output_artifact.write_text(result.text.rstrip("\n") + "\n", encoding="utf-8")
    ctx.archive.write_meta(
        output_artifact, "worker", agent, ctx.cur_stage, ctx.cur_round
    )
    ctx.log_file(result.text)
    if result.rc == QUOTA_ABORT_RC:
        raise WorkflowAbort(
            "!! Worker gave up on a quota/rate limit; aborting the run as resumable.",
            rc=QUOTA_ABORT_RC,
        )
    ctx.archive.archive_snapshot(
        ctx.agent_out,
        f"{slug}-final.raw",
        "worker",
        agent,
        ctx.cur_stage,
        ctx.cur_round,
    )
    ctx.archive.archive_git_state(
        "worker",
        agent,
        slug,
        ctx.cur_stage,
        ctx.cur_round,
        cwd=ctx.workspace,
    )
    ctx.archive.metric(
        "worker",
        agent,
        ctx.cur_round,
        int(time.monotonic() - started),
        ctx.session.last_cost,
        stage=ctx.cur_stage,
    )
    if not ctx.checking_protected:
        check_protected(ctx, agent)
```

實作者必須實際搬移完整 body，不得只保留上面的說明註解。`BaseException` 是刻意選擇：即使 Ctrl-C／SystemExit 與 control tampering 同時發生，也要讓 tampering 成為表層 typed stop，原始 exception 留在 `__cause__`；若 post-check clean，原始 exception 原樣重拋。

- [ ] **Step 6: `check_protected()` 改用 snapshot paths/base 並轉成 typed fail-closed**

完整函式改為：

```python
def check_protected(ctx: WorkflowContext, agent: AgentRef) -> None:
    controls = ctx.protected_controls
    if controls is None:
        return
    ctx.archive.log_section(
        "protected check",
        "workflow",
        None,
        ctx.cur_stage,
        ctx.cur_round,
        echo=ctx.echo,
    )
    recoveries = 0
    while True:
        try:
            violations = protected_violations(
                controls.paths, controls.base, ctx.workspace
            )
        except subprocess.CalledProcessError as exc:
            raise WorkflowAbort(
                "!! Unable to verify protected acceptance tests; "
                "git diff failed closed."
            ) from exc
        if not violations:
            return
        listing = "\n".join(f"  - {violation}" for violation in violations)
        ctx.log(f"!! Protected acceptance test files were modified:\n{listing}")
        if recoveries >= 2:
            ctx.notify(
                f"adversarial-ai-coding:[{ctx.cur_stage}] protected tests were "
                "modified and not restored; human intervention required"
            )
            raise WorkflowAbort(
                "!! Worker repeatedly modified protected tests and did not "
                "restore them; stopping for human intervention."
            )
        recoveries += 1
        prompt = render_prompt(
            ctx.prompts_dir,
            "protected-tests-modified",
            {
                "VIOLATIONS": "\n".join(violations),
                "BASE": controls.base,
                "SPEC_FILE": str(ctx.spec_dir / "spec.md"),
            },
        )
        ctx.checking_protected = True
        try:
            work(ctx, agent, prompt)
        finally:
            ctx.checking_protected = False
```

實作者必須保留現有完整 recovery loop；上面只標示需要替換的 data source 與 exception boundary。不得再次讀取 current control files 來取得 paths 或 base。

- [ ] **Step 7: acceptance stage 安全寫入並在新跑／resume 都啟用 snapshot**

在寫兩個 control files 前，先對兩個 path 都呼叫 `_require_regular_or_missing_control()`；兩個 preflight 都通過後才 `write_text()`。regular stale files 可以覆寫，missing files 可以建立，directory、symlink、junction 或其他 non-regular direct entry 必須 abort。

在 `if begin_stage(ctx, "write-acceptance-tests", ...)` block 結束後、進入 `write-code` 前，無條件呼叫：

```python
_activate_protected_controls(ctx)
```

這個位置同時涵蓋：

- 新跑：workflow 寫入與 archive controls、`end_stage()` 後啟用。
- resume 且 acceptance stage 已完成：`begin_stage()` 驗證 ledger/artifacts 後 skip，隨即由目前磁碟內容建立本 process snapshot。
- acceptance stage 未完成：只有成功跑完整個 stage 才會走到 activation。

將空 `names` 的 warning 從「test protection is disabled」改成「no acceptance-test paths were recorded; protected control files remain active」。空 list 的 path protection 是空集合，但兩個 controls 本身仍不可被 worker 改寫。

- [ ] **Step 8: 更新 stageflow 與 E2E 呼叫介面**

`tests/test_stageflow.py::test_write_code_routes_only_task_loop_repairs_and_commit_to_impl`：

- 保留它在 acceptance block 之前建立兩個 controls 的安排。
- `protected_violations` monkeypatch 第一個參數改名為 `protected`。
- 執行 `run_workflow()` 後斷言 `ctx.protected_controls is not None`。
- 原本 `protected-repair` 必須仍由 slot I 的斷言不可刪。

既有 routing test 的 `begin_stage` stub 已讓 `write-acceptance-tests` skip 並直接進入 `write-code`，所以在其 `run_workflow()` 後加入精確斷言即可覆蓋 resume-style skip path：

```python
assert ctx.protected_controls is not None
assert ctx.protected_controls.paths == frozenset({"acceptance_test.py"})
assert ctx.protected_controls.base == "base"
```

此外，在該 test 的第一個 `fake_work()` 呼叫中先斷言 `ctx.protected_controls is not None`，證明 snapshot 在第一個 implementation worker 前已啟用。不得透過 file presence 在 `work()` 裡自動 re-snapshot。

`tests/e2e/test_e2e.py` 的 final protected check 改成：

```python
paths = frozenset(
    line
    for line in protected.read_text(encoding="utf-8").splitlines()
    if line
)
assert protected_violations(
    paths, base_sha.read_text(encoding="utf-8").strip(), repo
) == []
```

- [ ] **Step 9: 更新人工 recovery 文件與 documentation tests**

English `README.md` 目前只寫「edit test and update base」，必須改成明確順序：

1. stop workflow；
2. edit corrected protected test；
3. commit corrected test；
4. 把該新 commit SHA 寫入 `.workflow/protected-base.sha`；
5. 或由人工從 `.workflow/protected-tests.txt` 移除該 path。

Traditional Chinese 已有「commit 新內容再更新 SHA」語意，只需與 process-local snapshot／resume 說明一致。兩份 README 都要說明 active process 內 controls 會以 exact bytes 保護；不要宣稱跨 process 或 concurrent pathname hardening。

在 `tests/test_documentation.py` 加：

```python
def test_protected_test_recovery_requires_commit_before_new_base():
    english = _read("README.md").lower()
    recovery = english[english.index("## protected acceptance tests") :]
    assert recovery.index("commit") < recovery.index("protected-base.sha")


def test_empty_path_list_does_not_disable_control_integrity_bilingually():
    for readme in (_read("README.md"), _read("README.zh-TW.md")):
        assert "protected-tests.txt" in readme
        assert "protected-base.sha" in readme
```

第二個 test 只鎖關鍵 anchors；語意 parity 仍需人工閱讀，不要寫脆弱的整段 literal 比對。

- [ ] **Step 10: 跑 Task 2 focused tests**

```bash
uv run --locked pytest \
  tests/test_gitops.py \
  tests/test_work.py \
  tests/test_stageflow.py \
  tests/test_resume_integration.py \
  tests/e2e/test_e2e.py::test_fixture_baseline \
  tests/test_documentation.py \
  -q -p no:cacheprovider
```

Expected:

- invalid base／git diff error 不再回傳 clean。
- worker pre/post tampering 全部 typed abort。
- ordinary protected-test violation 仍由同一 agent repair，最多兩次後停止。
- slot I protected repair routing 仍通過。
- resume activation 與 offline fixture 通過。

- [ ] **Step 11: 跑 Task 2 完整 local Windows gate**

```bash
uv run --locked pytest -q -p no:cacheprovider
git diff --check
```

Expected: full suite 完整結束且 PASS；live-agent E2E deselected；任何 symlink skip 必須列出 exact nodes 與 `WinError 1314` 理由；`git diff --check` 無輸出。

- [ ] **Step 12: 做 bounded security self-review**

逐項以 `rg` 與 diff 確認：

```bash
rg -n "protected-tests.txt|protected-base.sha|protected_violations|protected_controls" \
  src tests README.md README.zh-TW.md
git diff --stat HEAD
git diff HEAD -- src/adversarial_ai_coding/gitops.py \
  src/adversarial_ai_coding/workflow.py tests/test_gitops.py tests/test_work.py \
  tests/test_stageflow.py tests/e2e/test_e2e.py README.md README.zh-TW.md
```

必須能回答：

- active `work()` 是否只用既有 snapshot，不因 control file presence 自動建立新 trust？
- worker／archive error 時是否仍做 post verification，tampering 是否保留原 exception 作 cause？
- `check_protected()` 是否完全不讀 current controls 決定 paths/base？
- empty path list 是否仍有 active snapshot？
- 是否完全沒有 restore、quarantine、rmtree、runstate、CLI、lock 或 topology change？

- [ ] **Step 13: Commit Task 2**

```bash
git add src/adversarial_ai_coding/gitops.py \
  src/adversarial_ai_coding/workflow.py \
  tests/test_gitops.py tests/test_work.py tests/test_stageflow.py \
  tests/e2e/test_e2e.py tests/test_documentation.py \
  README.md README.zh-TW.md
git commit \
  -m "fix(workflow): fail closed on protected control changes" \
  -m "Keep an immutable process-local snapshot of protected test paths, base, and exact control-file bytes after the acceptance stage." \
  -m "Verify controls before and after every active worker boundary, use the trusted snapshot for git checks, and abort on control or git-diff failures without automatic restore." \
  -m "Preserve same-agent protected-test repair behavior and document the bounded threat model and manual recovery sequence."
```

Expected: commit only contains Task 2 files；無 `Co-Authored-By`。

---

### Task 3: Scope audit、fresh verification 與唯一一次 final review

**Files:**

- Read: entire `0ad4ce7..HEAD` diff
- No planned production changes

**Interfaces:**

- Consumes: Task 1、Task 2 commits
- Produces: 可交付或明確停止的 review decision

- [ ] **Step 1: 驗證 commit graph 與 preserved branch**

```bash
git log --oneline --reverse 0ad4ce7..HEAD
git rev-list --count 0ad4ce7..HEAD
git rev-list --count ee2ae5b..HEAD
git rev-parse feat/custom-impl-agent
git status --short
```

Expected:

- `0ad4ce7..HEAD` 正好 3 commits：closure plan、args fix、protected-control fix。
- `ee2ae5b..HEAD` 正好 11 commits：原 8 個 core commits + 上述 3 個。
- `feat/custom-impl-agent` 仍為 `cc2df4d...`。
- status clean。

- [ ] **Step 2: 驗證沒有 hardening scope leakage**

```bash
git diff --name-status 0ad4ce7..HEAD
git diff --stat 0ad4ce7..HEAD
git diff 0ad4ce7..HEAD -- src/adversarial_ai_coding/runstate.py \
  src/adversarial_ai_coding/cli.py src/adversarial_ai_coding/trusted_paths.py
```

Expected: 最後一個 diff 無輸出；沒有 `trusted_paths.py`；沒有 lock／restore／quarantine/topology implementation。

- [ ] **Step 3: 在 clean HEAD 跑 fresh focused 與 full verification**

```bash
uv run --locked pytest \
  tests/test_agents.py tests/test_session_resume.py \
  tests/test_gitops.py tests/test_work.py tests/test_stageflow.py \
  tests/test_resume_integration.py tests/e2e/test_e2e.py::test_fixture_baseline \
  tests/test_documentation.py -q -p no:cacheprovider
uv run --locked pytest -q -p no:cacheprovider
git diff --check
git status --short
```

Expected: 所有 offline tests PASS；live E2E deselected；diff check clean；worktree clean。

- [ ] **Step 4: 做一次 bounded fresh final review**

使用 `superpowers:requesting-code-review`。Reviewer scope 固定為：

- 原始 plan `docs/plans/20260712_custom_impl_agent_v3_opus48xhigh.md` 的 implementation-slot 核心契約。
- 本 closure plan 的兩個 fixes 與 explicit threat model。
- `ee2ae5b..HEAD` 全 diff、commit boundaries、tests 與雙語文件。

Reviewer 必須把第 3.2 節 deferred hardening 當成已揭露 non-blocking backlog，不得要求 core RC 重新引入 `cc2df4d` 架構。若發現真正新的 core blocker，停止並向使用者說明；不得自行開始第 2、3、4 輪 reviewer/fixer cycle。若只有 style、future hardening 或超出 threat model 的建議，記入 handoff，不改 code。

- [ ] **Step 5: Main agent 在 reviewer 後重跑必要 verification**

Reviewer 若沒有 production change，重跑 focused gate、`git diff --check`、`git status --short` 即可；若使用者另行批准修正 core blocker，該修正必須先新增獨立 task／commit，再重跑 full suite。不得只引用 reviewer 的測試報告宣稱完成。

---

### Task 4: Live E2E approval checkpoint 與交付

**Files:**

- Read: `tests/e2e/test_e2e.py`
- Read: generated `.workflow/runs/<id>/metrics.csv`（只有獲准執行時）
- No planned source change

**Interfaces:**

- Consumes: clean reviewed RC HEAD
- Produces: live evidence 或 explicit waiver、最終 handoff

- [ ] **Step 1: 執行前詢問使用者是否同意消耗真實 agent quota**

沒有明確同意就不得啟動 agent。若使用者 waiver，最終交付與 PR/body（若未來建立）逐字加入：

```text
Live agent E2E: waived by user; offline coverage only.
```

- [ ] **Step 2A: 若獲准，只跑一個 bounded live E2E**

```bash
export IMPL_AGENT=codex
export IMPL_MODEL=gpt-5.5
uv run --locked pytest \
  tests/e2e/test_e2e.py::test_full_workflow_e2e \
  -m e2e -q -s -p no:cacheprovider
```

Expected: test PASS。人工檢查保留的 E2E workspace：

- `metrics.csv` 的 write-code implement/build-repair/task-commit 相關 worker rows 有 `agent_slot=I`、`agent=codex`、`model=gpt-5.5`。
- spec、plan、full-gate repair、branch/final review 維持 owner/reviewer slots。
- 第一個 slot I 呼叫沒有錯用 owner 的 session ID。

若 live E2E 因 quota／外部服務失敗，不把它包裝成 product failure；保存 log、停止重試並向使用者報告。不得自動再燒第二次 quota。

- [ ] **Step 2B: 若 waiver，確認 offline evidence 已完整**

記錄 fresh full suite command、pass/skip/deselect counts、Windows symlink limitation、Ubuntu CI 未跑、repository 無 remote。

- [ ] **Step 3: 使用 `superpowers:finishing-a-development-branch` 完成交付選項**

不得自動 merge、push 或 cleanup。交付摘要至少包含：

- branch 與 HEAD SHA。
- 三個 closure commits 的 subjects。
- focused/full tests exact counts 與 commands。
- live E2E 結果或 waiver exact text。
- `No Git remote configured; Ubuntu CI was not run.`
- deferred hardening findings 與 core RC threat model。
- preserved `feat/custom-impl-agent@cc2df4d` 未改動。

## 7. 最終 Definition of Done

只有全部符合才可說「core RC 收尾完成」：

- [ ] `feat/custom-impl-agent-core-rc` 以 `0ad4ce7` 為祖先，closure 只有 3 commits。
- [ ] Task 1 能拒絕 Codex bypass/ephemeral aliases 與 attached reserved shorts，且不誤擋其他 adapter/custom args/`model_reasoning_effort`。
- [ ] Task 2 在 acceptance 後建立 immutable process-local snapshot，所有 active worker boundaries pre/post fail closed。
- [ ] `protected_violations()` 的 git failure 不再回傳 clean，`check_protected()` 只用 snapshot paths/base。
- [ ] empty protected path list 仍保護兩個 controls；nonregular、missing、modified、empty、invalid UTF-8 都 typed abort。
- [ ] ordinary protected-test repair 保持同 agent/ref，slot I routing 不退化。
- [ ] English manual recovery 明確要求 commit corrected test 後才更新 base SHA；雙語文件語意一致。
- [ ] 每個 task 有獨立 Conventional Commit、詳細 body、無 `Co-Authored-By`。
- [ ] fresh focused 與 local Windows full suite 完整通過；live E2E 有結果或 explicit waiver。
- [ ] 一次 bounded final review 完成，沒有自動展開 review/fix loop。
- [ ] `runstate.py`、`cli.py`、`trusted_paths.py`、lock、restore、quarantine、topology hardening 未進入 RC。
- [ ] 最終說明明載 repository 無 remote、Ubuntu CI 未跑，以及 deferred hardening 風險。

## 8. 接手者常見地雷

- 不要從 `cc2df4d` 開新 branch；那會把未解的 hardening architecture 一起帶回來。
- 不要用 `git checkout cc2df4d -- src/...` 批次覆蓋；args 的小 hunks可人工參考，protected-control 設計必須依本文件的 process-local scope 重做。
- 不要讓 `work()` 看到兩個檔案存在就自動 snapshot；worker 竄改後的內容不能成為新 trust anchor。
- 不要把 empty `protected-tests.txt` 解釋為 controls protection disabled。
- 不要在 post-check 只處理 successful worker；quota、archive、metric、Ctrl-C 等 failure path 也必須 verify。
- 不要 catch `git_out()` 的 `CalledProcessError` 後回傳空 violations。
- 不要把 Codex flags 加到所有 adapters 的聯集；custom args 仍是 wrapper contract。
- 不要把 `model_reasoning_effort` 當成 `model` key；config key 必須 split first `=` 後精確比對。
- 不要因 default pytest deselect live E2E 就寫「E2E 已通過」；只能寫 offline E2E/fixture 通過。
- 不要把 Windows symlink privilege skip 隱藏在總數裡；列出 exact skips。

## 9. 後續 hardening 建議（不在本計畫執行）

Core RC 完成交付後，若產品要宣稱可抵禦同 UID concurrent workspace actor，另開獨立 design/plan，先決策 platform primitives，再動 code：

1. Windows 與 POSIX 的 stable directory/entry handle-relative mutation 能力與抽象層。
2. OS-backed held lock 的 acquire/release/owner death 語意。
3. authoritative state write、ledger recovery 與 quarantine cleanup 的 no-write-through/no-delete-replacement 保證。
4. fresh state initialization 的 lock ownership cleanup。
5. deterministic final-syscall replacement tests，不能只在 syscall 前後多做 pathname check。

這份後續工作應該是新的安全子專案，不是本 core RC 的「再補一點」。
