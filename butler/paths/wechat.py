"""微信（com.tencent.xin）路径。无障碍很好：Cell/StaticText 规范，一屏 AX 约 1.4 s。

已知坑（真机实证）：
- 输入区默认语音态：左下角按钮显示「文本」时点它才出键盘，显示「语音」时点它会切成「按住 说话」。
- 系统键盘默认是拼音九宫格，英文/拼音会被输入法吞掉：输入前先用「下一个键盘」切到英文键盘。
- 语音消息 AX 只有 `Max,语音,4'',未播放`，点气泡旁「转文字」后文字直接挂回同一元素。
- 微信搜索页（黑底 + 键盘）曾被地标误认成 Instagram 搜索页，引擎已改为以 WDA 前台为准。
- 底部标签有时一次点不切（实测「我」），所以 goto_tab 点完必验、最多三次。
"""
from __future__ import annotations
import re

from butler import paths as P
from butler.paths.ctx import Ctx, PathBlocked, center, find, find_contains, names

APP = "wechat"
TABS = {"微信": "微信", "通讯录": "通讯录", "发现": "发现", "我": "我"}
VOICE_RE = re.compile(r"^(.+?),语音,(\d+)''(?:,(.*))?$")
KIND_RE = {
    "image": re.compile(r"^(.+?),图片,?$"),
    "video": re.compile(r"^(.+?),视频,?"),
    "file": re.compile(r"^(.+?),文件,(.*)$"),
    "location": re.compile(r"^(.+?),位置,(.*)$"),
    "card": re.compile(r"^(.+?),名片,(.*)$"),
    "link": re.compile(r"^(.+?),链接,(.*)$"),
    "sticker": re.compile(r"^(.+?),表情,?$"),
    "transfer": re.compile(r"^(.+?),转账,(.*)$"),
}


# ---------- 导航 ----------
def _drawer_open(d: dict) -> bool:
    """会话列表顶部再下拉会拉出「最近 / 小程序」抽屉，没有标签栏也没有返回键，只能上滑收起。"""
    return bool(find_contains(d, "最近使用的小程序") or find(d, "最近播放", "Button")
                or find_contains(d, "刚刚看过的直播"))


def _tab_active(d: dict, tab: str) -> bool:
    e = find(d, tab, "Button")
    return bool(e) and (e.get("value") == "1" or e.get("value") == 1)


def goto_tab(ctx: Ctx, tab: str, tries: int = 3) -> dict:
    """切到底部标签并验证（value=1 表示选中）。若当前不在一级页，先 back 几次。"""
    for i in range(tries):
        d = ctx.screen()
        if _drawer_open(d):
            ctx.swipe("up")
            ctx.wait(1.2)
            continue
        if _tab_active(d, tab) and find(d, "标签页栏", "TabBar"):
            return d
        if find(d, "标签页栏", "TabBar") is None:       # 二级页：返回
            if find(d, "返回", "Button"):
                ctx.tap("返回", soft=True)
            else:
                ctx.back()
            ctx.wait(1.2)
            continue
        e = find(d, tab, "Button")
        if e is None:
            raise PathBlocked("not_found", {"target": tab, "screen": d.get("screen_id")})
        cx, cy = center(e)
        ctx.tap(x=cx, y=cy - 6, soft=True)                     # 名字点击对「我」偶尔不生效，按控件中心坐标
        ctx.wait(1.5)
    d = ctx.screen()
    if not _tab_active(d, tab):
        raise PathBlocked("nav_failed", {"tab": tab})
    return d


def tap_found(ctx: Ctx, name: str, tag: str | None = None, expect=None, confirm: bool = False, tries: int = 2) -> dict:
    """深观察确认元素真的在屏上，再按它的矩形点。比按名字点稳：名字解析可能命中记忆里过期的矩形
    （实证：会话列表滚动后，记忆里的「搜索」矩形压在横幅上，点进了导入聊天记录页）。"""
    for _ in range(tries):
        d = ctx.screen()
        e = find(d, name, tag) or find_contains(d, name, tag)
        if e is not None:
            return ctx.tap_el(e, expect=expect, confirm=confirm)
        ctx.wait(1.0)
    raise PathBlocked("not_found", {"target": name, "screen": ctx.last.get("screen_id")})


def list_top(ctx: Ctx, marker: str, tag: str | None = None, tries: int = 6) -> dict:
    """把列表拉回顶部：直到 marker 元素可见。"""
    for _ in range(tries):
        d = ctx.screen()
        if _drawer_open(d):                          # 拉过头了：收起抽屉即回到顶部
            ctx.swipe("up")
            ctx.wait(1.2)
            return ctx.screen()
        if find(d, marker, tag) or find_contains(d, marker, tag):
            return d
        ctx.swipe("down")
        ctx.wait(1.0)
    return ctx.screen()


def to_root(ctx: Ctx, max_back: int = 6) -> dict:
    for _ in range(max_back):
        d = ctx.screen()
        if _drawer_open(d):
            ctx.swipe("up")
            ctx.wait(1.2)
            continue
        if find(d, "标签页栏", "TabBar") and not find(d, "返回", "Button"):
            return d
        if find(d, "返回", "Button"):
            ctx.tap("返回", soft=True)
        elif find(d, "取消", "Button"):
            ctx.tap("取消", soft=True)
        else:
            ctx.back()
        ctx.wait(1.2)
    return ctx.screen()


def chat_rows(d: dict) -> list[dict]:
    """会话列表行：Cell 名形如 `Max,[语音] 4",昨天 17:18`；未读数在 Cell 名里以 `,N条未读` 或首段数字出现。"""
    out = []
    for e in d.get("elements", []):
        if e.get("tag") != "Cell":
            continue
        nm = (e.get("name") or e.get("label") or "").strip()
        parts = [p.strip() for p in nm.split(",")]
        if len(parts) < 2:
            continue
        title = parts[0]
        unread = 0
        m = re.search(r"(\d+)条未读", nm)
        if m:
            unread = int(m.group(1))
        out.append({"id": f"chat:{title}", "title": title, "preview": ",".join(parts[1:-1]) if len(parts) > 2 else "",
                    "time": parts[-1], "unread": unread, "rect": e["rect"]})
    return out


def open_chat(ctx: Ctx, contact: str) -> dict:
    """打开与某联系人的会话：先在会话列表找，找不到走搜索。"""
    goto_tab(ctx, "微信")
    d = list_top(ctx, "搜索", "SearchField")
    for _ in range(3):
        row = next((r for r in chat_rows(d) if r["title"] == contact), None)
        if row:
            ctx.tap_el({"rect": row["rect"]}, expect="screen_change")
            ctx.wait(2.0)
            d2 = ctx.screen()
            if find(d2, contact, "NavigationBar") or find(d2, contact, "Other"):
                return d2
            return d2
        ctx.swipe("up")
        ctx.human_pause()
        d = ctx.screen()
    # 走搜索
    goto_tab(ctx, "微信")
    list_top(ctx, "搜索", "SearchField")
    tap_found(ctx, "搜索", "SearchField", expect="screen_change")
    ctx.wait(1.2)
    ensure_english_keyboard(ctx)
    ctx.type(contact)
    ctx.wait(2.0)
    d = ctx.screen()
    hit = _search_hit(d, "联系人", contact)
    if hit is None:
        raise PathBlocked("not_found", {"contact": contact, "hint": "搜索结果里没有这个联系人"})
    ctx.tap_el(hit, expect="screen_change")
    ctx.wait(2.0)
    return ctx.screen()


def ensure_english_keyboard(ctx: Ctx) -> None:
    d = ctx.screen()
    kb = find_contains(d, "下一个键盘", "Button")
    if kb and "拼音" not in (kb.get("value") or ""):     # value 是「下一个」要切到的键盘：显示「简体拼音」说明当前是英文
        ctx.tap_el(kb, soft=True)
        ctx.wait(1.0)


SEARCH_HEADERS = {"最常使用", "联系人", "群聊", "聊天记录", "公众号", "小程序", "搜索网络结果", "搜一搜", "视频号", "文章", "朋友圈"}
SEARCH_KINDS = {"联系人", "群聊", "公众号", "聊天记录", "小程序", "视频号"}


def search_results(d: dict) -> list[dict]:
    """搜索结果：带头像的本地条目 x≈68（下一行小字是类型「联系人/群聊/公众号」），网络联想词 x≈46。
    分区标题（最常使用/联系人/搜索网络结果…）会随情况变，不能靠它定位。"""
    els = sorted([e for e in d.get("elements", []) if e.get("tag") == "StaticText" and 110 < e["rect"][1] < 800],
                 key=lambda e: e["rect"][1])
    out, section = [], None
    for i, e in enumerate(els):
        nm = (e.get("name") or e.get("label") or "").strip()
        x = e["rect"][0]
        if not nm:
            continue
        if nm in SEARCH_HEADERS and x < 60:
            section = nm
            continue
        if nm in SEARCH_KINDS and x >= 60:            # 条目下方的类型小字，归到上一条
            if out and out[-1].get("kind") is None:
                out[-1]["kind"] = nm
            continue
        if nm.startswith("网络查找账号"):
            out.append({"text": nm.split(":", 1)[-1], "kind": "网络查找账号", "section": section})
            continue
        kind = None
        if x >= 60:
            kind = "本地" if section in (None, "最常使用") else section
        else:
            kind = "网络联想" if section == "搜索网络结果" else (section or "其他")
        out.append({"text": nm, "kind": kind, "section": section, "rect": e["rect"]})
    return out


def _search_hit(d: dict, section: str, text: str) -> dict | None:
    """本地条目里（联系人/群聊，带头像 x≥60）第一个等于 text 的。"""
    for r in search_results(d):
        if r["text"] == text and r.get("rect") and r["rect"][0] >= 60 and r["kind"] in ("本地", "联系人", "群聊", section):
            return r
    return None


# ---------- 消息解析 ----------
def voice_state(nm: str):
    m = VOICE_RE.match(nm.strip())
    return (m.group(1), m.group(2), (m.group(3) or "").strip()) if m else None


def parse_messages(d: dict) -> list[dict]:
    out = []
    for e in d.get("elements", []):
        nm = (e.get("name") or e.get("label") or "").strip()
        x, y, w, h = e["rect"]
        if not nm or not (50 < y < 810) or e["tag"] not in ("Other", "StaticText") or "," not in nm:
            continue
        if w < 300:                                             # 气泡行是通栏 Other
            continue
        st = voice_state(nm)
        if st:
            who, secs, tail = st
            spoken = tail if tail not in ("", "未播放", "已播放") else ""
            out.append({"who": who, "text": spoken or f"[语音 {secs}″ 未转文字]", "kind": "voice",
                        "seconds": int(secs), "transcribed": bool(spoken), "y": y, "rect": e["rect"]})
            continue
        kind = "text"
        for k, rx in KIND_RE.items():
            if rx.match(nm):
                kind = k
                break
        who, _, text = nm.partition(",")
        if who and len(who) <= 24:
            out.append({"who": who, "text": text.rstrip(","), "kind": kind, "y": y, "rect": e["rect"]})
    out.sort(key=lambda m: m["y"])
    seen, uniq = set(), []
    for m in out:
        key = (m["who"], m["text"], m["kind"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(m)
    return uniq


def transcribe_voices(ctx: Ctx, tries: int = 4) -> int:
    done = 0
    for _ in range(tries):
        pending = [m for m in parse_messages(ctx.screen()) if m["kind"] == "voice" and not m["transcribed"]]
        if not pending:
            break
        x, y, w, h = pending[0]["rect"]
        ctx.tap(x=220, y=y + h // 2 + 25, soft=True)           # 气泡旁「转文字」快捷按钮（实测坐标）
        ctx.wait(4.0)
        done += 1
    return done


def _msg_id(m: dict) -> str:
    return f"{m['who']}|{m['kind']}|{m['text'][:80]}"


# ---------- 输入 ----------
def open_keyboard(ctx: Ctx) -> bool:
    for _ in range(3):
        d = ctx.screen()
        ns = set(names(d))
        if "Send" in ns or "发送" in ns:
            return True
        target = "文本" if "文本" in ns else ("语音" if "语音" in ns else None)
        if target is None:
            return False
        ctx.tap(target, expect="screen_change", soft=True)
        ctx.wait(1.5)
    return "Send" in set(names(ctx.screen()))


def type_and_send(ctx: Ctx, text: str) -> bool:
    if not open_keyboard(ctx):
        raise PathBlocked("no_keyboard", {"hint": "切不到键盘输入态"})
    ctx.type(text)
    ctx.wait(1.0)
    ctx.tap("Send", confirm=True, expect="screen_change", soft=True)
    ctx.wait(2.5)
    return any(m["who"] == "我" and text in m["text"] for m in parse_messages(ctx.screen()))


# =====================================================================
# 采集
# =====================================================================
@P.path(APP, "inbox", doc="会话列表：标题/预览/时间/未读数", params={"n": "最多条数", "unread_only": "只要有未读的"}, star=True)
def inbox(ctx: Ctx, n: int = 30, unread_only: bool = False):
    goto_tab(ctx, "微信")
    list_top(ctx, "搜索", "SearchField")

    def extract(d):
        rows = chat_rows(d)
        return [r for r in rows if r["unread"] > 0] if unread_only else rows
    ctx.collect(extract, lambda r: r["id"], int(n))


@P.path(APP, "messages", doc="某会话当前可见消息（语音先转文字；图片留截图）", params={"contact": "联系人显示名"}, star=True)
def messages(ctx: Ctx, contact: str):
    open_chat(ctx, contact)
    n = transcribe_voices(ctx)
    d = ctx.screen()
    for m in parse_messages(d):
        it = {k: v for k, v in m.items() if k not in ("y", "rect")}
        it["contact"] = contact
        if m["kind"] == "image":
            it["screenshot"] = d.get("screenshot")
        ctx.emit(it, _msg_id(m))
    if n:
        ctx.note(f"转写了 {n} 条语音")


@P.path(APP, "history", doc="某会话历史：反复上翻直到不再增加", params={"contact": "联系人", "n": "最多条数"}, star=True)
def history(ctx: Ctx, contact: str, n: int = 50):
    open_chat(ctx, contact)
    transcribe_voices(ctx)

    def extract(d):
        return [dict({k: v for k, v in m.items() if k not in ("y", "rect")}, contact=contact) for m in parse_messages(d)]
    ctx.collect(extract, _msg_id, int(n), direction="down", stall=2)


@P.path(APP, "search", doc="全局搜索：联系人/群聊/聊天记录/公众号分区结果", params={"q": "关键词"}, star=True)
def search(ctx: Ctx, q: str):
    goto_tab(ctx, "微信")
    list_top(ctx, "搜索", "SearchField")
    tap_found(ctx, "搜索", "SearchField", expect="screen_change")
    ctx.wait(1.2)
    ensure_english_keyboard(ctx)
    ctx.type(q)
    ctx.wait(2.5)
    d = ctx.screen()
    for r in search_results(d):
        k = r["kind"] if r["kind"] != "本地" else "联系人"
        ctx.emit({"kind": k, "section": r.get("section"), "text": r["text"], "q": q}, f"{k}:{r['text']}")
    tap_found(ctx, "返回", "Button")


@P.path(APP, "contacts", doc="通讯录全量（按字母滚动）", params={"n": "最多条数"}, star=True)
def contacts(ctx: Ctx, n: int = 200):
    goto_tab(ctx, "通讯录")
    list_top(ctx, "新的朋友")

    def extract(d):
        out = []
        for e in d.get("elements", []):
            if e.get("tag") == "Cell":
                nm = (e.get("name") or "").strip().rstrip(",").strip()
                if nm and nm not in ("新的朋友", "仅聊天的朋友", "群聊", "标签", "公众号", "企业微信联系人", "服务号"):
                    out.append({"name": nm})
        return out
    ctx.collect(extract, lambda r: r["name"], int(n), stall=2)


def _find_contact_cell(ctx: Ctx, contact: str) -> dict:
    goto_tab(ctx, "通讯录")
    d = list_top(ctx, "新的朋友")
    for _ in range(8):
        cell = next((e for e in d.get("elements", []) if e.get("tag") == "Cell"
                     and (e.get("name") or "").strip().rstrip(",").strip() == contact), None)
        if cell:
            return cell
        ctx.swipe("up")
        ctx.human_pause()
        d = ctx.screen()
    raise PathBlocked("not_found", {"contact": contact})


@P.path(APP, "card", kind="action", doc="联系人名片：昵称/微信号/地区/备注", params={"contact": "联系人"}, star=True)
def card(ctx: Ctx, contact: str):
    cell = _find_contact_cell(ctx, contact)
    ctx.tap_el(cell, expect="screen_change")
    ctx.wait(1.5)
    d = ctx.screen()
    texts = [(e.get("name") or e.get("label") or "").strip() for e in d.get("elements", []) if e.get("tag") == "StaticText"]
    out = {"contact": contact, "wechat_id": None, "region": None, "remark": None, "raw": texts[:20],
           "screenshot": d.get("screenshot")}
    for i, t in enumerate(texts):
        if t.startswith("微信号") and i + 1 < len(texts):
            out["wechat_id"] = texts[i + 1] if t.endswith("：") else t.split("：", 1)[-1]
        if t.startswith("地区") and i + 1 < len(texts):
            out["region"] = texts[i + 1] if t.endswith("：") else t.split("：", 1)[-1]
        if t.startswith("备注"):
            out["remark"] = t.split("：", 1)[-1]
    ctx.tap("返回", soft=True)
    return out


@P.path(APP, "friend_requests", doc="新的朋友：待处理的好友申请", params={})
def friend_requests(ctx: Ctx):
    goto_tab(ctx, "通讯录")
    list_top(ctx, "新的朋友")
    tap_found(ctx, "新的朋友", expect="screen_change")
    ctx.wait(1.5)
    d = ctx.screen()
    for e in d.get("elements", []):
        if e.get("tag") == "Cell":
            nm = (e.get("name") or "").strip()
            if nm:
                ctx.emit({"row": nm, "pending": "接受" in nm or "查看" in nm}, nm)
    ctx.tap("返回", soft=True)


@P.path(APP, "moments", doc="朋友圈动态列表（作者/文字/图数/时间）", params={"n": "最多条数"}, star=True)
def moments(ctx: Ctx, n: int = 20):
    _open_moments_feed(ctx)

    def extract(d):
        return _moment_rows(d)
    ctx.collect(extract, lambda r: r["id"], int(n), stall=2)
    to_root(ctx)


def _open_moments_feed(ctx: Ctx) -> dict:
    """朋友圈入口。发现页开了「朋友圈」就进好友信息流；这台机的发现页没开（发现页管理在此版本里找不到），
    退回「我 → 朋友圈 → 我的朋友圈」＝自己的朋友圈相册（有发表入口和历史动态）。"""
    d = goto_tab(ctx, "发现")
    if find(d, "朋友圈", "StaticText"):
        tap_found(ctx, "朋友圈", "StaticText", expect="screen_change")
        ctx.wait(2.5)
        return ctx.screen()
    goto_tab(ctx, "我")
    tap_found(ctx, "朋友圈", "StaticText", expect="screen_change")
    ctx.wait(2.0)
    d = ctx.screen()
    e = find(d, "我的朋友圈", "Button") or find(d, "前往朋友圈", "StaticText")
    if e is not None:
        ctx.tap_el(e, expect="screen_change")
        ctx.wait(2.5)
    return ctx.screen()


_ALBUM_SKIP = {"发表", "私密发表", "今天", "Anna"}


def _moment_rows(d: dict) -> list[dict]:
    """好友信息流：Cell 名「作者,正文…」；自己的相册：StaticText 按年/日分组，日期行形如「3月12日」。"""
    out = []
    cells = [e for e in d.get("elements", []) if e.get("tag") == "Cell" and "," in (e.get("name") or "")]
    if cells:
        for e in cells:
            parts = [p.strip() for p in (e.get("name") or "").split(",")]
            out.append({"id": f"moment:{parts[0]}:{','.join(parts[1:])[:40]}", "author": parts[0],
                        "text": ",".join(parts[1:]), "rect": e["rect"]})
        return out
    date = None
    for e in sorted([e for e in d.get("elements", []) if e.get("tag") == "StaticText"], key=lambda e: e["rect"][1]):
        t = (e.get("name") or e.get("label") or "").strip()
        if not t or t in _ALBUM_SKIP or e["rect"][1] < 200:
            continue
        if re.fullmatch(r"\d{4}年|\d{1,2}月\d{1,2}日|昨天|今天", t):
            date = t
            continue
        out.append({"id": f"album:{date}:{t[:40]}", "author": "我", "date": date, "text": t, "rect": e["rect"]})
    return out


@P.path(APP, "moments_of", doc="某联系人的朋友圈", params={"contact": "联系人", "n": "最多条数"})
def moments_of(ctx: Ctx, contact: str, n: int = 20):
    cell = _find_contact_cell(ctx, contact)
    ctx.tap_el(cell, expect="screen_change")
    ctx.wait(1.5)
    ctx.tap("朋友圈", expect="screen_change")
    ctx.wait(2.5)

    def extract(d):
        out = []
        for b in d.get("text_blocks", []):
            t = b["text"]
            if len(t) > 3 and 100 < b["rect"][1] < 800 and t not in (contact,):
                out.append({"id": f"m:{contact}:{t[:40]}", "author": contact, "text": t})
        return out
    ctx.collect(extract, lambda r: r["id"], int(n), stall=2)
    to_root(ctx)


@P.path(APP, "favorites", doc="收藏列表", params={"n": "最多条数"})
def favorites(ctx: Ctx, n: int = 30):
    goto_tab(ctx, "我")
    ctx.tap("收藏", expect="screen_change")
    ctx.wait(2.0)

    def extract(d):
        return [{"id": f"fav:{(e.get('name') or '')[:60]}", "text": (e.get("name") or "").strip()}
                for e in d.get("elements", []) if e.get("tag") == "Cell" and (e.get("name") or "").strip()]
    ctx.collect(extract, lambda r: r["id"], int(n), stall=2)
    to_root(ctx)


@P.path(APP, "group_members", doc="群成员列表", params={"group": "群名", "n": "最多人数"}, star=True)
def group_members(ctx: Ctx, group: str, n: int = 100):
    open_chat(ctx, group)
    ctx.tap("更多", expect="screen_change")
    ctx.wait(2.0)

    def extract(d):
        out = []
        for e in d.get("elements", []):
            if e.get("tag") in ("StaticText", "Button", "Cell"):
                nm = (e.get("name") or e.get("label") or "").strip()
                if nm and 130 < e["rect"][1] < 780 and e["rect"][2] < 120 and nm not in ("群聊名称", "群公告", "备注", "我在本群的昵称"):
                    out.append({"name": nm})
        return out
    ctx.collect(extract, lambda r: r["name"], int(n), stall=2)
    to_root(ctx)


@P.path(APP, "group_info", kind="action", doc="群详情：群名/成员数/公告", params={"group": "群名"})
def group_info(ctx: Ctx, group: str):
    open_chat(ctx, group)
    ctx.tap("更多", expect="screen_change")
    ctx.wait(2.0)
    d = ctx.screen()
    texts = [(e.get("name") or e.get("label") or "").strip() for e in d.get("elements", []) if e.get("tag") == "StaticText"]
    out = {"group": group, "texts": texts[:40], "screenshot": d.get("screenshot")}
    m = re.search(r"聊天成员\s*\(?(\d+)\)?", " ".join(texts))
    out["member_count"] = int(m.group(1)) if m else None
    to_root(ctx)
    return out


@P.path(APP, "mentions", doc="群里 @我 的消息（扫会话列表里带 [有人@我] 的群）", params={"n": "最多条数"})
def mentions(ctx: Ctx, n: int = 20):
    goto_tab(ctx, "微信")
    d = ctx.screen()
    hits = [r for r in chat_rows(d) if "@我" in r["preview"]]
    for r in hits[: int(n)]:
        ctx.emit({"group": r["title"], "preview": r["preview"], "time": r["time"]}, f"mention:{r['title']}:{r['time']}")


# =====================================================================
# 写操作
# =====================================================================
@P.path(APP, "send", kind="action", writes=True, doc="给指定联系人发文字，发完在气泡读回",
        params={"to": "联系人", "text": "内容", "mode": "draft|send", "allow_repeat": "允许重复发"}, star=True)
def send(ctx: Ctx, to: str, text: str, mode: str = "draft", allow_repeat: bool = False):
    ctx.draft_or_go(mode, f"微信 → {to}: {text}", {"to": to, "text": text, "allow_repeat": allow_repeat})
    ctx.ledger_guard("dm", to, allow_repeat)
    open_chat(ctx, to)
    ok = type_and_send(ctx, text)
    if not ok:
        raise PathBlocked("send_unverified", {"hint": "点了发送但气泡里没读回", "screenshot": ctx.shot()})
    ctx.ledger_record("dm", to, text)
    to_root(ctx)
    return {"sent": True, "to": to, "text": text, "screenshot": ctx.shot()}


@P.path(APP, "send_bulk", kind="action", writes=True, doc="名单群发（模板变量 {name}），每人只发一次",
        params={"targets": "联系人列表", "template": "模板", "mode": "draft|send"}, star=True)
def send_bulk(ctx: Ctx, targets: list, template: str, mode: str = "draft", allow_repeat: bool = False):
    ctx.draft_or_go(mode, f"微信群发 {len(targets)} 人: {template[:40]}", {"targets": targets, "template": template})
    results = []
    for t in targets:
        text = template.replace("{name}", t)
        try:
            ctx.ledger_guard("dm", t, allow_repeat)
            open_chat(ctx, t)
            ok = type_and_send(ctx, text)
            if ok:
                ctx.ledger_record("dm", t, text)
            results.append({"to": t, "sent": ok})
            to_root(ctx)
        except PathBlocked as b:
            results.append({"to": t, "sent": False, "reason": b.reason})
        ctx.human_pause()
    return {"results": results}


def open_more_panel(ctx: Ctx) -> dict:
    """聊天页右下「更多」(+) 面板：相册/拍摄/位置/名片/文件…"""
    d = ctx.screen()
    more = [e for e in d.get("elements", []) if (e.get("name") == "更多") and e.get("tag") == "Button" and e["rect"][1] > 700]
    if not more:
        raise PathBlocked("not_found", {"target": "更多(+)"})
    ctx.tap_el(more[0], expect="screen_change")
    ctx.wait(1.5)
    return ctx.screen()


@P.path(APP, "send_photo", kind="action", writes=True, doc="从相册选最近第 nth 张发给联系人",
        params={"to": "联系人", "nth": "第几张（1=最新）", "mode": "draft|send"}, star=True)
def send_photo(ctx: Ctx, to: str, nth: int = 1, mode: str = "draft", allow_repeat: bool = True):
    ctx.draft_or_go(mode, f"微信 → {to}: 相册第 {nth} 张图", {"to": to, "nth": nth})
    ctx.ledger_guard("photo", to, allow_repeat)
    open_chat(ctx, to)
    open_more_panel(ctx)
    ctx.tap("相册", expect="screen_change")
    ctx.wait(2.5)
    d = ctx.screen()
    if find(d, "允许访问所有照片", "Button"):
        ctx.tap("允许访问所有照片", soft=True)
        ctx.wait(1.5)
        d = ctx.screen()
    cells = sorted([e for e in d.get("elements", []) if e.get("tag") in ("Cell", "Image", "Button")
                    and 90 < e["rect"][1] < 780 and 60 < e["rect"][2] < 200 and abs(e["rect"][2] - e["rect"][3]) < 20],
                   key=lambda e: (e["rect"][1], e["rect"][0]))
    if len(cells) < nth:
        raise PathBlocked("not_found", {"hint": f"相册网格里不足 {nth} 张", "found": len(cells)})
    ctx.tap_el(cells[nth - 1], soft=True)
    ctx.wait(1.2)
    d = ctx.screen()
    snd = find_contains(d, "发送", "Button") or find_contains(d, "发送", "StaticText")
    if snd is None:
        raise PathBlocked("not_found", {"target": "发送", "screenshot": d.get("screenshot")})
    ctx.tap_el(snd, confirm=True, expect="screen_change")
    ctx.wait(4.0)
    d = ctx.screen()
    ok = any(m["who"] == "我" and m["kind"] == "image" for m in parse_messages(d))
    if not ok:
        raise PathBlocked("send_unverified", {"screenshot": d.get("screenshot")})
    ctx.ledger_record("photo", to, f"photo#{nth}")
    to_root(ctx)
    return {"sent": True, "to": to, "screenshot": d.get("screenshot")}


@P.path(APP, "send_attachment", kind="action", writes=True, doc="发名片/位置/文件/收藏（更多面板里的项）",
        params={"to": "联系人", "kind": "名片|位置|文件|收藏", "pick": "要选的条目名（名片=联系人名）", "mode": "draft|send"})
def send_attachment(ctx: Ctx, to: str, kind: str, pick: str | None = None, mode: str = "draft"):
    ctx.draft_or_go(mode, f"微信 → {to}: {kind} {pick or ''}", {"to": to, "kind": kind, "pick": pick})
    open_chat(ctx, to)
    d = open_more_panel(ctx)
    if find(d, kind) is None:
        ctx.swipe("left")
        ctx.wait(1.0)
    ctx.tap(kind, expect="screen_change")
    ctx.wait(2.0)
    d = ctx.screen()
    if kind == "名片" and pick:
        hit = next((e for e in d.get("elements", []) if e.get("tag") == "Cell"
                    and (e.get("name") or "").strip().rstrip(",").strip() == pick), None)
        if hit is None:
            raise PathBlocked("not_found", {"pick": pick})
        ctx.tap_el(hit, expect="screen_change")
        ctx.wait(1.5)
        d = ctx.screen()
        snd = find(d, "发送", "Button") or find_contains(d, "发送", "Button")
        if snd:
            ctx.tap_el(snd, confirm=True, expect="screen_change")
            ctx.wait(3.0)
    elif kind == "位置":
        ctx.tap("发送位置", expect="screen_change", soft=True)
        ctx.wait(4.0)
        d = ctx.screen()
        snd = find(d, "发送", "Button")
        if snd:
            ctx.tap_el(snd, confirm=True, expect="screen_change")
            ctx.wait(3.0)
    d = ctx.screen()
    kinds = {"名片": "card", "位置": "location", "文件": "file", "收藏": "link"}
    ok = any(m["who"] == "我" and m["kind"] == kinds.get(kind, "text") for m in parse_messages(d))
    to_root(ctx)
    return {"sent": ok, "to": to, "kind": kind, "screenshot": d.get("screenshot")}


@P.path(APP, "forward", kind="action", writes=True, doc="把会话里最后一条含 match 的消息转发给别人",
        params={"from_contact": "来源会话", "match": "消息里包含的文字", "to": "转发给谁", "mode": "draft|send"})
def forward(ctx: Ctx, from_contact: str, match: str, to: str, mode: str = "draft"):
    ctx.draft_or_go(mode, f"微信转发 {from_contact}「{match}」→ {to}", {"from_contact": from_contact, "match": match, "to": to})
    open_chat(ctx, from_contact)
    msgs = [m for m in parse_messages(ctx.screen()) if match in m["text"]]
    if not msgs:
        raise PathBlocked("not_found", {"match": match})
    m = msgs[-1]
    x, y, w, h = m["rect"]
    ctx.act(op="long_press", x=int(x + w * 0.5), y=int(y + h * 0.5), soft=True)
    ctx.wait(1.5)
    d = ctx.screen()
    fw = find(d, "转发") or find_contains(d, "转发")
    if fw is None:
        raise PathBlocked("not_found", {"target": "转发", "hint": "长按菜单可能不进无障碍树", "screenshot": d.get("screenshot")})
    ctx.tap_el(fw, expect="screen_change")
    ctx.wait(1.5)
    d = ctx.screen()
    hit = next((e for e in d.get("elements", []) if e.get("tag") == "Cell" and (e.get("name") or "").startswith(to)), None)
    if hit is None:
        ctx.tap("搜索", soft=True)
        ensure_english_keyboard(ctx)
        ctx.type(to)
        ctx.wait(1.5)
        d = ctx.screen()
        hit = next((e for e in d.get("elements", []) if e.get("tag") == "Cell" and (e.get("name") or "").startswith(to)), None)
    if hit is None:
        raise PathBlocked("not_found", {"to": to})
    ctx.tap_el(hit, soft=True)
    ctx.wait(1.2)
    d = ctx.screen()
    snd = find(d, "发送", "Button") or find_contains(d, "发送", "Button")
    if snd is None:
        raise PathBlocked("not_found", {"target": "发送", "screenshot": d.get("screenshot")})
    ctx.tap_el(snd, confirm=True, expect="screen_change")
    ctx.wait(2.5)
    to_root(ctx)
    return {"forwarded": True, "to": to, "text": m["text"]}


@P.path(APP, "recall", kind="action", writes=True, doc="撤回我最后一条含 match 的消息（2 分钟内）",
        params={"contact": "会话", "match": "消息文字", "mode": "draft|send"})
def recall(ctx: Ctx, contact: str, match: str, mode: str = "draft"):
    ctx.draft_or_go(mode, f"微信撤回 {contact}「{match}」", {"contact": contact, "match": match})
    open_chat(ctx, contact)
    msgs = [m for m in parse_messages(ctx.screen()) if m["who"] == "我" and match in m["text"]]
    if not msgs:
        raise PathBlocked("not_found", {"match": match})
    x, y, w, h = msgs[-1]["rect"]
    ctx.act(op="long_press", x=int(x + w * 0.7), y=int(y + h * 0.5), soft=True)
    ctx.wait(1.5)
    d = ctx.screen()
    rc = find(d, "撤回") or find_contains(d, "撤回")
    if rc is None:
        raise PathBlocked("not_found", {"target": "撤回", "screenshot": d.get("screenshot")})
    ctx.tap_el(rc, confirm=True, expect="screen_change")
    ctx.wait(2.0)
    d = ctx.screen()
    ok = any("撤回了一条消息" in (e.get("name") or "") for e in d.get("elements", []))
    to_root(ctx)
    return {"recalled": ok}


@P.path(APP, "chat_setting", kind="action", writes=True, doc="会话设置开关：置顶聊天 / 消息免打扰",
        params={"contact": "会话", "setting": "置顶聊天|消息免打扰", "on": "true/false", "mode": "draft|send"})
def chat_setting(ctx: Ctx, contact: str, setting: str, on: bool = True, mode: str = "draft"):
    ctx.draft_or_go(mode, f"微信 {contact} {setting}={on}", {"contact": contact, "setting": setting, "on": on})
    open_chat(ctx, contact)
    ctx.tap("更多", expect="screen_change")
    ctx.wait(2.0)
    d = ctx.screen()
    sw = None
    for _ in range(4):
        sw = next((e for e in d.get("elements", []) if e.get("tag") == "Switch" and setting in (e.get("name") or e.get("label") or "")), None)
        if sw:
            break
        ctx.swipe("up")
        ctx.wait(1.0)
        d = ctx.screen()
    if sw is None:
        raise PathBlocked("not_found", {"setting": setting})
    cur = str(sw.get("value")) in ("1", "true", "True")
    if cur != bool(on):
        ctx.tap_el(sw, confirm=True, soft=True)
        ctx.wait(1.5)
        d = ctx.screen()
        sw = next((e for e in d.get("elements", []) if e.get("tag") == "Switch" and setting in (e.get("name") or e.get("label") or "")), sw)
    to_root(ctx)
    return {"setting": setting, "on": str(sw.get("value")) in ("1", "true", "True")}


@P.path(APP, "post_moment", kind="action", writes=True, doc="发朋友圈：文字 + 相册最近 photos 张图（0=纯文字）",
        params={"text": "文字", "photos": "图片张数", "mode": "draft|send"}, star=True)
def post_moment(ctx: Ctx, text: str, photos: int = 1, mode: str = "draft", private: bool = False):
    ctx.draft_or_go(mode, f"发朋友圈{'（私密）' if private else ''}: {text[:40]} + {photos} 图",
                    {"text": text, "photos": photos, "private": private})
    _open_moments_feed(ctx)
    d = ctx.screen()
    cam = (find(d, "私密发表", "StaticText") if private else None) or find(d, "发表", "StaticText") \
        or find(d, "拍照分享", "Button") or find_contains(d, "拍照", "Button")
    if cam is None:
        raise PathBlocked("not_found", {"target": "朋友圈发表入口", "screenshot": d.get("screenshot")})
    if photos <= 0 and cam.get("tag") == "Button":
        ctx.act(op="long_press", x=center(cam)[0], y=center(cam)[1], soft=True)
    else:
        ctx.tap_el(cam, expect="screen_change")
    ctx.wait(1.5)
    d = ctx.screen()
    if photos > 0:
        pick = find(d, "从相册选择") or find_contains(d, "相册")
        if pick:
            ctx.tap_el(pick, expect="screen_change")
            ctx.wait(2.5)
            d = ctx.screen()
        cells = sorted([e for e in d.get("elements", []) if e.get("tag") in ("Cell", "Image", "Button")
                        and 90 < e["rect"][1] < 780 and 60 < e["rect"][2] < 200 and abs(e["rect"][2] - e["rect"][3]) < 20],
                       key=lambda e: (e["rect"][1], e["rect"][0]))
        for c in cells[:photos]:
            ctx.tap_el(c, soft=True)
            ctx.wait(0.8)
        d = ctx.screen()
        nxt = find(d, "完成", "Button") or find(d, "下一步", "Button") or find_contains(d, "完成", "Button")
        if nxt is None:
            raise PathBlocked("not_found", {"target": "完成", "screenshot": d.get("screenshot")})
        ctx.tap_el(nxt, expect="screen_change")
        ctx.wait(2.0)
    d = ctx.screen()
    tv = next((e for e in d.get("elements", []) if e.get("tag") == "TextView"), None)
    if tv:
        ctx.tap_el(tv, soft=True)
        ctx.wait(0.8)
    ctx.type(text)
    ctx.wait(1.0)
    d = ctx.screen()
    pub = find(d, "发表", "Button") or find_contains(d, "发表", "Button")
    if pub is None:
        raise PathBlocked("not_found", {"target": "发表", "screenshot": d.get("screenshot")})
    ctx.tap_el(pub, confirm=True, expect="screen_change")
    ctx.wait(4.0)
    d = ctx.screen()
    ok = any(text[:10] in (e.get("name") or "") for e in d.get("elements", []))
    to_root(ctx)
    return {"posted": ok, "text": text, "screenshot": d.get("screenshot")}


@P.path(APP, "moment_react", kind="action", writes=True, doc="给朋友圈第一条含 match 的动态点赞或评论",
        params={"match": "动态里包含的文字或作者", "action": "like|comment", "text": "评论内容", "mode": "draft|send"}, star=True)
def moment_react(ctx: Ctx, match: str, action: str = "like", text: str = "", mode: str = "draft"):
    ctx.draft_or_go(mode, f"朋友圈 {action} 「{match}」 {text}", {"match": match, "action": action, "text": text})
    _open_moments_feed(ctx)
    row = None
    for _ in range(6):
        d = ctx.screen()
        row = next((r for r in _moment_rows(d) if match in r["author"] or match in r["text"]), None)
        if row:
            break
        ctx.swipe("up")
        ctx.human_pause()
    if row is None:
        raise PathBlocked("not_found", {"match": match})
    x, y, w, h = row["rect"]
    d = ctx.screen()
    btn = next((e for e in d.get("elements", []) if e.get("tag") == "Button" and (e.get("name") in ("评论", "更多", "赞"))
                and y <= e["rect"][1] <= y + h), None)
    if btn is None:
        raise PathBlocked("not_found", {"target": "评论按钮", "screenshot": d.get("screenshot")})
    ctx.tap_el(btn, soft=True)
    ctx.wait(1.2)
    d = ctx.screen()
    if action == "like":
        like = find(d, "赞", "Button") or find(d, "赞") or find(d, "取消", "Button")
        if like is None:
            raise PathBlocked("not_found", {"target": "赞", "screenshot": d.get("screenshot")})
        ctx.tap_el(like, confirm=True, soft=True)
        ctx.wait(1.5)
        to_root(ctx)
        return {"liked": True, "match": match}
    cm = find(d, "评论", "Button") or find(d, "评论")
    if cm is None:
        raise PathBlocked("not_found", {"target": "评论", "screenshot": d.get("screenshot")})
    ctx.tap_el(cm, expect="screen_change", soft=True)
    ctx.wait(1.2)
    ctx.type(text)
    ctx.wait(0.8)
    d = ctx.screen()
    snd = find(d, "发送", "Button") or find(d, "Send", "Button")
    if snd is None:
        raise PathBlocked("not_found", {"target": "发送", "screenshot": d.get("screenshot")})
    ctx.tap_el(snd, confirm=True, soft=True)
    ctx.wait(2.0)
    d = ctx.screen()
    ok = any(text in (e.get("name") or "") for e in d.get("elements", []))
    to_root(ctx)
    return {"commented": ok, "match": match, "text": text}


@P.path(APP, "create_group", kind="action", writes=True, doc="发起群聊并拉人", params={"members": "联系人列表", "mode": "draft|send"})
def create_group(ctx: Ctx, members: list, mode: str = "draft"):
    ctx.draft_or_go(mode, f"微信建群: {members}", {"members": members})
    goto_tab(ctx, "微信")
    ctx.tap("快捷操作", expect="screen_change")
    ctx.wait(1.2)
    ctx.tap("发起群聊", expect="screen_change")
    ctx.wait(2.0)
    for m in members:
        d = ctx.screen()
        cell = next((e for e in d.get("elements", []) if e.get("tag") == "Cell" and (e.get("name") or "").strip().startswith(m)), None)
        if cell is None:
            raise PathBlocked("not_found", {"member": m})
        ctx.tap_el(cell, soft=True)
        ctx.wait(0.8)
    d = ctx.screen()
    done = find_contains(d, "完成", "Button")
    if done is None:
        raise PathBlocked("not_found", {"target": "完成"})
    ctx.tap_el(done, confirm=True, expect="screen_change")
    ctx.wait(3.0)
    d = ctx.screen()
    to_root(ctx)
    return {"created": True, "screenshot": d.get("screenshot")}


# =====================================================================
# 自动回复
# =====================================================================
def _pick_reply(last_text: str, rules: list, default: str | None) -> str | None:
    for r in rules or []:
        kws = r.get("match") if isinstance(r.get("match"), list) else [r.get("match")]
        if any(k and k in last_text for k in kws):
            return r.get("reply")
    return default


def _llm_reply(ctx: Ctx, contact: str, history: list[dict], prompt: str) -> str | None:
    """用引擎的 OpenRouter 客户端按对话上下文生成回复；没配 key 或出错就返回 None（退回规则/默认话术）。"""
    try:
        from engine.llm import make_client
        client = make_client(ctx.actions.root)
    except Exception:  # noqa: BLE001
        client = None
    if client is None:
        return None
    convo = "\n".join(f"{'我' if m['who'] == '我' else contact}: {m['text']}" for m in history[-12:])
    msgs = [{"role": "system", "content": prompt},
            {"role": "user", "content": f"以下是微信里和「{contact}」的最近对话，最后一条是对方发的。请直接给出我要回复的一句话，不要解释：\n{convo}"}]
    try:
        r = client.chat(msgs, purpose="wechat_auto_reply", ref=contact, max_tokens=200)
        text = (getattr(r, "text", None) or (r.get("text") if isinstance(r, dict) else None) or "").strip()
        return text.strip('"').strip() or None
    except Exception as e:  # noqa: BLE001
        ctx.note(f"LLM 回复失败：{type(e).__name__}: {e}"[:200])
        return None


def _already_replied(ctx: Ctx, contact: str, last_text: str) -> bool:
    if ctx.ledger is None:
        return False
    key = last_text[:80]
    return any(r.get("target") == contact and r.get("in_reply_to") == key
               for r in ctx.ledger.list(APP, "reply", limit=500))


@P.path(APP, "auto_reply", kind="action", writes=True,
        doc="自动回复：扫会话（有未读的 / 指定联系人 / 列表前 scan_top 个），对方最后一条 → 规则命中话术 → 否则 LLM（配了 key 且给了 llm_prompt）→ 否则 default。"
            "同一条来信只回一次（台账）。默认草稿，mode=send 直接发。",
        params={"rules": "[{match: 关键词或列表, reply: 话术}]", "default": "无命中时的话术（空=不回）",
                "llm_prompt": "给 LLM 的人设/口吻说明（空=不用 LLM）", "contacts": "只处理这些联系人",
                "scan_top": "没指定 contacts 时扫列表前几个会话", "mode": "draft|send"}, star=True)
def auto_reply(ctx: Ctx, rules: list | None = None, default: str | None = None, llm_prompt: str | None = None,
               contacts: list | None = None, scan_top: int = 5, mode: str = "draft"):
    d = goto_tab(ctx, "微信")
    list_top(ctx, "搜索", "SearchField")
    d = ctx.screen()
    rows = chat_rows(d)
    if contacts:
        todo = [r["title"] for r in rows if r["title"] in contacts]
        todo += [c for c in contacts if c not in todo]            # 列表里暂时看不到的也去开
    else:
        todo = [r["title"] for r in rows if r["unread"] > 0] or [r["title"] for r in rows[: int(scan_top)]]
    handled = []
    for contact in todo:
        if contact in ("文件传输助手",):
            continue
        try:
            open_chat(ctx, contact)
        except PathBlocked as b:
            handled.append({"contact": contact, "skipped": b.reason})
            continue
        transcribe_voices(ctx)
        msgs = parse_messages(ctx.screen())
        rec = {"contact": contact}
        if not msgs or msgs[-1]["who"] == "我":
            rec["skipped"] = "最后一条是我发的" if msgs else "没读到消息"
            handled.append(rec)
            to_root(ctx)
            continue
        last = msgs[-1]
        rec["last"] = last["text"]
        if _already_replied(ctx, contact, last["text"]):
            rec["skipped"] = "这条已回过"
            handled.append(rec)
            to_root(ctx)
            continue
        reply = _pick_reply(last["text"], rules or [], None)
        source = "rule" if reply else None
        if not reply and llm_prompt:
            reply = _llm_reply(ctx, contact, msgs, llm_prompt)
            source = "llm" if reply else None
        if not reply and default:
            reply, source = default, "default"
        rec["reply"], rec["source"] = reply, source
        if reply:
            if mode == "send":
                ok = type_and_send(ctx, reply)
                rec["sent"] = ok
                if ok:
                    ctx.ledger_record("reply", contact, reply, {"in_reply_to": last["text"][:80], "source": source})
            else:
                dr = ctx.drafts.add(APP, "send", {"to": contact, "text": reply, "allow_repeat": True},
                                    f"自动回复 {contact}「{last['text'][:30]}」→ {reply[:40]}")
                rec["draft"] = dr["id"]
                ctx.ledger_record("reply", contact, reply, {"in_reply_to": last["text"][:80], "source": source, "draft": dr["id"]})
        ctx.emit(dict(rec, id=f"{contact}|{last['text'][:80]}"))
        handled.append(rec)
        to_root(ctx)
    return {"handled": handled}


reset = to_root
