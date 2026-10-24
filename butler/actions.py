"""动作服务：闸门 → 观察 → 解析 → 拟人 → 执行 → 验证 → 日志 → 广播。一次调用闭环。"""
from __future__ import annotations
import copy
import dataclasses
import json
import queue
import secrets
import threading
import time
from datetime import datetime
from pathlib import Path

from butler import extract
from butler.gates import pre_gates, risk_kind
from butler.humanize import PRESS_MS, Humanizer, RateLimiter
from butler.memory import MemoryStore
from butler import screen as S
from butler.names import normalize
from butler.observe import Observation, Observer
from butler.resolve import Resolution, Resolver
from butler.verify import check_expect, screen_changed, wait_stable
from engine import anchor as anchor_mod

MAX_SHOTS = 300
# facebook-wda 的 session 失效不会自愈（见 engine/device.py）：命中这些标记就让 DeviceManager 重建
_STALE_MARKERS = ("invalid session id", "call depth exceed", "Connection refused", "ConnectionError", "RemoteDisconnected")
_DIRS = {"up": ((0.5, 0.7), (0.5, 0.3)), "down": ((0.5, 0.3), (0.5, 0.7)),
         "left": ((0.8, 0.5), (0.2, 0.5)), "right": ((0.2, 0.5), (0.8, 0.5))}
_MAX_KNOWN = 80


def _known(elements: list[dict]) -> list[dict]:
    return [{"name": e.get("name") or e.get("label") or "", "tag": e["tag"], "rect": e["rect"]}
            for e in elements][:_MAX_KNOWN]


class ActionService:
    def __init__(self, driver_provider, memory: MemoryStore, root: Path, settings_loader, humanizer=None,
                 limiter=None, sleep=time.sleep, clock=time.monotonic, now_hhmm=None, invalidate=None):
        self.provider, self.memory, self.root = driver_provider, memory, Path(root)
        self.invalidate = invalidate or (lambda: None)
        self.settings_loader = settings_loader
        self.human = humanizer or Humanizer()
        self.limiter = limiter or RateLimiter()
        self.sleep, self.clock = sleep, clock
        self.now_hhmm = now_hhmm or (lambda: datetime.now().strftime("%H:%M"))
        self.shots_dir = self.root / "shots"
        self.shots_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.root / "sessions" / "actions.jsonl"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._subs: list[queue.Queue] = []
        self._observers: dict[int, Observer] = {}
        # 镜像：最近一帧（引擎每次观察/动作顺手更新，窗口不用自己去 WDA 抓）、最近一次落点、最近已知状态
        self.last_frame: dict = {"png": b"", "ts": 0.0, "source": ""}
        self.last_tap: dict | None = None
        self.last_state: dict = {"foreground": None, "locked": None, "ts": 0.0}
        self._mirroring = False       # 镜像自己在抓帧时持有锁，不算「Agent 正在操作」

    # ---- 基础 ----
    depth_override: int | None = None          # 路径层临时覆盖（导航用浅、读正文用深）

    def ax_depth_for(self, bundle: str | None) -> int:
        """该 App 该用多深的 AX 快照。X 浅（防自杀），Instagram 深（浅了读不到正文）。"""
        if self.depth_override:
            return int(self.depth_override)
        st = self.settings_loader()
        by_app = st.get("ax_depth_by_app") or {}
        return int(by_app.get(bundle or "", st.get("ax_depth", 20)))

    def _observer(self, driver) -> Observer:
        key = id(driver)
        if key not in self._observers:
            self._observers.clear()
            self._observers[key] = Observer(driver, self.memory, clock=self.clock,
                                            depth_provider=self.ax_depth_for)
        return self._observers[key]

    def scale(self) -> float:
        d = self.provider()
        return self._observer(d).scale() if d is not None else 1.0

    def save_shot(self, png: bytes) -> str:
        name = f"{int(time.time() * 1000)}-{secrets.token_hex(2)}.png"
        (self.shots_dir / name).write_bytes(png)
        old = sorted(self.shots_dir.glob("*.png"))
        for p in old[:-MAX_SHOTS]:
            p.unlink(missing_ok=True)
        return name

    def save_as(self, name: str, shot: str | None = None) -> dict:
        """把一张截图归档到 saved/ 下，起个人能看懂的名字。
        shots/ 是滚动缓存（超过 MAX_SHOTS 就删最旧的），Agent 想留下的图必须显式存出来。
        不给 shot 就存当前屏幕。"""
        from datetime import date
        safe = "".join(ch for ch in name if ch not in '/\\:*?"<>|').strip() or "shot"
        if shot:
            src = self.shots_dir / Path(shot).name
            if not src.exists():
                return {"ok": False, "error": f"截图 {shot} 不在缓存里（可能已被轮转删除）"}
            png = src.read_bytes()
        else:
            driver = self.provider()
            if driver is None:
                return {"ok": False, "error": "device not connected"}
            png = driver.screenshot_png()
        out_dir = self.root / "saved"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{date.today().isoformat()}-{safe}.png"
        n = 2
        while path.exists():                                # 同名不覆盖，自动加序号
            path = out_dir / f"{date.today().isoformat()}-{safe}-{n}.png"
            n += 1
        path.write_bytes(png)
        return {"ok": True, "path": str(path), "name": path.name, "bytes": len(png)}

    def saved_list(self, limit: int = 50) -> list[dict]:
        out_dir = self.root / "saved"
        if not out_dir.exists():
            return []
        files = sorted(out_dir.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
        return [{"name": f.name, "path": str(f), "bytes": f.stat().st_size} for f in files[:limit]]

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        self._subs.append(q)
        return q

    def unsubscribe(self, q) -> None:
        if q in self._subs:
            self._subs.remove(q)

    def recent(self, n: int) -> list[dict]:
        if not self.log_path.exists():
            return []
        lines = self.log_path.read_text().splitlines()
        return [json.loads(line) for line in lines[-n:]]

    def _log(self, entry: dict) -> None:
        with self.log_path.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        for q in list(self._subs):
            q.put(copy.deepcopy(entry))

    # ---- 镜像 ----
    def _frame(self, png: bytes, source: str) -> None:
        if png:
            self.last_frame = {"png": png, "ts": time.time(), "source": source}

    def mirror_frame(self, max_age_s: float = 0.5) -> dict:
        """给窗口的一帧。引擎空闲且缓存旧于 max_age_s 才真抓一张（0.3 s）；忙时直接给最近一帧，
        绝不和 Agent 的动作抢 WDA（X 上一次 AX 几十秒，中间插截图会拖慢任务）。"""
        fresh_needed = time.time() - self.last_frame["ts"] > max_age_s
        if fresh_needed and self._lock.acquire(blocking=False):
            self._mirroring = True
            try:
                driver = self.provider()
                if driver is not None:
                    self._frame(driver.screenshot_png(), "mirror")
                    fg = getattr(driver, "foreground_bundle", lambda: None)()
                    try:
                        locked = bool(driver.locked())
                    except Exception:  # noqa: BLE001
                        locked = None
                    self.last_state = {"foreground": fg, "locked": locked, "ts": time.time()}
            except Exception as e:  # noqa: BLE001 — 镜像失败只影响画面，不影响任务
                if self.is_stale(e):
                    self.reset_session()
            finally:
                self._mirroring = False
                self._lock.release()
        return self.last_frame

    def busy(self) -> bool:
        return self._lock.locked() and not self._mirroring

    def mirror_info(self) -> dict:
        tap = None
        if self.last_tap and time.time() - self.last_tap["ts"] < 3.0:
            tap = dict(self.last_tap, age_ms=int((time.time() - self.last_tap["ts"]) * 1000))
        return {"ts": self.last_frame["ts"], "age_ms": int((time.time() - self.last_frame["ts"]) * 1000) if self.last_frame["ts"] else None,
                "source": self.last_frame["source"], "busy": self.busy(), "tap": tap,
                "foreground": self.last_state.get("foreground"), "locked": self.last_state.get("locked")}

    def log_open(self, kind: str, value: str, ok: bool, note: str = "") -> None:
        self._log({"ok": ok, "action_id": secrets.token_hex(4), "op": "open", "open": {"kind": kind, "value": value},
                   "note": note, "ts": time.time(), "blocked": None if ok else {"reason": note}})

    def _app_wide_present(self, obs: Observation) -> list[dict]:
        """当前画面上确实对得上锚点的应用级常驻元素（学过的标签栏等）。
        Agent 问「屏幕上有什么」时必须能看到它们，否则学了也用不上。"""
        if not obs.bundle or not obs.png:
            return []
        from engine import anchor as anchor_mod
        out = []
        for rec in self.memory.app_elements(obs.bundle).values():
            if rec.get("fixed"):                            # 半透明标签栏这类：位置固定、外观随背景变
                out.append({"name": rec["name"], "label": rec.get("label", ""), "value": "",
                            "tag": rec.get("tag", "Learned"), "rect": rec["rect"],
                            "dynamic": False, "source": "app_memory"})
                continue
            b64 = (rec.get("anchor") or {}).get(obs.appearance)
            if not b64:
                continue
            try:
                ok, sim = anchor_mod.looks_same(anchor_mod.crop(obs.png, rec["rect"], obs.scale),
                                                anchor_mod.decode(b64), S.ELEMENT_THR)
            except Exception:  # noqa: BLE001
                continue
            if ok:
                out.append({"name": rec["name"], "label": rec.get("label", ""), "value": "",
                            "tag": rec.get("tag", "Learned"), "rect": rec["rect"],
                            "dynamic": False, "source": "app_memory"})
        return out

    # ---- 观察 ----
    def _obs_payload(self, obs: Observation) -> dict:
        self._frame(obs.png, "observe")
        self.last_state = {"foreground": obs.bundle, "locked": self.last_state.get("locked"), "ts": time.time()}
        merged = list(obs.elements) + [e for e in self._app_wide_present(obs)
                                       if not any((x.get("name") or "") == e["name"] for x in obs.elements)]
        return {"bundle": obs.bundle, "screen_id": obs.screen_id, "screen_label": obs.screen_label,
                "appearance": obs.appearance, "deep": obs.deep, "source": obs.source, "ax_ms": obs.ax_ms,
                "elements": [{**{k: e[k] for k in ("name", "label", "value", "tag", "rect", "dynamic")}, **({"traits": e["traits"]} if e.get("traits") else {})} for e in merged],
                "text_blocks": extract.text_blocks(obs.elements), "list_items": extract.list_items(obs.elements),
                "screenshot": self.save_shot(obs.png)}

    def is_stale(self, exc: BaseException) -> bool:
        text = f"{type(exc).__name__}: {exc}"
        return any(m in text for m in _STALE_MARKERS)

    def reset_session(self) -> None:
        """WDA session 失效不会自愈（见 engine/device.py）：显式作废并清掉观察器缓存。"""
        self.invalidate()
        self._observers.clear()

    def observe(self, deep: bool) -> dict:
        driver = self.provider()
        if driver is None:
            return {"error": "device not connected"}
        with self._lock:
            try:
                return self._obs_payload(self._observer(driver).observe(deep=deep))
            except Exception as e:  # noqa: BLE001
                if not self.is_stale(e):
                    raise
                self.reset_session()
                driver = self.provider()
                if driver is None:
                    return {"error": "device not connected"}
                return self._obs_payload(self._observer(driver).observe(deep=deep))

    def learn(self, name: str, rect: list[int], app_wide: bool = False, fixed: bool = False) -> dict:
        """给 AX 看不见的元素起名：把当前屏该矩形的截图存成锚点写进记忆，之后可用名字定位。
        Telegram 系（Swiftgram）自绘 UI 的标签栏就没有任何 name/label，只能靠这个。"""
        driver = self.provider()
        if driver is None:
            return {"ok": False, "error": "device not connected"}
        with self._lock:
            ob = self._observer(driver)
            obs = ob.observe(deep=False)
            if obs.screen_id is None:                      # 当前屏还没进记忆，先深观察建档
                obs = ob.observe(deep=True)
            if obs.screen_id is None:
                return {"ok": False, "error": "无法识别当前屏幕，记不住这个元素"}
            from engine import anchor as anchor_mod
            from butler import screen as S
            key = normalize(name)
            png = anchor_mod.make_anchor(obs.png, list(rect), obs.scale)
            rec = {"name": name, "label": name, "tag": "Learned", "rect": list(rect),
                   "dynamic": False, "fixed": bool(fixed), "anchor": {S.appearance(obs.png): png}}
            if app_wide:
                self.memory.save_app_element(obs.bundle, key, rec)
            else:
                self.memory.upsert_elements(obs.screen_id, {key: rec})
            return {"ok": True, "screen_id": obs.screen_id, "bundle": obs.bundle,
                    "name": name, "rect": list(rect), "app_wide": app_wide, "fixed": bool(fixed)}

    # ---- 意图复验 ----
    def check_intent(self, png: bytes, scale: float, intent: dict) -> dict:
        """坐标点击带来的「意图」：调用方是从哪张截图的哪个矩形算出这个点的。
        按下前在当前截图上裁同一矩形做 NCC：≥阈值放行；不够就在 ±12 pt 内重定位；仍找不到就判 moved，不按。
        均值差相似度对文字行无效（真机实测同行偏移 4 pt 与换行内容都是 0.88），这里只用 NCC。"""
        rect = intent.get("rect")
        shot = intent.get("shot")
        if not rect or not shot:
            return {"status": "unchecked", "why": "no_rect_or_shot"}
        src = self.shots_dir / Path(str(shot)).name
        if not src.exists():
            return {"status": "unchecked", "why": "shot_gone"}
        try:
            tpl = anchor_mod.crop(src.read_bytes(), list(rect), scale)
            cur = anchor_mod.crop(png, list(rect), scale)
            sim = anchor_mod.ncc(tpl, cur)
        except Exception as e:  # noqa: BLE001 — 复验本身出错不拦动作，但要记下来
            return {"status": "unchecked", "why": f"{type(e).__name__}: {e}"[:120]}
        if sim is None:
            return {"status": "unchecked", "why": "flat"}
        if sim >= anchor_mod.NCC_THR:
            return {"status": "verified", "sim": round(sim, 3)}
        best, dx, dy = anchor_mod.locate(png, tpl, list(rect), scale)
        if best is not None and best >= anchor_mod.NCC_THR:
            return {"status": "relocated", "sim": round(sim, 3), "best": round(best, 3), "dx": dx, "dy": dy}
        return {"status": "moved", "sim": round(sim, 3), "best": round(best, 3) if best is not None else None,
                "name": intent.get("name"), "hint": "目标不在原位置（内容变了或页面滚了），没有点；重新观察后再定位"}

    # ---- 动作 ----
    def act(self, req: dict) -> dict:
        driver = self.provider()
        if driver is None:
            return self._blocked(req, "no_device", {}, None, 0, 0)
        if not self._lock.acquire(blocking=False):
            return self._blocked(req, "busy", {"hint": "上一个动作还没结束，别并发调用"}, None, 0, 0)
        try:
            return self._act(driver, req)
        except Exception as e:  # noqa: BLE001 — 设备层任何异常都不该变成 HTTP 500，让 Agent 看到 blocked
            text = f"{type(e).__name__}: {e}"
            stale = self.is_stale(e)
            if stale:
                self.reset_session()
            return self._blocked(req, "device_error", {"error": text[:300], "session_reset": stale}, None, 0, 0)
        finally:
            self._lock.release()

    def _act(self, driver, req: dict) -> dict:
        t0 = self.clock()
        op = req.get("op")
        ob = self._observer(driver)
        resolver = Resolver(ob, self.memory)
        before = ob.settle(sleeper=self.sleep)
        res: Resolution | None = None
        blind = False
        if op in ("tap", "wait_for", "long_press"):
            if req.get("target"):
                if op in ("tap", "long_press"):
                    res = resolver.resolve(str(req["target"]), before)
                else:
                    res = self._wait_for(resolver, ob, req)
            elif op in ("tap", "long_press") and "x" in req and "y" in req:
                res = resolver.resolve_coords(int(req["x"]), int(req["y"]), before)
                blind = res.name is None
            else:
                return self._blocked(req, "bad_request", {"hint": "tap 需要 target 或 x/y"}, before, t0, 0)
            if res.status != "ok":
                return self._blocked(req, res.status, {}, res.observation, t0, 0, candidates=res.candidates)
            before = res.observation
        elif op == "type" and req.get("target"):
            res = resolver.resolve(str(req["target"]), before)
            if res.status != "ok":
                return self._blocked(req, res.status, {}, res.observation, t0, 0, candidates=res.candidates)
            before = res.observation
        elif op not in ("type", "swipe", "home", "back"):
            return self._blocked(req, "bad_request", {"hint": f"未知 op {op}"}, before, t0, 0)

        target_name = res.name if res else None
        target_tag = None
        if res is not None and res.rect:
            el = next((e for e in before.elements if e.get("rect") == res.rect), None)
            target_tag = (el or {}).get("tag")
        b = pre_gates(driver, req, target_name, before.bundle, self.limiter, self.settings_loader(), self.now_hhmm(),
                      elements=before.elements if before.source == "ax" else None, target_tag=target_tag)
        if b:
            return self._blocked(req, b.reason, b.detail, before, t0, 0)

        # ---- 拟人 + 执行 ----
        st = self.settings_loader()
        self.human.configure(st)
        risky = risk_kind(target_name) is not None and target_tag not in ("Cell", "StaticText", "TextView")
        delay_kind = "read" if op == "wait_for" else ("tap" if (risky or op == "type") else "nav")
        fast_app = before.bundle in Observer.AX_GAP_BY_APP   # 小红书采集：10-20 用户定不要拟人停顿
        human_s = 0.0 if fast_app else self.human.pre_delay(delay_kind, len(req.get("text") or ""))
        self.sleep(human_s)
        swipe = None
        intent_chk = None
        if op in ("tap", "long_press"):
            if "x" in req and "y" in req:
                # 坐标点击：点调用方给的点（只加 ±3 抖动）。之前落在「包含该点的元素」矩形里随机取点，
                # 大图片盖住小图标时会点歪（实证：小红书搜索图标被信息流封面吞掉）。
                x = int(req["x"]) + self.human.randint(-3, 3)
                y = int(req["y"]) + self.human.randint(-3, 3)
                intent = req.get("intent")
                if isinstance(intent, dict):
                    # 复验用按下前一刻的新截图（settle 那张已过去 1-2 s），同时它也成为前后比对的「前」图
                    fresh = driver.screenshot_png()
                    before = dataclasses.replace(before, png=fresh)
                    intent_chk = self.check_intent(fresh, before.scale, intent)
                    if intent_chk["status"] == "moved":
                        return self._blocked(req, "moved", intent_chk, before, t0, int(human_s * 1000))
                    if intent_chk["status"] == "relocated":
                        x, y = x + int(intent_chk["dx"]), y + int(intent_chk["dy"])
            else:
                x, y = self.human.tap_point(res.rect)
            self.last_tap = {"x": int(x), "y": int(y), "ts": time.time(), "name": target_name or (req.get("intent") or {}).get("name") or "",
                             "status": (intent_chk or {}).get("status")}
            driver.tap_press(x, y, PRESS_MS if op == "tap" else int(req.get("ms", 900)))
        elif op == "type":
            if res is not None:
                x, y = self.human.tap_point(res.rect)
                driver.tap_press(x, y, PRESS_MS)
                self.sleep(0.4)
                human_s += 0.4
            for chunk in self.human.type_chunks(str(req.get("text", ""))):
                driver.type_text(chunk)
                g = self.human.chunk_gap()
                self.sleep(g)
                human_s += g
        elif op == "swipe":
            if req.get("from") and req.get("to"):
                (x1, y1), (x2, y2) = req["from"], req["to"]
            else:
                (fx, fy), (tx, ty) = _DIRS.get(req.get("direction", "up"), _DIRS["up"])
                w, h = driver.window_size()
                x1, y1, x2, y2 = int(w * fx), int(h * fy), int(w * tx), int(h * ty)
            x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
            driver.swipe_ms(x1, y1, x2, y2, self.human.swipe_ms())
            swipe = [x1, y1, x2, y2]
            g = 0.3 if fast_app else self.human.after_scroll()
            self.sleep(g)
            human_s += g
        elif op == "home":
            driver.home()
        elif op == "back":
            w, h = driver.window_size()
            driver.swipe_ms(2, int(h * 0.5), int(w * 0.6), int(h * 0.5), 300)

        # ---- 验证 ----
        if op == "wait_for":
            after_png = before.png
        else:
            after_png = wait_stable(driver.screenshot_png, self.sleep, self.clock)
        changed = screen_changed(before.png, after_png)
        self._frame(after_png, "action")
        after_obs = self._after_observation(ob, before, after_png, changed)
        matched, note = check_expect(req.get("expect"), after_obs, changed)
        expect = req.get("expect")
        # 只有「屏幕上应有某段文本」才值得一次 AX 快照（fast 给不出全部文本）。
        # element / screen 类 expect 一律 fast：X 主页 AX 要 30-90 s，为确认一个标签栏元素付这个代价不值当；
        # fast 认不出就如实说「屏幕已变化但未能快速确认」，让 Agent 需要时自己 GET /screen。
        if not matched and isinstance(expect, dict) and "text" in expect and after_obs.source != "ax":
            after_obs = ob.observe(deep=True)
            matched, note = check_expect(expect, after_obs, changed)
        if not matched and isinstance(expect, dict) and "element" in expect and changed and after_obs.source == "unknown":
            matched, note = True, "屏幕已变化，但未能快速确认目标元素（未抓 AX）"
        read_back = None
        if op == "type" and res is not None:
            el = next((e for e in before.elements if (e.get("name") or e.get("label")) == res.name), None)
            if el:
                read_back = driver.find_value(el["tag"], el.get("name") or el.get("label"))
                if read_back is not None and str(req.get("text", "")) not in read_back:
                    matched, note = False, f"读回输入框值为「{read_back}」，不含本次输入"
        kind = None if target_tag in ("Cell", "StaticText", "TextView") else risk_kind(target_name)
        if kind:
            self.limiter.record(before.bundle or "unknown", kind)
        out = {"ok": matched, "action_id": secrets.token_hex(4), "op": op,
               "resolved": ({"name": res.name, "label": res.label, "rect": res.rect, "method": res.method,
                             "confidence": res.confidence} if res else None),
               "blind": blind, "intent": intent_chk,
               "verification": {"screen_changed": changed, "before_screen": before.screen_id,
                                "after_screen": after_obs.screen_id, "expect_matched": matched, "note": note,
                                "read_back": read_back},
               "screen": {"bundle": after_obs.bundle, "screen_id": after_obs.screen_id,
                          "screen_label": after_obs.screen_label, "screenshot": self.save_shot(after_png),
                          "known_elements": _known(after_obs.elements)},
               "blocked": None, "humanized_ms": int(human_s * 1000), "ms": int((self.clock() - t0) * 1000)}
        if swipe:
            out["swipe"] = swipe
        self._log({**out, "ts": time.time(), "req": req, "bundle": before.bundle,
                   "before_png": self.save_shot(before.png), "after_png": out["screen"]["screenshot"]})
        return out

    def _after_observation(self, ob: Observer, before: Observation, after_png: bytes, changed: bool) -> Observation:
        from butler.observe import _mem_to_dicts
        sid, rec = ob.identify(after_png)
        # 新屏幕可能还在加载（信息流、头像延迟绘制）：多试几次 fast 重截再认，比掉进 AX 便宜得多
        for _ in range(3):
            if rec is not None:
                break
            self.sleep(0.6)
            after_png = ob.driver.screenshot_png()
            sid, rec = ob.identify(after_png)
        fg = ob._true_foreground()                       # 地标只看画面，跨 App 会误认；以 WDA 前台为准
        if rec is not None and fg and rec.get("bundle") != fg:
            sid, rec = None, None
        if rec is None:
            return Observation(fg or before.bundle, None, "", before.appearance, False, [], after_png,
                               before.scale, 0, "unknown")
        return Observation(rec["bundle"], sid, rec.get("label", ""), before.appearance, False, _mem_to_dicts(rec),
                           after_png, before.scale, 0, "memory")

    def _wait_for(self, resolver: Resolver, ob: Observer, req: dict) -> Resolution:
        deadline = self.clock() + min(float(req.get("timeout", 10)), 30.0)
        while True:
            obs = ob.observe(deep=False)
            if obs.source == "memory":
                m = Resolver.match_elements(str(req["target"]), obs.elements)
                if m.tier != "none" and len(m.hits) == 1:
                    e = obs.elements[m.hits[0]]
                    return Resolution("ok", obs, e.get("name") or e.get("label"), e.get("label") or "", e["rect"], "memory", 0.9)
            last = resolver.resolve(str(req["target"]), obs)
            if last.status == "ok" or self.clock() >= deadline:
                return last
            self.sleep(1.0)

    def _blocked(self, req, reason, detail, obs, t0, human_ms, candidates=None) -> dict:
        screen = None
        if obs is not None:
            screen = {"bundle": obs.bundle, "screen_id": obs.screen_id, "screen_label": obs.screen_label,
                      "screenshot": self.save_shot(obs.png) if obs.png else None,
                      "known_elements": _known(obs.elements)}
        out = {"ok": False, "action_id": secrets.token_hex(4), "op": req.get("op"), "resolved": None, "blind": False,
               "verification": None, "screen": screen,
               "blocked": {"reason": reason, "detail": detail, "candidates": candidates or []},
               "humanized_ms": human_ms, "ms": int((self.clock() - t0) * 1000) if t0 else 0}
        self._log({**out, "ts": time.time(), "req": req, "bundle": obs.bundle if obs else None,
                   "before_png": None, "after_png": None})
        return out
