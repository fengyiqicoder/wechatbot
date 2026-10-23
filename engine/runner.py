"""单队列运行器：同一手机任何时刻只跑一个 workflow；事件广播给 SSE 订阅者。"""
from __future__ import annotations
import queue
import threading
import time
from pathlib import Path

from engine.executor import Executor, RunControl
from engine.store import load_settings
from engine.workflow import missing_params

# facebook-wda 的 session 失效不会自愈（见 engine/device.py 模块注释）：
# invalid session id 错误、以及重试触顶后的 "call depth exceed" RuntimeError，
# 都意味着缓存的 driver 已经废了，必须让 DeviceManager 在下一个 run 前重建 session。
_STALE_SESSION_MARKERS = ("invalid session id", "call depth exceed")


def _is_stale_session(exc: BaseException | None, message: str | None = None) -> bool:
    text = message if message is not None else str(exc)
    if any(m in text for m in _STALE_SESSION_MARKERS):
        return True
    if exc is not None:
        try:
            import wda
            if isinstance(exc, wda.WDAInvalidSessionIdError):
                return True
        except Exception:  # noqa: BLE001 — wda 不可用时退化为纯文本匹配
            pass
    return False


class RunQueue:
    def __init__(self, driver_provider, workflow_store, run_store, root: Path, executor_factory=None,
                 invalidate=None, llm_factory=None):
        self.driver_provider = driver_provider
        self.workflows = workflow_store
        self.runs = run_store
        self.root = Path(root)
        self.executor_factory = executor_factory or self._default_factory
        # 探测到 session 失效时调用；由 server.py 接上 DeviceManager.invalidate，
        # 默认是空操作（测试/无设备场景不需要真的重建什么）。
        self.invalidate = invalidate or (lambda: None)
        self.llm_factory = llm_factory or (lambda: None)
        self._q: queue.Queue = queue.Queue()
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._current: dict | None = None
        self._control: RunControl | None = None
        self._thread: threading.Thread | None = None
        # workflow_id -> run_id：已入队但 worker 还没开始跑的那一个。调度器不知道队列状态，
        # 会按间隔一直 POST /runs；一旦某个 run 卡在 needs_takeover，worker 被 wait_resume()
        # 挡住，如果每次都真的新建 run，恢复时会突然爆发式补跑一堆——对 post 类 workflow
        # 就是连发好几条真实推文。所以同一个 workflow 同时最多只允许一个「排队中」的 run。
        self._pending_by_workflow: dict[str, str] = {}
        self._hold_owner: str | None = None

    def _default_factory(self, driver, control, emit, delay_range):
        return Executor(driver, self.workflows, self.runs, emit=emit, control=control, delay_range=delay_range,
                        llm=self.llm_factory())

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="run-queue", daemon=True)
        self._thread.start()

    def enqueue(self, workflow_id: str, params: dict) -> dict:
        wf = self.workflows.get(workflow_id)
        if wf is None:
            raise KeyError(workflow_id)
        missing = missing_params(wf, params)
        if missing:
            raise ValueError(f"缺少参数：{', '.join(missing)}")
        with self._lock:
            existing_id = self._pending_by_workflow.get(workflow_id)
            if (existing_id is None and self._current
                    and self._current.get("workflow_id") == workflow_id
                    and self._current.get("status") == "needs_takeover"):
                # 只在「卡在需接管」时把当前这个也算进「正在占用」——这正是会长时间挡住
                # worker、让调度器一直打空枪的场景。普通正常跑（甚至刚跑完但 _current 还
                # 没来得及被 finally 清掉）不应该被当成重复，否则会把两次故意的连续手动
                # 运行、或调度器两次正常触发的 run 错误地合并成一个。
                existing_id = self._current["id"]
            if existing_id is not None:
                existing = self.runs.get(existing_id)
                if existing is not None:
                    return existing
            run = self.runs.create(workflow_id, params)
            self._pending_by_workflow[workflow_id] = run["id"]
        self._emit(run)
        self._q.put(run["id"])
        return run

    def stop(self, run_id: str) -> bool:
        with self._lock:
            if self._current and self._current["id"] == run_id and self._control:
                self._control.request_stop()
                return True
        return False

    def resume(self, run_id: str) -> bool:
        with self._lock:
            if self._current and self._current["id"] == run_id and self._control:
                self._control.resume()
                return True
        return False

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def pending(self) -> int:
        return self._q.qsize()

    def current(self) -> dict | None:
        return self._current

    def hold(self, owner: str) -> None:
        with self._lock:
            self._hold_owner = owner

    def release(self, owner: str) -> None:
        with self._lock:
            if self._hold_owner == owner:
                self._hold_owner = None

    def held(self) -> str | None:
        return self._hold_owner

    # ---- 内部 ----
    def emit_event(self, ev: dict) -> None:
        with self._lock:
            subs = list(self._subs)
        for s in subs:
            s.put(ev)

    def _emit(self, run: dict) -> None:
        self.emit_event({"type": "run", "run": run})

    def _loop(self) -> None:
        while True:
            run_id = self._q.get()
            run = self.runs.get(run_id)
            if run is None:
                continue
            # 示教会话占用手机期间不启动新 run（已入队的排着）。必须先等 hold 放手，
            # 再「腾去重名额」和「置 _current」——而且这两步要在同一临界区里原子完成。
            # 否则 hold 等待窗口里若先腾了名额、_current 又还没设，调度器此刻再 POST 同一
            # workflow 会既不命中 pending、也不命中 _current，凭空多建一个 run；等示教放手
            # 后两个 run 依次执行，对 post 类 workflow 就是连发两条真实推文（违反铁律）。
            # 等 hold 期间 pending 仍指向本 run，并发 enqueue 命中去重、返回本 run。
            while self._hold_owner is not None:
                time.sleep(0.2)
            control = RunControl()
            with self._lock:
                wf_id = run.get("workflow_id")
                if wf_id is not None and self._pending_by_workflow.get(wf_id) == run_id:
                    del self._pending_by_workflow[wf_id]
                self._current, self._control = run, control
            try:
                self._execute(run, control)
            except Exception as e:  # noqa: BLE001 — 队列线程必须活到下一个 run；任何未预料的
                # 异常（设备掉线、settings.json 被写坏、workflow 文件损坏等）都只应终结当前
                # run，不能杀死这个 daemon 线程，否则队列会永久卡死、后续 run 再也跑不起来。
                run.update({"status": "failed", "error": f"运行器异常：{e}", "finished_at": time.time()})
                self.runs.save(run)
                self._emit(run)
                if _is_stale_session(e):
                    self.invalidate()
            finally:
                with self._lock:
                    self._current, self._control = None, None

    def _execute(self, run: dict, control: RunControl) -> None:
        wf = self.workflows.get(run["workflow_id"])
        driver = self.driver_provider()
        error = None
        if wf is None:
            error = "workflow 已不存在"
        elif driver is None:
            error = "设备未连接"
        elif driver.locked():
            error = "手机屏幕已锁定，请解锁后重试"
        if error:
            run.update({"status": "failed", "error": error, "finished_at": time.time()})
            self.runs.save(run)
            self._emit(run)
            return
        delay = tuple(load_settings(self.root)["human_delay"])
        ex = self.executor_factory(driver, control, self._emit, delay)
        try:
            ex.execute(run, wf)
            # 真实 Executor 在 _publish 里已经落盘；这里兜底保存一次，
            # 以覆盖测试用的简化 executor_factory（不落盘、只 emit）的情况。
            self.runs.save(run)
        except Exception as e:  # noqa: BLE001 — 执行器自身崩溃也要落状态
            run.update({"status": "failed", "error": f"执行器异常：{e}", "finished_at": time.time()})
            self.runs.save(run)
            self._emit(run)
            if _is_stale_session(e):
                self.invalidate()
            return
        # Executor 内部把步骤异常吞掉转成了 run["error"] 文本（不会重新抛出），
        # 所以 session 失效要在这里从落盘的错误文案里识别，而不是靠 except 捕获异常对象。
        if run.get("status") == "failed" and _is_stale_session(None, run.get("error")):
            self.invalidate()
