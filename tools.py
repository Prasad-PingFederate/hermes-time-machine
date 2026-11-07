"""
Tool schemas and handlers for hermes-time-machine:
  1. time_machine_list_snapshots
  2. time_machine_diff
  3. time_machine_restore
  4. time_machine_branch
"""
from __future__ import annotations

import json
import logging
from functools import partial
from typing import Any, Dict, Optional

try:
    from .ledger import TimeMachineLedger, resolve_working_dir
except (ImportError, ValueError):
    from ledger import TimeMachineLedger, resolve_working_dir

logger = logging.getLogger(__name__)

TOOLSET_NAME = "time_machine"

LIST_SNAPSHOTS_SCHEMA: Dict[str, Any] = {
    "name": "time_machine_list_snapshots",
    "description": (
        "List Time Machine snapshots (paired filesystem shadow-git checkpoints and "
        "conversation message watermarks) for the current or specified session and working directory."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Optional session ID filter. Omit to list snapshots across recent sessions.",
            },
            "working_dir": {
                "type": "string",
                "description": "Optional project directory path to filter checkpoints by.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of snapshots to return (default 20).",
                "default": 20,
            },
        },
        "required": [],
    },
}

DIFF_SCHEMA: Dict[str, Any] = {
    "name": "time_machine_diff",
    "description": (
        "Show the unified git diff between a Time Machine snapshot (or checkpoint commit hash) "
        "and the current working directory state."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "snapshot_id": {
                "type": "string",
                "description": "Snapshot ID (e.g. 'snap_...') or shadow git commit hash prefix.",
            },
            "compare_to_id": {
                "type": "string",
                "description": (
                    "Optional snapshot ID to diff against, or 'WORKING_TREE' to inspect uncommitted "
                    "drift since the snapshot. Omit to diff against the previous turn."
                ),
            },
            "working_dir": {
                "type": "string",
                "description": "Optional working directory override if passing a raw commit hash.",
            },
        },
        "required": ["snapshot_id"],
    },
}

RESTORE_SCHEMA: Dict[str, Any] = {
    "name": "time_machine_restore",
    "description": (
        "Restore the working directory files to a Time Machine snapshot using Hermes's "
        "TimeMachineLedger via standard git plumbing (automatically taking a safety backup checkpoint first), and "
        "optionally rewind the conversation history in state.db to that turn."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "snapshot_id": {
                "type": "string",
                "description": "Snapshot ID (e.g. 'snap_...') or checkpoint commit hash to restore.",
            },
            "file_path": {
                "type": "string",
                "description": "Optional single file path to restore instead of the entire project tree.",
            },
            "file_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of specific file paths to restore. If provided, ONLY these files are reverted to the snapshot's state while preserving all other edits.",
            },
            "confirm": {
                "type": "boolean",
                "description": "Explicit confirmation flag required to execute restoration (default true).",
                "default": True,
            },
            "restore_files": {
                "type": "boolean",
                "description": "Whether to restore filesystem state (default true).",
                "default": True,
            },
            "rewind_conversation": {
                "type": "boolean",
                "description": (
                    "Whether to also soft-delete conversation messages in state.db after this "
                    "snapshot's message watermark (default false during active agent turn)."
                ),
                "default": False,
            },
            "working_dir": {
                "type": "string",
                "description": "Optional working directory override.",
            },
        },
        "required": ["snapshot_id"],
    },
}

BRANCH_SCHEMA: Dict[str, Any] = {
    "name": "time_machine_branch",
    "description": (
        "Fork a new Hermes session in state.db from a historical snapshot, copying conversation "
        "messages up to that snapshot's watermark and optionally restoring the filesystem to match."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "snapshot_id": {
                "type": "string",
                "description": "Snapshot ID (e.g. 'snap_...') to branch from.",
            },
            "branch_title": {
                "type": "string",
                "description": "Optional human-readable title for the newly created session branch.",
            },
            "restore_files": {
                "type": "boolean",
                "description": "Whether to also restore the working tree files to the snapshot's checkpoint (default true).",
                "default": True,
            },
            "working_dir": {
                "type": "string",
                "description": "Optional working directory override.",
            },
        },
        "required": ["snapshot_id"],
    },
}


RECOVER_SCHEMA: Dict[str, Any] = {
    "name": "time_machine_recover",
    "description": (
        "Autonomous self-healing diagnostic and recovery tool. Analyzes recent failed tool results "
        "or test tracebacks, identifies culprit files edited before the failure, optionally "
        "surgically restores them, and automatically re-verifies test suites in one step."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "error_summary": {
                "type": "string",
                "description": "Optional error message, traceback, or test failure output to diagnose. If omitted, uses the last recorded command failure in the session.",
            },
            "auto_apply": {
                "type": "boolean",
                "description": "If true, immediately applies surgical restoration of identified culprit files to the pre-error state.",
                "default": False,
            },
            "confirm": {
                "type": "boolean",
                "description": "Confirmation guard required when auto_apply is true (default true).",
                "default": True,
            },
            "verify_command": {
                "type": "string",
                "description": "Optional test or build command to run after restoration to verify the fix (e.g. 'pytest -q'). Defaults to the last failed command.",
            },
            "auto_verify": {
                "type": "boolean",
                "description": "Whether to automatically execute verify_command after restore to confirm green tests (default true).",
                "default": True,
            },
            "session_id": {
                "type": "string",
                "description": "Optional session ID filter. Defaults to active session.",
            },
            "working_dir": {
                "type": "string",
                "description": "Optional working directory override.",
            },
        },
    },
}

SEARCH_SCHEMA: Dict[str, Any] = {
    "name": "time_machine_search",
    "description": (
        "Search across historical snapshot diffs and file edits to find which turn "
        "introduced, modified, or removed a specific symbol, line of code, or file."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Code string, function name, symbol, or error message to search for in historical diffs.",
            },
            "session_id": {
                "type": "string",
                "description": "Optional session ID filter. Defaults to current session.",
            },
            "file_path": {
                "type": "string",
                "description": "Optional file path filter to narrow search to a specific file.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum matching snapshots to return (default 10).",
                "default": 10,
            },
            "working_dir": {
                "type": "string",
                "description": "Optional working directory override.",
            },
        },
        "required": ["query"],
    },
}


def handle_list_snapshots(
    args: Dict[str, Any],
    *,
    ledger: Optional[TimeMachineLedger] = None,
    **kwargs: Any,
) -> str:
    ledger = ledger or TimeMachineLedger()
    session_id = args.get("session_id") or kwargs.get("session_id")
    working_dir = args.get("working_dir")
    limit = int(args.get("limit") or 20)

    snapshots = ledger.list_snapshots(
        session_id=session_id if args.get("session_id") else None,
        working_dir=working_dir,
        limit=limit,
        include_raw_checkpoints=True,
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

    working_dir = args.get("working_dir")
    compare_to_id = args.get("compare_to_id")
    res = ledger.get_diff(snapshot_id=snapshot_id, compare_to_id=compare_to_id, working_dir=working_dir)
    return json.dumps(res, indent=2, default=str)


def handle_restore(
    args: Dict[str, Any],
    *,
    ledger: Optional[TimeMachineLedger] = None,
    **kwargs: Any,
) -> str:
    ledger = ledger or TimeMachineLedger()
    snapshot_id = (args.get("snapshot_id") or "").strip()
    if not snapshot_id:
        return json.dumps({"ok": False, "error": "snapshot_id is required."})

    confirm = bool(args.get("confirm", True))
    dry_run = bool(args.get("dry_run", False))
    if not confirm:
        dry_run = True

    restore_files = bool(args.get("restore_files", True))
    rewind_conversation = bool(args.get("rewind_conversation", False))
    file_path = args.get("file_path")
    file_paths = args.get("file_paths")
    working_dir = args.get("working_dir")

    res = ledger.restore_snapshot(
        snapshot_id=snapshot_id,
        restore_files=restore_files,
        rewind_conversation=rewind_conversation,
        file_path=file_path,
        file_paths=file_paths,
        working_dir=working_dir,
        dry_run=dry_run,
        confirm=confirm,
    )
    return json.dumps(res, indent=2, default=str)


def handle_branch(
    args: Dict[str, Any],
    *,
    ledger: Optional[TimeMachineLedger] = None,
    **kwargs: Any,
) -> str:
    ledger = ledger or TimeMachineLedger()
    snapshot_id = (args.get("snapshot_id") or "").strip()
    if not snapshot_id:
        return json.dumps({"ok": False, "error": "snapshot_id is required."})

    branch_title = args.get("branch_title")
    restore_files = bool(args.get("restore_files", True))
    working_dir = args.get("working_dir")

    res = ledger.branch_from_snapshot(
        snapshot_id=snapshot_id,
        branch_title=branch_title,
        restore_files=restore_files,
        working_dir=working_dir,
    )
    return json.dumps(res, indent=2, default=str)



def handle_recover(
    args: Dict[str, Any],
    *,
    ledger: Optional[TimeMachineLedger] = None,
    **kwargs: Any,
) -> str:
    ledger = ledger or TimeMachineLedger()
    session_id = args.get("session_id") or kwargs.get("session_id")
    error_summary = args.get("error_summary")
    auto_apply = bool(args.get("auto_apply", False))
    confirm = bool(args.get("confirm", True))
    working_dir = args.get("working_dir")
    verify_command = args.get("verify_command")
    auto_verify = bool(args.get("auto_verify", True))

    dry_run = bool(args.get("dry_run", False))
    if not confirm:
        dry_run = True

    res = ledger.recover(
        session_id=session_id,
        error_summary=error_summary,
        auto_apply=auto_apply,
        confirm=confirm,
        working_dir=working_dir,
        verify_command=verify_command,
        auto_verify=auto_verify,
        dry_run=dry_run,
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
    working_dir = args.get("working_dir")
    limit = int(args.get("limit") or 10)

    res = ledger.search_snapshots(
        query=query,
        session_id=session_id,
        file_path=file_path,
        working_dir=working_dir,
        limit=limit,
    )
    return json.dumps(res, indent=2, default=str)


def register_time_machine_tools(ctx, ledger: Optional[TimeMachineLedger] = None) -> None:
    """Register all 4 Time Machine tools with Hermes PluginContext."""
    ledger = ledger or TimeMachineLedger()

    tools_spec = [
        (
            "time_machine_list_snapshots",
            LIST_SNAPSHOTS_SCHEMA,
            partial(handle_list_snapshots, ledger=ledger),
        ),
        (
            "time_machine_diff",
            DIFF_SCHEMA,
            partial(handle_diff, ledger=ledger),
        ),
        (
            "time_machine_restore",
            RESTORE_SCHEMA,
            partial(handle_restore, ledger=ledger),
        ),
        (
            "time_machine_branch",
            BRANCH_SCHEMA,
            partial(handle_branch, ledger=ledger),
        ),
        (
            "time_machine_recover",
            RECOVER_SCHEMA,
            partial(handle_recover, ledger=ledger),
        ),
        (
            "time_machine_search",
            SEARCH_SCHEMA,
            partial(handle_search, ledger=ledger),
        ),
    ]

    for name, schema, handler in tools_spec:
        ctx.register_tool(
            name=name,
            toolset=TOOLSET_NAME,
            schema=schema,
            handler=handler,
            description=schema["description"],
        )
