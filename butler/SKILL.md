# 微信管家引擎 HTTP API

基址 `http://127.0.0.1:8790`（`WECHATBOT_PORT` 可改）。一般不直接调，用 MCP 工具（mcp_server.py）。

- `GET /status` 设备、WDA、前台 App
- `GET /screen` 元素表 + 截图；`?fast=1` 只用屏幕记忆
- `POST /act` 单步动作（tap/type/swipe/home/back/wait_for），高风险动作要 `confirm: true`
- `GET /paths?app_=wechat` 微信路径清单；`POST /jobs` 起任务，`GET /jobs/{id}`、`/jobs/{id}/items` 取结果
- `GET /drafts`、`POST /drafts/{id}/send` 草稿；`GET /ledger` 发送台账；`POST /watch` 定时监听
