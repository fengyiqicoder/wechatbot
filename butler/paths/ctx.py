"""路径执行上下文：包住 ActionService，给路径函数一套短小、稳的动作/观察/采集原语。

原则照旧：看不到不动手（blocked 直接抛 PathBlocked，路径不做盲重试）；
每次滚动都走拟人延迟；坐标点击前必须确认目标 App 在前台。
"""
from __future__ import annotations
import time

from butler.names import normalize


class PathError(Exception):
    pass


class PathBlocked(PathError):
    def __init__(self, reason: str, detail: dict | None = None, req: dict | None = None):
        super().__init__(f"{reason}: {detail}")
        self.reason, self.detail, self.req = reason, detail or {}, req or {}


class Cancelled(PathError):
    pass


class DraftMade(PathError):
    """写类路径在 draft 模式下用它提前结束：不是错误，任务状态记为 done，结果带 draft。"""
    def __init__(self, draft: dict):
        super().__init__("draft")
        self.draft = draft


# ---- 元素表小工具（纯函数，也给验收脚本用）----
def _nm(e: dict) -> str:
    return (e.get("name") or e.get("label") or "").strip()


def names(d: dict) -> list[str]:
    return [_nm(e) for e in d.get("elements", []) if _nm(e)]


def has(d: dict, target: str) -> bool:
    t = normalize(target)
    return any(normalize(n) == t for n in names(d))


def find(d: dict, target: str, tag: str | None = None) -> dict | None:
    t = normalize(target)
    for e in d.get("elements", []):
        if tag and e.get("tag") != tag:
            continue
        if normalize(_nm(e)) == t:
            return e
    return None


def find_contains(d: dict, needle: str, tag: str | None = None) -> dict | None:
    for e in d.get("elements", []):
        if tag and e.get("tag") != tag:
            continue
        if needle in _nm(e):
            return e
    return None


def texts(d: dict) -> list[str]:
    return [b["text"] for b in d.get("text_blocks", [])]


def center(e: dict) -> tuple[int, int]:
    x, y, w, h = e["rect"]
    return x + w // 2, y + h // 2


def _inside(pt: tuple[int, int], rect) -> bool:
    x, y, w, h = rect
    return x <= pt[0] <= x + w and y <= pt[1] <= y + h


def _contains(outer, inner) -> bool:
    x, y, w, h = inner
    X, Y, W, H = outer
    return x >= X and y >= Y and x + w <= X + W and y + h <= Y + H


_INTERACTIVE = {"Button", "Switch", "TextField", "SearchField", "SecureTextField", "Link", "Slider", "Stepper",
                "Toggle", "SegmentedControl", "PageIndicator"}


def safe_point(e: dict, elements: list[dict]) -> tuple[int, int]:
    """点一个容器（Cell / 行）时避开它内部的按钮：随机落点落在行内的赞 / 头像 / 关注上会跳去别的页。
    候选依次：几何中心 → 行内最大的文本子元素中心 → 中线上 25% / 75% → 竖线上 25% / 75%；取第一个不在
    任何可点子元素矩形里的点；都不行就回到中心。"""
    rect = e["rect"]
    area = rect[2] * rect[3]
    kids = [c for c in elements if c is not e and c.get("rect") and c["rect"] != rect
            and _contains(rect, c["rect"]) and c["rect"][2] * c["rect"][3] < area]
    hot = [c["rect"] for c in kids if c.get("tag") in _INTERACTIVE]
    if not hot:
        return center(e)
    x, y, w, h = rect
    cx, cy = center(e)
    cands = [(cx, cy)]
    texts_ = sorted([c for c in kids if c.get("tag") in ("StaticText", "TextView") and _nm(c)],
                    key=lambda c: -(c["rect"][2] * c["rect"][3]))
    cands += [center(c) for c in texts_[:3]]
    cands += [(x + w // 4, cy), (x + 3 * w // 4, cy), (cx, y + h // 4), (cx, y + 3 * h // 4)]
    for pt in cands:
        if _inside(pt, rect) and not any(_inside(pt, r) for r in hot):
            return pt
    return cx, cy


class Ctx:
    def __init__(self, actions, catalog, spec, job, ledger=None, drafts=None, settings_loader=None,
                 sleeper=time.sleep, clock=time.monotonic):
        self.actions, self.catalog, self.spec, self.job = actions, catalog, spec, job
        self.ledger, self.drafts = ledger, drafts
        self.settings_loader = settings_loader or (lambda: {})
        self.sleep, self.clock = sleeper, clock
        self.bundle = spec.bundle
        self._seen: set = set(job.get("seen", set())) if job else set()
        self.items: list[dict] = []
        self.last: dict = {}
        self.wrote = False            # 本次任务是否已做过带 confirm 的动作（决定失败后能不能整段重试）

    # ---- 任务状态 ----
    def check_cancel(self) -> None:
        if self.job is not None and self.job.get("cancel"):
            raise Cancelled()

    def check_cancel_soft(self) -> None:
        """两次尝试之间被取消：直接抛 Cancelled 让任务终态为 cancelled。"""
        self.check_cancel()

    def note(self, msg: str) -> None:
        if self.job is not None:
            self.job.setdefault("notes", []).append({"t": time.time(), "msg": str(msg)[:300]})
            self.job["notes"] = self.job["notes"][-200:]
            hook = self.job.get("_on_update")
            if hook:
                hook()

    def emit(self, item: dict, key=None) -> bool:
        k = key if key is not None else item.get("id") or item.get("key")
        if k is None:
            k = repr(sorted(item.items()))
        if k in self._seen:
            return False
        self._seen.add(k)
        item = dict(item)
        item.setdefault("id", k if isinstance(k, str) else str(k))
        item.setdefault("app", self.spec.app)
        item.setdefault("ts", int(time.time()))
        self.items.append(item)
        if self.job is not None:
            self.job["count"] = len(self.items)
            self.job["seen"] = self._seen
            sink = self.job.get("_sink")
            if sink:
                sink(item)
        return True

    @property
    def seen(self) -> set:
        return self._seen

    # ---- 观察 ----
    def screen(self, deep: bool = True, retries: int = 2, depth: int | None = None, ensure: bool = True) -> dict:
        """depth：本次 AX 快照深度（导航可用 20 省时间；Instagram 评论面板深度 30 一次要 36 s）。
        ensure：观察前先确认目标 App 在前台，掉出去就拉回来。否则闪退后路径会对着桌面反复「返回」
        （真机实证：小红书搜索任务在桌面上连做 12 次返回手势，0 条结果）。"""
        self.check_cancel()
        if ensure and not self.in_app():
            self.note("目标 App 不在前台，先拉回来再观察")
            if not self.ensure_app():
                raise PathBlocked("app_gone", {"bundle": self.bundle, "hint": "拉不起目标 App"})
        d: dict = {}
        prev = self.actions.depth_override
        self.actions.depth_override = depth
        try:
            for i in range(retries + 1):
                d = self.actions.observe(deep=deep)
                if "error" not in d:
                    # 权限弹窗（Alert）挡着时整棵树只剩弹窗、标签栏都不在（Reddit 通知弹窗实证）：先关掉再看一次
                    if deep and any(e.get("tag") == "Alert" for e in d.get("elements", [])) and self._dismiss_alert_els(d):
                        d = self.actions.observe(deep=deep)
                    self.last = d
                    return d
                if i < retries:
                    self.sleep(1.5)
        finally:
            self.actions.depth_override = prev
        raise PathBlocked("no_device", {"error": d.get("error")})

    def _dismiss_alert_els(self, d: dict) -> bool:
        els = d.get("elements", [])
        for lab in self.SYSTEM_DENY:
            e = next((x for x in els if x.get("tag") == "Button" and (x.get("name") or x.get("label") or "").strip() == lab), None)
            if e is not None:
                drv = self.actions.provider()
                try:
                    drv.tap_press(e["rect"][0] + e["rect"][2] // 2, e["rect"][1] + e["rect"][3] // 2, 80)
                except Exception:  # noqa: BLE001
                    return False
                self.note(f"权限弹窗：选了「{lab}」")
                self.sleep(1.5)
                return True
        return False

    def foreground(self) -> str | None:
        """问 WDA 拿真前台（app_current 很便宜且准确）；记忆识别只看画面，跨 App 会误认。"""
        try:
            drv = self.actions.provider()
            b = drv.foreground_bundle() if drv is not None else None
        except Exception:  # noqa: BLE001
            b = None
        if b:
            return b
        return self.actions.observe(deep=False).get("bundle")

    def in_app(self) -> bool:
        return self.foreground() == self.bundle

    # 系统权限弹窗（跟踪 / 通知 / 相册…）挡在 App 前面时，一律选权限更少的那项，App 才拉得起来
    SYSTEM_DENY = ("要求App不跟踪", "要求 App 不跟踪", "不允许", "暂不", "以后", "以后再说", "暂不允许", "稍后", "不了，谢谢", "取消",
                   "Ask App Not to Track", "Don't Allow", "Not Now", "Later")

    def dismiss_system_alert(self) -> bool:
        """系统/应用内权限弹窗（Alert）：选权限更少的那项。用驱动直接点，绕过引擎的弹窗闸门。"""
        d = self.actions.observe(deep=True)
        els = d.get("elements", [])
        if not any(e.get("tag") == "Alert" for e in els) and d.get("bundle") not in ("com.apple.springboard", None):
            return False
        for lab in self.SYSTEM_DENY:
            e = next((x for x in els if x.get("tag") == "Button" and (x.get("name") or x.get("label") or "").strip() == lab), None)
            if e is not None:
                drv = self.actions.provider()
                try:
                    drv.tap_press(e["rect"][0] + e["rect"][2] // 2, e["rect"][1] + e["rect"][3] // 2, 80)
                except Exception:  # noqa: BLE001
                    return False
                self.note(f"系统弹窗：选了「{lab}」")
                self.sleep(1.5)
                return True
        return False

    def ensure_app(self, tries: int = 3, settle: float = 2.5) -> bool:
        """不在目标 App 前台就拉起（闪退后的恢复入口）。系统弹窗挡着就先关掉。"""
        for _ in range(tries):
            if self.in_app():
                return True
            if self.dismiss_system_alert() and self.in_app():
                return True
            drv = self.actions.provider()
            if drv is None:
                raise PathBlocked("no_device")
            try:
                drv.launch_app(self.bundle)
            except Exception as e:  # noqa: BLE001
                if self.actions.is_stale(e):
                    self.actions.reset_session()
                    continue
                raise
            self.sleep(settle)
        return self.in_app()

    def restart_app(self, settle: float = 4.0) -> bool:
        """杀掉再拉起目标 App：导航卡在退不出的全屏页（Reddit 互动帖实证）时的最后手段。"""
        drv = self.actions.provider()
        if drv is None:
            raise PathBlocked("no_device")
        fn = getattr(drv, "terminate_app", None)
        if fn is not None and fn(self.bundle):
            self.note("导航卡死，杀掉 App 重开")
            self.sleep(1.5)
        return self.ensure_app(settle=settle)

    # ---- 动作 ----
    def act(self, soft: bool = False, **req) -> dict:
        """一步动作。busy 等一会儿再试；device_error 重置 session 再试一次；其它 blocked 抛错。"""
        self.check_cancel()
        last: dict = {}
        for i in range(6):
            r = self.actions.act(req)
            if r.get("ok"):
                return r
            last = r
            reason = (r.get("blocked") or {}).get("reason")
            if reason == "busy":
                self.sleep(1.0 + i)
                continue
            if reason == "alert" and i < 2 and self.dismiss_system_alert():   # 权限弹窗：关掉再来一次
                continue
            if reason == "device_error" and i < 2:
                self.sleep(2.0)
                continue
            break
        if soft:
            return last
        b = last.get("blocked") or {}
        if not b:                                       # 动作执行了但验证没过（expect 不满足 / 读回不含输入）
            raise PathBlocked("verify_failed", {"verification": last.get("verification"),
                                                "screenshot": (last.get("screen") or {}).get("screenshot")}, req)
        raise PathBlocked(b.get("reason", "unknown"),
                          b.get("detail") or {k: v for k, v in b.items() if k != "reason"}, req)

    def tap(self, target: str | None = None, x: int | None = None, y: int | None = None, expect=None,
            confirm: bool = False, soft: bool = False, intent: dict | None = None) -> dict:
        req: dict = {"op": "tap"}
        if target is not None:
            req["target"] = target
        else:
            if x is None or y is None:
                raise PathError("tap 需要 target 或坐标")
            if not self.in_app():          # 坐标点击铁律：目标 App 必须在前台
                raise PathBlocked("not_in_app", {"hint": "目标 App 不在前台，拒绝按坐标点击"})
            req["x"], req["y"] = int(x), int(y)
            if intent:
                req["intent"] = intent
        if expect:
            req["expect"] = expect
        if confirm:
            req["confirm"] = True
        r = self.act(soft=soft, **req)
        if confirm and r.get("ok"):
            self.wrote = True
        return r

    def tap_el(self, e: dict, expect=None, confirm: bool = False, soft: bool = False, verify: bool = True) -> dict:
        """点一个从 ctx.screen() 拿到的元素。带意图：引擎按下前在新截图上复验该矩形（NCC），偏了就重定位，
        内容变了就不点（moved）。moved 时这里重新观察一次、按名字找回同一元素再点一次；还找不到才抛。
        元素若来自快观察（记忆），矩形是上次记的，改走按名字解析（引擎会用记忆锚点复核）；解析不了再深读找回。"""
        name, tag = _nm(e), e.get("tag")
        if self.last.get("source") == "memory" and name and e.get("source", "memory") == "memory":
            r = self.tap(target=name, expect=expect, confirm=confirm, soft=True)
            if r.get("ok"):
                return r
            if (r.get("blocked") or {}).get("reason") not in ("ambiguous", "not_found", "moved"):
                return r if soft else self._raise_from(r, {"op": "tap", "target": name})
            d = self.screen()
            again = find(d, name, tag)
            if again is None:
                if soft:
                    return r
                raise PathBlocked("not_found", {"target": name, "screenshot": d.get("screenshot")})
            e = again
        pt = safe_point(e, self.last.get("elements", [])) if e.get("rect") else center(e)
        intent = None
        if verify and e.get("rect") and self.last.get("screenshot"):
            intent = {"name": name, "tag": tag, "rect": list(e["rect"]), "shot": self.last["screenshot"]}
        r = self.tap(x=pt[0], y=pt[1], expect=expect, confirm=confirm, soft=True, intent=intent)
        if r.get("ok") or (r.get("blocked") or {}).get("reason") != "moved":
            if r.get("ok") or soft:
                return r
            return self._raise_from(r, {"op": "tap", "x": pt[0], "y": pt[1], "intent": intent})
        self.note(f"目标「{name[:30]}」不在原位（{(r.get('blocked') or {}).get('detail', {}).get('sim')}），重新观察后再找")
        d = self.screen()
        again = find(d, name, tag) if name else None
        if again is None:
            if soft:
                return r
            raise PathBlocked("moved", {"target": name, "screenshot": d.get("screenshot")})
        pt = safe_point(again, d.get("elements", []))
        intent = {"name": name, "tag": tag, "rect": list(again["rect"]), "shot": d.get("screenshot")}
        return self.tap(x=pt[0], y=pt[1], expect=expect, confirm=confirm, soft=soft, intent=intent)

    def _raise_from(self, r: dict, req: dict):
        b = r.get("blocked") or {}
        if not b:
            raise PathBlocked("verify_failed", {"verification": r.get("verification"),
                                                "screenshot": (r.get("screen") or {}).get("screenshot")}, req)
        raise PathBlocked(b.get("reason", "unknown"), b.get("detail") or {k: v for k, v in b.items() if k != "reason"}, req)

    def type(self, text: str, soft: bool = False) -> dict:
        return self.act(soft=soft, op="type", text=text, confirm=True)

    def swipe(self, direction: str = "up", soft: bool = True) -> dict:
        return self.act(soft=soft, op="swipe", direction=direction)

    def back(self, soft: bool = True) -> dict:
        return self.act(soft=soft, op="back")

    def home(self) -> dict:
        return self.act(soft=True, op="home")

    def wait(self, s: float) -> None:
        self.check_cancel()
        self.sleep(s)

    def human_pause(self) -> None:
        self.wait(self.actions.human.after_scroll())

    def until(self, pred, timeout: float = 8.0, poll: float = 0.7, depth: int | None = None,
              deep: bool = True, fast: bool = False) -> dict | None:
        """条件等待，替代固定 sleep：深读直到 pred 满足或超时。满足返回那次观察（并更新 self.last），超时返回 None。
        fast=True 时先用快观察（0.3 s，记忆里的静态元素）试 pred——**只给导航类条件用**（标签栏在不在、
        某个按钮在不在）。内容类条件（结果行、卡片）绝不能走快路径：记忆里标识符命名的 cell 会被当成静态
        元素返回旧行，真机实证 Instagram 搜索结果因此三次全空。"""
        deadline = self.clock() + timeout
        d: dict = {}
        while True:
            self.check_cancel()
            d = {}
            if fast:
                try:
                    d = self.actions.observe(deep=False)
                except Exception:  # noqa: BLE001
                    d = {}
            if fast and "error" not in d and d.get("source") == "memory":
                d = {**d, "elements": [e for e in d.get("elements", []) if not e.get("dynamic")],
                     "text_blocks": [], "list_items": []}
                if d["elements"] and pred(d):
                    self.last = d
                    return d
            if deep:
                d = self.screen(depth=depth)
                if pred(d):
                    return d
            if self.clock() >= deadline:
                return None
            self.sleep(poll)

    def tap_until(self, e: dict, pred, timeout: float = 8.0, depth: int | None = None, expect=None,
                  confirm: bool = False) -> dict | None:
        """点元素，然后等 pred 成立。点击失败抛错；条件超时返回 None。"""
        self.tap_el(e, expect=expect, confirm=confirm)
        return self.until(pred, timeout=timeout, depth=depth)

    # ---- 采集循环 ----
    def collect(self, extract, key, n: int, stall: int = 2, direction: str = "up",
                max_swipes: int | None = None, deep: bool = True, on_recover=None, expect_some: bool = False) -> list[dict]:
        """滚动 → 抽取 → 去重 → 到 n 或连续 stall 屏无新增即停。extract(d) -> list[item]。
        expect_some：这类页面按理不会是空的（搜索结果/信息流），一条都没抽到就当失败抛出，让任务级重试接手，
        而不是「done，0 条」蒙混过去。"""
        swipes, idle = 0, 0
        max_swipes = max_swipes if max_swipes is not None else max(4, n * 2)
        while True:
            self.check_cancel()
            if not self.in_app():
                self.note("目标 App 掉出前台，重新拉起后继续")
                if not self.ensure_app():
                    raise PathBlocked("app_gone", {"bundle": self.bundle})
                if on_recover:
                    on_recover(self)
            d = self.screen(deep=deep)
            new = 0
            for it in extract(d):
                if len(self.items) >= n:                  # 要 10 条就只给 10 条，多出来的不入结果
                    break
                if self.emit(it, key(it)):
                    new += 1
            if len(self.items) >= n:
                break
            idle = idle + 1 if new == 0 else 0
            if idle >= stall or swipes >= max_swipes:
                break
            self.swipe(direction)
            swipes += 1
            self.human_pause()
        if expect_some and not self.items:
            raise PathBlocked("empty_result", {"hint": "这一页按理应有内容却一条都没抽到", "screenshot": self.shot()})
        return self.items[:n]

    # ---- 写操作三件套：草稿 / 台账 / 读回 ----
    def draft_or_go(self, mode: str, summary: str, params: dict) -> None:
        if mode == "send":
            return
        if self.drafts is None:
            raise PathError("没有草稿存储")
        rec = self.drafts.add(self.spec.app, self.spec.name, params, summary)
        raise DraftMade(rec)

    def ledger_guard(self, kind: str, target: str, allow_repeat: bool = False) -> None:
        if self.ledger is None:
            return
        if self.ledger.blocked(self.spec.app, target):
            raise PathBlocked("blocklisted", {"target": target})
        if not allow_repeat and self.ledger.has(self.spec.app, kind, target):
            raise PathBlocked("already_sent", {"kind": kind, "target": target,
                                               "hint": "台账里已发过；要重复发请传 allow_repeat"})

    def ledger_record(self, kind: str, target: str, content: str, extra: dict | None = None) -> None:
        self.wrote = True
        if self.ledger is not None:
            self.ledger.record(self.spec.app, kind, target, content, extra)

    def shot(self) -> str | None:
        return self.last.get("screenshot")
