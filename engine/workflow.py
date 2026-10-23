"""Workflow DSL：校验、参数渲染、日志文案。执行期不含任何 LLM。"""
from __future__ import annotations
import copy
import re
from urllib.parse import quote

OPS = {"launch_app", "open_url", "tap", "type", "swipe", "wait_for", "human_delay", "llm_text", "home"}
TRIGGER_MODES = {"manual", "scheduled", "loop"}
REQUIRED_STEP_FIELDS = {
    "launch_app": ["bundle_id"],
    "open_url": ["url"],
    "tap": ["rect"],
    "type": ["text"],
    "swipe": ["from", "to"],
    "wait_for": ["rect", "anchor"],
    "human_delay": ["min_s", "max_s"],
    "llm_text": ["prompt", "out"],
    "home": [],
}
REQUIRED_TOP = ["id", "name", "app", "steps"]
_VAR = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def validate(data: dict) -> list[str]:
    errs: list[str] = []
    for k in REQUIRED_TOP:
        if not data.get(k):
            errs.append(f"缺少字段 {k}")
    trigger = data.get("trigger") or {"mode": "manual"}
    mode = trigger.get("mode")
    if mode not in TRIGGER_MODES:
        errs.append(f"trigger.mode 非法: {mode}")
    elif mode == "scheduled" and not re.fullmatch(r"\d{2}:\d{2}", str(trigger.get("at", ""))):
        errs.append("scheduled 触发需要 at (HH:MM)")
    elif mode == "loop" and not isinstance(trigger.get("every_min"), (int, float)):
        errs.append("loop 触发需要 every_min")
    anchors = data.get("anchors") or {}
    for i, step in enumerate(data.get("steps") or []):
        op = step.get("op")
        if op not in OPS:
            errs.append(f"steps[{i}] op 非法: {op}")
            continue
        for f in REQUIRED_STEP_FIELDS[op]:
            if f not in step:
                errs.append(f"steps[{i}] ({op}) 缺少 {f}")
        if "rect" in step and not (isinstance(step["rect"], list) and len(step["rect"]) == 4):
            errs.append(f"steps[{i}] rect 必须是 [x, y, w, h]")
        if "at" in step and not (isinstance(step["at"], list) and len(step["at"]) == 2
                                  and all(isinstance(v, (int, float)) for v in step["at"])):
            errs.append(f"steps[{i}] at 必须是 [dx, dy]")
        if step.get("anchor") and step["anchor"] not in anchors and not step.get("no_anchor"):
            errs.append(f"steps[{i}] 锚点不存在: {step['anchor']}")
    return errs


def missing_params(workflow: dict, params: dict) -> list[str]:
    return [p["name"] for p in workflow.get("params", [])
            if p.get("required") and not str(params.get(p["name"], "")).strip()]


def render_value(value: str, params: dict, encode: bool = False) -> str:
    def sub(m: re.Match) -> str:
        v = str(params.get(m.group(1), ""))
        return quote(v, safe="") if encode else v
    return _VAR.sub(sub, value)


def render_steps(workflow: dict, params: dict) -> list[dict]:
    steps = copy.deepcopy(workflow["steps"])
    for step in steps:
        for key in ("url", "text", "bundle_id", "prompt"):
            if isinstance(step.get(key), str):
                step[key] = render_value(step[key], params, encode=(key == "url"))
    return steps


def describe(step: dict) -> str:
    op = step.get("op")
    sel = (step.get("selector") or {}).get("ax_id")
    if op == "open_url":
        return f"打开 {step.get('url', '')}"
    if op == "launch_app":
        return f"启动 {step.get('bundle_id', '')}"
    if op == "tap":
        return f"点 {sel}" if sel else f"点 ({', '.join(str(v) for v in step.get('rect', []))})"
    if op == "wait_for":
        return f"等待 {sel or step.get('anchor')} 出现"
    if op == "type":
        return f"输入「{step.get('text', '')}」"
    if op == "swipe":
        return f"滑动 {step.get('from')} → {step.get('to')}"
    if op == "human_delay":
        return f"停顿 {step.get('min_s')}–{step.get('max_s')} 秒"
    if op == "llm_text":
        return f"LLM 取值 → {step.get('out')}"
    if op == "home":
        return "回桌面"
    return str(op)
