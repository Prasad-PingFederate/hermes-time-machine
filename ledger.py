"""
SQLite Ledger + CheckpointManager & SessionDB Bridge for hermes-time-machine.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Mutating tools that warrant an automatic pre-tool filesystem checkpoint
MUTATING_TOOLS = {
    "write_file",
    "patch",
    "edit_file",
    "multi_patch",
    "terminal",
    "bash",
    "execute_code",
}


def resolve_hermes_home() -> Path:
    """Resolve active profile's HERMES_HOME using Hermes's canonical resolver."""
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except ImportError:
        env_home = os.environ.get("HERMES_HOME")
        if env_home:
            return Path(env_home).expanduser().resolve()
        return Path.home() / ".hermes"


def resolve_working_dir(
    args: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    hermes_home: Optional[Path] = None,
) -> str:
    """
    Determine the effective working directory for a tool call.
    1. Check tool args (workdir, working_dir, cwd, or file path parent)
    2. Check session row in state.db or TERMINAL_CWD env var
    3. Fallback to os.getcwd()
    """
    args = args or {}
    for key in ("workdir", "working_dir", "cwd", "directory"):
        val = args.get(key)
        if isinstance(val, str) and val.strip():
            p = Path(val).expanduser().resolve()
            if p.is_dir():
                return str(p)

    for path_key in ("path", "file_path", "target_file", "filepath"):
        val = args.get(path_key)
        if isinstance(val, str) and val.strip():
            p = Path(val).expanduser().resolve()
            parent = p if p.is_dir() else p.parent
            if parent.exists():
                # Walk up to git root if inside a repo, else parent
                cur = parent
                while cur != cur.parent:
                    if (cur / ".git").exists():
                        return str(cur)
                    cur = cur.parent
                return str(parent)

    if session_id:
        home = hermes_home or resolve_hermes_home()
        state_db = home / "state.db"
        if state_db.exists():
            try:
                with sqlite3.connect(str(state_db), timeout=2.0) as conn:
                    cur = conn.execute(
                        "SELECT cwd FROM sessions WHERE id = ?", (session_id,)
                    )
                    row = cur.fetchone()
                    if row and row[0] and Path(row[0]).is_dir():
                        return str(Path(row[0]).resolve())
            except Exception:
                pass

    env_cwd = os.environ.get("TERMINAL_CWD")
    if env_cwd and Path(env_cwd).is_dir():
        return str(Path(env_cwd).resolve())

    return str(Path.cwd().resolve())


class TimeMachineLedger:
    """
    Stores unified snapshots pairing:
      - session_id, turn_id, tool_call_id, tool_name
      - state.db message watermark (message_id_watermark, message_count_watermark)
      - CheckpointManager shadow git commit (checkpoint_hash, post_checkpoint_hash, working_dir)
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.hermes_home = resolve_hermes_home()
        self.db_path = Path(db_path) if db_path else (self.hermes_home / "time_machine.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    turn_id TEXT,
                    tool_call_id TEXT,
                    tool_name TEXT NOT NULL,
                    tool_args_summary TEXT,
                    working_dir TEXT NOT NULL,
                    checkpoint_hash TEXT,
                    post_checkpoint_hash TEXT,
                    reason TEXT,
                    message_id_watermark INTEGER,
                    message_count_watermark INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'pending',
                    duration_ms REAL,
                    files_changed INTEGER DEFAULT 0,
                    insertions INTEGER DEFAULT 0,
                    deletions INTEGER DEFAULT 0,
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tm_session ON snapshots(session_id, created_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tm_call_id ON snapshots(tool_call_id)"
            )
            conn.commit()

    def _get_checkpoint_manager(self):
        """Return an active Hermes CheckpointManager instance if available."""
        try:
            from tools.checkpoint_manager import CheckpointManager
            return CheckpointManager(enabled=True)
        except Exception as exc:
            logger.debug("CheckpointManager import failed: %s", exc)
            return None

    def _project_hash16(self, working_dir: str) -> str:
        abs_dir = str(Path(working_dir).expanduser().resolve())
        return hashlib.sha256(abs_dir.encode("utf-8")).hexdigest()[:16]

    def _read_shadow_ref_tip(self, working_dir: str) -> Optional[str]:
        store_dir = self.hermes_home / "checkpoints" / "store"
        if not store_dir.exists():
            return None
        hash16 = self._project_hash16(working_dir)
        ref_file = store_dir / "refs" / "hermes" / hash16
        if ref_file.exists():
            try:
                return ref_file.read_text(encoding="utf-8").strip()
            except OSError:
                pass
        try:
            res = subprocess.run(
                ["git", f"--git-dir={store_dir}", "rev-parse", f"refs/hermes/{hash16}"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass
        return None

    def ensure_and_get_checkpoint(self, working_dir: str, reason: str) -> Optional[str]:
        abs_dir = str(Path(working_dir).expanduser().resolve())
        mgr = self._get_checkpoint_manager()
        if mgr is not None:
            try:
                if hasattr(mgr, "new_turn"):
                    mgr.new_turn()
                mgr.ensure_checkpoint(abs_dir, reason=reason)
                checkpoints = mgr.list_checkpoints(abs_dir)
                if checkpoints:
                    return checkpoints[0].get("hash")
            except Exception as exc:
                logger.warning("CheckpointManager.ensure_checkpoint failed: %s", exc)

        return self._read_shadow_ref_tip(abs_dir)

    def _get_message_watermark(self, session_id: str) -> tuple[Optional[int], int]:
        state_db = self.hermes_home / "state.db"
        if not state_db.exists() or not session_id:
            return None, 0
        try:
            with sqlite3.connect(str(state_db), timeout=2.0) as conn:
                cur = conn.execute(
                    """
                    SELECT MAX(id), COUNT(*)
                    FROM messages
                    WHERE session_id = ? AND COALESCE(active, 1) = 1
                    """,
                    (session_id,),
                )
                row = cur.fetchone()
                if row:
                    return row[0], int(row[1] or 0)
        except Exception as exc:
            logger.debug("Failed reading message watermark from state.db: %s", exc)
        return None, 0

    def record_pre_tool(
        self,
        *,
        session_id: str,
        turn_id: Optional[str],
        tool_call_id: Optional[str],
        tool_name: str,
        args: Optional[Dict[str, Any]] = None,
        working_dir: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if tool_name.startswith("time_machine_"):
            return None

        wdir = working_dir or resolve_working_dir(args, session_id, self.hermes_home)
        reason = f"pre:{tool_name}"
        checkpoint_hash = None
        if tool_name in MUTATING_TOOLS:
            checkpoint_hash = self.ensure_and_get_checkpoint(wdir, reason=reason)
        else:
            checkpoint_hash = self._read_shadow_ref_tip(wdir)

        msg_id_wm, msg_count_wm = self._get_message_watermark(session_id)
        snap_id = f"snap_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
        args_summary = self._summarize_args(tool_name, args or {})

        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO snapshots (
                    id, session_id, turn_id, tool_call_id, tool_name,
                    tool_args_summary, working_dir, checkpoint_hash,
                    reason, message_id_watermark, message_count_watermark,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'in_flight', ?)
                """,
                (
                    snap_id,
                    session_id or "unknown",
                    turn_id,
                    tool_call_id,
                    tool_name,
                    args_summary,
                    wdir,
                    checkpoint_hash,
                    reason,
                    msg_id_wm,
                    msg_count_wm,
                    now,
                ),
            )
            conn.commit()

        return self.get_snapshot(snap_id)

    def record_post_tool(
        self,
        *,
        session_id: str,
        turn_id: Optional[str],
        tool_call_id: Optional[str],
        tool_name: str,
        args: Optional[Dict[str, Any]] = None,
        status: str = "ok",
        duration_ms: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        if tool_name.startswith("time_machine_"):
            return None

        row = None
        with self._lock, self._connect() as conn:
            if tool_call_id:
                cur = conn.execute(
                    "SELECT * FROM snapshots WHERE tool_call_id = ? ORDER BY created_at DESC LIMIT 1",
                    (tool_call_id,),
                )
                row = cur.fetchone()
            if not row and session_id:
                cur = conn.execute(
                    """
                    SELECT * FROM snapshots
                    WHERE session_id = ? AND tool_name = ? AND status = 'in_flight'
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (session_id, tool_name),
                )
                row = cur.fetchone()

        if not row:
            return None

        snap = dict(row)
        wdir = snap["working_dir"]
        post_hash = snap.get("checkpoint_hash")
        files_changed = 0
        insertions = 0
        deletions = 0

        if tool_name in MUTATING_TOOLS and status == "ok":
            mgr = self._get_checkpoint_manager()
            if mgr is not None:
                try:
                    checkpoints = mgr.list_checkpoints(wdir)
                    if checkpoints:
                        latest = checkpoints[0]
                        post_hash = latest.get("hash") or post_hash
                        files_changed = int(latest.get("files_changed") or 0)
                        insertions = int(latest.get("insertions") or 0)
                        deletions = int(latest.get("deletions") or 0)
                except Exception:
                    pass

        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE snapshots
                SET post_checkpoint_hash = ?,
                    status = ?,
                    duration_ms = ?,
                    files_changed = ?,
                    insertions = ?,
                    deletions = ?
                WHERE id = ?
                """,
                (
                    post_hash,
                    status,
                    duration_ms,
                    files_changed,
                    insertions,
                    deletions,
                    snap["id"],
                ),
            )
            conn.commit()

        return self.get_snapshot(snap["id"])

    def get_snapshot(self, snap_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            cur = conn.execute("SELECT * FROM snapshots WHERE id = ?", (snap_id,))
            row = cur.fetchone()
            return dict(row) if row else None

    def list_for_session(self, session_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM snapshots WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
                (session_id, limit),
            )
            return [dict(r) for r in cur.fetchall()]

    @staticmethod
    def _summarize_args(tool_name: str, args: Dict[str, Any]) -> str:
        if not args:
            return ""
        for k in ("path", "file_path", "target_file", "command", "query"):
            if k in args and isinstance(args[k], str):
                val = args[k].strip().replace("\n", " ")
                return f"{k}={val[:80]}"
        raw = json.dumps(args, ensure_ascii=False, default=str)
        return raw[:120]
