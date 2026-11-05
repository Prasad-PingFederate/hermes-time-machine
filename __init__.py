"""hermes-time-machine plugin entry point."""

from pathlib import Path
from .ledger import TimeMachineLedger
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

# Global ledger instance (initialized on first use)
_ledger = None

def get_ledger(data_dir: Path = None) -> TimeMachineLedger:
    global _ledger
    if _ledger is None:
        if data_dir is None:
            data_dir = Path.home() / ".hermes" / "plugins" / "hermes-time-machine" / "data"
        _ledger = TimeMachineLedger(data_dir)
    return _ledger

def on_pre_tool(tool_name: str, args: dict, **kwargs):
    """Record intent before tool execution."""
    # TODO: capture working_dir, session_id from kwargs / context
    pass

def on_post_tool(tool_name: str, args: dict, result: str, **kwargs):
    """Record outcome and link to checkpoint after tool execution."""
    ledger = get_ledger()
    # TODO: extract session_id, working_dir, affected files, diff from context/result
    # ledger.record(...)
    pass

def on_session_start(**kwargs):
    """Ensure checkpoints are enabled if configured."""
    pass

def register(ctx):
    # Hooks
    ctx.register_hook("pre_tool_call", on_pre_tool)
    ctx.register_hook("post_tool_call", on_post_tool)
    ctx.register_hook("on_session_start", on_session_start)

    # Tools
    ctx.register_tool(
        name="time_machine_list_snapshots",
        toolset="time-machine",
        schema=LIST_SCHEMA,
        handler=list_snapshots_handler,
    )
    ctx.register_tool(
        name="time_machine_diff",
        toolset="time-machine",
        schema=DIFF_SCHEMA,
        handler=diff_handler,
    )
    ctx.register_tool(
        name="time_machine_restore",
        toolset="time-machine",
        schema=RESTORE_SCHEMA,
        handler=restore_handler,
    )
    ctx.register_tool(
        name="time_machine_branch",
        toolset="time-machine",
        schema=BRANCH_SCHEMA,
        handler=branch_handler,
    )

    # Optional slash command
    # ctx.register_command("rewind", rewind_command, "Open or control the Time Machine")
