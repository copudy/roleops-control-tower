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

## 驗證摘要

| 範圍 | 證據與結果 | 適用邊界 |
| --- | --- | --- |
| 首發本機基線（A） | 2026-11-13，[47e9efe](https://github.com/copudy/roleops-control-tower/commit/47e9efef8d267e6222b2e130aec5f4c7253ea10b)：Windows Python 3.12 乾淨副本記錄 209 個測試、48 個子測試通過；首頁、自檢與版本查詢 HTTP 200；Gitleaks 8.30.1 零命中。 | 使用合成資料及服務替身，有 6 筆 MCP 棄用警告；此為該基線的歷史結果。 |
| AWS 指定情境（B） | 作者操作並回報客服／IT 指引、CRM 申請與審核回查、越權及管理員展示通過。 | 原收據與環境識別保留於私有專案；公開程式及測試可供檢查契約。 |
| 近期完整重跑（E） | 2026-11-25 未取得完整測試結果。 | 預設 E5 初始化缺少 model.onnx；切換本機設定後仍遇 Windows FAISS 原生載入異常。上方指令已列出供應商設定，環境相容性仍待排除。 |

[專題展示影片](https://drive.google.com/file/d/1rquCmsrJ6von0V3GhVpVN7F95xZCSd9a/view?usp=sharing)呈現錄製時的成果。本 repo 未提供持續在線的 AWS 展示網址；本機契約測試與錄影不能作為即時雲端健康或效能證據。

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
