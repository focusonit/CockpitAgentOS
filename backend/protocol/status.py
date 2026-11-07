"""执行层返回的状态码（开发计划 7.3）。

大脑拿到非 Success 的状态码后，要向用户解释原因，并在可能时给出替代方案。
"""

from __future__ import annotations

SUCCESS = "Success"
PART_SUCCEED = "PartSucceed"
PRECOND_SPEED = "PrecondSpeedNotSupported"
PRECOND_POWER = "PrecondPowerNotSupported"
PRECOND_USER = "PrecondUserNotSupported"
PRECOND = "PrecondNotSupported"
NOT_AC = "NotAC"
NOT_FOUND = "NotFound"
NOT_NET = "NotNet"
BT_DISCONNECTED = "BluetoothDisconnected"
UNKNOWN_COMMAND = "UnknownCommand"
INVALID_PARAM = "InvalidParam"
TIMEOUT = "Timeout"
ROLLED_BACK = "RolledBack"
FAILED = "Failed"
# 不是执行层的结果：需要确认的操作被拦下，等用户确认后才执行
PENDING_CONFIRM = "PendingConfirm"

# 给大脑和调试面板看的说明
DESCRIPTIONS: dict[str, str] = {
    SUCCESS: "执行成功",
    PART_SUCCEED: "只执行了一部分（例如车速过高，车窗只开了一条缝）",
    PRECOND_SPEED: "行驶中不能执行，需要停车后再操作",
    PRECOND_POWER: "车辆未上电",
    PRECOND_USER: "当前座位没有权限，只有主驾能操作",
    PRECOND: "当前车况不满足条件（例如不在 P 挡）",
    NOT_AC: "空调没有打开",
    NOT_FOUND: "当前车型没有这个配置",
    NOT_NET: "网络不可用",
    BT_DISCONNECTED: "蓝牙没有连接",
    UNKNOWN_COMMAND: "协议里没有这条指令",
    INVALID_PARAM: "参数不合法",
    TIMEOUT: "执行超时",
    ROLLED_BACK: "同批指令有失败，已回滚",
    FAILED: "执行失败",
    PENDING_CONFIRM: "需要用户确认后才执行",
}

OK_STATUSES = frozenset({SUCCESS, PART_SUCCEED})


def is_ok(status: str) -> bool:
    return status in OK_STATUSES
