"""Time Machine ledger: links every tool call to a checkpoint or lightweight snapshot."""

import json
import os
import sqlite3
import subprocess
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

LEDGER_DB = "time_machine.db"
CHECKPOINT_STORE = Path.home() / ".hermes" / "checkpoints" / "store"

class TimeMachineLedger:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / LEDGER_DB
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    turn_id INTEGER,
                    tool_call_id TEXT,
                    tool_name TEXT,
                    timestamp TEXT NOT NULL,
                    working_dir TEXT,
                    checkpoint_sha TEXT,
                    local_tree_sha TEXT,
                    affected_files TEXT,
                    unified_diff TEXT,
                    command_output TEXT,
                    parent_snapshot_id INTEGER,
                    metadata TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_session ON snapshots(session_id, timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sha ON snapshots(checkpoint_sha)")

    def record(
        self,
        session_id: str,
        tool_name: str,
        working_dir: str,
        affected_files: List[str],
        unified_diff: str = "",
        command_output: str = "",
        turn_id: Optional[int] = None,
        tool_call_id: Optional[str] = None,
        checkpoint_sha: Optional[str] = None,
        parent_snapshot_id: Optional[int] = None,
        metadata: Optional[dict] = None,
    ) -> int:
        ts = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO snapshots
                (session_id, turn_id, tool_call_id, tool_name, timestamp, working_dir,
                 checkpoint_sha, affected_files, unified_diff, command_output,
                 parent_snapshot_id, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id, turn_id, tool_call_id, tool_name, ts, working_dir,
                    checkpoint_sha, json.dumps(affected_files), unified_diff, command_output,
                    parent_snapshot_id, json.dumps(metadata or {}),
                ),
            )
            return cur.lastrowid

    def list_for_session(self, session_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM snapshots WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def get(self, snapshot_id: int) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
            return dict(row) if row else None


class ShadowGitHelper:
    """Lightweight helper that prefers Hermes shadow store, falls back to local tree snapshots."""

    @staticmethod
    def get_project_hash(working_dir: str) -> str:
        abs_path = str(Path(working_dir).resolve())
        return hashlib.sha256(abs_path.encode()).hexdigest()[:16]

    @staticmethod
    def latest_checkpoint_sha(working_dir: str) -> Optional[str]:
        try:
            project_hash = ShadowGitHelper.get_project_hash(working_dir)
            ref = CHECKPOINT_STORE / "refs" / "hermes" / project_hash
            if ref.exists():
                return ref.read_text().strip()
        except Exception:
            pass
        return None

    @staticmethod
    def create_lightweight_tree(working_dir: str, snapshot_dir: Path) -> Optional[str]:
        try:
            snapshot_dir.mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()
            env["GIT_DIR"] = str(snapshot_dir)
            env["GIT_WORK_TREE"] = working_dir
            subprocess.run(["git", "init", "--bare"], cwd=snapshot_dir, env=env, check=True, capture_output=True)
            subprocess.run(["git", "add", "-A"], cwd=working_dir, env=env, check=True, capture_output=True)
            result = subprocess.run(
                ["git", "write-tree"],
                cwd=working_dir,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip()
        except Exception:
            return None

    @staticmethod
    def restore_from_sha(working_dir: str, sha: str, use_hermes_store: bool = True) -> bool:
        try:
            env = os.environ.copy()
            if use_hermes_store and (CHECKPOINT_STORE / "objects").exists():
                env["GIT_DIR"] = str(CHECKPOINT_STORE)
            else:
                return False
            env["GIT_WORK_TREE"] = working_dir
            subprocess.run(
                ["git", "checkout", "-f", sha, "--", "."],
                cwd=working_dir,
                env=env,
                check=True,
                capture_output=True,
            )
            return True
        except Exception:
            return False
