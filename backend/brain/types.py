"""大脑与评测框架共用的数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.vehicle.state import VehicleState

# 领域工具的参数里，指令名放在这个键下，其余键是协议的命名参数。
COMMAND_KEY = "command"
STATUS_OK = "Success"


@dataclass(frozen=True)
class ToolCall:
    """一次工具调用。

    领域工具：``args = {"command": "ACCtrl.SetTemperature", "Area": "主驾", "Number": 20}``
    子代理：  ``args = {"task": "半小时后关空调"}``
    """

    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    status: str = STATUS_OK
    result: Any = None
    needs_confirm: bool = False

    @property
    def command(self) -> str | None:
        value = self.args.get(COMMAND_KEY)
        return value if isinstance(value, str) else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "args": self.args,
            "status": self.status,
            "result": self.result,
            "needs_confirm": self.needs_confirm,
        }


@dataclass(frozen=True)
class Turn:
    """一轮输入：要么是乘员说的话，要么是系统事件（例如【到点触发定时任务】）。"""

    seat: str
    text: str = ""
    event: str | None = None

    @property
    def is_event(self) -> bool:
        return self.event is not None


@dataclass(frozen=True)
class TurnResult:
    calls: tuple[ToolCall, ...] = ()
    reply: str = ""
    confirm_requested: bool = False
    # 本轮产生的结构化数据，供后续轮次引用，例如 {"poi": [{"name": ...}]}
    data: dict[str, Any] = field(default_factory=dict)
    # 执行工具后的车辆状态；None 表示大脑没有改动车辆（不进 to_dict，状态另行下发）
    state: "VehicleState | None" = field(default=None, compare=False, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "calls": [c.to_dict() for c in self.calls],
            "reply": self.reply,
            "confirm_requested": self.confirm_requested,
            "data": self.data,
        }
