"""Backend routes for the Desktop Time Machine UI."""

from fastapi import APIRouter, Query

router = APIRouter()

# These will be injected by the plugin at runtime
_ledger = None

def set_ledger(ledger):
    global _ledger
    _ledger = ledger

@router.get("/snapshots")
def list_snapshots(session_id: str = Query(...), limit: int = 100):
    if not _ledger:
        return {"error": "Ledger not initialized"}
    return {"snapshots": _ledger.list_for_session(session_id, limit)}

@router.get("/snapshots/{snapshot_id}")
def get_snapshot(snapshot_id: int):
    if not _ledger:
        return {"error": "Ledger not initialized"}
    snap = _ledger.get(snapshot_id)
    return snap or {"error": "Not found"}

@router.post("/restore")
def restore(snapshot_id: int, rewind_chat: bool = False):
    # Call into the same restore logic used by the tool
    return {"status": "queued", "snapshot_id": snapshot_id, "rewind_chat": rewind_chat}
