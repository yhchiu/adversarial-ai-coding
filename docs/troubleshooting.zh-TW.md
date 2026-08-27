# 疑難排解

英文版請見 [Troubleshooting](troubleshooting.md)。

## 先看 run 產物

先確認哪個 stage 失敗,不要立刻重跑整條 workflow。以下的 `<RUN_ID>` 請換成
AAC 印出的 run ID。

| 證據 | 路徑 | 用途 |
| --- | --- | --- |
| 完整 run log | `aac/.run/archive/<RUN_ID>/logs/001-run.log` | stage 切換、命令、重試與最後錯誤 |
| 最近一次可讀 agent 輸出 | `aac/.run/last-agent-output.txt` | AAC 從最近一次 agent 呼叫渲染出的文字 |
| 最近一次原始 CLI stream | `aac/.run/last-agent-cli.raw` | 未加工的 JSONL 或 CLI 輸出,用來查 adapter/schema 問題 |
| 續跑設定 | `aac/.run/state/<RUN_ID>/settings.json` | resume 時保存的有效設定 |
| stage 台帳 | `aac/.run/state/<RUN_ID>/ledger.json` | 哪些 stage 已完成、resume 時會跳過 |

修好原因後,使用 AAC 印出的完整 `RESUME_RUN=...` 命令。Resume 會跳過已完成
stage,不必為已成功的 AI 工作再次付費。

## Reviewer 沒有寫出 `verdict.json`

AAC 從檔案讀 reviewer 產物,不會解析 stdout 上的 JSON verdict。Reviewer 必須寫:

- `aac/.run/review.md`:人可讀的 review;
- `aac/.run/verdict.json`:格式為
  `{"approved": false, "blockers": ["must-fix issue"], "suggestions": []}` 的機器可讀裁決。

先在 `logs/001-run.log` 找 reviewer exit code 與最後一個 tool call。內建 reviewer
若因命令權限失敗,依下一節修正。自訂 reviewer 則要修 wrapper,讓它在目前 repo
寫出這兩個檔案。持續缺少或寫出無效 verdict 會消耗 review round,最後在
`MAX_ROUNDS` 中止。

## Run 卡在權限詢問

AAC 以非互動模式呼叫 agent,沒有人能回答權限 prompt。

| Adapter | AAC 加入的參數與設定 | 實際行為 |
| --- | --- | --- |
| Claude Code | `--permission-mode acceptEdits --allowedTools <TOOLS>` | `acceptEdits` 會核准檔案編輯與常見 filesystem 命令;`TOOLS` 提供額外預先核准的 tool 規則。 |
| Codex | 全新 worker 與 reviewer 使用 `--sandbox workspace-write`;續跑 worker 使用 `-c sandbox_mode="workspace-write"`。AAC 不會加入 `--approve-for-me`,也不會覆寫 approval reviewer。 | Codex 強制使用 workspace-write sandbox,approval policy 與 reviewer 則繼承 Codex config 或允許的使用者參數。 |
| Antigravity (`agy`) | `--dangerously-skip-permissions` | 略過所有 tool 權限詢問;AAC 不會另外加入 sandbox。 |
| OpenCode | `--auto` | 自動核准使用者 OpenCode config 未明確 deny 的所有權限。 |
| 自訂 adapter | 無 | Wrapper command 與 `AGENT_A_ARGS`、`AGENT_B_ARGS` 或 `IMPL_ARGS` 負責所有權限及 sandbox 行為。 |

各 adapter 的處理方式不同。

### Claude Code:把確切命令放進 `TOOLS`

AAC 把 `TOOLS` 原樣傳給 Claude Code 的 `--allowedTools`。目前完整預設值是:

```bash
TOOLS='Bash(git *),Bash(go test *),Bash(go build *),Bash(go vet *)'
```

設定 `TOOLS` 會取代整個值,不是附加。請保留這次 run 仍需要的規則,再明確加入
log 中被擋的命令。例如要加入 Go 格式化,完整命令是:

```bash
TOOLS='Bash(git *),Bash(go test *),Bash(go build *),Bash(go vet *),Bash(gofmt *)' \
  aac request.md
```

上例實際新增的是 `Bash(gofmt *)`。其他最小權限起點如下:

```bash
# npm 專案:只允許這條 workflow 真的需要的 scripts。
TOOLS='Bash(git *),Bash(npm test),Bash(npm run build),Bash(npm run lint)' \
  aac request.md

# Cargo 專案:允許自動偵測到的 build 與完整 gate。
TOOLS='Bash(git *),Bash(cargo build),Bash(cargo test)' \
  aac request.md

# 自訂 pytest gate 的 Python 專案。
TOOLS='Bash(git *),Bash(uv run pytest *)' \
  aac request.md
```

請使用能匹配 log 中命令的最窄規則。不要把 Go 規則放寬成 `Bash(go *)`:它也會
允許可執行任意程式碼的 `go run`。同一份 allowlist 同時套用 Claude worker 與
reviewer。最新 `--allowedTools` 規則語法以
[Claude Code CLI reference](https://docs.anthropic.com/en/docs/claude-code/cli-usage)
為準。

### Codex

AAC 管理 Codex sandbox,包含 resume 在內都使用 `workspace-write`。不要在
`AGENT_A_ARGS`、`AGENT_B_ARGS` 或 `IMPL_ARGS` 加入 `--sandbox`、`-s`、`--yolo` 或
`sandbox_mode` override;AAC 會在 preflight 拒絕這些保留參數。

Codex 若無法寫檔,先確認目標在 repo 內,而且目前 OS 使用者有寫入權限。Repo 外
的路徑不應成為關閉 sandbox 的理由;請把產物移進 repo,或把外部操作拆開由人執行。

### Antigravity (`agy`)

AAC 已用 `--dangerously-skip-permissions` 啟動 `agy`。目前版本若仍詢問權限,請保存
完整 prompt,再用 `agy --help` 核對安裝版本;AAC 沒有另一個待開啟的權限設定。
此模式權限很廣,建議搭配 `USE_WORKTREE=1` 或容器。

### OpenCode

AAC 以 `--auto` 啟動 OpenCode,但使用者 OpenCode config 裡的 deny 規則仍優先。
先在 `logs/001-run.log` 找出被拒絕的 tool/path,確定操作合理後才縮小或移除該 deny
規則。

## 沒有互動終端可供核准

`HUMAN_GATE=1` 需要 TTY。`HUMAN_GATE_PLAN=1` 也獨立需要 TTY,而且會在第一個
AI 呼叫前檢查。CI 或其他無人環境請使用:

```bash
HUMAN_GATE=0 HUMAN_GATE_PLAN=0 NOTIFY_CMD='your-notifier' aac request.md
```

這只是移除互動核准,不會自動產生等價的安全關卡。請用受保護測試、確定性的
`GATE_CMD` 與 PR review 補上控制。

## 逐任務品質關卡一直失敗

兩種 gate 的執行時機不同:

- `BUILD_GATE_CMD` 在每個實作 task 後執行,應是快速編譯或 type-check。後續 task
  尚未完成時,完整 acceptance test 仍為紅燈可能是合理狀態。
- `GATE_CMD` 在所有實作 task 後執行,應包含完整 build、lint 與 test suite。

Go 預設逐任務跑 `go build ./...`,完整 gate 跑
`go build ./... && go vet ./... && go test ./...`。Cargo 使用 `cargo build` 與
`cargo test`。有 `test` script 的 npm 專案用 `npm test` 作完整 gate,但不會自動
產生逐任務 build gate。

若 `BUILD_GATE_CMD` 現在跑完整 acceptance suite,請換成只編譯的命令;沒有合理的
快速 gate 時可留空。Gate command 是 workflow 自己執行;只有 Claude agent 為了
診斷或修正也嘗試跑相同命令時,才需要把它加入 `TOOLS`。

## Windows 上 reviewer 誤報檔案損壞

先在 agent 外用 `git diff` 或直接檢查檔案 bytes。若檔案確實是有效 UTF-8,只有
review 內容變成亂碼,規格、計畫與測試資料可盡量維持 ASCII;非 ASCII 的 source
test data 用 `\u4e0a` 等 Unicode escape 表示,並遵守
[`resources/AGENTS.template.md`](../resources/AGENTS.template.md)。

不要因為單一 agent 用 Windows system code page 錯誤渲染就重寫有效檔案。若 bytes
真的被改壞,只從版本控制還原受影響檔案,再 resume run。

## 速率限制與 quota 錯誤

AAC 只讀 agent 自己的錯誤通道做 quota 判斷,不掃 agent 執行過的指令輸出。因此
測試檔名 `test_ratelimit_parsing.py` 不會意外讓 workflow 進入等待。

各 adapter 的錯誤通道不同:

- Claude 使用結構化 result 與回報的 reset epoch。
- Codex 使用 `error`、`turn.failed` event 與 JSON 外的 CLI 文字。
- OpenCode 使用 `error` event 與 JSON 外的 CLI 文字,並保留 provider HTTP status,
  因為各 provider 措辭不同。
- Agy 沒有結構化 event 邊界,所以仍掃完整輸出。

有 reset time 時,AAC 會加一小段 buffer 後等到該時刻:

| 訊息或欄位 | Agent | 等待方式 |
| --- | --- | --- |
| 回報的 reset epoch | Claude | 等到該時刻,再加 2 分鐘 |
| `resets 10:50am` | Claude | 等到該時刻,再加 2 分鐘 |
| `try again in 90s` | Codex | 等該段時間,再加 30 秒 |
| `try again at Jul 14th, 2026 7:23 PM` | Codex | 等到該 timestamp,再加 30 秒 |
| `try again at 12:50 AM` | Codex | 等到下一次該時刻,再加 30 秒 |

xAI/OpenCode 的 `personal-team-blocked:spending-limit` 與 Grok Build 的
`usage balance exhausted` 都需要帳號端處理。AAC 會直接以 exit code 75 中止,
不 sleep、不重送。其他無法解析 reset time 的限額訊息,從 `RETRY_BASE_WAIT` 開始
指數退避,並受 `RETRY_MAX_WAIT` 與 `RETRY_MAX` 限制。

解析出的 reset 若比 `RETRY_MAX_RESET_WAIT` 更遠(預設 6 小時),AAC 不會空等好幾天,
而是以 exit code 75 中止。Exit code 75 代表可續跑的 quota abort:等 quota 重置或
換掉已耗盡的 agent/model 後,使用印出的 `RESUME_RUN` 命令。若任何限額都要立刻
失敗,設定 `RETRY_ON_LIMIT=0`。
