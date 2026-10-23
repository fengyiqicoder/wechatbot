# 微信管家安装说明（给 Claude 看的）

> 你是正在帮用户安装微信管家的 Claude。按顺序执行，每一步都核对结果再往下走。
> 需要用户在 iPhone 或 Xcode 上手动操作的步骤，停下来用一两句话告诉用户点哪里，等用户说好了再继续。
> 仓库默认在 `~/wechatbot`；如果用户克隆到了别处，下面的路径都换成实际路径。

## 0. 前置检查

逐条检查，缺什么就告诉用户怎么补，补齐再继续：

| 检查 | 命令 | 不满足时告诉用户 |
|---|---|---|
| macOS | `sw_vers` | 只支持 Mac |
| Xcode | `xcodebuild -version` | 从 App Store 装 Xcode，装完打开一次、同意协议 |
| Python ≥ 3.10 | `python3 -V` | `brew install python@3.12` |
| iPhone 已连接 | 先跑完第 1 步，再 `~/wechatbot/.venv/bin/pymobiledevice3 usbmux list --usb` | 用数据线连 Mac，解锁手机，弹窗点「信任此电脑」 |
| 手机装了微信并已登录 | 问用户 | — |

## 1. 装环境 + 编译手机端驱动

```bash
~/wechatbot/scripts/setup.sh
```

这一步装 Python 依赖、下载 [WebDriverAgent](https://github.com/appium/WebDriverAgent)（WDA，Appium 的开源 iOS 驱动），用用户自己的 Apple ID 签名后装到 iPhone 上。首次约 3–5 分钟。

按报错处理：

- **「Xcode 里还没有登录 Apple ID」**：让用户打开 Xcode → Settings → Accounts → 左下角 + → Apple ID 登录。免费 Apple ID 就够用。
- **「Developer Mode」相关 / 手机上提示需要开发者模式**：让用户在 iPhone 上打开 设置 → 隐私与安全性 → 开发者模式 → 打开，手机会重启。
- **「iPhone 上要信任开发者」**：让用户在 iPhone 上打开 设置 → 通用 → VPN与设备管理 → 点开发者 App 里自己的 Apple ID → 信任。然后重跑 `~/wechatbot/scripts/start_wda.sh`。
- **其它编译失败**：看 `~/Library/Application Support/WeChatBot/logs/wda_build.log` 的最后几十行。

最后看到 `WDA 就绪` 就算成功。

## 2. 接入 Claude

```bash
claude mcp add wechatbot --scope user -- ~/wechatbot/.venv/bin/python ~/wechatbot/mcp_server.py
```

如果当前不是 Claude Code（比如 Claude Desktop），把下面这段加进它的 MCP 配置（`~/Library/Application Support/Claude/claude_desktop_config.json` 的 `mcpServers`），路径换成绝对路径：

```json
"wechatbot": {"command": "/Users/<用户名>/wechatbot/.venv/bin/python", "args": ["/Users/<用户名>/wechatbot/mcp_server.py"]}
```

## 3. 验收

告诉用户：重启 Claude（或新开一个会话）让工具生效，然后：

1. 说「看看我的微信」→ Claude 调 `wechat_status` 和 `wechat_inbox`，能列出会话就说明通了。
2. 输入 `/mcp__wechatbot__takeover`（或直接说「接管我的微信」）→ 一键接管：Claude 先问几句你的说话风格和哪些人可以直接代回，之后巡视未读、按你的口吻拟回复、给你汇报。

## 注意事项（安装完转述给用户）

- 手机要一直插着线、保持解锁时才能操作；锁屏时管家会停下来等。
- 免费 Apple ID 签的 WDA 7 天过期，过期后重跑 `~/wechatbot/scripts/start_wda.sh --build`。付费开发者账号是一年。
- 默认 23:00–07:30 是静默时段，不操作手机。可以在 `~/Library/Application Support/WeChatBot/settings.json` 里改 `sleep_window`。
- 所有数据只在这台 Mac 上，不上传任何服务器。
