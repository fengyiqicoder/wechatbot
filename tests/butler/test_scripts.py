from engine.store import WorkflowStore
from butler.scripts import entries_to_workflow
from tests.butler.test_screen import BOXES, png

PNG = png(boxes=BOXES)


def entries():
    return [
        {"op": "open", "ok": True, "open": {"kind": "launch", "value": "x"}},
        {"op": "tap", "ok": True, "resolved": {"name": "私信", "rect": [10, 100, 40, 15], "method": "ax"}, "before_png": "b.png"},
        {"op": "type", "ok": True, "req": {"text": "hi"}},
        {"op": "swipe", "ok": True, "swipe": [50, 140, 50, 60]},
        {"op": "tap", "ok": False, "blocked": {"reason": "ambiguous"}},
        {"op": "home", "ok": True},
    ]


def test_entries_to_workflow_maps_ops_and_anchors(tmp_path):
    wf = entries_to_workflow(entries(), "测试", "sb.test", 1.0, load_png=lambda n: PNG)
    ops = [s["op"] for s in wf["steps"]]
    assert ops == ["launch_app", "tap", "type", "swipe", "home"]
    tap = wf["steps"][1]
    assert tap["rect"] == [10, 100, 40, 15] and tap["selector"] == {"ax_id": "私信"} and tap["anchor"] == "a1"
    assert wf["anchors"]["a1"]["threshold"] == 0.88 and wf["anchors"]["a1"]["png"]
    assert wf["steps"][3] == {"op": "swipe", "from": [50, 140], "to": [50, 60]}
    WorkflowStore(tmp_path).save(wf)
