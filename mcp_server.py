"""微信管家 MCP：把引擎（butler/server.py，127.0.0.1:8790）包成 Claude 能直接调用的工具。

引擎和 WDA 都由这里按需拉起：第一次调用任何工具时自动启动，不用手动开。
采集/写操作都走引擎的路径任务（/jobs），这里同步等任务结束再返回。
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests
from mcp.server.fastmcp import FastMCP, Image

BASE = Path(__file__).resolve().parent
PY = sys.executable
PORT = int(os.environ.get("WECHATBOT_PORT", "8790"))
API = f"http://127.0.0.1:{PORT}"
HOME = Path(os.environ.get("WECHATBOT_HOME") or Path.home() / "Library" / "Application Support" / "WeChatBot")
TAKEOVER = HOME / "takeover.json"
DEFAULT_RULES = {
    "mode": "ask",                 # ask：每条回复先给主人过目；auto：白名单里的人直接回
    "auto_contacts": [],           # auto 模式下可以直接回的联系人/群
    "never": ["文件传输助手"],      # 永远不碰的会话
    "persona": "",                 # 主人的说话风格、身份、常用语
    "notes": "",                   # 其他交代：哪些事不能替我答应、哪些话题转给我
    "every_minutes": 5,
}

mcp = FastMCP("wechatbot", instructions=(
    "微信管家：通过 USB 连着的 iPhone 操作真实微信。读消息、搜索、看朋友圈是只读的，随便用；"
    "发消息、发朋友圈会真的发出去，除非主人已经在接管规则里授权（wechat_takeover_rules 的 mode=auto 且对方在 auto_contacts 里），"
    "否则发之前必须先把内容给主人确认。一次只能做一件事，不要并发调用。"))


# ---------------------------------------------------------------- 引擎与设备
def _get(path: str, timeout: float = 30, **params):
    r = requests.get(API + path, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _post(path: str, body: dict | None = None, timeout: float = 60):
    r = requests.post(API + path, json=body or {}, timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError(f"{r.status_code}: {r.text[:300]}")
    return r.json()


def _engine_up() -> bool:
    try:
        return requests.get(API + "/paths", timeout=2).ok
    except requests.RequestException:
        return False


def _start_engine() -> None:
    if _engine_up():
        return
    log = HOME / "logs"
    log.mkdir(parents=True, exist_ok=True)
    subprocess.run([PY, str(BASE / "scripts" / "detach.py"), "--log", str(log / "engine.log"), "--",
                    PY, "-m", "butler.server"], cwd=BASE, check=True, capture_output=True)
    for _ in range(40):
        if _engine_up():
            return
        time.sleep(0.5)
    raise RuntimeError(f"引擎没起来，见 {log / 'engine.log'}")


def _ensure() -> dict:
    """确保引擎和 WDA 都在；返回 /status。"""
    _start_engine()
    st = _get("/status", timeout=20)
    if st.get("wda_connected"):
        return st
    r = subprocess.run([str(BASE / "scripts" / "start_wda.sh")], cwd=BASE, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise RuntimeError("手机没连上：" + (r.stdout + r.stderr).strip()[-600:])
    _post("/reconnect", timeout=30)
    st = _get("/status", timeout=20)
    if not st.get("wda_connected"):
        raise RuntimeError("WDA 起来了但会话建不起来：解锁 iPhone 后再试")
    return st


def _job(path: str, params: dict, timeout: float = 600) -> dict:
    """跑一条微信路径并等它结束。返回 {status, result|error, items?}。"""
    _ensure()
    for _ in range(30):
        try:
            j = _post("/jobs", {"app": "wechat", "path": path, "params": params})
            break
        except RuntimeError as e:
            if not str(e).startswith("409"):
                raise
            time.sleep(2)
    else:
        raise RuntimeError("引擎一直在忙（有任务在跑），稍后再试")
    jid, t0 = j["id"], time.time()
    while time.time() - t0 < timeout:
        j = _get(f"/jobs/{jid}")
        if j["status"] not in ("queued", "running"):
            break
        time.sleep(1.5)
    else:
        _post(f"/jobs/{jid}/cancel")
        raise RuntimeError(f"任务超时（{int(timeout)} 秒）已取消")
    out = {"status": j["status"]}
    if j.get("result") is not None:
        out["result"] = j["result"]
    if j.get("error"):
        out["error"] = j["error"]
    if j.get("notes"):
        out["notes"] = [n.get("msg") for n in j["notes"]][-5:]
    if j.get("kind") == "collect":
        items = _get(f"/jobs/{jid}/items")
        out["items"] = [{k: v for k, v in it.items() if k not in ("rect", "y")} for it in items]
    return out


def _fmt(x) -> str:
    return json.dumps(x, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- 只读
@mcp.tool()
def wechat_status() -> str:
    """检查手机、WDA、引擎是否就绪（没起来会自动拉起，首次约 1 分钟）。返回设备、是否锁屏、前台 App。"""
    st = _ensure()
    return _fmt({k: st.get(k) for k in ("wda_connected", "locked", "device", "foreground", "busy")})


@mcp.tool()
def wechat_inbox(n: int = 20, unread_only: bool = False) -> str:
    """会话列表：每行有标题（联系人/群名）、最后一条预览、时间、未读数。unread_only=True 只看有未读的。"""
    return _fmt(_job("inbox", {"n": n, "unread_only": unread_only}))


@mcp.tool()
def wechat_read(contact: str, history: int = 0) -> str:
    """打开和某人/某群的会话读消息。语音会先转成文字；who=我 是主人自己发的。
    history=0 只读当前一屏；>0 向上翻页最多读这么多条。contact 用会话列表里显示的名字。"""
    if history > 0:
        return _fmt(_job("history", {"contact": contact, "n": history}))
    return _fmt(_job("messages", {"contact": contact}))


@mcp.tool()
def wechat_search(q: str) -> str:
    """微信全局搜索：返回联系人、群聊、聊天记录、公众号等分区结果。"""
    return _fmt(_job("search", {"q": q}))


@mcp.tool()
def wechat_contacts(n: int = 200) -> str:
    """通讯录全量（按字母滚动读取，人多时较慢）。"""
    return _fmt(_job("contacts", {"n": n}))


@mcp.tool()
def wechat_contact_card(contact: str) -> str:
    """联系人名片：昵称、微信号、地区、备注。"""
    return _fmt(_job("card", {"contact": contact}))


@mcp.tool()
def wechat_moments(n: int = 10, contact: str = "") -> str:
    """朋友圈动态（作者、文字、图片数、时间）。给 contact 就只看这个人的朋友圈。"""
    if contact:
        return _fmt(_job("moments_of", {"contact": contact, "n": n}))
    return _fmt(_job("moments", {"n": n}))


@mcp.tool()
def wechat_group_members(group: str, n: int = 100) -> str:
    """群成员列表。"""
    return _fmt(_job("group_members", {"group": group, "n": n}))


@mcp.tool()
def wechat_mentions(n: int = 20) -> str:
    """群里 @我 的消息。"""
    return _fmt(_job("mentions", {"n": n}))


@mcp.tool()
def wechat_screenshot() -> Image:
    """当前手机屏幕截图。工具读不到的内容（图片、表情、自绘界面）用它自己看。"""
    _ensure()
    st = _get("/screen", fast=1, timeout=30)
    name = st.get("screenshot")
    if not name:
        raise RuntimeError("没拿到截图")
    r = requests.get(f"{API}/shots/{Path(name).name}", timeout=30)
    r.raise_for_status()
    return Image(data=r.content, format="png")


# ---------------------------------------------------------------- 写操作
@mcp.tool()
def wechat_send(to: str, text: str) -> str:
    """给联系人或群发一条文字消息，发完在聊天气泡里读回确认。会真的发出去：
    没有接管授权时，必须先把 to 和 text 给主人确认过再调用。"""
    r = _job("send", {"to": to, "text": text, "mode": "send", "allow_repeat": True})
    if r["status"] == "done":
        _record_reply(to, text)
    return _fmt(r)


@mcp.tool()
def wechat_post_moment(text: str, photos: int = 0, private: bool = False) -> str:
    """发朋友圈：文字 + 相册里最近的 photos 张照片（0=纯文字）。private=True 仅自己可见。
    会真的发出去，必须先给主人确认。"""
    return _fmt(_job("post_moment", {"text": text, "photos": photos, "private": private, "mode": "send"}))


@mcp.tool()
def wechat_set_chat(contact: str, setting: str, on: bool = True) -> str:
    """会话设置开关。setting 取「置顶聊天」或「消息免打扰」。"""
    return _fmt(_job("chat_setting", {"contact": contact, "setting": setting, "on": on, "mode": "send"}))


# ---------------------------------------------------------------- 接管
def _load_rules() -> dict:
    try:
        return {**DEFAULT_RULES, **json.loads(TAKEOVER.read_text())}
    except (OSError, ValueError):
        return dict(DEFAULT_RULES)


def _record_reply(to: str, text: str) -> None:
    log = HOME / "replies.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as f:
        f.write(json.dumps({"ts": int(time.time()), "to": to, "text": text}, ensure_ascii=False) + "\n")


@mcp.tool()
def wechat_takeover_rules(update: dict | None = None) -> str:
    """读取或修改接管规则（存在本机）。不给 update 就是读取。字段：
    mode: "ask"（每条回复先给主人过目）| "auto"（auto_contacts 里的人直接回，其余仍先问）；
    auto_contacts: 可以直接代回的联系人/群名列表；never: 永远不碰的会话；
    persona: 主人的身份、说话风格、常用语；notes: 其他交代；every_minutes: 巡视间隔。"""
    rules = _load_rules()
    if update:
        rules.update({k: v for k, v in update.items() if k in DEFAULT_RULES})
        TAKEOVER.parent.mkdir(parents=True, exist_ok=True)
        TAKEOVER.write_text(json.dumps(rules, ensure_ascii=False, indent=2))
    return _fmt(rules)


@mcp.tool()
def wechat_recent_replies(n: int = 20) -> str:
    """管家最近替主人发出去的消息（本机记录），用来汇报和避免重复回复。"""
    log = HOME / "replies.jsonl"
    if not log.exists():
        return "[]"
    lines = log.read_text().strip().splitlines()[-n:]
    return _fmt([json.loads(x) for x in lines])


TAKEOVER_PROMPT = """你现在是我的微信管家，通过 wechatbot 工具操作我 iPhone 上的真实微信。

第一步：调 wechat_status 确认手机就绪；再调 wechat_takeover_rules 读规则。
如果 persona 是空的，先问我三件事再开始：我是谁/怎么称呼、说话风格（可以让我举两句平时的回复）、哪些人可以直接替我回。问完用 wechat_takeover_rules 存下来。

然后巡视一轮：
1. wechat_inbox(unread_only=True) 看有谁找我。跳过 never 里的会话。
2. 对每个有未读的会话 wechat_read 读上下文，判断要不要回：
   - 广告、群里的闲聊、不需要我表态的，不回，只记一笔；
   - 需要回的，用我的口吻（persona）写回复，短、自然、像真人，不要说自己是 AI；
   - 涉及钱、答应时间/承诺、敏感话题、或者你没把握的，不回，标成「需要你亲自处理」。
3. 发送规则：mode=auto 且对方在 auto_contacts 里 → 直接 wechat_send；其他情况 → 把拟好的回复列给我，我说「发」再发。
4. 一轮结束后给我一份简报：谁找了我、说了什么、你回了什么、哪些等我处理。

我说「持续接管」时，用 /loop 每 every_minutes 分钟重复巡视一轮（只在有新消息时汇报）；我说「停」就停。"""


@mcp.prompt(name="takeover", description="一键接管微信：巡视未读、按你的口吻代回、汇报")
def takeover() -> str:
    return TAKEOVER_PROMPT


if __name__ == "__main__":
    mcp.run()
