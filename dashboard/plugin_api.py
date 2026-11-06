"""
FastAPI backend router for hermes-time-machine.
Mounted automatically by Hermes Dashboard / Desktop Gateway at:
  /api/plugins/hermes-time-machine/
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from ledger import TimeMachineLedger, resolve_hermes_home  # noqa: E402

router = APIRouter()


def _get_ledger() -> TimeMachineLedger:
    return TimeMachineLedger()


def _get_latest_session_id() -> Optional[str]:
    state_db = resolve_hermes_home() / "state.db"
    if not state_db.exists():
        return None
    try:
        with sqlite3.connect(str(state_db), timeout=2.0) as conn:
            row = conn.execute(
                "SELECT id FROM sessions ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            return row[0] if row else None
    except Exception:
        return None


class ManualSnapshotRequest(BaseModel):
    session_id: Optional[str] = None
    working_dir: Optional[str] = None
    reason: Optional[str] = "manual_ui_checkpoint"


class RestoreRequest(BaseModel):
    restore_files: bool = True
    rewind_conversation: bool = False
    file_path: Optional[str] = None
    file_paths: Optional[List[str]] = None
    working_dir: Optional[str] = None


class BranchRequest(BaseModel):
    branch_title: Optional[str] = None
    restore_files: bool = True
    working_dir: Optional[str] = None



class RecoverRequest(BaseModel):
    error_summary: Optional[str] = None
    auto_apply: bool = False
    confirm: bool = True
    session_id: Optional[str] = None
    working_dir: Optional[str] = None

@router.get("/status")
async def get_status(session_id: Optional[str] = Query(default=None)) -> Dict[str, Any]:
    ledger = _get_ledger()
    active_session = session_id or _get_latest_session_id()
    snapshots = ledger.list_snapshots(
        session_id=session_id,
        limit=25,
        include_raw_checkpoints=True,
    )
    latest = snapshots[0] if snapshots else None
    safe_snap = ledger.get_last_safe_snapshot(active_session) if active_session else None
    has_self_heal = bool(latest and (latest.get("self_heal_candidate") or latest.get("status") == "error"))
    storage = ledger.get_storage_stats() if hasattr(ledger, "get_storage_stats") else {}
    return {
        "ok": True,
        "active_session_id": active_session,
        "snapshot_count": len(snapshots),
        "total_snapshots": storage.get("total_snapshots", len(snapshots)),
        "total_sessions": storage.get("total_sessions", 1),
        "self_heal_alerts_count": storage.get("self_heal_alerts_count", 1 if has_self_heal else 0),
        "store_size_bytes": storage.get("store_size_bytes", 0),
        "total_storage_bytes": storage.get("total_storage_bytes", 0),
        "latest_snapshot": latest,
        "last_safe_snapshot": safe_snap,
        "self_heal_available": has_self_heal,
        "recommended_snapshot_id": (latest or {}).get("recommended_snapshot_id") or (safe_snap or {}).get("id"),
        "hermes_home": str(ledger.hermes_home),
    }


@router.get("/sessions")
async def list_recent_sessions(limit: int = Query(default=20, ge=1, le=100)) -> Dict[str, Any]:
    ledger = _get_ledger()
    state_db = ledger.hermes_home / "state.db"
    sessions = []
    if state_db.exists():
        try:
            with sqlite3.connect(str(state_db), timeout=3.0) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT id, source, title, model, parent_session_id,
                           started_at, message_count, tool_call_count, rewind_count
                    FROM sessions
                    ORDER BY started_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                sessions = [dict(r) for r in rows]
        except Exception:
            pass

    return {
        "ok": True,
        "sessions": sessions,
    }


@router.get("/snapshots")
async def list_snapshots(
    session_id: Optional[str] = Query(default=None),
    working_dir: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    include_raw: bool = Query(default=True),
) -> Dict[str, Any]:
    ledger = _get_ledger()
    effective_session = session_id if session_id not in (None, "", "all") else None
    snapshots = ledger.list_snapshots(
        session_id=effective_session,
        working_dir=working_dir,
        limit=limit,
        include_raw_checkpoints=include_raw,
    )
    return {
        "ok": True,
        "session_id": effective_session or _get_latest_session_id(),
        "count": len(snapshots),
        "snapshots": snapshots,
    }


@router.post("/snapshots")
async def create_manual_snapshot(req: ManualSnapshotRequest) -> Dict[str, Any]:
    ledger = _get_ledger()
    sess = req.session_id or _get_latest_session_id() or "manual"
    snap = ledger.record_pre_tool(
        session_id=sess,
        turn_id="manual",
        tool_call_id=None,
        tool_name="write_file",
        args={"reason": req.reason},
        working_dir=req.working_dir,
    )
    if snap:
        snap = ledger.record_post_tool(
            session_id=sess,
            turn_id="manual",
            tool_call_id=None,
            tool_name="write_file",
            status="ok",
            duration_ms=0.0,
        ) or snap
    return {"ok": True, "snapshot": snap}


@router.post("/snapshots/recover")
async def recover_snapshots(req: RecoverRequest) -> Dict[str, Any]:
    ledger = _get_ledger()
    res = ledger.recover(
        session_id=req.session_id or _get_latest_session_id(),
        error_summary=req.error_summary,
        auto_apply=req.auto_apply,
        confirm=req.confirm,
        working_dir=req.working_dir,
    )
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Recovery failed"))
    return res


@router.get("/snapshots/export")
async def export_timeline(session_id: Optional[str] = Query(default=None)) -> Dict[str, Any]:
    ledger = _get_ledger()
    md = ledger.export_timeline_markdown(session_id=session_id)
    return {"ok": True, "markdown": md}


@router.get("/snapshots/{snapshot_id}")
async def get_snapshot_detail(
    snapshot_id: str,
    include_diff: bool = Query(default=True),
    working_dir: Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    ledger = _get_ledger()
    snap = ledger.get_snapshot(snapshot_id)
    diff_res = (
        ledger.get_diff(snapshot_id, working_dir=working_dir)
        if include_diff
        else {"ok": True, "diff": ""}
    )
    if not snap and not diff_res.get("ok"):
        raise HTTPException(status_code=404, detail=f"Snapshot '{snapshot_id}' not found")

    return {
        "ok": True,
        "snapshot": snap or diff_res.get("snapshot"),
        "diff": diff_res.get("diff", ""),
        "files": diff_res.get("files", []),
        "checkpoint_hash": (snap or {}).get("checkpoint_hash") or diff_res.get("checkpoint_hash"),
        "working_dir": (snap or {}).get("working_dir") or diff_res.get("working_dir"),
    }


@router.get("/snapshots/{snapshot_id}/diff")
async def get_snapshot_diff(
    snapshot_id: str,
    compare_to: Optional[str] = Query(default=None),
    working_dir: Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    ledger = _get_ledger()
    res = ledger.get_diff(snapshot_id, compare_to_id=compare_to, working_dir=working_dir)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Diff failed"))
    return res


@router.post("/snapshots/prune")
async def prune_snapshots(
    session_id: Optional[str] = Query(default=None),
    keep_last: int = Query(default=50, ge=5, le=500),
) -> Dict[str, Any]:
    ledger = _get_ledger()
    return ledger.prune_snapshots(session_id=session_id, keep_last=keep_last)


@router.post("/snapshots/{snapshot_id}/restore")
async def restore_snapshot(snapshot_id: str, req: RestoreRequest) -> Dict[str, Any]:
    ledger = _get_ledger()
    res = ledger.restore_snapshot(
        snapshot_id=snapshot_id,
        restore_files=req.restore_files,
        rewind_conversation=req.rewind_conversation,
        file_path=req.file_path,
        file_paths=req.file_paths,
        working_dir=req.working_dir,
    )
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Restore failed"))
    return res


@router.post("/snapshots/{snapshot_id}/branch")
async def branch_snapshot(snapshot_id: str, req: BranchRequest) -> Dict[str, Any]:
    ledger = _get_ledger()
    res = ledger.branch_from_snapshot(
        snapshot_id=snapshot_id,
        branch_title=req.branch_title,
        restore_files=req.restore_files,
        working_dir=req.working_dir,
    )
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Branch failed"))
    return res





@router.post("/snapshots/{snapshot_id}/retry")
async def restore_and_retry(snapshot_id: str, req: RestoreRequest) -> Dict[str, Any]:
    ledger = _get_ledger()
    res = ledger.restore_snapshot(
        snapshot_id=snapshot_id,
        restore_files=req.restore_files,
        rewind_conversation=req.rewind_conversation,
        file_path=req.file_path,
        file_paths=req.file_paths,
        working_dir=req.working_dir,
    )
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("error", "Restore failed"))
    diag = ledger.diagnose_failure(working_dir=req.working_dir)
    return {
        **res,
        "retry_command": diag.get("retry_command") or "pytest",
    }
