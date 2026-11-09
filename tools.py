"""
Tool schemas and handlers for hermes-time-machine:
  1. time_machine_list_snapshots
  2. time_machine_diff
  3. time_machine_restore
  4. time_machine_recover
  5. time_machine_search
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

try:
    from .ledger import TimeMachineLedger, resolve_working_dir
    from .schemas import (
        LIST_SCHEMA,
        DIFF_SCHEMA,
        RESTORE_SCHEMA,
        RECOVER_SCHEMA,
        SEARCH_SCHEMA,
        ALL_SCHEMAS,
    )
except (ImportError, ValueError):
    from ledger import TimeMachineLedger, resolve_working_dir
    from schemas import (
        LIST_SCHEMA,
        DIFF_SCHEMA,
        RESTORE_SCHEMA,
        RECOVER_SCHEMA,
        SEARCH_SCHEMA,
        ALL_SCHEMAS,
    )

LIST_SNAPSHOTS_SCHEMA = LIST_SCHEMA

logger = logging.getLogger(__name__)

TOOLSET_NAME = "time_machine"


def _extract_session_cwd(kwargs: Dict[str, Any]) -> Optional[str]:
    """Extract session cwd strictly from context/kwargs, never trusting model overrides."""
    ctx = kwargs.get("ctx")
    if ctx:
        if callable(getattr(ctx, "session_cwd", None)):
            try:
                scwd = ctx.session_cwd()
                if scwd:
                    return str(scwd)
            except Exception:
                pass
        scwd = getattr(ctx, "session_cwd", None)
        if scwd and isinstance(scwd, (str, bytes)):
            return str(scwd)
    return kwargs.get("session_cwd") or kwargs.get("working_dir")


def handle_list_snapshots(
    args: Dict[str, Any],
    *,
    ledger: Optional[TimeMachineLedger] = None,
    **kwargs: Any,
) -> str:
    ledger = ledger or TimeMachineLedger()
    session_id = args.get("session_id") or kwargs.get("session_id")
    limit = int(args.get("limit") or 50)
    session_cwd = _extract_session_cwd(kwargs)

    snapshots = ledger.list_snapshots(
        session_id=session_id if args.get("session_id") else None,
        working_dir=session_cwd,
        limit=limit,
    )

    return json.dumps(
        {
            "ok": True,
            "count": len(snapshots),
            "snapshots": snapshots,
        },
        indent=2,
        default=str,
    )


def handle_diff(
    args: Dict[str, Any],
    *,
    ledger: Optional[TimeMachineLedger] = None,
    **kwargs: Any,
) -> str:
    ledger = ledger or TimeMachineLedger()
    snapshot_id = (args.get("snapshot_id") or "").strip()
    if not snapshot_id:
        return json.dumps({"ok": False, "error": "snapshot_id is required."})

    session_cwd = _extract_session_cwd(kwargs)
    compare_to_id = args.get("compare_to_id")
    res = ledger.get_diff(snapshot_id=snapshot_id, compare_to_id=compare_to_id, working_dir=session_cwd)
    return json.dumps(res, indent=2, default=str)


def handle_restore(
    args: Dict[str, Any],
    *,
    ledger: Optional[TimeMachineLedger] = None,
    user_approved: bool = False,
    **kwargs: Any,
) -> str:
    ledger = ledger or TimeMachineLedger()
    snapshot_id = (args.get("snapshot_id") or "").strip()
    if not snapshot_id:
        return json.dumps({"ok": False, "error": "snapshot_id is required."})

    # Default to dry-run preview mode. Never touch disk unless user_approved is True.
    dry_run = bool(args.get("dry_run", True))
    if not user_approved:
        dry_run = True

    file_path = args.get("file_path")
    file_paths = args.get("file_paths")
    session_cwd = _extract_session_cwd(kwargs)

    res = ledger.restore_snapshot(
        snapshot_id=snapshot_id,
        file_path=file_path,
        file_paths=file_paths,
        working_dir=session_cwd,
        dry_run=dry_run,
        user_approved=user_approved,
    )
    return json.dumps(res, indent=2, default=str)


def handle_recover(
    args: Dict[str, Any],
    *,
    ledger: Optional[TimeMachineLedger] = None,
    user_approved: bool = False,
    **kwargs: Any,
) -> str:
    ledger = ledger or TimeMachineLedger()
    session_id = args.get("session_id") or kwargs.get("session_id")
    error_summary = args.get("error_summary")
    session_cwd = _extract_session_cwd(kwargs)

    # Recovery always runs as a diagnosis and preview plan; user approves restore via /tm restore.
    # Returns suggested_verify_command so the model executes verification via Hermes' terminal tool.
    res = ledger.recover(
        session_id=session_id,
        error_summary=error_summary,
        working_dir=session_cwd,
        dry_run=True,
        user_approved=user_approved,
    )
    return json.dumps(res, indent=2, default=str)


def handle_search(
    args: Dict[str, Any],
    *,
    ledger: Optional[TimeMachineLedger] = None,
    **kwargs: Any,
) -> str:
    ledger = ledger or TimeMachineLedger()
    query = (args.get("query") or "").strip()
    if not query:
        return json.dumps({"ok": False, "error": "query parameter is required."})

    session_id = args.get("session_id") or kwargs.get("session_id")
    file_path = args.get("file_path")
    session_cwd = _extract_session_cwd(kwargs)
    limit = int(args.get("limit") or 10)

    res = ledger.search_snapshots(
        query=query,
        session_id=session_id,
        file_path=file_path,
        working_dir=session_cwd,
        limit=limit,
    )
    return json.dumps(res, indent=2, default=str)


def register_time_machine_tools(ctx, ledger: Optional[TimeMachineLedger] = None) -> None:
    """Register all 5 time_machine_* tools with the plugin context."""

    def _get_ledger() -> TimeMachineLedger:
        return ledger if ledger is not None else TimeMachineLedger()

    def list_wrapper(args, **kw):
        return handle_list_snapshots(args, ledger=_get_ledger(), ctx=ctx, **kw)

    def diff_wrapper(args, **kw):
        return handle_diff(args, ledger=_get_ledger(), ctx=ctx, **kw)

    def restore_wrapper(args, **kw):
        return handle_restore(args, ledger=_get_ledger(), ctx=ctx, **kw)

    def recover_wrapper(args, **kw):
        return handle_recover(args, ledger=_get_ledger(), ctx=ctx, **kw)

    def search_wrapper(args, **kw):
        return handle_search(args, ledger=_get_ledger(), ctx=ctx, **kw)

    if hasattr(ctx, "register_tool"):
        ctx.register_tool(
            name="time_machine_list_snapshots",
            toolset="time_machine",
            schema=LIST_SCHEMA,
            handler=list_wrapper,
            description=LIST_SCHEMA["description"],
        )
        ctx.register_tool(
            name="time_machine_diff",
            toolset="time_machine",
            schema=DIFF_SCHEMA,
            handler=diff_wrapper,
            description=DIFF_SCHEMA["description"],
        )
        ctx.register_tool(
            name="time_machine_restore",
            toolset="time_machine",
            schema=RESTORE_SCHEMA,
            handler=restore_wrapper,
            description=RESTORE_SCHEMA["description"],
        )
        ctx.register_tool(
            name="time_machine_recover",
            toolset="time_machine",
            schema=RECOVER_SCHEMA,
            handler=recover_wrapper,
            description=RECOVER_SCHEMA["description"],
        )
        ctx.register_tool(
            name="time_machine_search",
            toolset="time_machine",
            schema=SEARCH_SCHEMA,
            handler=search_wrapper,
            description=SEARCH_SCHEMA["description"],
        )

