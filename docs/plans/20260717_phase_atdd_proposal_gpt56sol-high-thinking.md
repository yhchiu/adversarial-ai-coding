引入 Phase 的概念, 每個階段都有自己的 acceptance-test，對多代理 AI coding 流程而言，這可能比「強制 A 為每個內部函式撰寫 implementation tests」更實用。

但前提是：

> **Phase 必須是可以獨立驗收的垂直功能切片，而不是單純的內部技術層。**

這種方法可稱為：

> **Phased ATDD／Incremental Acceptance-Test-Driven Development**

它不是傳統單元級 TDD，但可以解決目前流程中「acceptance tests 可能跨越多個 task 一直保持 Red，最後才一次變 Green」的主要問題。你目前 README 的設計是每個 task 只執行 compile-only 的 `BUILD_GATE_CMD`，全部完成後才執行完整 `GATE_CMD`。

---

## 建議的 Phase 流程

```text
Spec
  ↓
Plan 拆成多個可獨立驗收的 Phase
  ↓
Phase 1
  B 寫 Phase 1 acceptance tests
  A review tests
  workflow 確認測試因功能尚未實作而 Red
  tests 設為 protected
  A 實作
  Phase 1 gate：舊測試 + Phase 1 tests 全綠
  commit
  ↓
Phase 2
  B 寫 Phase 2 acceptance tests
  A review tests
  workflow 確認 Red
  tests 設為 protected
  A 實作
  Phase 2 gate：Phase 1 + Phase 2 全綠
  commit
  ↓
Phase 3...
  ↓
Final gate：所有 Phase tests 全綠
  ↓
B branch review
```

核心規則是：

> 正在開發的 Phase 可以是 Red；所有已完成 Phase 必須持續 Green。

這比「全部 acceptance tests 一開始都建立，直到最後才全綠」更容易定位錯誤。

---

## 這種做法的優點

### 1. 減少 implementation tests 與內部結構的耦合

implementation tests 常會直接依賴：

* private/internal class
* 目前的模組切分
* 特定資料結構
* 特定呼叫順序
* mock 的內部互動

未來大幅重構時，程式外部行為沒有改變，卻可能需要修改大量 implementation tests。

Phase acceptance tests 如果只驗證穩定邊界，例如 CLI、API、公開 service interface 或檔案輸出，通常更能容許內部重構。

### 2. 測試數量較少、維護成本較低

AI 很容易產生大量低價值 implementation tests，例如只重複實作邏輯，或大量驗證 mock 呼叫次數。

Phase acceptance tests 聚焦在：

* 規格行為
* 關鍵場景
* 邊界條件
* 錯誤處理
* phase 間整合

通常訊噪比更高。

### 3. 保留 B 與 A 的職責分離

仍由 B 寫測試、A 實作，因此保留了你原本流程中重要的對抗性：

* A 不能只測自己想到的路徑
* A 不能任意降低 assertion
* 測試和實作不共享完全相同的盲點
* protected tests 防止 A 為了通過而修改測試

### 4. 比目前的最後一次整體驗收更容易除錯

假設 Phase 3 完成後測試失敗，問題範圍通常只在：

* Phase 3 的變更
* Phase 3 與既有 Phase 的整合點

而不是在十幾個尚未完整驗證的 task 中搜索。

---

## Phase 必須是垂直切片

這是成敗關鍵。

### 不好的 Phase 拆法

```text
Phase 1：建立 database layer
Phase 2：建立 service layer
Phase 3：建立 API layer
Phase 4：串接 UI
```

這是水平技術分層。

Phase 1 的資料庫層可能沒有獨立的使用者價值，也很難透過穩定的外部介面驗收。最後仍然要等到 Phase 4 才能確認整體行為。

### 較好的 Phase 拆法

以新增 `--json` 功能為例：

```text
Phase 1：基本成功結果可輸出 JSON
Phase 2：空結果輸出合法的空陣列
Phase 3：特殊字元與 Unicode 正確 escaping
Phase 4：錯誤狀態維持既有 exit code
Phase 5：未使用 --json 時舊行為完全不變
```

每個 Phase 都有：

* 明確輸入
* 可觀察輸出
* 可測試的完成條件
* 對使用者或其他模組有意義的增量
* 完成後可以永久保持 Green

---

## Phase test 不必全部是完整 E2E

「Acceptance test」不代表每個測試都必須從瀏覽器或完整系統最外層執行。

可以依照 Phase 的穩定邊界分成：

```text
System acceptance test
    從 CLI、HTTP API、UI 等最外層驗證

Component acceptance test
    從一個子系統的公開介面驗證

Contract test
    驗證兩個模組或服務之間的協定

Integration test
    驗證資料庫、檔案系統、網路等整合行為
```

例如 parser Phase 可以從 parser 的公開入口輸入完整資料，再驗證解析結果，而不需要經過整個 UI。這仍然是 black-box 或 boundary-oriented test，不一定是脆弱的 implementation test。

這是很重要的折衷：

> 測試公開行為邊界，而不是測試每個內部函式，也不必所有測試都跑完整系統。

---

## 完全不寫 implementation tests 的風險

Phase acceptance tests 雖然可以提供良好的功能與重構安全網，但仍有幾類問題比較難覆蓋。

### 複雜組合邏輯

例如：

* 權限規則矩陣
* 日期與時區計算
* 價格計算
* parser grammar
* 狀態機
* retry/backoff
* 資料轉換

若所有組合都透過高層 acceptance test 驗證，測試可能變得很慢、很難維護。

### concurrency 與時序行為

例如：

* race condition
* lock ownership
* queue backpressure
* timeout
* cancellation
* reconnect state machine

這些通常需要較低層、可精確控制時序的測試，純外部 acceptance tests 可能不穩定或難以重現。

### 失敗定位較慢

Acceptance test 只能告訴你：

```text
「使用者場景失敗」
```

implementation/component test 則可能直接指出：

```text
「retry counter 在第三次失敗後沒有 reset」
```

當系統變大時，只有 acceptance tests 會讓診斷成本逐漸提高。

### 難以到達的錯誤分支

例如：

* 磁碟只寫入一半
* 特定 network timeout
* checksum collision
* dependency 回傳不合法資料
* transaction commit 特定階段失敗

透過完整外部介面製造這些狀態可能很昂貴或不穩定。

---

## 不建議「禁止 implementation tests」

我建議的政策不是：

```text
所有 task 都必須寫 implementation tests
```

也不是：

```text
永遠禁止 implementation tests
```

而是：

> **預設使用 Phase acceptance tests；只有在 acceptance tests 無法快速、穩定、充分驗證時，才加入精準的低層測試。**

可以設計成觸發條件：

以下任一情況成立，就要求 A 增加 component/implementation test：

* 該邏輯有大量輸入組合或邊界條件
* 涉及 parser、狀態機、演算法或資料轉換
* 涉及 concurrency、timeout、retry 或 cancellation
* Phase acceptance test 執行時間太長
* 錯誤難以透過 acceptance test 定位
* 某個 bug 無法穩定從外部重現
* 某個內部元件有明確且穩定的公開契約
* 過去曾在相同區域出現 regression

這樣不會為了形式而產生大量低價值單元測試，但仍保留必要的精細安全網。

---

## 建議增加 `PHASE_GATE_CMD`

目前可以從兩級 gate：

```text
BUILD_GATE_CMD
GATE_CMD
```

調整為三級：

```text
BUILD_GATE_CMD
    快速編譯檢查，可在 Phase 實作過程重複執行

PHASE_GATE_CMD
    build + 所有已完成 Phase tests + 當前 Phase tests

GATE_CMD
    完整 build、lint、static analysis、全部 Phase tests、其他測試
```

概念上：

```text
Phase N 實作中：
    BUILD_GATE_CMD 可快速執行

Phase N 準備完成：
    PHASE_GATE_CMD 必須全部 Green

所有 Phase 完成：
    GATE_CMD 必須全部 Green
```

如果不同 Phase 有不同測試集合，可以使用 tag 或 marker，例如：

```bash
pytest -m "phase1 or phase2 or phase3"
```

或者讓 workflow 維護：

```text
.workflow/phase-tests/
├── phase-01.txt
├── phase-02.txt
└── phase-03.txt
```

每完成一個 Phase，就把該 Phase 測試納入永久 regression gate。

---

## 測試應該 Just-in-Time 產生

不建議 B 在所有 Phase 開始前一次寫完所有測試，因為會出現：

* 很多預期失敗的測試長期污染結果
* 後期 Phase 可能因早期設計調整而失去合理性
* B 可能需要猜測尚未確定的中間介面
* 很難區分預期 Red 和真正 regression

建議每個 Phase 開始時才寫該 Phase 的測試：

```text
Phase N 開始
  ↓
B 根據 spec + plan + 已完成狀態寫測試
  ↓
A review
  ↓
確認 Red 原因正確
  ↓
保護測試
  ↓
A 實作
```

但所有 Phase 的 acceptance criteria 應在 `spec.md` 或 `plan.md` 中提前定義，避免 B 在實作途中任意改變需求。

---

## 對你的流程最合適的版本

我會建議採用：

```text
Spec + Human Gate
  ↓
Plan 拆成 vertical phases
  ↓
For each Phase:
    B 寫該 Phase acceptance/component/contract tests
    A review
    workflow 驗證測試正確 Red
    protected-test check
    A 實作
    build gate
    phase gate：歷史 Phase + 當前 Phase 全綠
    B review Phase diff
    commit
  ↓
Full gate
  ↓
A self-review
  ↓
B final acceptance
```

並設定：

```text
Implementation tests：
    非每個 task 強制
    由複雜度與風險觸發
```

## 最終判斷

**這個方向是好的，而且比目前「所有 acceptance tests 到最後才一次驗收」更接近漸進式開發。**

在以下條件下，它可以作為主要測試策略：

* Phase 是垂直功能切片
* 每個 Phase 都能獨立觀察與驗收
* Phase 測試夠快、穩定、可重複
* 已完成 Phase 永遠維持 Green
* 測試會永久保留作為 regression suite
* 允許對少數高風險內部邏輯補充低層測試

因此，最合理的政策是：

> **以 Phase acceptance tests 為預設，不強制全面 implementation testing，但不要禁止必要的 component 或 implementation tests。**

這會比「完全只做最終 acceptance tests」安全，也比「每個函式都要求單元測試」更適合你的 adversarial AI coding 工作流。

