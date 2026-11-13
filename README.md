# RoleOps Control Tower

RoleOps 是企業角色導向的 AI 助理與存取申請治理平台。Mentor 提供知識與操作指引，Dispatcher 依身分與職務派任助理。軟體存取申請由後端受控工具建單，再由主管審核。

## 四條展示路線

| 路線 | 重點 |
| --- | --- |
| 客服 | 依角色取得政策與案例，整理後續作業所需資訊 |
| IT | 依權限取得支援知識與作業指引 |
| CRM | 查政策、送出軟體存取申請、主管審核與回查 |
| 管理員 | 追查治理事件、檢視服務自檢與維運介面 |

## 架構

瀏覽器介面呼叫 Python Web 服務。後端判斷身分與操作權限，查詢已索引的知識來源，並經 MCP 執行受控的存取申請動作。AWS 展示環境由 CloudFront 提供 HTTPS 入口，EC2 運行應用服務，S3、SQS 與 DynamoDB 分別承擔文件、索引事件及業務資料。

- [後端入口](app/web.py)
- [知識索引](indexer/worker.py)
- [MCP 工具](mcp_server/server.py)
- [測試案例](tests_extension/)

## 本機驗證入口

使用 Python 3.12，從 repo 根目錄執行下列指令，然後開啟 http://127.0.0.1:8000。

```text
python -m pip install -r requirements.lock
python launch_local.py
```

本機展示使用合成資料、索引向量與回覆，資料存於 `runtime/`。

正式 AWS 部署與資料還原使用私有環境設定。公開版的還原身分檢查使用佔位值。
