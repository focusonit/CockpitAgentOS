"""电话 App：通讯录查询、拨号、挂断、回拨、通话记录。蓝牙断开由前置条件规则判断。"""

from __future__ import annotations

from typing import Any

from backend.apps.common import load, now_of, ok
from backend.protocol import status as st
from backend.protocol.spec import InvalidParamError
from backend.vehicle.devices.base import Ctx, Handler, Outcome

RESCUE = {"BcallCall.DialContact": "道路救援", "EcallCall.DialContact": "紧急呼叫"}


def contacts() -> list[dict[str, Any]]:
    return load("contacts")["contacts"]


def find_contacts(name: str) -> list[dict[str, Any]]:
    """精确名字优先；否则按别名、包含关系匹配；“老王”“小王”按姓匹配。"""
    name = name.strip()
    exact = [c for c in contacts() if c["name"] == name]
    if exact:
        return exact
    hits = [c for c in contacts() if name in c.get("aliases", []) or name in c["name"]]
    if not hits and len(name) == 2 and name[0] in "老小":
        hits = [c for c in contacts() if c["name"].startswith(name[1])]
    return hits


def search(ctx: Ctx) -> Outcome:
    name = str(ctx.arg("Name") or "")
    if not name:
        raise InvalidParamError("PhoneCall.Search 需要 Name")
    hits = find_contacts(name)
    if not hits:
        return ok(ctx, f"通讯录里没有找到{name}", {"phone.last_results": []}, {"contacts": []})
    listing = "；".join(f"{i}. {c['name']}" for i, c in enumerate(hits, 1))
    text = f"找到{len(hits)}个联系人：{listing}" + ("，要打给哪一个？" if len(hits) > 1 else "")
    return ok(ctx, text, {"phone.last_results": hits}, {"contacts": hits})


def _dial(ctx: Ctx, name: str, number: str, kind: str = "呼出") -> Outcome:
    entry = {"name": name, "phone": number, "type": kind, "at": now_of(ctx).isoformat(timespec="minutes")}
    log = [*(ctx.state.get("phone.call_log") or []), entry]
    return ok(ctx, f"正在呼叫{name}", {"phone.in_call": True, "phone.contact": name, "phone.call_log": log}, {"contact": entry})


def dial_contact(ctx: Ctx) -> Outcome:
    name, number = str(ctx.arg("Name") or ""), str(ctx.arg("Phone") or "")
    if not name and not number:
        raise InvalidParamError("PhoneCall.DialContact 需要 Name 或 Phone")
    if not name:
        return _dial(ctx, number, number)
    hits = find_contacts(name)
    if len(hits) == 1:
        return _dial(ctx, hits[0]["name"], hits[0]["phone"])
    if len(hits) > 1:
        listing = "、".join(c["name"] for c in hits)
        return Outcome(st.FAILED, data={"contacts": hits}, message=f"有{len(hits)}个联系人匹配{name}：{listing}，请说要打给哪一个")
    if number:
        return _dial(ctx, name, number)
    return Outcome(st.NOT_FOUND, message=f"通讯录里没有{name}")


def dial_number(ctx: Ctx) -> Outcome:
    number = str(ctx.arg("Phone") or "")
    if not number:
        raise InvalidParamError("PhoneCall.DialNumber 需要 Phone")
    return _dial(ctx, number, number)


def hang_up(ctx: Ctx) -> Outcome:
    if not ctx.state.get("phone.in_call"):
        return ok(ctx, "现在没有在通话")
    return ok(ctx, "已挂断", {"phone.in_call": False, "phone.contact": ""})


def answer(ctx: Ctx) -> Outcome:
    return ok(ctx, "已接听", {"phone.in_call": True})


def recall(ctx: Ctx) -> Outcome:
    log = ctx.state.get("phone.call_log") or []
    if not log:
        return Outcome(st.FAILED, message="没有通话记录")
    return _dial(ctx, log[-1]["name"], log[-1]["phone"])


def call_log(ctx: Ctx) -> Outcome:
    log = list(ctx.state.get("phone.call_log") or [])
    kind = ctx.arg("CallLogType")
    if kind:
        log = [e for e in log if kind in e.get("type", "")]
    text = "；".join(f"{e['name']} {e['type']} {e['at'][11:]}" for e in log[-5:]) or "没有通话记录"
    return ok(ctx, text, data={"call_log": log})


def sync(ctx: Ctx) -> Outcome:
    return ok(ctx, f"通讯录已同步，共{len(contacts())}个联系人")


def rescue(ctx: Ctx) -> Outcome:
    return _dial(ctx, RESCUE[ctx.command], "救援热线", "救援")


HANDLERS: dict[tuple[str, str], Handler] = {
    ("contact_query", "PhoneCall.Search"): search,
    ("phone", "PhoneCall.DialContact"): dial_contact,
    ("phone", "PhoneCall.DialNumber"): dial_number,
    ("phone", "PhoneCall.HangUp"): hang_up,
    ("phone", "PhoneCall.Answer"): answer,
    ("phone", "PhoneCall.ReCall"): recall,
    ("phone", "PhoneCall.OpenCallLog"): call_log,
    ("phone", "PhoneCall.SyncAddressBook"): sync,
    ("phone", "BcallCall.DialContact"): rescue,
    ("phone", "EcallCall.DialContact"): rescue,
}
