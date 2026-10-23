"""确定性执行器：等锚点 → 坐标动作 → 拟人间隔；锚点超时一次 AX 自愈；失败请求接管。

回放期仅 `llm_text` 步骤调用 LLM（截图+规则卡→一段文字，写入变量供后续步骤引用），其余步骤不含任何 LLM 判断。"""
from __future__ import annotations
import copy
import io
import random
import threading
import time

from PIL import Image

from engine import anchor
from engine.workflow import describe, render_value

POLL_S = 0.25
DEFAULT_TIMEOUT_S = 10


class Takeover(Exception):
    pass


class Stopped(Exception):
    pass


class RunControl:
    def __init__(self):
        self.stop_requested = False
        self._resume = threading.Event()

    def request_stop(self) -> None:
        self.stop_requested = True
        self._resume.set()

    def resume(self) -> None:
        self._resume.set()

    def wait_resume(self) -> bool:
        self._resume.wait()
        self._resume.clear()
        return not self.stop_requested


class Executor:
    def __init__(self, driver, workflow_store, run_store, emit=lambda run: None, control=None,
                 sleeper=time.sleep, clock=time.monotonic, rng=random.uniform, delay_range=(0.8, 2.0),
                 llm=None):
        self.driver = driver
        self.workflows = workflow_store
        self.runs = run_store
        self.emit = emit
        self.control = control or RunControl()
        self.sleep = sleeper
        self.clock = clock
        self.rng = rng
        self.delay_range = delay_range
        self.llm = llm
        self._scale: float | None = None
        self._vars: dict = {}

    # ---- 对外 ----
    def execute(self, run: dict, workflow: dict) -> dict:
        run["status"] = "running"
        run["started_at"] = time.time()
        self._publish(run)
        self._vars = {}
        steps = copy.deepcopy(workflow["steps"])
        for n, step in enumerate(steps):
            # 每步执行前渲染一次：run 参数与此前 llm_text 产出的变量合并。llm_text 步骤
            # 必然先于消费其输出的步骤，轮到消费步骤时该变量已在 self._vars 里，
            # 故只需一个渲染点，render_value 对真正缺失的占位符仍按空串处理。
            step = self._render_step(step, run["params"])
            entry = {"n": n, "op": step["op"], "desc": describe(step), "status": "running",
                     "healed": False, "ms": None, "error": None, "usage": None}
            run["steps"].append(entry)
            self._publish(run)
            if not self._run_step(run, entry, n, step, workflow):
                self._finish(run)
                return run
            self._human_delay()
        run["status"] = "success"
        self._finish(run)
        return run

    # ---- 单步（含接管循环）----
    def _run_step(self, run, entry, n, step, workflow) -> bool:
        while True:
            t0 = self.clock()
            try:
                self._check_stop()
                self._do(step, n, workflow, entry)
                entry["status"] = "ok"
                entry["ms"] = int((self.clock() - t0) * 1000)
                self._snapshot(run, n)
                self._publish(run)
                return True
            except Stopped:
                entry["status"] = "stopped"
                run["status"] = "stopped"
                return False
            except Takeover as e:
                entry["error"] = str(e)
                entry["status"] = "needs_takeover"
                run["status"] = "needs_takeover"
                self._snapshot(run, n)
                self._publish(run)
                if self.control.wait_resume():
                    run["status"] = "running"
                    entry["status"] = "running"
                    entry["error"] = None
                    self._publish(run)
                    continue
                entry["status"] = "stopped"
                run["status"] = "stopped"
                return False
            except Exception as e:  # noqa: BLE001 — 任何其他异常都算本次运行失败
                entry["status"] = "failed"
                entry["error"] = str(e)
                run["status"] = "failed"
                run["error"] = f"步骤 {n + 1}（{entry['desc']}）失败：{e}"
                self._snapshot(run, n)
                return False

    def _do(self, step, n, workflow, entry) -> None:
        op = step["op"]
        if op == "launch_app":
            self.driver.launch_app(step["bundle_id"])
        elif op == "open_url":
            self.driver.open_url(step["url"])
        elif op == "tap":
            self._wait_anchor(step, n, workflow, entry)
            x, y, w, h = step["rect"]
            at = step.get("at")
            if isinstance(at, list) and len(at) == 2:
                self.driver.tap(x + at[0], y + at[1])
            else:
                self.driver.tap(x + w // 2, y + h // 2)
        elif op == "wait_for":
            self._wait_anchor(step, n, workflow, entry)
        elif op == "type":
            self.driver.type_text(step["text"])
        elif op == "swipe":
            (x1, y1), (x2, y2) = step["from"], step["to"]
            self.driver.swipe(x1, y1, x2, y2, step.get("duration", 0.3))
        elif op == "home":
            self.driver.home()
        elif op == "human_delay":
            self.sleep(self.rng(step["min_s"], step["max_s"]))
        elif op == "llm_text":
            if self.llm is None:
                raise RuntimeError("未配置 OpenRouter key，无法执行 llm_text")
            from engine import teach_obs
            from engine.llm import image_part
            system = "你在替用户写一段将被直接输入到 iPhone 应用里的文字。只输出正文，不要引号、不要解释。"
            if workflow.get("rules"):
                system += "\n规则：" + str(workflow["rules"])
            content = [{"type": "text", "text": step["prompt"]}]
            if step.get("screen", "image") == "image":
                content.append(image_part(teach_obs.downscale_jpeg(self.driver.screenshot_png())))
            res = self.llm.chat([{"role": "system", "content": system}, {"role": "user", "content": content}],
                                purpose="llm_text", ref=step.get("out", ""), max_tokens=400)
            entry["usage"] = {"prompt_tokens": res.prompt_tokens, "completion_tokens": res.completion_tokens,
                              "cost_usd": res.cost_usd}
            text = res.text.strip()
            limit = int(step.get("max_chars", 280))
            if not text or len(text) > limit:
                raise ValueError(f"llm_text 输出为空或超长（{len(text)}/{limit}）")
            self._vars[step["out"]] = text
        else:
            raise ValueError(f"未知步骤 {op}")

    def _render_step(self, step: dict, params: dict) -> dict:
        """用 run 参数叠加 llm_text 产出的变量，渲染该步的模板字段。"""
        merged = {**params, **self._vars}
        step = dict(step)
        for key in ("text", "url", "bundle_id", "prompt"):
            v = step.get(key)
            if isinstance(v, str):
                step[key] = render_value(v, merged, encode=(key == "url"))
        return step

    # ---- 锚点与自愈 ----
    def _wait_anchor(self, step, n, workflow, entry) -> None:
        if not step.get("anchor"):
            if step.get("no_anchor") and (step.get("selector") or {}).get("ax_id"):
                self._heal(step, n, workflow, entry)
            return
        a = workflow["anchors"][step["anchor"]]
        png = a.get("png")
        if not png:
            # 正常不会发生（PUT /workflows/{id} 会把已录制的锚点图续回去）；一旦真的没有，
            # 给个能定位问题的错误而不是裸的 KeyError('png')，落到 run["error"] 里让人能看懂。
            raise ValueError(f"锚点 {step['anchor']} 没有参考图（png），workflow 可能需要重新录制")
        ref = anchor.decode(png)
        thr = a.get("threshold", 0.92)
        deadline = self.clock() + step.get("timeout", DEFAULT_TIMEOUT_S)
        while True:
            png = self.driver.screenshot_png()
            if anchor.similarity(anchor.crop(png, step["rect"], self._get_scale(png)), ref) >= thr:
                return
            if self.clock() >= deadline:
                break
            self._check_stop()
            self.sleep(POLL_S)
        self._heal(step, n, workflow, entry)

    def _heal(self, step, n, workflow, entry) -> None:
        ax_id = (step.get("selector") or {}).get("ax_id")
        if not ax_id:
            raise Takeover(f"锚点超时，且步骤没有 AX 选择器：{entry['desc']}")
        el = next((e for e in self.driver.elements() if e.name == ax_id or e.label == ax_id), None)
        if el is None:
            raise Takeover(f"锚点超时，AX 树里也找不到 {ax_id}，请手动处理后点「恢复」")
        rect = [el.x, el.y, el.width, el.height]
        png = self.driver.screenshot_png()
        new_png = anchor.make_anchor(png, rect, self._get_scale(png))
        step["rect"] = rect
        step["healed"] = True
        workflow["steps"][n]["rect"] = rect
        workflow["steps"][n]["healed"] = True
        if step.get("anchor"):
            workflow["anchors"][step["anchor"]]["png"] = new_png
        else:
            key = f"a{len(workflow.get('anchors') or {}) + 1}"
            step["anchor"] = key
            workflow["steps"][n]["anchor"] = key
            workflow.setdefault("anchors", {})[key] = {"png": new_png, "threshold": 0.92}
            workflow["steps"][n]["no_anchor"] = False
        self.workflows.save(workflow)
        entry["healed"] = True

    # ---- 杂项 ----
    def _get_scale(self, png: bytes) -> float:
        if self._scale is None:
            w_px = Image.open(io.BytesIO(png)).size[0]
            w_pt = self.driver.window_size()[0]
            self._scale = w_px / w_pt if w_pt else 1.0
        return self._scale

    def _check_stop(self) -> None:
        if self.control.stop_requested:
            raise Stopped()

    def _human_delay(self) -> None:
        lo, hi = self.delay_range
        if hi > 0:
            self.sleep(self.rng(lo, hi))

    def _snapshot(self, run, n) -> None:
        try:
            self.runs.save_step_png(run["id"], n, self.driver.screenshot_png())
        except Exception:  # noqa: BLE001 — 截图失败不影响运行结果
            pass

    def _publish(self, run) -> None:
        self.runs.save(run)
        # emit 一份深拷贝：run 在后续步骤中原地变化，订阅者持有的历史事件不应随之改写。
        self.emit(copy.deepcopy(run))

    def _finish(self, run) -> None:
        run["finished_at"] = time.time()
        self._publish(run)
