# 關卡(Gates)

[English](gates.md) | 繁體中文

關卡是「workflow 自己判定,而不是聽 agent 說」的地方。Agent 說「測試都過了」
只是回報,不是證明;關卡就是把那句話變成事實,或者把這次 run 擋下來。

一共三類:

| 類型 | 判定什麼 | 上限 |
|---|---|---|
| **指令關卡** | 程式能不能編譯、測試過不過? | `MAX_ROUNDS` |
| **人工關卡** | 這次 run 該不該繼續花錢? | 人 |
| **完整性關卡** | 有沒有人動了考卷或計畫? | `MAX_ROUNDS`、2 次補救 |

本文是三者的完整參考。關卡在流程中的位置見
[`how-it-works.zh-TW.md`](how-it-works.zh-TW.md);關卡執行時的權限錯誤見
[`troubleshooting.zh-TW.md`](troubleshooting.zh-TW.md)。

## 指令關卡

三個指令,由 workflow 透過平台 shell 執行,工作目錄是工作區。exit code 0
是通過,其餘一律是失敗。

| 變數 | 預設 | 是什麼 |
|---|---|---|
| `GATE_CMD` | 自動偵測 | 完整關卡:編譯、靜態檢查與全部測試,含受保護的驗收測試。 |
| `BUILD_GATE_CMD` | 自動偵測 | 逐任務關卡。只驗編譯:每個實作任務後執行,此時驗收測試本來就還是紅的。 |
| `PHASE_GATE_CMD` | 空,退回 `GATE_CMD` | 分階段 ATDD 專用:phase 前的紅燈檢查與 phase 後的 phase gate。 |

### 各自跑在哪裡

預設流程:

```text
plan.md 的每個任務:
    實作 → BUILD_GATE_CMD → commit
所有任務完成 → GATE_CMD        (此時驗收測試必須全綠)
Branch review → 每輪修正後 → GATE_CMD
Self review → GATE_CMD
Final acceptance review
```

分階段 ATDD(`PHASES=1`)多兩個位置,兩者都用 `PHASE_GATE_CMD`,空的話用
`GATE_CMD`:

```text
每個 phase:
    reviewer 寫該 phase 的測試 → 紅燈檢查   (必須失敗)
    每個任務:實作 → BUILD_GATE_CMD → commit
    phase gate                              (必須通過)
    (PHASE_REVIEW=1) phase review → 每輪修正後 → phase gate
```

紅燈檢查是唯一「期待失敗」的關卡。程式還沒寫就會過的測試證明不了任何事,
所以測試若一開始就是綠的,會退回給寫測試的那一方。標題以
`(regression-guard)` 結尾的 phase 則相反:那種測試鎖的是既有行為,必須當場
就通過。

### 關卡失敗會怎樣

所有指令關卡共用同一套 `gate_loop`:

1. 執行指令,exit 0 就結束。
2. 失敗時,把 stdout 與 stderr 合併後的**最後 150 行**交給該 stage 負責的
   agent,要求修復。
3. 再跑一次。重複直到通過,或嘗試次數達到 `MAX_ROUNDS`(預設 3)。
4. 最後一次仍失敗時,發出 `NOTIFY_CMD` 通知,並以**最後 50 行**輸出中止這次
   run。分支與先前所有 commit 都保留;修好之後用 `RESUME_RUN` 續跑。

誰負責修取決於關卡位置:逐任務關卡與 phase gate 由實作 slot 修,完整關卡與
self-review 關卡由 owner 修,review 迴圈裡的關卡由當時修 review findings 的
那一方修。

## 自動偵測

關卡指令的解析順序如下,第一個非空值勝出:

1. 本次 run 的環境變數
2. 續跑 snapshot 裡記錄的值
3. 從工作區自動偵測

空字串視同未設定,所以 `GATE_CMD= aac request.md` **不會**關掉關卡,它會往下
落到 snapshot,再落到偵測。

| 偵測到 | `GATE_CMD` | `BUILD_GATE_CMD` |
|---|---|---|
| `go.mod` | `go build ./... && go vet ./... && go test ./...` | `go build ./...` |
| `package.json` 且有 `test` script | `npm test` | (無) |
| `package.json` 但沒有 | (無) | (無) |
| `Cargo.toml` | `cargo test` | `cargo build` |
| Python 專案(見下) | 交給管環境的工具跑 pytest | (無) |
| 其他 | (無) | (無) |

偵測依上表順序**取第一個命中**就停,所以同時是 Go 服務與 npm 前端的 repo 會
用 Go 的關卡。要兩邊都跑,請自己設 `GATE_CMD`。

`PHASE_GATE_CMD` 不會被偵測。留空就用 `GATE_CMD`,而這通常正是你要的:phase
gate 本來就是完整測試套件,只是提早跑。

### Python 的偵測條件

三個條件同時成立才會給 Python 關卡:

1. marker 檔:`pyproject.toml`、`setup.py` 或 `setup.cfg`
2. 有寫明用 pytest:`[tool.pytest.ini_options]` 區段、`pytest.ini`、
   `tox.ini` 的 `[pytest]`、`setup.cfg` 的 `[tool:pytest]`,或
   `pyproject.toml` 裡出現 pytest
3. 至少一個測試檔:根目錄或 `tests/` 底下的 `test_*.py`、`*_test.py`

第三條不是保險。pytest 收不到任何測試時回 exit code 5,關卡會判定失敗,於是
還沒有測試的專案會被派 agent 去修根本沒壞的東西,每輪一次,一路撞到
`MAX_ROUNDS`。

指令會指名「管這個環境的工具」,因為**用哪個直譯器跑**比 `python` 怎麼拼
重要:

| 工作區裡有 | 指令 |
|---|---|
| `uv.lock` | `uv run pytest` |
| `poetry.lock` | `poetry run pytest` |
| `.venv/Scripts/python.exe` 或 `.venv/bin/python` | 該直譯器加 `-m pytest` |
| 都沒有,但 PATH 有 `python` | `python -m pytest` |
| 都沒有,只有 `python3` | `python3 -m pytest` |

Python 專案不會有 `BUILD_GATE_CMD`:沒有值得逐任務執行的編譯步驟。

### 偵測不到時

啟動時會印出解析後的完整關卡;沒有的話印這段警告:

```text
(warning: no quality gate command detected; deterministic gates are
disabled. Set GATE_CMD to enable one.)
```

這種狀態下 run 仍然跑得完,而這正是危險之處:所有確定性檢查都被跳過,程式
能動的唯一證據就剩下 agent 自己說的話。各個值空掉的代價:

| 空的是 | 後果 |
|---|---|
| `GATE_CMD` | 沒有完整關卡、review 迴圈裡也沒有關卡;分階段模式下若 `PHASE_GATE_CMD` 也沒設,紅燈檢查與 phase gate 一併消失。驗收測試被寫出來、被 commit,然後從來沒被執行過。 |
| `BUILD_GATE_CMD` | 沒有逐任務的編譯檢查,問題會延到完整關卡才爆,而且要從更多任務裡找。而且是靜默的:不會有警告。 |
| `PHASE_GATE_CMD` | 退回 `GATE_CMD`。只有兩者都空時,紅燈檢查才會被跳過,並印出自己的警告。 |

### 自己設定關卡

只要是非互動、失敗時 exit 非零、能從 repo 根目錄執行的指令都可以:

```bash
# Make 專案
GATE_CMD='make build && make test' BUILD_GATE_CMD='make build' aac request.md

# Gradle
GATE_CMD='./gradlew build' BUILD_GATE_CMD='./gradlew compileJava' aac request.md

# .NET
GATE_CMD='dotnet test' BUILD_GATE_CMD='dotnet build' aac request.md

# 沒有 lock 檔、也偵測不到 venv 的 Python 專案
GATE_CMD='python -m pytest -q' aac request.md

# Monorepo:只對某個套件設關卡
GATE_CMD='npm --prefix services/api test' aac request.md

# phase gate 用比完整套件更快的指令
PHASES=1 GATE_CMD='make test-all' PHASE_GATE_CMD='make test-fast' aac request.md
```

幾個能省下一次白跑的注意事項:

- 指令交給平台 shell:Windows 是 `cmd.exe`,其餘是 `sh`。`&&` 串接是可攜的;
  `;`、subshell 與巢狀單引號不是。
- Windows 上要跑 Go race 測試需要一個 linker 參數,完整值在 README 的環境
  變數表 `GATE_CMD` 那一列。
- `BUILD_GATE_CMD` 要快。它每個任務都跑,而且**必須容忍驗收測試是紅的**——
  build gate 若跑了測試,會在最後一個任務之前每次都失敗。
- 需要憑證、資料庫或網路服務的關卡,失敗起來和「程式壞掉」長得一模一樣,
  agent 會去改程式。請只對 agent 真的修得動的東西設關卡。

## 人工關卡

| 變數 | 預設 | 關卡 |
|---|---|---|
| `HUMAN_GATE` | `1` | 開始實作前先核准 `spec.md`。 |
| `HUMAN_GATE_PLAN` | `0` | Plan review 之後核准 `plan.md`。 |

兩者都會停下來、印出檔案路徑並要求輸入 `y`,其他任何輸入都會中止 run。你可以
先編輯該檔案,編輯內容會跟著該 stage 一起 commit。雙 spec 模式另有兩個人工
關卡:選出 base spec 的 `[a/b/ma/mb]` 決策,以及你編輯完 merge request 後的
確認。

Spec 關卡也是分階段 ATDD 可能被提議的地方:未設定 `PHASES` 且未匯入 plan 時,
spec reviewer 會判斷適不適合,建議啟用時會問
`Enable Phased ATDD for this run? [y/N]:`。`HUMAN_GATE=0` 時該建議只會寫進
log,絕不會自動套用。

人工關卡採 fail closed:沒有互動終端時,詢問直接拒絕而不是視為核准;而
`HUMAN_GATE_PLAN=1` 與 `DUAL_SPEC=1` 在 stdin 不是終端時,會在 preflight 就被
拒絕——在任何要付費的呼叫發生之前。

## 完整性關卡

**受保護的驗收測試。** Reviewer 的測試一旦 commit,路徑會記進
`aac/.run/protected-tests.txt`,基準 commit 記進 `aac/.run/protected-base.sha`。
之後**每一次會改檔的呼叫**結束後,workflow 都會對該基準跑 `git diff`;git 本身
出錯時 fail closed。有違規就交回該 agent 還原,連續兩次補救失敗即中止 run。
測試真的寫錯時的逃生口見
[受保護測試的逃生口](../README.zh-TW.md#受保護測試的逃生口)。

**Plan 結構。** 分階段模式下,plan review 之後 workflow 會確定性解析 plan:
phase 從 1 連號、每個 phase 都有 `Acceptance:` 行與至少一個 `- [ ]` 任務、
沒有任務落在 phase 之外。結構有問題會在任何實作開始前交回 owner。

**裁決。** 每個 review 迴圈結束與否取決於 reviewer 的 `verdict.json`,不是
散文。只有 blocker 會讓迴圈重跑,同樣受 `MAX_ROUNDS` 限制;suggestions 累積到
收尾階段處理。

## `MAX_ROUNDS`

`MAX_ROUNDS`(預設 `3`)是上述所有迴圈的上限:指令關卡的修復、紅燈檢查的
修復、review 輪數,以及 phase gate。調高讓 agent 在真的難修的失敗上多試幾次,
調低則能更早停下空轉的 run。它不管受保護測試的守衛——那邊固定是 2 次補救。

## `TOOLS` 與關卡的關係

關卡指令是 workflow 自己執行的,所以 `TOOLS` 完全不影響關卡能不能跑。它影響的
是 agent:Claude slot 若不能執行專案的測試,就無法驗證自己的產出,而非互動
模式下跳出權限 prompt 就是卡住。

預設 allowlist 依工作區偵測,而且是**聯集**(一個 repo 可能同時是好幾種):

| 偵測到 | 加入的規則 |
|---|---|
| 一律 | `Bash(git *)` |
| `go.mod` | `Bash(go test *),Bash(go build *),Bash(go vet *)` |
| `package.json` | `Bash(npm test)` |
| `Cargo.toml` | `Bash(cargo build),Bash(cargo test)` |
| 有寫明用 pytest 的 Python 專案 | `Bash(pytest *),Bash(uv run pytest *),Bash(poetry run pytest *),Bash(python -m pytest *),Bash(python3 -m pytest *)` |

四種都沒偵測到時,以上規則全部保留。與關卡不同,這裡是聯集,而且不要求已經有
測試檔——這次 run 的工作就是去寫它們。自訂 `GATE_CMD` 通常也要配一條對應規則:
關卡用 `make` 但 `TOOLS` 沒有 `Bash(make *)`,agent 就無法重現它被要求修復的
那個失敗。

## 續跑

關卡指令會寫進 run snapshot,所以續跑時沿用開跑當時的關卡,即使現在重新偵測會
得到不同答案。該次 attempt 再傳一次變數即可覆蓋 snapshot。`PHASES` 是例外:
它決定 stage 圖,因此在 run 開始時就固定,續跑時給不同值會被拒絕。

## 延伸閱讀

- [`how-it-works.zh-TW.md`](how-it-works.zh-TW.md) — 逐 stage 的完整流程
- [`troubleshooting.zh-TW.md`](troubleshooting.zh-TW.md) — 權限錯誤、`TOOLS`
  規則語法、quota 等待
- [README:環境變數](../README.zh-TW.md#環境變數) — 所有環境變數,含關卡
