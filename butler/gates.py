"""动作前闸门：锁屏 → 系统弹窗 → 高风险需确认 → 静默窗 → 限额。命中即 Blocked，不动手。"""
from __future__ import annotations
import time as _time
from dataclasses import dataclass, field

from butler.humanize import in_quiet_hours
from butler.names import normalize

RISK_WORDS = {
    "like": ["点赞", "喜欢", "like", "heart"],
    "comment": ["评论", "回复", "reply", "comment"],
    "dm": ["发送", "已发送", "send", "sent"],
    "follow": ["关注", "follow"],
    "post": ["发布", "发帖", "post", "tweet", "publish"],
    "delete": ["删除", "delete", "remove", "取消关注", "unfollow", "block", "拉黑"],
}
# 输入本身不算写操作：任何真正对外发布（发送/评论/发帖）都得再点一个带 confirm 的按钮，那一下才受静默窗管。
# 把 type 算成写操作只会让夜里的搜索类采集任务在输入关键词时被静默窗挡死（真机实证：小红书搜索 02:30 被拦）。
_WRITE_OPS: set[str] = set()
# 列表行的名字里会混进消息预览（微信的 Cell 叫 "Max,回复,13:29"），拿它做风险词匹配会误判成
# 「发评论」。点开一行只是导航，不是写操作——只有真正的可点控件才参与风险判定。
_NON_ACTION_TAGS = {"Cell", "StaticText", "TextView", "Table", "CollectionView", "ScrollView"}


@dataclass
class Blocked:
    reason: str
    detail: dict = field(default_factory=dict)


def risk_kind(name: str | None) -> str | None:
    if not name:
        return None
    n = normalize(name).lower()
    for kind in ("delete", "post", "comment", "like", "dm", "follow"):
        if any(w in n for w in RISK_WORDS[kind]):
            return kind
    return None


def alert_from_elements(elements: list[dict] | None) -> dict | None:
    """深观察拿到的元素树里若有 Alert，就是系统弹窗；比单独调 WDA alert 接口省一次 AX 快照。"""
    if not elements:
        return None
    alert = next((e for e in elements if e.get("tag") == "Alert"), None)
    if alert is None:
        return None
    ax, ay, aw, ah = alert["rect"]
    inside = [e for e in elements if e is not alert and e["rect"][0] >= ax and e["rect"][1] >= ay
              and e["rect"][0] + e["rect"][2] <= ax + aw and e["rect"][1] + e["rect"][3] <= ay + ah]
    title = next((e.get("label") or e.get("name") for e in inside if e.get("tag") == "StaticText"), "") or alert.get("label", "")
    buttons = [e.get("label") or e.get("name") for e in inside if e.get("tag") == "Button"]
    return {"title": title, "buttons": buttons}


def pre_gates(driver, req: dict, target_name, bundle, limiter, settings, now_hhmm, elements=None,
              target_tag: str | None = None) -> Blocked | None:
    try:
        if driver.locked():
            return Blocked("locked", {"hint": "解锁 iPhone 后重试"})
    except Exception:  # noqa: BLE001 — 锁屏探测失败不拦，后面的验证会暴露
        pass
    alert = alert_from_elements(elements)
    # 调用方看过弹窗后明确选其中一个按钮（alert_choice=true 且目标就是弹窗里的按钮）才放行；高风险仍要 confirm
    if alert and not (req.get("alert_choice") and target_name in alert["buttons"]):
        return Blocked("alert", alert)
    kind = None if (target_tag in _NON_ACTION_TAGS) else risk_kind(target_name)
    if kind and not req.get("confirm"):
        return Blocked("needs_confirm", {"kind": kind, "target": target_name})
    writes = kind is not None or req.get("op") in _WRITE_OPS
    # 验收/调试临时旁路静默窗：settings.quiet_bypass_until（epoch 秒）到期自动失效，不再靠脚本手动恢复
    bypass = float(settings.get("quiet_bypass_until") or 0) > _time.time()
    if writes and not bypass and in_quiet_hours(now_hhmm, settings.get("sleep_window", {})):
        return Blocked("quiet_hours", {"window": settings.get("sleep_window")})
    if kind:
        wait = limiter.check(bundle or "unknown", kind)
        if wait is not None:
            return Blocked("rate_limit", {"kind": kind, "retry_after_s": wait})
    return None
