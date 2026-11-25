# 驗證紀錄與已知限制

[返回 README](../README.md)

## 證據分級

| 等級 | 本文件的用法 |
| --- | --- |
| A 原始／實測 | 本機執行結果、程式與測試原件 |
| B 一手回報 | 專題作者操作 AWS 後確認的展示結果 |
| E 待驗 | 這次沒有實際執行或未取得完整原件的範圍 |

## 可重現的測試方式

使用 Python 3.12，在 repo 根目錄、已啟用虛擬環境的終端執行：

```bash
python -m pip install -r requirements-dev.txt
python -m pytest tests tests_extension -q
```

執行測試前，設定本機供應商模式，避免測試初始化預設尋找 E5 模型資產：

PowerShell：

```powershell
$env:EMBED_PROVIDER = 'default'
$env:GENERATION_PROVIDER = 'bedrock'
$env:PYTHONUTF8 = '1'
python -m pytest tests tests_extension -q
```

macOS / Linux：

```bash
EMBED_PROVIDER=default GENERATION_PROVIDER=bedrock PYTHONUTF8=1 python -m pytest tests tests_extension -q
```

這裡的 bedrock 是選擇本機合成回覆分支；local 模式下不會因此呼叫 Bedrock。Groq 契約測試會另行指定供應商替身。

測試使用合成資料、暫存資料庫、AWS／供應商替身，以及測試內建立的身分。它們驗證程式契約；真實雲端網路、IAM、配額、模型品質及告警送達需另外實測。

## 測試入口與觀察目標

| 測試入口 | 驗證重點 |
| --- | --- |
| [test_core.py](../tests/test_core.py)、[test_boundaries.py](../tests/test_boundaries.py) | 核心業務與授權邊界 |
| [test_mcp.py](../tests/test_mcp.py) | MCP 呼叫身分與申請工具 |
| [test_reliability_unittest.py](../tests/test_reliability_unittest.py) | 可靠性與失敗處理 |
| [test_demo_lines.py](../tests_extension/test_demo_lines.py) | 客服、IT、封存來源、越權與治理 |
| [test_integrated_lines.py](../tests_extension/test_integrated_lines.py) | 來源回覆、模型拒絕、申請失敗與狀態歷史 |
| [test_wire_facts.py](../tests_extension/test_wire_facts.py) | 模型事實代碼與來源事實映射 |
| [test_context_boundary.py](../tests_extension/test_context_boundary.py) | 上下文與資料邊界 |
| [test_approval_repair.py](../tests_extension/test_approval_repair.py) | 審核流程 |
| [test_maintenance_dashboard.py](../tests_extension/test_maintenance_dashboard.py) | 維運介面與資料呈現 |

## 已有驗證紀錄

**2026-11-13 首發檢查（A）**：公開基線提交為 [47e9efe](https://github.com/copudy/roleops-control-tower/commit/47e9efef8d267e6222b2e130aec5f4c7253ea10b)，105 個檔案。私有發布紀錄保留 Windows Python 3.12 乾淨副本的 209 個測試、48 個子測試通過紀錄，以及 6 筆 MCP 棄用警告。本機首頁、自檢與版本查詢回應 HTTP 200；Gitleaks 8.30.1 掃描零筆命中。

這是首發時的結果；以下另記本次文件更新的檢查。

**2026-11-25 文件更新（A）**：更新 README，新增架構、展示與驗證三份指南；42 個相對連結存在性、Markdown 指令框配對與 Git 差異格式檢查通過；Gitleaks 8.30.1 對本次四份文件掃描零筆命中。

**本次測試重跑未完成（E）**：直接執行測試時預設 E5 初始化尋找未提供的 model.onnx，測試因此失敗；切換本機供應商設定後，執行環境又出現 FAISS 載入的 Windows 原生異常，未取得完整測試結果。上方已補列測試供應商設定。本次僅更新公開文件，沒有更動應用程式；本次結果與 11 月 13 日首發驗證分開記錄。

**AWS 展示驗收（B）**：專題作者已回報客服／IT 指引、CRM 申請與審核回查、越權與管理員展示通過。逐次操作的原收據、環境 ID 及私有維運資料保留於私有專案，公開讀者可由本 repo 的程式與測試檢查契約。

**目前運行狀態（E）**：此 repo 沒有提供持續在線的 AWS 展示網址。本次文件更新沒有操作 AWS 或呼叫真實模型，沒有新增正式環境驗收結論。

## 已知限制與後續方向

| 項目 | 現況 | 後續方向 |
| --- | --- | --- |
| 合成知識與有限情境 | 來源與涵蓋規則圍繞專題案例 | 擴充文件類型與獨立評測集 |
| 本機模型路徑 | 合成向量／回覆，未執行真實 E5／Groq | 在隔離雲端環境評測品質、延遲與成本 |
| 高可用 | 單一 EC2 的三服務架構 | 評估多機部署、共用資料與索引發布 |
| 外部開通 | MCP 建單，主管審核後外部系統開通 | 增加可稽核的外部開通整合 |
| Prompt A／B／C | 可配置的提示版本 | 用一致測資建立對照評測 |
| 部署與還原 | 需私有設定；部分身分值為佔位值 | 建立可公開的參數化部署及還原演練 |
| 告警 | 程式具備指標與維運入口 | 各告警觸發、SNS 送達及維護通知逐項驗收 |

本專題沒有提供通用的「AI 事實自動驗真」承諾。版本、雜湊、授權與輸出契約讓指定案例的處理可核對，文件本身的正確性及業務決策仍由負責人治理。
