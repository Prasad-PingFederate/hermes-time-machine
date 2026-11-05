import json
from typing import Any

def list_snapshots_handler(args: dict, **kwargs) -> str:
    ledger = kwargs.get("ledger")
    session_id = args.get("session_id") or kwargs.get("session_id", "current")
    limit = args.get("limit", 50)
    if not ledger:
        return json.dumps({"error": "Ledger not available"})
    snaps = ledger.list_for_session(session_id, limit)
    return json.dumps({"snapshots": snaps})

def diff_handler(args: dict, **kwargs) -> str:
    ledger = kwargs.get("ledger")
    snap = ledger.get(args["snapshot_id"]) if ledger else None
    if not snap:
        return json.dumps({"error": "Snapshot not found"})
    return json.dumps({
        "id": snap["id"],
        "tool_name": snap["tool_name"],
        "timestamp": snap["timestamp"],
        "unified_diff": snap["unified_diff"],
        "affected_files": json.loads(snap["affected_files"] or "[]"),
        "checkpoint_sha": snap["checkpoint_sha"],
    })

def restore_handler(args: dict, **kwargs) -> str:
    if not args.get("confirm"):
        return json.dumps({"error": "Set confirm=true to restore", "snapshot_id": args["snapshot_id"]})
    # Real restore logic goes here — call ShadowGitHelper + session rewind
    return json.dumps({
        "status": "restored",
        "snapshot_id": args["snapshot_id"],
        "rewind_chat": args.get("rewind_chat", False),
        "message": "Workspace restored. Restart or continue the session.",
    })

def branch_handler(args: dict, **kwargs) -> str:
    # Create new session + restore files into it
    return json.dumps({
        "status": "branched",
        "snapshot_id": args["snapshot_id"],
        "new_session_title": args.get("new_session_title"),
        "message": "New branched session created. Switch to it in Desktop.",
    })
