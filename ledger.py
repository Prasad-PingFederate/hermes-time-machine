"""
SQLite Ledger + CheckpointManager & SessionDB Bridge for hermes-time-machine.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import re
import logging
import os
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

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
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except ImportError:
        env_home = os.environ.get("HERMES_HOME")
        if env_home:
            return Path(env_home).expanduser().resolve()
        return Path.home() / ".hermes"



def validate_safe_path(working_dir: str, rel_path: str) -> Path:
    """Validate that rel_path does not escape working_dir (prevents ../ path traversal attacks)."""
    root = Path(working_dir).expanduser().resolve()
    target = (root / rel_path).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"Security Error: path {rel_path!r} escapes workspace root {root}")
    return target


def file_exists_in_commit(
    commit_hash: str,
    rel_path: str,
    working_dir: str,
    store_dir: Optional[Path] = None,
) -> bool:
    """Check if rel_path existed at commit_hash in shadow git store or local git repository."""
    norm_path = rel_path.replace("\\", "/").lstrip("/")
    if store_dir and store_dir.exists():
        try:
            res = subprocess.run(
                ["git", f"--git-dir={store_dir}", "cat-file", "-e", f"{commit_hash}:{norm_path}"],
                capture_output=True,
                timeout=5,
            )
            if res.returncode == 0:
                return True
        except Exception:
            pass

    try:
        res = subprocess.run(
            ["git", "cat-file", "-e", f"{commit_hash}:{norm_path}"],
            cwd=working_dir,
            capture_output=True,
            timeout=5,
        )
        return res.returncode == 0
    except Exception:
        return False


def resolve_working_dir(
    args: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    hermes_home: Optional[Path] = None,
) -> str:
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
                cur = parent
                while cur != cur.parent:
                    if (cur / ".git").exists():
                        return str(cur)
                    cur = cur.parent
                return str(parent)

    if session_id:
        home = hermes_home or resolve_hermes_home()
        tm_db = home / "time_machine.db"
        if tm_db.exists():
            try:
                with sqlite3.connect(str(tm_db), timeout=2.0) as conn:
                    row = conn.execute(
                        "SELECT working_dir FROM snapshots WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
                        (session_id,),
                    ).fetchone()
                    if row and row[0] and Path(row[0]).is_dir():
                        return str(Path(row[0]).resolve())
            except Exception:
                pass

    env_cwd = os.environ.get("TERMINAL_CWD")
    if env_cwd and Path(env_cwd).is_dir():
        return str(Path(env_cwd).resolve())

    return str(Path.cwd().resolve())



def parse_unified_diff(diff_text: str) -> List[Dict[str, Any]]:
    """Parse git unified diff into structured per-file records with +/- line stats."""
    if not diff_text or not diff_text.strip():
        return []

    files = []
    chunks = re.split(r"(?=diff --git )", diff_text)
    for chunk in chunks:
        if not chunk.startswith("diff --git"):
            continue
        lines = chunk.splitlines()
        if not lines:
            continue
        first_line = lines[0]
        m = re.match(r"diff --git a/(.*?) b/(.*)", first_line)
        path = m.group(2) if m else "unknown"

        status = "modified"
        if any(l.startswith("new file mode") for l in lines[:5]):
            status = "added"
        elif any(l.startswith("deleted file mode") for l in lines[:5]):
            status = "deleted"

        additions = 0
        deletions = 0
        chunk_lines = []
        in_hunk = False
        for l in lines:
            if l.startswith("@@"):
                in_hunk = True
            if in_hunk:
                chunk_lines.append(l)
                if l.startswith("+") and not l.startswith("+++"):
                    additions += 1
                elif l.startswith("-") and not l.startswith("---"):
                    deletions += 1

        files.append({
            "path": path,
            "status": status,
            "additions": additions,
            "deletions": deletions,
            "diff_chunk": "\n".join(chunk_lines) if chunk_lines else chunk,
        })
    return files


class TimeMachineLedger:
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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tm_session ON snapshots(session_id, created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tm_call_id ON snapshots(tool_call_id)")
            try:
                conn.execute("ALTER TABLE snapshots ADD COLUMN self_heal_candidate INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute("ALTER TABLE snapshots ADD COLUMN recommended_snapshot_id TEXT")
            except sqlite3.OperationalError:
                pass
            conn.commit()

    def _get_checkpoint_manager(self):
        try:
            from tools.checkpoint_manager import CheckpointManager
            return CheckpointManager(enabled=True)
        except Exception as exc:
            logger.debug("CheckpointManager import failed: %s", exc)
            return None

    def _get_session_db(self):
        try:
            from hermes_state import SessionDB
            return SessionDB(db_path=self.hermes_home / "state.db")
        except Exception as exc:
            logger.debug("SessionDB import failed: %s", exc)
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
        turn_id: Optional[str] = None,
        tool_call_id: Optional[str] = None,
        tool_name: str,
        args: Optional[Dict[str, Any]] = None,
        working_dir: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if tool_name.startswith("time_machine_"):
            return None

        wdir = working_dir or resolve_working_dir(args, session_id, self.hermes_home)
        reason = f"pre:{tool_name}"
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
        turn_id: Optional[str] = None,
        tool_call_id: Optional[str] = None,
        tool_name: str,
        args: Optional[Dict[str, Any]] = None,
        status: str = "ok",
        duration_ms: Optional[float] = None,
        self_heal_candidate: bool = False,
        recommended_snapshot_id: Optional[str] = None,
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
            post_hash = self.ensure_and_get_checkpoint(wdir, reason=f"post:{tool_name}") or post_hash
            mgr = self._get_checkpoint_manager()
            if mgr is not None:
                try:
                    checkpoints = mgr.list_checkpoints(wdir)
                    if checkpoints:
                        latest = checkpoints[0]
                        files_changed = int(latest.get("files_changed") or 0)
                        insertions = int(latest.get("insertions") or 0)
                        deletions = int(latest.get("deletions") or 0)
                except Exception:
                    pass

        if status != "ok" or self_heal_candidate:
            cmd = (args or {}).get("command") or (args or {}).get("cmd") or snap.get("tool_args_summary") or ""
            if cmd and tool_name in {"terminal", "bash", "execute_code", "run_command"}:
                if not hasattr(self, "_last_failed_commands"):
                    self._last_failed_commands = {}
                clean_cmd = str(cmd).replace("command=", "").strip()
                self._last_failed_commands[session_id or "default"] = clean_cmd

        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE snapshots
                SET post_checkpoint_hash = ?,
                    status = ?,
                    duration_ms = ?,
                    files_changed = ?,
                    insertions = ?,
                    deletions = ?,
                    self_heal_candidate = ?,
                    recommended_snapshot_id = ?
                WHERE id = ?
                """,
                (
                    post_hash,
                    status,
                    duration_ms,
                    files_changed,
                    insertions,
                    deletions,
                    1 if self_heal_candidate else 0,
                    recommended_snapshot_id,
                    snap["id"],
                ),
            )
            conn.commit()

        return self.get_snapshot(snap["id"])


    def get_last_safe_snapshot(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                SELECT * FROM snapshots
                WHERE session_id = ? AND status = 'ok' AND checkpoint_hash IS NOT NULL
                ORDER BY created_at DESC LIMIT 1
                """,
                (session_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def get_snapshot(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        if not snapshot_id:
            return None
        with self._lock, self._connect() as conn:
            cur = conn.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,))
            row = cur.fetchone()
            if not row:
                cur = conn.execute(
                    """
                    SELECT * FROM snapshots
                    WHERE id LIKE ? OR checkpoint_hash LIKE ? OR post_checkpoint_hash LIKE ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (f"{snapshot_id}%", f"{snapshot_id}%", f"{snapshot_id}%"),
                )
                row = cur.fetchone()
            return dict(row) if row else None

    def list_snapshots(
        self,
        session_id: Optional[str] = None,
        working_dir: Optional[str] = None,
        limit: int = 50,
        include_raw_checkpoints: bool = True,
    ) -> List[Dict[str, Any]]:
        clauses = []
        params: List[Any] = []
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if working_dir:
            abs_wdir = str(Path(working_dir).expanduser().resolve())
            clauses.append("working_dir = ?")
            params.append(abs_wdir)

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM snapshots {where_sql} ORDER BY created_at DESC LIMIT ?"
        params.append(int(limit))

        with self._lock, self._connect() as conn:
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

        if include_raw_checkpoints:
            target_dir = working_dir or (rows[0]["working_dir"] if rows else None)
            if target_dir:
                known_hashes = {
                    r.get("checkpoint_hash") for r in rows if r.get("checkpoint_hash")
                } | {
                    r.get("post_checkpoint_hash") for r in rows if r.get("post_checkpoint_hash")
                }
                mgr = self._get_checkpoint_manager()
                if mgr is not None:
                    try:
                        for cp in mgr.list_checkpoints(target_dir)[:limit]:
                            cp_hash = cp.get("hash")
                            if cp_hash and cp_hash not in known_hashes:
                                rows.append({
                                    "id": f"cp_{cp.get('short_hash', cp_hash[:8])}",
                                    "session_id": session_id or "shadow-store",
                                    "turn_id": None,
                                    "tool_call_id": None,
                                    "tool_name": "checkpoint",
                                    "tool_args_summary": cp.get("reason", "auto checkpoint"),
                                    "working_dir": str(Path(target_dir).expanduser().resolve()),
                                    "checkpoint_hash": cp_hash,
                                    "post_checkpoint_hash": cp_hash,
                                    "reason": cp.get("reason", "checkpoint"),
                                    "message_id_watermark": None,
                                    "message_count_watermark": 0,
                                    "status": "ok",
                                    "duration_ms": None,
                                    "files_changed": int(cp.get("files_changed") or 0),
                                    "insertions": int(cp.get("insertions") or 0),
                                    "deletions": int(cp.get("deletions") or 0),
                                    "created_at": time.time(),
                                })
                    except Exception:
                        pass

        return rows[:limit]

    def get_diff(
        self,
        snapshot_id: str,
        compare_to_id: Optional[str] = None,
        working_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        snap = self.get_snapshot(snapshot_id)
        target_hash = snap["checkpoint_hash"] if snap else snapshot_id
        wdir = (
            snap["working_dir"]
            if snap and snap.get("working_dir")
            else (working_dir or resolve_working_dir(hermes_home=self.hermes_home))
        )

        if not target_hash:
            return {
                "ok": False,
                "snapshot_id": snapshot_id,
                "working_dir": wdir,
                "error": f"Snapshot '{snapshot_id}' does not have an associated checkpoint hash.",
            }

        other_snap = None
        base_hash = None
        mode = "parent"

        if compare_to_id == "WORKING_TREE":
            mode = "working_tree"
        elif compare_to_id:
            other_snap = self.get_snapshot(compare_to_id)
            base_hash = other_snap["checkpoint_hash"] if other_snap else compare_to_id
            mode = "arbitrary"
        elif snap and snap.get("session_id"):
            # Find previous snapshot in same session
            with self._lock, self._connect() as conn:
                prev_row = conn.execute(
                    """
                    SELECT * FROM snapshots
                    WHERE session_id = ? AND created_at < ? AND checkpoint_hash IS NOT NULL AND checkpoint_hash != ''
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (snap["session_id"], snap["created_at"]),
                ).fetchone()
                if prev_row:
                    prev_dict = dict(prev_row)
                    base_hash = prev_dict.get("checkpoint_hash")
                    mode = "parent_snapshot"

        diff_text = ""
        store_dir = self.hermes_home / "checkpoints" / "store"
        hash16 = self._project_hash16(wdir)
        env = {
            **os.environ,
            "GIT_DIR": str(store_dir),
            "GIT_WORK_TREE": str(Path(wdir).resolve()),
            "GIT_INDEX_FILE": str(store_dir / "indexes" / hash16),
        }

        # Case 1: Live Working Tree Drift (diff target_hash against current uncommitted workspace)
        if mode == "working_tree":
            try:
                res = subprocess.run(
                    ["git", "diff", target_hash],
                    cwd=wdir,
                    env=env if store_dir.exists() else None,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if res.returncode == 0:
                    diff_text = res.stdout
                else:
                    res_loc = subprocess.run(["git", "diff", target_hash], cwd=wdir, capture_output=True, text=True, timeout=15)
                    if res_loc.returncode == 0:
                        diff_text = res_loc.stdout
            except Exception as exc:
                logger.debug("working_tree diff error: %s", exc)

        # Case 2: Arbitrary A <-> B or Parent Snapshot comparison
        elif base_hash and base_hash != target_hash:
            try:
                res = subprocess.run(
                    ["git", "diff", base_hash, target_hash],
                    cwd=wdir,
                    env=env if store_dir.exists() else None,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if res.returncode == 0:
                    diff_text = res.stdout
                else:
                    res_loc = subprocess.run(["git", "diff", base_hash, target_hash], cwd=wdir, capture_output=True, text=True, timeout=15)
                    if res_loc.returncode == 0:
                        diff_text = res_loc.stdout
            except Exception as exc:
                logger.debug("arbitrary diff error: %s", exc)

        # Case 3: Single commit delta (target_hash~1 .. target_hash or CheckpointManager.diff)
        if not diff_text and mode != "working_tree":
            mgr = self._get_checkpoint_manager()
            if mgr is not None:
                try:
                    diff_text = mgr.diff(wdir, target_hash) or ""
                except Exception:
                    pass

            if not diff_text:
                try:
                    res = subprocess.run(
                        ["git", "show", target_hash, "--format="],
                        cwd=wdir,
                        env=env if store_dir.exists() else None,
                        capture_output=True,
                        text=True,
                        timeout=15,
                    )
                    if res.returncode == 0:
                        diff_text = res.stdout
                    else:
                        res_loc = subprocess.run(["git", "show", target_hash, "--format="], cwd=wdir, capture_output=True, text=True, timeout=15)
                        if res_loc.returncode == 0:
                            diff_text = res_loc.stdout
                except Exception as exc:
                    logger.debug("git show fallback error: %s", exc)

        files_structured = parse_unified_diff(diff_text)
        tot_adds = sum(f["additions"] for f in files_structured)
        tot_dels = sum(f["deletions"] for f in files_structured)

        return {
            "ok": True,
            "snapshot_id": snap["id"] if snap else snapshot_id,
            "compare_to_id": compare_to_id or ("WORKING_TREE" if mode == "working_tree" else "parent"),
            "checkpoint_hash": target_hash,
            "base_hash": base_hash,
            "working_dir": wdir,
            "mode": mode,
            "diff": diff_text or "(No filesystem differences)",
            "files": files_structured,
            "total_files": len(files_structured),
            "total_additions": tot_adds,
            "total_deletions": tot_dels,
            "summary": {
                "files_changed": len(files_structured),
                "additions": tot_adds,
                "deletions": tot_dels,
            },
            "snapshot": snap,
            "compare_to_snapshot": other_snap,
        }

    def restore_snapshot(
        self,
        snapshot_id: str,
        *,
        restore_files: bool = True,
        rewind_conversation: bool = False,
        file_path: Optional[str] = None,
        file_paths: Optional[List[str]] = None,
        working_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        snap = self.get_snapshot(snapshot_id)
        commit_hash = snap["checkpoint_hash"] if snap else snapshot_id
        wdir = (
            snap["working_dir"]
            if snap and snap.get("working_dir")
            else (working_dir or resolve_working_dir(hermes_home=self.hermes_home))
        )

        target_files = []
        if file_paths:
            target_files.extend([f.strip() for f in file_paths if f and f.strip()])
        if file_path and file_path.strip() not in target_files:
            target_files.append(file_path.strip())

        # Path Traversal Security Check
        validated_targets = []
        for tf in target_files:
            try:
                abs_target = validate_safe_path(wdir, tf)
                rel_target = str(abs_target.relative_to(Path(wdir).resolve())).replace("\\", "/")
                validated_targets.append((rel_target, abs_target))
            except ValueError as val_err:
                return {
                    "ok": False,
                    "snapshot_id": snapshot_id,
                    "working_dir": wdir,
                    "error": str(val_err),
                }

        result: Dict[str, Any] = {
            "ok": True,
            "snapshot_id": snap["id"] if snap else snapshot_id,
            "checkpoint_hash": commit_hash,
            "working_dir": wdir,
            "files_restored": False,
            "restored_files": [vt[0] for vt in validated_targets] if validated_targets else "all",
            "unlinked_new_files": [],
            "conversation_rewound": False,
            "messages_deactivated": 0,
            "pre_restore_backup_id": None,
        }

        if restore_files:
            if not commit_hash:
                return {
                    **result,
                    "ok": False,
                    "error": f"No checkpoint hash available for snapshot '{snapshot_id}'.",
                }

            # 1. "Rollback of a Rollback" Safety Backup: Capture pre-restore safety snapshot
            try:
                safety_snap = self.record_pre_tool(
                    session_id=snap.get("session_id") if snap else "default",
                    tool_name="pre_restore_safety_backup",
                    args={
                        "target_snapshot_id": snapshot_id,
                        "target_commit": commit_hash,
                        "file_paths": [vt[0] for vt in validated_targets] or None,
                    },
                    working_dir=wdir,
                )
                if safety_snap:
                    result["pre_restore_backup_id"] = safety_snap.get("id")
            except Exception as exc:
                logger.warning("Could not capture pre-restore safety snapshot: %s", exc)

            store_dir = self.hermes_home / "checkpoints" / "store"
            hash16 = self._project_hash16(wdir)
            env = {
                **os.environ,
                "GIT_DIR": str(store_dir),
                "GIT_WORK_TREE": str(Path(wdir).resolve()),
                "GIT_INDEX_FILE": str(store_dir / "indexes" / hash16),
            }

            # 2. Surgical File Restoration (Handles newly created files without git pathspec failure)
            if validated_targets:
                restored_any = False
                unlinked = []
                errors = []

                for rel_tf, abs_tf in validated_targets:
                    existed = file_exists_in_commit(commit_hash, rel_tf, wdir, store_dir)
                    if existed:
                        # Restore file from commit
                        res = subprocess.run(
                            ["git", "checkout", commit_hash, "--", rel_tf],
                            cwd=wdir,
                            env=env if store_dir.exists() else None,
                            capture_output=True,
                            text=True,
                            timeout=15,
                        )
                        if res.returncode == 0:
                            restored_any = True
                        else:
                            res_loc = subprocess.run(["git", "checkout", commit_hash, "--", rel_tf], cwd=wdir, capture_output=True, text=True, timeout=15)
                            if res_loc.returncode == 0:
                                restored_any = True
                            else:
                                errors.append(f"{rel_tf}: {res.stderr or res_loc.stderr or 'checkout failed'}")
                    else:
                        # Newly created file that did not exist in commit_hash: unlink from disk!
                        if abs_tf.exists():
                            try:
                                if abs_tf.is_file():
                                    abs_tf.unlink()
                                elif abs_tf.is_dir():
                                    shutil.rmtree(abs_tf, ignore_errors=True)
                                unlinked.append(rel_tf)
                                restored_any = True
                            except OSError as rm_err:
                                errors.append(f"Could not remove newly created {rel_tf}: {rm_err}")

                result["files_restored"] = restored_any
                result["unlinked_new_files"] = unlinked
                if not restored_any and errors:
                    result["ok"] = False
                    result["error"] = "; ".join(errors)
                    return result

            else:
                # Full Workspace Restoration
                mgr = self._get_checkpoint_manager()
                if mgr is not None:
                    restore_res = mgr.restore(wdir, commit_hash)
                    result["checkpoint_restore_result"] = restore_res
                    if isinstance(restore_res, dict) and not restore_res.get("ok", True):
                        result["ok"] = False
                        result["error"] = (
                            restore_res.get("error")
                            or restore_res.get("reason")
                            or "CheckpointManager.restore returned ok=False"
                        )
                        return result
                    if restore_res is False:
                        result["ok"] = False
                        result["error"] = "CheckpointManager.restore returned False"
                        return result
                    result["files_restored"] = True
                else:
                    res = subprocess.run(
                        ["git", "checkout", "-f", commit_hash],
                        cwd=wdir,
                        env=env if store_dir.exists() else None,
                        capture_output=True,
                        text=True,
                        timeout=15,
                    )
                    if res.returncode != 0:
                        res_loc = subprocess.run(["git", "checkout", "-f", commit_hash], cwd=wdir, capture_output=True, text=True, timeout=15)
                        if res_loc.returncode != 0:
                            result["ok"] = False
                            result["error"] = res.stderr or res_loc.stderr or "git checkout failed"
                            return result
                    result["files_restored"] = True

                # Clean up newly-created files recorded in subsequent snapshots of this session
                if snap and snap.get("session_id"):
                    with self._lock, self._connect() as conn:
                        later_rows = conn.execute(
                            "SELECT tool_args_summary FROM snapshots WHERE session_id = ? AND created_at > ?",
                            (snap["session_id"], snap["created_at"]),
                        ).fetchall()
                        for r in later_rows:
                            summary = r[0] or ""
                            # Simple regex matching file names in tool args
                            for m_path in re.findall(r'[\'\"]([\w./\\-]+\.[a-zA-Z0-9]+)[\'\"]', summary):
                                try:
                                    abs_file = (Path(wdir) / m_path).resolve()
                                    if abs_file.exists() and abs_file.is_file():
                                        rel_f = str(abs_file.relative_to(Path(wdir).resolve())).replace("\\", "/")
                                        if not file_exists_in_commit(commit_hash, rel_f, wdir, store_dir):
                                            abs_file.unlink()
                                            result["unlinked_new_files"].append(rel_f)
                                except Exception:
                                    pass

        # Conversation rewind: Only if explicitly requested
        if rewind_conversation and snap and snap.get("session_id"):
            session_id = snap["session_id"]
            watermark_id = snap.get("message_id_watermark")
            deactivated = self._rewind_session_to_watermark(session_id, watermark_id)
            result["conversation_rewound"] = True
            result["messages_deactivated"] = deactivated
            result["session_id"] = session_id
            result["message_id_watermark"] = watermark_id

        return result

    def _rewind_session_to_watermark(
        self, session_id: str, watermark_id: Optional[int]
    ) -> int:
        if watermark_id is None:
            return 0

        state_db_path = self.hermes_home / "state.db"
        if not state_db_path.exists():
            return 0

        before_active = 0
        first_user_after_id: Optional[int] = None

        try:
            with sqlite3.connect(str(state_db_path), timeout=5.0) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id = ? AND COALESCE(active, 1) = 1",
                    (session_id,),
                ).fetchone()
                before_active = int(row[0] or 0) if row else 0

                user_row = conn.execute(
                    """
                    SELECT id FROM messages
                    WHERE session_id = ? AND id > ? AND role = 'user' AND COALESCE(active, 1) = 1
                    ORDER BY id ASC LIMIT 1
                    """,
                    (session_id, watermark_id),
                ).fetchone()
                if user_row:
                    first_user_after_id = int(user_row[0])
        except Exception as exc:
            logger.error("Failed reading state.db before rewind: %s", exc)
            return 0

        rewound_via_session_db = False
        if first_user_after_id is not None:
            db = self._get_session_db()
            if db is not None and hasattr(db, "rewind_to_message"):
                try:
                    rewound_via_session_db = bool(
                        db.rewind_to_message(session_id, first_user_after_id)
                    )
                except Exception as exc:
                    logger.debug("SessionDB.rewind_to_message fallback: %s", exc)

        try:
            with sqlite3.connect(str(state_db_path), timeout=5.0) as conn:
                conn.execute(
                    """
                    UPDATE messages
                    SET active = 0
                    WHERE session_id = ? AND id > ? AND COALESCE(active, 1) = 1
                    """,
                    (session_id, watermark_id),
                )
                row = conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id = ? AND COALESCE(active, 1) = 1",
                    (session_id,),
                ).fetchone()
                after_active = int(row[0] or 0) if row else 0
                deactivated = max(0, before_active - after_active)

                conn.execute(
                    """
                    UPDATE sessions
                    SET message_count = ?,
                        rewind_count = COALESCE(rewind_count, 0) + ?
                    WHERE id = ?
                    """,
                    (
                        after_active,
                        1 if (deactivated > 0 and not rewound_via_session_db) else 0,
                        session_id,
                    ),
                )
                conn.commit()
                return deactivated
        except Exception as exc:
            logger.error(
                "Failed finalizing rewind for session %s to watermark %s: %s",
                session_id,
                watermark_id,
                exc,
            )
            return 0

    def branch_from_snapshot(
        self,
        snapshot_id: str,
        *,
        branch_title: Optional[str] = None,
        restore_files: bool = True,
        working_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        snap = self.get_snapshot(snapshot_id)
        if not snap:
            return {
                "ok": False,
                "error": f"Snapshot '{snapshot_id}' not found in Time Machine ledger.",
            }

        parent_session_id = snap["session_id"]
        watermark_id = snap.get("message_id_watermark")
        commit_hash = snap.get("checkpoint_hash")
        wdir = snap.get("working_dir") or working_dir or resolve_working_dir(hermes_home=self.hermes_home)

        new_session_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        db = self._get_session_db()
        state_db_path = self.hermes_home / "state.db"

        parent_meta: Dict[str, Any] = {}
        copied_messages = 0

        if state_db_path.exists():
            try:
                with sqlite3.connect(str(state_db_path), timeout=5.0) as conn:
                    conn.row_factory = sqlite3.Row
                    parent_row = conn.execute(
                        "SELECT * FROM sessions WHERE id = ?", (parent_session_id,)
                    ).fetchone()
                    if parent_row:
                        parent_meta = dict(parent_row)
            except Exception as exc:
                logger.warning("Could not read parent session %s: %s", parent_session_id, exc)

        title = branch_title or (
            f"{parent_meta.get('title') or parent_session_id} [branch @{snap['id'][:12]}]"
        )

        created_via_db = False
        if db is not None and hasattr(db, "create_session"):
            try:
                db.create_session(
                    session_id=new_session_id,
                    source=parent_meta.get("source") or "cli",
                    user_id=parent_meta.get("user_id"),
                    model=parent_meta.get("model"),
                    system_prompt=parent_meta.get("system_prompt"),
                    parent_session_id=parent_session_id,
                    title=title,
                )
                created_via_db = True
            except TypeError:
                try:
                    db.create_session(
                        session_id=new_session_id,
                        source=parent_meta.get("source") or "cli",
                        model=parent_meta.get("model"),
                        parent_session_id=parent_session_id,
                    )
                    created_via_db = True
                except Exception as exc:
                    logger.debug("SessionDB.create_session fallback: %s", exc)

        if state_db_path.exists():
            with sqlite3.connect(str(state_db_path), timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                if not created_via_db:
                    conn.execute(
                        """
                        INSERT INTO sessions (
                            id, source, user_id, model, system_prompt,
                            parent_session_id, title, started_at, message_count
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                        """,
                        (
                            new_session_id,
                            parent_meta.get("source") or "cli",
                            parent_meta.get("user_id"),
                            parent_meta.get("model"),
                            parent_meta.get("system_prompt"),
                            parent_session_id,
                            title,
                            time.time(),
                        ),
                    )
                else:
                    try:
                        conn.execute(
                            "UPDATE sessions SET title = ? WHERE id = ?",
                            (title, new_session_id),
                        )
                    except Exception:
                        pass

                if watermark_id is not None:
                    msg_rows = conn.execute(
                        """
                        SELECT role, content, tool_call_id, tool_calls, name,
                               timestamp, token_count, finish_reason, reasoning,
                               reasoning_details, codex_reasoning_items
                        FROM messages
                        WHERE session_id = ? AND COALESCE(active, 1) = 1 AND id <= ?
                        ORDER BY id ASC
                        """,
                        (parent_session_id, watermark_id),
                    ).fetchall()
                else:
                    msg_rows = conn.execute(
                        """
                        SELECT role, content, tool_call_id, tool_calls, name,
                               timestamp, token_count, finish_reason, reasoning,
                               reasoning_details, codex_reasoning_items
                        FROM messages
                        WHERE session_id = ? AND COALESCE(active, 1) = 1
                        ORDER BY id ASC
                        """,
                        (parent_session_id,),
                    ).fetchall()

                for m in msg_rows:
                    conn.execute(
                        """
                        INSERT INTO messages (
                            session_id, role, content, tool_call_id, tool_calls,
                            name, timestamp, token_count, finish_reason,
                            reasoning, reasoning_details, codex_reasoning_items, active
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                        """,
                        (
                            new_session_id,
                            m["role"],
                            m["content"],
                            m["tool_call_id"],
                            m["tool_calls"],
                            m["name"],
                            m["timestamp"] or time.time(),
                            m["token_count"],
                            m["finish_reason"],
                            m["reasoning"],
                            m["reasoning_details"],
                            m["codex_reasoning_items"],
                        ),
                    )
                    copied_messages += 1

                conn.execute(
                    "UPDATE sessions SET message_count = ? WHERE id = ?",
                    (copied_messages, new_session_id),
                )
                conn.commit()

        files_restored = False
        restore_details = None
        if restore_files and commit_hash:
            mgr = self._get_checkpoint_manager()
            if mgr is not None:
                restore_details = mgr.restore(wdir, commit_hash)
                files_restored = bool(
                    restore_details.get("ok", True)
                    if isinstance(restore_details, dict)
                    else True
                )

        return {
            "ok": True,
            "snapshot_id": snap["id"],
            "parent_session_id": parent_session_id,
            "new_session_id": new_session_id,
            "title": title,
            "copied_messages": copied_messages,
            "message_id_watermark": watermark_id,
            "checkpoint_hash": commit_hash,
            "working_dir": wdir,
            "files_restored": files_restored,
            "checkpoint_restore_result": restore_details,
        }

    @staticmethod
    def _summarize_args(tool_name: str, args: Dict[str, Any]) -> str:
        if not args:
            return ""
        for k in ("path", "file_path", "target_file", "command", "query"):
            if k in args and isinstance(args[k], str):
                val = args[k].strip().replace("\n", " ")
                return f"{k}={val[:80]}"
        raw = json.dumps(args, ensure_ascii=False, default=str)
        return raw[:100]


    def prune_snapshots(self, session_id: Optional[str] = None, keep_last: int = 50) -> Dict[str, Any]:
        """Keep most recent keep_last snapshots per session, preserving baselines and safety backups."""
        with self._lock, self._connect() as conn:
            query = "SELECT id, tool_name, recommended_snapshot_id FROM snapshots"
            params = []
            if session_id:
                query += " WHERE session_id = ?"
                params.append(session_id)
            query += " ORDER BY created_at DESC"

            rows = [dict(r) for r in conn.execute(query, tuple(params)).fetchall()]
            total_before = len(rows)

            keep_ids = set()
            # 1. Keep the most recent keep_last
            for r in rows[:keep_last]:
                keep_ids.add(r["id"])

            # 2. Always keep protected snapshots
            for r in rows:
                if r.get("tool_name") in ("session_start", "pre_restore_safety_backup"):
                    keep_ids.add(r["id"])
                if r.get("recommended_snapshot_id"):
                    keep_ids.add(r["id"])
                    keep_ids.add(r["recommended_snapshot_id"])

            delete_ids = [r["id"] for r in rows if r["id"] not in keep_ids]
            if delete_ids:
                placeholders = ",".join("?" for _ in delete_ids)
                conn.execute(f"DELETE FROM snapshots WHERE id IN ({placeholders})", tuple(delete_ids))
                conn.commit()

            return {
                "ok": True,
                "session_id": session_id,
                "keep_last": keep_last,
                "total_before": total_before,
                "pruned_count": len(delete_ids),
                "retained_count": len(keep_ids),
            }

    def get_storage_stats(self) -> Dict[str, Any]:
        """Return total snapshots, sessions, self-heal alerts, and estimated store size."""
        total_snaps = 0
        total_sess = 0
        heal_alerts = 0
        db_size = 0

        if self.db_path.exists():
            db_size = self.db_path.stat().st_size

        store_dir = self.hermes_home / "checkpoints" / "store"
        store_size = 0
        if store_dir.exists():
            for f in store_dir.rglob("*"):
                if f.is_file():
                    store_size += f.stat().st_size

        with self._lock, self._connect() as conn:
            try:
                row = conn.execute("SELECT COUNT(*), COUNT(DISTINCT session_id) FROM snapshots").fetchone()
                if row:
                    total_snaps = int(row[0] or 0)
                    total_sess = int(row[1] or 0)
                heal_row = conn.execute("SELECT COUNT(*) FROM snapshots WHERE self_heal_candidate = 1").fetchone()
                if heal_row:
                    heal_alerts = int(heal_row[0] or 0)
            except Exception:
                pass

        return {
            "total_snapshots": total_snaps,
            "total_sessions": total_sess,
            "self_heal_alerts_count": heal_alerts,
            "db_size_bytes": db_size,
            "store_size_bytes": store_size,
            "total_storage_bytes": db_size + store_size,
        }

    def diagnose_failure(
        self,
        session_id: Optional[str] = None,
        error_text: Optional[str] = None,
        working_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Diagnose failure root-cause by correlating test/compiler error output with
        recently modified files and checkpoints. Returns culprit files, recommended
        safe snapshot, confidence score, and suggested action.
        """
        sess = session_id or "default"
        snaps = self.list_snapshots(session_id=sess, working_dir=working_dir, limit=15)

        failed_snap = None
        candidate_safe_snap = None
        last_failed_command = ""

        for s in snaps:
            if s.get("self_heal_candidate") or s.get("status") == "error":
                if not failed_snap:
                    failed_snap = s
                    if s.get("tool_args_summary"):
                        last_failed_command = s.get("tool_args_summary")
            elif s.get("tool_name") in MUTATING_TOOLS and s.get("status") == "ok":
                if not candidate_safe_snap:
                    candidate_safe_snap = s

        if failed_snap and failed_snap.get("recommended_snapshot_id"):
            rec_id = failed_snap.get("recommended_snapshot_id")
            found_snap = self.get_snapshot(rec_id)
            if found_snap:
                candidate_safe_snap = found_snap

        if not candidate_safe_snap:
            candidate_safe_snap = self.get_last_safe_snapshot(sess)

        err_msg = error_text or (failed_snap.get("tool_args_summary") if failed_snap else "") or ""

        culprits: List[Dict[str, Any]] = []
        wdir = (
            (candidate_safe_snap or {}).get("working_dir")
            or working_dir
            or resolve_working_dir(hermes_home=self.hermes_home)
        )

        if candidate_safe_snap:
            diff_res = self.get_diff(candidate_safe_snap["id"], compare_to_id="WORKING_TREE", working_dir=wdir)
            files = diff_res.get("files", [])
            for f in files:
                fpath = f.get("path")
                if not fpath:
                    continue
                basename = Path(fpath).name
                in_traceback = (basename in err_msg) or (fpath in err_msg)
                confidence = 0.95 if in_traceback else 0.80
                reason = (
                    f"Referenced in failure traceback and modified since safe checkpoint {candidate_safe_snap['id'][:12]}"
                    if in_traceback
                    else f"Modified after safe checkpoint {candidate_safe_snap['id'][:12]}"
                )
                culprits.append({
                    "path": fpath,
                    "status": f.get("status", "modified"),
                    "confidence": confidence,
                    "in_traceback": in_traceback,
                    "reason": reason,
                })

        # Also detect newly created untracked files via git status
        try:
            res_status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=wdir,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res_status.returncode == 0:
                for line in res_status.stdout.splitlines():
                    if line.startswith("??"):
                        untracked_rel = line[3:].strip()
                        # Ignore SQLite DB, cache, or hermes internal state files
                        if untracked_rel.endswith((".db", ".db-shm", ".db-wal", ".pyc")) or ".hermes" in untracked_rel:
                            continue
                        if untracked_rel and not any(c["path"] == untracked_rel for c in culprits):
                            culprits.append({
                                "path": untracked_rel,
                                "status": "untracked",
                                "confidence": 0.88,
                                "in_traceback": untracked_rel in err_msg,
                                "reason": "Newly created untracked file after safe checkpoint",
                            })
        except Exception:
            pass

        culprits.sort(key=lambda x: x["confidence"], reverse=True)
        # Prioritize files in traceback + untracked files created during the failed turn
        traceback_or_new = [c for c in culprits if c.get("in_traceback") or c.get("status") == "untracked"]
        if traceback_or_new:
            culprit_paths = [c["path"] for c in traceback_or_new]
        else:
            culprit_paths = [c["path"] for c in culprits]
        overall_confidence = "high" if any(c["confidence"] >= 0.9 for c in culprits) else ("medium" if culprits else "low")
        rec_id = candidate_safe_snap.get("id") if candidate_safe_snap else None

        explanation = (
            f"Detected {len(culprits)} culprit file(s) [{', '.join(culprit_paths)}] edited prior to failure. "
            f"Recommended safe checkpoint: {rec_id}."
            if culprit_paths and rec_id
            else "No specific modified files detected prior to command failure."
        )

        return {
            "ok": True,
            "session_id": sess,
            "working_dir": wdir,
            "failed_snapshot_id": (failed_snap or {}).get("id"),
            "recommended_snapshot_id": rec_id,
            "culprits": culprits,
            "culprit_files": culprit_paths,
            "confidence": overall_confidence,
            "confidence_score": max([c["confidence"] for c in culprits], default=0.5),
            "explanation": explanation,
            "retry_command": last_failed_command,
            "suggested_action": f"time_machine_recover(auto_apply=true, confirm=true)" if culprit_paths else "time_machine_restore(confirm=true)",
        }

    def recover(
        self,
        session_id: Optional[str] = None,
        error_summary: Optional[str] = None,
        auto_apply: bool = False,
        confirm: bool = True,
        working_dir: Optional[str] = None,
        verify_command: Optional[str] = None,
        auto_verify: bool = True,
    ) -> Dict[str, Any]:
        """
        Autonomous self-healing recovery: diagnoses root-cause culprits, optionally
        auto-applies surgical rollback with safety backup, and automatically re-verifies
        test suites in a single atomic tool call.
        """
        diag = self.diagnose_failure(
            session_id=session_id,
            error_text=error_summary,
            working_dir=working_dir,
        )

        result = {
            "ok": True,
            "diagnosis": diag,
            "auto_applied": False,
            "restored_files": [],
            "pre_restore_backup_id": None,
            "retry_command": diag.get("retry_command"),
            "verification": None,
        }

        if not auto_apply:
            result["message"] = (
                f"Diagnostic complete. {diag['explanation']} "
                f"Call with auto_apply=true and confirm=true to automatically revert."
            )
            return result

        if not confirm:
            return {
                **result,
                "ok": False,
                "error": "Confirmation guard required: set confirm=true to execute autonomous recovery.",
            }

        rec_id = diag.get("recommended_snapshot_id")
        if not rec_id:
            return {
                **result,
                "ok": False,
                "error": "Cannot auto-recover: no valid safe snapshot identified.",
            }

        culprit_files = diag.get("culprit_files")
        restore_res = self.restore_snapshot(
            snapshot_id=rec_id,
            file_paths=culprit_files if culprit_files else None,
            working_dir=diag.get("working_dir") or working_dir,
            restore_files=True,
            rewind_conversation=False,
        )

        if not restore_res.get("ok"):
            return {
                **result,
                "ok": False,
                "error": f"Recovery restoration failed: {restore_res.get('error')}",
                "restore_details": restore_res,
            }

        result["auto_applied"] = True
        result["restored_files"] = restore_res.get("restored_files")
        result["unlinked_new_files"] = restore_res.get("unlinked_new_files")
        result["pre_restore_backup_id"] = restore_res.get("pre_restore_backup_id")

        # Auto-Verification
        verification_info = None
        wdir_target = diag.get("working_dir") or working_dir or resolve_working_dir(hermes_home=self.hermes_home)
        cmd_candidate = (
            verify_command
            or diag.get("retry_command")
            or getattr(self, "_last_failed_commands", {}).get(session_id or "default")
        )
        if cmd_candidate and cmd_candidate.startswith("command="):
            cmd_candidate = cmd_candidate.split("=", 1)[1].strip()

        if auto_verify and cmd_candidate:
            try:
                v_res = subprocess.run(
                    cmd_candidate,
                    shell=True,
                    cwd=wdir_target,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                passed = (v_res.returncode == 0)
                out_snippet = (v_res.stdout.strip() or v_res.stderr.strip())
                lines = out_snippet.splitlines()
                summary = "\n".join(lines[-5:]) if len(lines) > 5 else out_snippet[:300]
                verification_info = {
                    "command": cmd_candidate,
                    "exit_code": v_res.returncode,
                    "passed": passed,
                    "output_summary": summary,
                }
            except subprocess.TimeoutExpired:
                verification_info = {
                    "command": cmd_candidate,
                    "exit_code": -1,
                    "passed": False,
                    "output_summary": "Verification command timed out after 15s.",
                }
            except Exception as v_err:
                verification_info = {
                    "command": cmd_candidate,
                    "exit_code": -1,
                    "passed": False,
                    "output_summary": f"Verification error: {v_err}",
                }

        result["verification"] = verification_info
        v_msg = ""
        if verification_info:
            v_msg = f" Verification '{verification_info['command']}' {'PASSED ✔' if verification_info['passed'] else 'FAILED ❌'}."

        result["message"] = (
            f"Successfully auto-recovered! Reverted {restore_res.get('restored_files')} "
            f"to safe snapshot {rec_id}. Safety snapshot {restore_res.get('pre_restore_backup_id')} captured.{v_msg}"
        )
        return result

    def export_timeline_markdown(self, session_id: Optional[str] = None) -> str:
        """Export session Time Machine history as structured Markdown report."""
        sess = session_id or "default"
        snaps = self.list_snapshots(session_id=session_id, limit=200)
        stats = self.get_storage_stats()

        lines = [
            "# ⏱️ Hermes Time Machine Timeline Report",
            f"**Session:** `{sess}` | **Exported At:** `{datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}`",
            f"**Total Snapshots:** {len(snaps)} | **Store Size:** {round(stats.get('total_storage_bytes', 0) / 1024, 1)} KB\n",
            "## Snapshots History\n",
            "| Step | Timestamp | Tool | Checkpoint | Status | Changes | Details |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
        ]

        for idx, s in enumerate(reversed(snaps), 1):
            ts = datetime.fromtimestamp(s['created_at']).strftime('%H:%M:%S') if s.get('created_at') else '-'
            tool = s.get('tool_name', 'unknown')
            sha = (s.get('checkpoint_hash') or '')[:8] or 'no-git'
            st = '✅ ok' if s.get('status') == 'ok' else '❌ error'
            if s.get('self_heal_candidate'):
                st = '⚠️ heal-candidate'
            changes = f"+{s.get('insertions', 0)} / -{s.get('deletions', 0)}"
            summary = (s.get('tool_args_summary') or s.get('reason') or '').replace('\n', ' ')[:50]
            lines.append(f"| {idx} | {ts} | `{tool}` | `{sha}` | {st} | {changes} | {summary} |")

        return "\n".join(lines)

    def search_snapshots(
        self,
        query: str,
        session_id: Optional[str] = None,
        file_path: Optional[str] = None,
        limit: int = 20,
        working_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Search historical snapshot diffs, modified files, and tool arguments to find
        which turn introduced, modified, or removed a specific symbol, line of code, or file.
        """
        q = (query or "").strip()
        if not q:
            return {"ok": False, "error": "Query string is required."}

        snaps = self.list_snapshots(session_id=session_id, limit=100, include_raw_checkpoints=False)
        matches: List[Dict[str, Any]] = []
        q_lower = q.lower()

        for s in snaps:
            snap_id = s["id"]
            wdir = s.get("working_dir") or working_dir or resolve_working_dir(hermes_home=self.hermes_home)
            diff_res = self.get_diff(snap_id, working_dir=wdir)
            files_structured = diff_res.get("files", [])

            matching_lines: List[Dict[str, Any]] = []
            matched_files: List[str] = []

            for f in files_structured:
                fpath = f.get("path") or ""
                if file_path and file_path not in fpath:
                    continue

                chunk = f.get("diff_chunk") or ""
                for line in chunk.splitlines():
                    if (line.startswith("+") or line.startswith("-")) and not line.startswith("+++") and not line.startswith("---"):
                        if q_lower in line.lower():
                            if fpath not in matched_files:
                                matched_files.append(fpath)
                            matching_lines.append({
                                "file": fpath,
                                "type": "addition" if line.startswith("+") else "deletion",
                                "line": line[:160],
                            })

            args_summary = s.get("tool_args_summary") or s.get("reason") or ""
            args_matched = q_lower in args_summary.lower()

            if matching_lines or args_matched:
                matches.append({
                    "snapshot_id": snap_id,
                    "turn_id": s.get("turn_id"),
                    "created_at": s.get("created_at"),
                    "tool_name": s.get("tool_name"),
                    "checkpoint_hash": s.get("checkpoint_hash"),
                    "matched_files": matched_files,
                    "matching_lines": matching_lines[:10],
                    "total_match_lines": len(matching_lines),
                    "tool_args_summary": args_summary,
                })
                if len(matches) >= limit:
                    break

        return {
            "ok": True,
            "query": q,
            "session_id": session_id,
            "file_path": file_path,
            "total_matches": len(matches),
            "matches": matches,
        }
