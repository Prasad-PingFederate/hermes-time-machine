"""hermes-time-machine plugin entry point."""

from functools import partial
from .ledger import TimeMachineLedger, resolve_hermes_home
from .tools import (
    list_snapshots_handler,
    diff_handler,
    restore_handler,
    branch_handler,
)
from .schemas import (
    LIST_SCHEMA,
    DIFF_SCHEMA,
    RESTORE_SCHEMA,
    BRANCH_SCHEMA,
)

_ledger = None

def get_ledger() -> TimeMachineLedger:
    global _ledger
    if _ledger is None:
        _ledger = TimeMachineLedger()
    return _ledger

def on_pre_tool(tool_name: str, args: dict, **kwargs):
    ledger = get_ledger()
    ledger.record_pre_tool(
        session_id=kwargs.get("session_id"),
        turn_id=kwargs.get("turn_id"),
        tool_call_id=kwargs.get("tool_call_id"),
        tool_name=tool_name,
        args=args,
    )

def on_post_tool(tool_name: str, args: dict, result: str, **kwargs):
    ledger = get_ledger()
    ledger.record_post_tool(
        session_id=kwargs.get("session_id"),
        turn_id=kwargs.get("turn_id"),
        tool_call_id=kwargs.get("tool_call_id"),
        tool_name=tool_name,
        args=args,
        status=kwargs.get("status", "ok"),
        duration_ms=kwargs.get("duration_ms"),
    )

def on_session_start(**kwargs):
    # Ensure checkpoints are enabled if configured
    pass

def register(ctx):
    # Hooks
    ctx.register_hook("pre_tool_call", on_pre_tool)
    ctx.register_hook("post_tool_call", on_post_tool)
    ctx.register_hook("on_session_start", on_session_start)

    # Tools — inject ledger via partial
    ledger = get_ledger()
    ctx.register_tool(
        name="time_machine_list_snapshots",
        toolset="time-machine",
        schema=LIST_SCHEMA,
        handler=partial(list_snapshots_handler, ledger=ledger),
    )
    ctx.register_tool(
        name="time_machine_diff",
        toolset="time-machine",
        schema=DIFF_SCHEMA,
        handler=partial(diff_handler, ledger=ledger),
    )
    ctx.register_tool(
        name="time_machine_restore",
        toolset="time-machine",
        schema=RESTORE_SCHEMA,
        handler=partial(restore_handler, ledger=ledger),
    )
    ctx.register_tool(
        name="time_machine_branch",
        toolset="time-machine",
        schema=BRANCH_SCHEMA,
        handler=partial(branch_handler, ledger=ledger),
    )
