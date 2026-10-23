# 微信管家 WeChatBot

**一键 AI 接管微信。**

让 Claude 直接操作你 iPhone 上的真实微信：读未读消息、按你的口吻代回、搜聊天记录、看朋友圈，最后给你一份简报。不用 Hook、不用改微信、不登录网页版，就是一台插着线的 iPhone。

官网：<https://wechatbot.000ooo.ooo>

## 安装

把下面这段复制进 Claude Code，剩下的它来做：

```
帮我安装微信管家：git clone https://github.com/fengyiqicoder/wechatbot ~/wechatbot ，然后严格按照 ~/wechatbot/INSTALL.md 一步步装好。需要我在手机或 Xcode 上点的步骤，停下来告诉我。
```

需要：一台 Mac（装好 Xcode）、一部 iPhone（装好微信），一根数据线，一个 Apple ID（免费的就行）。

## 能做什么

| 你说 | 管家做 |
|---|---|
| 接管我的微信（`/mcp__wechatbot__takeover`） | 巡视未读 → 读上下文 → 用你的口吻拟回复 → 白名单里的人直接回，其余先给你看 → 汇报 |
| 谁找我了 | 列出有未读的会话和预览 |
| 看看我和张三最近聊了什么 | 读会话，语音自动转文字 |
| 搜一下谁提过「报销」 | 微信全局搜索 |
| 帮我回老妈：今晚回家吃饭 | 先给你确认，确认后发送，并在聊天气泡里读回核对 |
| 看看朋友圈最近有什么 | 朋友圈作者、文字、图片数、时间 |

工具清单：`wechat_status` `wechat_inbox` `wechat_read` `wechat_search` `wechat_contacts` `wechat_contact_card` `wechat_moments` `wechat_group_members` `wechat_mentions` `wechat_screenshot` `wechat_send` `wechat_post_moment` `wechat_set_chat` `wechat_takeover_rules` `wechat_recent_replies`

## 安全设计

- **发出去之前先问你。** 默认每条回复都要你过目；只有你明确放进「直接代回」名单的人，管家才会不问就回。
- **钱、承诺、敏感话题不碰**，标成「需要你亲自处理」。
- **看不清就不动手。** 每次点击前先确认目标真的在屏幕上，点完核对结果，失败如实报告，不盲目重试。
- **像人一样操作**：随机停顿、落点偏移、分段打字；23:00–07:30 默认静默。
- **数据只在你的 Mac 上**，不经过任何服务器。

## 已知限制

- **慢**：每个动作都在真手机上点，一次读会话约 20–60 秒，通讯录、名片、朋友圈约 1–2 分钟。适合「巡视 + 代回」，不适合秒回。
- 手机要**插着线、解锁**；锁屏时管家会停下来等。
- 免费 Apple ID 签的手机端驱动 **7 天过期**，过期后重跑 `scripts/start_wda.sh --build`（付费开发者账号是一年）。
- 发纯文字朋友圈需要「发现」页里开着朋友圈；关掉的话只能带图发。
- 目前只支持 iPhone + Mac。

## 工作原理

```
Claude ──MCP──▶ mcp_server.py ──HTTP──▶ 引擎（butler/，127.0.0.1:8790）
                                           │  闸门 → 观察 → 定位 → 拟人 → 执行 → 验证
                                           ▼
                              WebDriverAgent（iPhone 上的 XCTest 进程，USB 转发）
                                           ▼
                                     iPhone 上的微信
```

引擎来自 SuperBright 的真机控制链：优先读无障碍树（微信的控件很规范，一屏约 1.4 秒），读不到的退回截图；常去的界面会记住，下次 0.3 秒就能认出来。

## 开发

```bash
.venv/bin/python -m pytest -q tests     # 离线测试，不需要手机
.venv/bin/python -m butler.server       # 单独起引擎
```

## 许可

MIT
