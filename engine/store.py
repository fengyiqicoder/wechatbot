"""本机存储：Workflow JSON、Run 记录与步骤截图、设置。全部接受可注入 root 便于测试。"""
from __future__ import annotations
import copy
import json
import os
import secrets
import shutil
import tempfile
import time
from pathlib import Path

from engine.workflow import validate

DEFAULT_ROOT = Path.home() / "Library" / "Application Support" / "WeChatBot"
DEFAULT_SETTINGS = {
    "human_delay": [0.8, 2.0],
    # AX 快照深度：默认 20（X 必须浅，深了会把它卡到触发自我看门狗自杀）；
    # 个别 App 要更深才读得到内容，在 ax_depth_by_app 里按 bundle 覆盖。
    "ax_depth": 20,
    "ax_depth_by_app": {"com.burbn.instagram": 30},
    "sleep_window": {"start": "23:00", "end": "07:30"},
    "openrouter_key": "",
    "model": "x-ai/grok-4.6",
    "teach_cap_usd": 0.5,
    "daily_cap_usd": 5.0,
    "prices": {"x-ai/grok-4.6": [2.0, 6.0]},
}


def _atomic_write_text(path: Path, text: str) -> None:
    """先写临时文件再 os.replace，避免并发读到半个文件。"""
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def strip_anchors(wf: dict) -> dict:
    out = copy.deepcopy(wf)
    out["anchors"] = {k: {kk: vv for kk, vv in v.items() if kk != "png"} for k, v in (wf.get("anchors") or {}).items()}
    return out


class WorkflowStore:
    def __init__(self, root: Path):
        self.dir = Path(root) / "workflows"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, wf_id: str) -> Path:
        return self.dir / f"{wf_id}.json"

    def seed_builtin(self, builtin_dir: Path) -> int:
        n = 0
        for f in sorted(Path(builtin_dir).glob("*.json")):
            wf = json.loads(f.read_text())
            existing = self.get(wf["id"])
            if existing and existing.get("source") == "user":
                continue
            if existing and existing.get("version", 0) >= wf.get("version", 0):
                continue
            self.save(wf)
            n += 1
        return n

    def list(self) -> list[dict]:
        items = [strip_anchors(json.loads(p.read_text())) for p in self.dir.glob("*.json")]
        return sorted(items, key=lambda w: (w.get("source") != "builtin", w.get("name", "")))

    def get(self, wf_id: str, full: bool = True) -> dict | None:
        p = self._path(wf_id)
        if not p.exists():
            return None
        wf = json.loads(p.read_text())
        return wf if full else strip_anchors(wf)

    def save(self, wf: dict) -> None:
        errs = validate(wf)
        if errs:
            raise ValueError("; ".join(errs))
        _atomic_write_text(self._path(wf["id"]), json.dumps(wf, ensure_ascii=False, indent=2))

    def delete(self, wf_id: str) -> bool:
        wf = self.get(wf_id)
        if wf is None:
            return False
        if wf.get("source") == "builtin":
            raise ValueError("内置 workflow 不可删除")
        self._path(wf_id).unlink()
        return True

    def duplicate(self, wf_id: str) -> dict:
        src = self.get(wf_id)
        if src is None:
            raise KeyError(wf_id)
        n = 1
        while self._path(f"{wf_id}.copy-{n}").exists():
            n += 1
        out = copy.deepcopy(src)
        out.update({"id": f"{wf_id}.copy-{n}", "name": f"{src['name']}（副本）", "source": "user", "version": 1})
        self.save(out)
        return out


class RunStore:
    # 同一 workflow 最多保留这么多个 run（含步骤截图）；超出的最旧目录整个删掉。
    # loop 型 workflow 一天能跑出成百上千个 run，没有这道闸 runs/ 会无限增长。
    MAX_RUNS_PER_WORKFLOW = 50

    def __init__(self, root: Path):
        self.dir = Path(root) / "runs"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _dir(self, run_id: str) -> Path:
        return self.dir / run_id

    def create(self, workflow_id: str, params: dict) -> dict:
        run = {
            # 微秒精度：毫秒在快速连续创建（测试、loop workflow）时容易撞车，
            # 撞车会让「按文件名排序=按时间排序」这个 list()/_prune() 都依赖的前提失效。
            "id": f"{int(time.time() * 1_000_000)}-{secrets.token_hex(2)}",
            "workflow_id": workflow_id, "params": params, "status": "queued",
            "created_at": time.time(), "started_at": None, "finished_at": None, "error": None, "steps": [],
        }
        self.save(run)
        self._prune(workflow_id)
        return run

    def save(self, run: dict) -> None:
        d = self._dir(run["id"])
        d.mkdir(parents=True, exist_ok=True)
        _atomic_write_text(d / "run.json", json.dumps(run, ensure_ascii=False))

    def get(self, run_id: str) -> dict | None:
        p = self._dir(run_id) / "run.json"
        return json.loads(p.read_text()) if p.exists() else None

    def list(self, workflow_id: str | None = None, limit: int = 50) -> list[dict]:
        if not workflow_id:  # None 或空字符串都当「不过滤」，和改动前的 `if workflow_id:` 语义一致
            # 不按 workflow 过滤时不需要把所有 run 都反序列化一遍：run id 是
            # "{us}-{hex}"，目录名倒序排列就是创建时间倒序，最多打开 limit 个文件。
            names = sorted((p.name for p in self.dir.iterdir() if p.is_dir()), reverse=True)
            runs = []
            for name in names:
                p = self.dir / name / "run.json"
                if not p.exists():
                    continue
                try:
                    runs.append(json.loads(p.read_text()))
                except (OSError, json.JSONDecodeError):
                    continue
                if len(runs) >= limit:
                    break
        else:
            # 按 workflow_id 过滤：没有额外索引，只能全部读一遍才知道谁属于该 workflow；
            # MAX_RUNS_PER_WORKFLOW 的保留上限保证了这个「全部」不会无限增长。
            runs = [json.loads(p.read_text()) for p in self.dir.glob("*/run.json")]
            runs = [r for r in runs if r["workflow_id"] == workflow_id]
        runs.sort(key=lambda r: r["created_at"], reverse=True)
        return runs[:limit]

    def _prune(self, workflow_id: str, keep: int = MAX_RUNS_PER_WORKFLOW) -> None:
        ids = []
        for p in self.dir.iterdir():
            if not p.is_dir():
                continue
            run_json = p / "run.json"
            if not run_json.exists():
                continue
            try:
                wf = json.loads(run_json.read_text()).get("workflow_id")
            except (OSError, json.JSONDecodeError):
                continue
            if wf == workflow_id:
                ids.append(p.name)
        ids.sort(reverse=True)  # 目录名前缀是微秒时间戳，倒序 = 新 → 旧
        for old in ids[keep:]:
            shutil.rmtree(self.dir / old, ignore_errors=True)

    def save_step_png(self, run_id: str, n: int, png: bytes) -> None:
        self.step_png_path(run_id, n).write_bytes(png)

    def step_png_path(self, run_id: str, n: int) -> Path:
        d = self._dir(run_id)
        d.mkdir(parents=True, exist_ok=True)
        return d / f"step-{n}.png"


def load_settings(root: Path) -> dict:
    p = Path(root) / "settings.json"
    data = json.loads(p.read_text()) if p.exists() else {}
    return {**copy.deepcopy(DEFAULT_SETTINGS), **data}


def save_settings(root: Path, settings: dict) -> dict:
    current = load_settings(root)
    merged = dict(current)
    for k, v in settings.items():
        if isinstance(v, dict) and isinstance(current.get(k), dict):
            merged[k] = {**current[k], **v}
        else:
            merged[k] = v
    Path(root).mkdir(parents=True, exist_ok=True)
    _atomic_write_text(Path(root) / "settings.json", json.dumps(merged, ensure_ascii=False, indent=2))
    return merged
