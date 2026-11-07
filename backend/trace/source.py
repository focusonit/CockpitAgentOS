"""一条回复是谁给出的：从这一轮的链路记录里推断，前端在每条回复上标出来。"""

from __future__ import annotations

from typing import Any

from backend.trace.recorder import TraceEvent, TraceRecorder

# kind → 默认标签；llm 的标签另外带上模型名
LABELS = {
    "llm": "云端大模型",
    "gate": "端侧安全闸门",
    "unsupported": "端侧闸门 · 未上线能力",
    "fast_path": "端侧快速通道",
    "offline": "端侧 · 断网兜底",
    "confirm": "端侧 · 确认流程",
    "echo": "Echo 占位（没配大模型）",
    "rule": "车机规则",
}


def _fast_path_answered(event: TraceEvent) -> bool:
    return event.stage == "fast_path" and not event.message.endswith("转云端")


def reply_source(trace: TraceRecorder) -> dict[str, Any]:
    """返回 ``{"kind", "label"}``。有大模型参与就算大模型，否则按端侧环节判断。"""
    events = trace.events
    llm = [e for e in events if e.stage == "llm"]
    if llm:
        model = llm[-1].data.get("model")
        return {"kind": "llm", "label": f"{LABELS['llm']} · {model}" if model else LABELS["llm"]}
    for event in events:
        if event.data.get("route") == "edge" and event.stage in ("gate", "unsupported"):
            return {"kind": event.stage, "label": LABELS[event.stage]}
        if _fast_path_answered(event):
            return {"kind": "fast_path", "label": LABELS["fast_path"]}
        if event.data.get("route") == "offline":
            return {"kind": "offline", "label": LABELS["offline"]}
        if event.stage == "confirm":
            return {"kind": "confirm", "label": LABELS["confirm"]}
        if event.stage == "planner":
            return {"kind": "echo", "label": LABELS["echo"]}
    return {"kind": "rule", "label": LABELS["rule"]}
