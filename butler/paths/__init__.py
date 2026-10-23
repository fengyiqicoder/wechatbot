"""路径层：把 20 个原子操作叠成「采集」与「写操作」两类可复用路径，按 App 注册。

每条路径是 `fn(ctx, **params)`：
- kind="collect"：用 ctx.emit()/ctx.collect() 产出条目，任务结束时 items 就是结果；
- kind="action"：返回一个 dict 当结果；写类路径（writes=True）默认 mode="draft" 只出草稿，
  mode="send" 才真的动手，动手前查台账，动完读回再记台账。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable

APP_BUNDLES = {
    "wechat": "com.tencent.xin",
}
APP_NAMES = {"wechat": "微信"}


@dataclass
class PathSpec:
    app: str
    name: str
    fn: Callable
    kind: str = "collect"            # collect | action
    writes: bool = False
    doc: str = ""
    params: dict = field(default_factory=dict)   # 名 -> 说明
    star: bool = False

    @property
    def bundle(self) -> str:
        return APP_BUNDLES[self.app]

    def describe(self) -> dict:
        return {"app": self.app, "name": self.name, "kind": self.kind, "writes": self.writes,
                "doc": self.doc, "params": self.params, "star": self.star}


PATHS: dict[tuple[str, str], PathSpec] = {}


def path(app: str, name: str, kind: str = "collect", writes: bool = False, doc: str = "",
         params: dict | None = None, star: bool = False):
    def deco(fn):
        PATHS[(app, name)] = PathSpec(app, name, fn, kind, writes, doc, params or {}, star)
        return fn
    return deco


def get(app: str, name: str) -> PathSpec | None:
    return PATHS.get((app, name))


def listing(app: str | None = None) -> list[dict]:
    return [s.describe() for (a, _), s in sorted(PATHS.items()) if app is None or a == app]


def load_all() -> None:
    """导入各 App 模块以完成注册（延迟导入，避免循环）。"""
    import importlib
    for m in ("wechat",):
        try:
            importlib.import_module(f"butler.paths.{m}")
        except ModuleNotFoundError as e:  # 还没写的模块跳过
            if f"butler.paths.{m}" not in str(e):
                raise
