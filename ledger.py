"""
SQLite Ledger + Checkpoint Store for hermes-time-machine.

Stores snapshots strictly in HERMES_HOME/plugin-data/time-machine/ (ledger.db and bare git store).
Zero writes to core SQLite databases or core checkpoints/store.
Restores files exclusively from the plugin's own snapshot store without touching the user's .git repo.
"""
from __future__ import annotations
from contextlib import contextmanager

import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

FILE_MUTATING_TOOLS = frozenset({
    "write_file",
    "patch",
    "edit_file",
    "apply_patch",
    "multi_patch",
})

COMMAND_TOOLS = frozenset({
    "terminal",
    "bash",
    "execute_code",
    "run_command",
    "browser_exec",
    "code_execution",
})

MUTATING_TOOLS = FILE_MUTATING_TOOLS | COMMAND_TOOLS

SAFE_GIT_ENV_KEYS = frozenset({
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "HOME",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "TMP",
    "TEMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
})


def _minimal_git_env(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """
    Construct a hardened, minimal environment for Git subprocesses.
    Strips secret API keys (OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.) and
    forces non-interactive execution with no global/system config tampering.
    """
    env = {k: os.environ[k] for k in SAFE_GIT_ENV_KEYS if k in os.environ}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_AUTHOR_NAME"] = os.environ.get("GIT_AUTHOR_NAME", "Hermes Time Machine")
    env["GIT_AUTHOR_EMAIL"] = os.environ.get("GIT_AUTHOR_EMAIL", "time-machine@hermes.local")
    env["GIT_COMMITTER_NAME"] = os.environ.get("GIT_COMMITTER_NAME", "Hermes Time Machine")
    env["GIT_COMMITTER_EMAIL"] = os.environ.get("GIT_COMMITTER_EMAIL", "time-machine@hermes.local")
    if extra:
        env.update(extra)
    return env


def resolve_hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home
        return Path(get_hermes_home())
    except ImportError:
        env_home = os.environ.get("HERMES_HOME")
        if env_home:
            return Path(env_home).expanduser().resolve()
        return Path.home() / ".hermes"


def resolve_working_dir(session_cwd: Optional[str] = None, hermes_home: Optional[Any] = None, **kwargs: Any) -> str:
    """Resolve workspace directory, strictly bound to session_cwd."""
    if session_cwd and str(session_cwd).strip():
        try:
            p = Path(session_cwd).expanduser().resolve()
            if p.is_dir():
                return str(p)
        except Exception:
            pass
    return str(Path.cwd().resolve())


def validate_safe_path(working_dir: str, rel_path: str) -> Path:
    """Validate that rel_path does not escape working_dir (prevents ../ path traversal attacks)."""
    root = Path(working_dir).expanduser().resolve()
    target = (root / rel_path).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"Security Error: path {rel_path!r} escapes workspace root {root}")
    return target


def parse_unified_diff(diff_text: str) -> List[Dict[str, Any]]:
    """Parse git unified diff output into structured per-file change entries."""
    if not diff_text or not diff_text.strip():
        return []

    files = []
    current_file = None
    additions = 0
    deletions = 0
    patch_lines = []

    def _build_entry(f_name: str, adds: int, dels: int, p_lines: list[str]) -> Dict[str, Any]:
        p_text = "\n".join(p_lines)
        status = "modified"
        if "new file mode" in p_text or "--- /dev/null" in p_text:
            status = "added"
        elif "deleted file mode" in p_text or "+++ /dev/null" in p_text:
            status = "deleted"
        return {
            "file": f_name,
            "path": f_name,
            "status": status,
            "additions": adds,
            "deletions": dels,
            "patch": p_text,
            "diff_chunk": p_text,
        }

    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            if current_file:
                files.append(_build_entry(current_file, additions, deletions, patch_lines))
            m = re.search(r"b/(.+)$", line)
            current_file = m.group(1) if m else "unknown"
            additions = 0
            deletions = 0
            patch_lines = [line]
        elif line.startswith("+++ b/"):
            current_file = line[6:]
            patch_lines.append(line)
        elif line.startswith("+") and not line.startswith("+++"):
            additions += 1
            patch_lines.append(line)
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
            patch_lines.append(line)
        else:
            patch_lines.append(line)

    if current_file:
        files.append(_build_entry(current_file, additions, deletions, patch_lines))

    return files


def file_exists_in_commit(commit_hash: str, rel_path: str, store_dir: Path) -> bool:
    """Check if a file path existed in a commit within the plugin's bare git store."""
    try:
        norm = rel_path.replace("\\", "/").lstrip("/")
        res = subprocess.run(
            ["git", f"--git-dir={store_dir}", "cat-file", "-e", f"{commit_hash}:{norm}"],
            capture_output=True,
            timeout=5,
            env=_minimal_git_env(),
        )
        return res.returncode == 0
    except Exception:
        return False


def _extract_args(snap: Dict[str, Any]) -> Dict[str, Any]:
    raw = snap.get("tool_args_summary") or ""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        raw = raw.strip()
        if (raw.startswith("{") and raw.endswith("}")) or (raw.startswith("[") and raw.endswith("]")):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                try:
                    import ast
                    parsed = ast.literal_eval(raw)
                    if isinstance(parsed, dict):
                        return parsed
                except Exception:
                    pass
        m = re.match(r"^([a-zA-Z_]+)=(.*)$", raw)
        if m:
            return {m.group(1): m.group(2)}
    return {}


class TimeMachineLedger:
    def __init__(
        self,
        db_path: Optional[Path] = None,
        plugin_data_dir: Optional[Path] = None,
        max_snapshots_per_session: int = 100,
    ):
        self.hermes_home = resolve_hermes_home()
        self.plugin_data_dir = (
            Path(plugin_data_dir).resolve()
            if plugin_data_dir
            else (self.hermes_home / "plugin-data" / "time-machine")
        )
        self.plugin_data_dir.mkdir(parents=True, exist_ok=True)
        self.store_dir = self.plugin_data_dir / "store"
        self.db_path = Path(db_path) if db_path else (self.plugin_data_dir / "ledger.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_snapshots_per_session = max(10, int(max_snapshots_per_session or 100))
        self._lock = threading.Lock()
        self._last_failed_commands: Dict[str, str] = {}
        self._ensure_store_dir()
        self._init_db()
        self._get_db = self._connect

    @property
    def data_dir(self) -> Path:
        """Alias for plugin_data_dir."""
        return self.plugin_data_dir

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

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
                    created_at REAL NOT NULL,
                    self_heal_candidate INTEGER DEFAULT 0,
                    recommended_snapshot_id TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tm_session ON snapshots(session_id, created_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tm_call_id ON snapshots(tool_call_id)")
            conn.commit()

    def _ensure_store_dir(self) -> Path:
        """Ensure the plugin's private bare git store is initialized."""
        if not (self.store_dir / "HEAD").exists():
            self.store_dir.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "init", "--bare", str(self.store_dir)],
                capture_output=True,
                check=True,
                timeout=10,
                env=_minimal_git_env(),
            )
        return self.store_dir

    def _project_hash16(self, working_dir: str) -> str:
        abs_dir = str(Path(working_dir).expanduser().resolve())
        return hashlib.sha256(abs_dir.encode("utf-8")).hexdigest()[:16]

    def _read_snapshot_ref_tip(self, working_dir: str) -> Optional[str]:
        """Read the tip commit of this project in the plugin's bare git store."""
        if not self.store_dir.exists():
            return None
        hash16 = self._project_hash16(working_dir)
        try:
            res = subprocess.run(
                ["git", f"--git-dir={self.store_dir}", "rev-parse", f"refs/time-machine/{hash16}"],
                capture_output=True,
                text=True,
                timeout=5,
                env=_minimal_git_env(),
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass
        return None

    def ensure_and_get_checkpoint(self, working_dir: str, reason: str) -> Optional[str]:
        """Capture a checkpoint of working_dir into the plugin's private bare git store."""
        abs_dir = str(Path(working_dir).expanduser().resolve())
        store = self._ensure_store_dir()
        hash16 = self._project_hash16(abs_dir)
        idx_dir = self.plugin_data_dir / "indexes"
        idx_dir.mkdir(parents=True, exist_ok=True)
        idx_file = idx_dir / hash16

        env = _minimal_git_env({
            "GIT_DIR": str(store),
            "GIT_WORK_TREE": abs_dir,
            "GIT_INDEX_FILE": str(idx_file),
        })
        try:
            subprocess.run(["git", "add", "-A"], cwd=abs_dir, env=env, capture_output=True, timeout=10)
            tree_res = subprocess.run(["git", "write-tree"], cwd=abs_dir, env=env, capture_output=True, text=True, timeout=5)
            if tree_res.returncode == 0 and tree_res.stdout.strip():
                tree_sha = tree_res.stdout.strip()
                parent_sha = self._read_snapshot_ref_tip(abs_dir)
                cmd = ["git", "commit-tree", tree_sha, "-m", reason or "checkpoint"]
                if parent_sha:
                    cmd.extend(["-p", parent_sha])
                commit_res = subprocess.run(cmd, cwd=abs_dir, env=env, capture_output=True, text=True, timeout=5)
                if commit_res.returncode == 0 and commit_res.stdout.strip():
                    new_sha = commit_res.stdout.strip()
                    subprocess.run(
                        ["git", "update-ref", f"refs/time-machine/{hash16}", new_sha],
                        cwd=abs_dir,
                        env=env,
                        capture_output=True,
                        timeout=5,
                    )
                    return new_sha
                else:
                    logger.debug("commit-tree failed (code %s): %s", commit_res.returncode, commit_res.stderr)
            else:
                logger.debug("write-tree failed (code %s): %s", tree_res.returncode, tree_res.stderr)
        except Exception as exc:
            logger.debug("Plugin git store checkpoint failed: %s", exc)

        tip = self._read_snapshot_ref_tip(abs_dir)
        if tip:
            return tip
        return None

    def _prune_old_snapshots(self, session_id: str) -> None:
        """Prune oldest snapshots when session count exceeds max_snapshots_per_session."""
        if not self.max_snapshots_per_session or self.max_snapshots_per_session <= 0:
            return
        try:
            with self._lock, self._connect() as conn:
                rows = conn.execute(
                    "SELECT id FROM snapshots WHERE session_id = ? ORDER BY created_at DESC",
                    (session_id,),
                ).fetchall()
                if len(rows) > self.max_snapshots_per_session:
                    to_delete = [r[0] for r in rows[self.max_snapshots_per_session:]]
                    placeholders = ",".join("?" for _ in to_delete)
                    conn.execute(f"DELETE FROM snapshots WHERE id IN ({placeholders})", to_delete)
        except Exception as exc:
            logger.debug("Snapshot prune error: %s", exc)

    def record_pre_tool(
        self,
        session_id: str = "default",
        turn_id: Optional[str] = None,
        tool_call_id: Optional[str] = None,
        tool_name: str = "tool",
        tool_args_summary: Optional[str] = None,
        working_dir: Optional[str] = None,
        reason: Optional[str] = None,
        args: Optional[Any] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Record pre-tool snapshot into the plugin's bare git store."""
        if args is not None and not tool_args_summary:
            tool_args_summary = json.dumps(args) if isinstance(args, (dict, list)) else str(args)
        wdir = resolve_working_dir(working_dir)
        snap_id = f"snap_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
        now = time.time()
        cp_reason = reason or f"pre-{tool_name}"
        checkpoint_hash = self.ensure_and_get_checkpoint(wdir, cp_reason)

        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO snapshots (
                    id, session_id, turn_id, tool_call_id, tool_name,
                    tool_args_summary, working_dir, checkpoint_hash, reason,
                    message_id_watermark, message_count_watermark, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    snap_id,
                    session_id,
                    turn_id,
                    tool_call_id,
                    tool_name,
                    tool_args_summary or "",
                    wdir,
                    checkpoint_hash,
                    cp_reason,
                    None,
                    0,
                    now,
                ),
            )

        self._prune_old_snapshots(session_id)

        return {
            "id": snap_id,
            "session_id": session_id,
            "turn_id": turn_id,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "working_dir": wdir,
            "checkpoint_hash": checkpoint_hash,
            "created_at": now,
        }

    def record_post_tool(
        self,
        session_id: str = "default",
        turn_id: Optional[str] = None,
        tool_call_id: Optional[str] = None,
        tool_name: str = "tool",
        duration_ms: Optional[float] = None,
        status: str = "ok",
        self_heal_candidate: bool = False,
        recommended_snapshot_id: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        now = time.time()
        with self._lock, self._connect() as conn:
            row = None
            if tool_call_id:
                row = conn.execute(
                    "SELECT id, working_dir, checkpoint_hash FROM snapshots WHERE tool_call_id = ? ORDER BY created_at DESC LIMIT 1",
                    (tool_call_id,),
                ).fetchone()

            if not row and turn_id:
                row = conn.execute(
                    "SELECT id, working_dir, checkpoint_hash FROM snapshots WHERE session_id = ? AND turn_id = ? ORDER BY created_at DESC LIMIT 1",
                    (session_id, turn_id),
                ).fetchone()

            if not row:
                row = conn.execute(
                    "SELECT id, working_dir, checkpoint_hash FROM snapshots WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
                    (session_id,),
                ).fetchone()

            if not row:
                return

            snap_id = row["id"]
            wdir = row["working_dir"]
            pre_hash = row["checkpoint_hash"]

            post_hash = None
            files_changed = 0
            insertions = 0
            deletions = 0

            if tool_name in FILE_MUTATING_TOOLS:
                post_hash = self.ensure_and_get_checkpoint(wdir, f"post-{tool_name}")
                if pre_hash and post_hash and pre_hash != post_hash:
                    try:
                        res = subprocess.run(
                            ["git", f"--git-dir={self.store_dir}", "diff", "--numstat", pre_hash, post_hash],
                            capture_output=True,
                            text=True,
                            timeout=5,
                            env=_minimal_git_env(),
                        )
                        if res.returncode == 0:
                            for line in res.stdout.splitlines():
                                parts = line.split("\t")
                                if len(parts) >= 3:
                                    files_changed += 1
                                    try:
                                        insertions += int(parts[0])
                                    except ValueError:
                                        pass
                                    try:
                                        deletions += int(parts[1])
                                    except ValueError:
                                        pass
                    except Exception:
                        pass

            conn.execute(
                """
                UPDATE snapshots SET
                    post_checkpoint_hash = COALESCE(?, post_checkpoint_hash),
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
                    snap_id,
                ),
            )
            return dict(conn.execute("SELECT * FROM snapshots WHERE id = ?", (snap_id,)).fetchone() or {})
        return None

    def get_snapshot(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
            if row:
                return dict(row)
            row_cp = conn.execute("SELECT * FROM snapshots WHERE checkpoint_hash = ? ORDER BY created_at DESC LIMIT 1", (snapshot_id,)).fetchone()
            if row_cp:
                return dict(row_cp)
        return None

    def list_snapshots(
        self,
        session_id: Optional[str] = None,
        working_dir: Optional[str] = None,
        limit: int = 50,
        include_raw_checkpoints: bool = True,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        with self._lock, self._connect() as conn:
            query = "SELECT * FROM snapshots"
            params: List[Any] = []
            clauses = []
            if session_id:
                clauses.append("session_id = ?")
                params.append(session_id)
            if working_dir:
                clauses.append("working_dir = ?")
                params.append(str(Path(working_dir).expanduser().resolve()))

            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            rows = [dict(r) for r in conn.execute(query, params).fetchall()]

        if not include_raw_checkpoints:
            return [r for r in rows if r.get("checkpoint_hash")]
        return rows

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
            for r in rows[:keep_last]:
                keep_ids.add(r["id"])

            for r in rows:
                if r.get("tool_name") in ("session_start", "pre_restore_safety_backup"):
                    keep_ids.add(r["id"])
                if r.get("recommended_snapshot_id"):
                    keep_ids.add(r["id"])
                    keep_ids.add(r["recommended_snapshot_id"])

            to_delete = [r["id"] for r in rows if r["id"] not in keep_ids]
            if to_delete:
                placeholders = ",".join("?" for _ in to_delete)
                conn.execute(f"DELETE FROM snapshots WHERE id IN ({placeholders})", to_delete)

            return {
                "ok": True,
                "total_before": total_before,
                "pruned_count": len(to_delete),
                "remaining_count": total_before - len(to_delete),
            }

    def get_storage_stats(self) -> Dict[str, Any]:
        """Return total snapshots, sessions, self-heal alerts, and estimated store size."""
        total_snaps = 0
        total_sess = 0
        heal_alerts = 0
        db_size = 0

        if self.db_path.exists():
            db_size = self.db_path.stat().st_size

        store_size = 0
        if self.store_dir.exists():
            for f in self.store_dir.rglob("*"):
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
            "self_heal_alerts": heal_alerts,
            "db_size_bytes": db_size,
            "store_size_bytes": store_size,
            "total_storage_bytes": db_size + store_size,
        }

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

    def get_diff(
        self,
        snapshot_id: str,
        compare_to_id: Optional[str] = None,
        working_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Compute unified diff between snapshot and working tree or parent."""
        snap = self.get_snapshot(snapshot_id)
        target_hash = snap["checkpoint_hash"] if snap else snapshot_id
        wdir = (
            snap["working_dir"]
            if snap and snap.get("working_dir")
            else resolve_working_dir(working_dir)
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
        store = self.store_dir
        hash16 = self._project_hash16(wdir)
        idx_file = self.plugin_data_dir / "indexes" / hash16
        env = _minimal_git_env({
            "GIT_DIR": str(store),
            "GIT_WORK_TREE": str(Path(wdir).resolve()),
            "GIT_INDEX_FILE": str(idx_file),
        })

        if mode == "working_tree":
            try:
                res = subprocess.run(
                    ["git", f"--git-dir={store}", f"--work-tree={wdir}", "diff", target_hash],
                    capture_output=True,
                    timeout=15,
                    env=env,
                )
                if res.returncode == 0:
                    diff_text = res.stdout.decode("utf-8", "replace")
            except Exception as exc:
                logger.debug("working_tree diff error: %s", exc)
        elif base_hash and base_hash != target_hash:
            try:
                res = subprocess.run(
                    ["git", f"--git-dir={store}", "diff", base_hash, target_hash],
                    capture_output=True,
                    timeout=15,
                    env=env,
                )
                if res.returncode == 0:
                    diff_text = res.stdout.decode("utf-8", "replace")
            except Exception as exc:
                logger.debug("arbitrary diff error: %s", exc)

        if not diff_text and mode != "working_tree":
            try:
                res = subprocess.run(
                    ["git", f"--git-dir={store}", "show", target_hash, "--format="],
                    capture_output=True,
                    timeout=15,
                    env=env,
                )
                if res.returncode == 0:
                    diff_text = res.stdout.decode("utf-8", "replace")
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
        commit_hash: Optional[str] = None,
        file_path: Optional[str] = None,
        file_paths: Optional[List[str]] = None,
        working_dir: Optional[str] = None,
        dry_run: bool = True,
        user_approved: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Surgically restore files from the plugin's bare git store.
        Defaults to dry-run preview. Never touches disk unless user_approved is True and dry_run is False.
        Never runs git checkout -f in the user's repository.
        """
        snap = self.get_snapshot(snapshot_id)
        if commit_hash is None or commit_hash == "None" or not commit_hash:
            commit_hash = (snap.get("checkpoint_hash") if snap else None) or snapshot_id

        snap_wdir = snap.get("working_dir") if snap else None
        target_cwd = working_dir or kwargs.get("session_cwd")

        if target_cwd is not None:
            active_cwd = resolve_working_dir(target_cwd)
            if snap_wdir:
                resolved_snap_wdir = resolve_working_dir(snap_wdir)
                if Path(resolved_snap_wdir).resolve() != Path(active_cwd).resolve():
                    return {
                        "ok": False,
                        "snapshot_id": snapshot_id,
                        "working_dir": active_cwd,
                        "error": (
                            f"Refusing to restore snapshot '{snapshot_id}': snapshot workspace "
                            f"'{resolved_snap_wdir}' does not match active session_cwd '{active_cwd}'."
                        ),
                    }
                wdir = resolved_snap_wdir
            else:
                wdir = active_cwd
        else:
            wdir = resolve_working_dir(snap_wdir)

        target_files = []
        if file_paths:
            target_files.extend([f.strip() for f in file_paths if f and f.strip()])
        if file_path and file_path.strip() not in target_files:
            target_files.append(file_path.strip())

        # Path Traversal Guard: ensure all target files resolve strictly within wdir
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

        store = self.store_dir
        self._ensure_store_dir()

        # Verify commit exists strictly in plugin's isolated bare store
        c_check = subprocess.run(
            ["git", f"--git-dir={store}", "cat-file", "-t", str(commit_hash)],
            capture_output=True,
            text=True,
            timeout=5,
            env=_minimal_git_env(),
        )
        if c_check.returncode != 0 or c_check.stdout.strip() != "commit":
            return {
                "ok": False,
                "snapshot_id": snapshot_id,
                "working_dir": wdir,
                "error": f"Target commit '{commit_hash}' does not exist in Time Machine store.",
            }

        planned_restores = [vt[0] for vt in validated_targets] if validated_targets else ["(all files)"]
        planned_unlinks = []

        # Identify files that did not exist in commit_hash (newly created files to unlink)
        for rel_tf, abs_tf in validated_targets:
            if not file_exists_in_commit(commit_hash, rel_tf, store):
                if abs_tf.exists():
                    planned_unlinks.append(rel_tf)

        # DRY RUN MODE (Default): Return preview diff and instructions without touching disk
        if dry_run or not user_approved:
            diff_res = self.get_diff(snapshot_id, compare_to_id="WORKING_TREE", working_dir=wdir)
            return {
                "ok": True,
                "dry_run": True,
                "snapshot_id": snap["id"] if snap else snapshot_id,
                "checkpoint_hash": commit_hash,
                "working_dir": wdir,
                "files_restored": False,
                "planned_restored_files": planned_restores,
                "planned_unlinked_files": planned_unlinks,
                "planned_deleted_stray_files": planned_unlinks,
                "diff": diff_res.get("diff", "(No diff)"),
                "message": (
                    f"Dry run preview for snapshot '{snapshot_id}'. "
                    f"Planned surgical restores: {planned_restores}. "
                    f"Planned deletions of new files: {planned_unlinks}. "
                    f"No files were modified on disk. To execute, run: /tm restore {snapshot_id} "
                    f"(or confirm via Hermes approval prompt / Dashboard)."
                ),
            }

        # EXECUTION MODE (User Approved):
        # 1. Take a pre-restore safety backup into plugin store first
        backup_hash = self.ensure_and_get_checkpoint(wdir, f"pre-restore-safety-backup-{snapshot_id}")
        backup_snap = None
        if backup_hash:
            backup_snap = self.record_pre_tool(
                session_id=snap.get("session_id") if snap else "recovery",
                tool_name="pre_restore_safety_backup",
                tool_args_summary=f"pre_restore_safety_backup before reverting to {snapshot_id}",
                working_dir=wdir,
                reason=f"pre_restore_safety_backup before reverting to {snapshot_id}",
            )

        # 2. Extract and restore files strictly from plugin bare git store (NEVER git checkout -f in user repo)
        restored_files = []
        unlinked_files = []
        errors = []

        if validated_targets:
            for rel_tf, abs_tf in validated_targets:
                existed = file_exists_in_commit(commit_hash, rel_tf, store)
                if existed:
                    res = subprocess.run(
                        ["git", f"--git-dir={store}", "show", f"{commit_hash}:{rel_tf}"],
                        capture_output=True,
                        timeout=10,
                        env=_minimal_git_env(),
                    )
                    if res.returncode == 0:
                        try:
                            abs_tf.parent.mkdir(parents=True, exist_ok=True)
                            abs_tf.write_bytes(res.stdout)
                            restored_files.append(rel_tf)
                        except OSError as io_err:
                            errors.append(f"Write error on {rel_tf}: {io_err}")
                    else:
                        errors.append(f"Extract error on {rel_tf}: {res.stderr.decode('utf-8', 'replace')}")
                else:
                    if abs_tf.exists():
                        try:
                            abs_tf.unlink()
                            unlinked_files.append(rel_tf)
                        except OSError as rm_err:
                            errors.append(f"Could not remove {rel_tf}: {rm_err}")
        else:
            # Full restoration from store via git archive (safe, never detaches user's HEAD)
            try:
                archive_res = subprocess.run(
                    ["git", f"--git-dir={store}", "archive", commit_hash],
                    capture_output=True,
                    timeout=30,
                    env=_minimal_git_env(),
                )
                if archive_res.returncode == 0:
                    import io
                    import tarfile
                    with tarfile.open(fileobj=io.BytesIO(archive_res.stdout)) as tf_archive:
                        if hasattr(tarfile, "data_filter"):
                            tf_archive.extractall(path=wdir, filter="data")
                        else:
                            tf_archive.extractall(path=wdir)
                    restored_files.append("(all workspace files)")
                else:
                    errors.append(f"Full archive extraction failed: {archive_res.stderr.decode('utf-8', 'replace')}")
            except Exception as a_exc:
                errors.append(f"Full extraction error: {a_exc}")
                if archive_res.returncode == 0:
                    import tarfile
                    import io
                    with tarfile.open(fileobj=io.BytesIO(archive_res.stdout)) as tf_archive:
                        tf_archive.extractall(path=wdir)
                    restored_files.append("(all workspace files)")
                else:
                    errors.append(f"Full archive extraction failed: {archive_res.stderr.decode('utf-8', 'replace')}")
            except Exception as a_exc:
                errors.append(f"Full extraction error: {a_exc}")

        ok = len(errors) == 0 and (len(restored_files) > 0 or len(unlinked_files) > 0)
        return {
            "ok": ok,
            "dry_run": False,
            "snapshot_id": snap["id"] if snap else snapshot_id,
            "checkpoint_hash": commit_hash,
            "working_dir": wdir,
            "files_restored": len(restored_files) > 0,
            "restored_files": restored_files,
            "unlinked_new_files": unlinked_files,
            "pre_restore_backup_id": backup_snap.get("id") if backup_snap else None,
            "errors": errors if errors else None,
            "message": (
                f"Successfully restored {len(restored_files)} file(s) from snapshot '{snapshot_id}'. "
                f"Safety backup captured: '{backup_snap.get('id') if backup_snap else None}'."
                if ok else f"Restoration encountered errors: {'; '.join(errors)}"
            ),
        }

    def diagnose_failure(
        self,
        session_id: Optional[str] = None,
        error_text: Optional[str] = None,
        working_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Diagnose failure root-cause by correlating error output with recently modified files.
        Only selects candidate snapshots with verified commits from the plugin's store.
        """
        sess = session_id or "default"
        wdir = resolve_working_dir(working_dir)
        snaps = self.list_snapshots(session_id=sess, limit=50)

        err_msg = error_text or ""
        last_failed_command = self._last_failed_commands.get(sess, "")

        for s in snaps:
            if s.get("status") == "error" or s.get("self_heal_candidate"):
                if s.get("tool_args_summary"):
                    last_failed_command = s.get("tool_args_summary")
                break

        file_mut_snaps = [s for s in reversed(snaps) if s.get("tool_name") in FILE_MUTATING_TOOLS]

        session_touched_files = []
        snap_for_file = {}

        for s in file_mut_snaps:
            args = _extract_args(s)
            fpath = args.get("path") or args.get("file_path") or args.get("file")
            if fpath:
                norm = str(fpath).replace("\\", "/").lstrip("/")
                if norm not in session_touched_files:
                    session_touched_files.append(norm)
                if norm not in snap_for_file and s.get("checkpoint_hash"):
                    snap_for_file[norm] = s

        implicated_files = []
        for f in session_touched_files:
            basename = Path(f).name
            if (basename in err_msg) or (f in err_msg):
                implicated_files.append(f)

        if not implicated_files and file_mut_snaps:
            last_mut_args = _extract_args(file_mut_snaps[-1])
            last_path = last_mut_args.get("path") or last_mut_args.get("file_path") or last_mut_args.get("file")
            if last_path:
                implicated_files.append(str(last_path).replace("\\", "/").lstrip("/"))

        if not implicated_files and err_msg:
            try:
                p_wdir = Path(wdir).resolve()
                candidates = re.findall(
                    r'(?:FAILED\s+)?([a-zA-Z0-9_\-\\\/\.]+\.[a-zA-Z0-9_]+)(?:::|:|\s|,|\"|\'|\))',
                    err_msg,
                )
                for cand in candidates:
                    cand_clean = cand.replace("\\", "/").strip().lstrip("/")
                    if cand_clean.endswith((".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".yaml", ".yml", ".toml", ".rs", ".go")):
                        check_file = p_wdir / cand_clean
                        if check_file.exists() and check_file.is_file():
                            if cand_clean not in implicated_files:
                                implicated_files.append(cand_clean)
            except Exception:
                pass

        culprit_paths = list(implicated_files)

        recommended_snap = None
        if culprit_paths and culprit_paths[0] in snap_for_file:
            recommended_snap = snap_for_file[culprit_paths[0]]

        if not recommended_snap and file_mut_snaps:
            for s in reversed(file_mut_snaps):
                if s.get("checkpoint_hash"):
                    recommended_snap = s
                    break

        if not recommended_snap and snaps:
            for s in reversed(snaps):
                if s.get("checkpoint_hash"):
                    recommended_snap = s
                    break

        rec_id = recommended_snap.get("id") if (recommended_snap and recommended_snap.get("checkpoint_hash")) else None

        # Detect newly created stray files created in working_dir after recommended safe snapshot
        if recommended_snap:
            rec_time = float(recommended_snap.get("created_at") or 0.0)
            rec_cp = recommended_snap.get("checkpoint_hash")
            try:
                p_wdir = Path(wdir).resolve()
                for entry in p_wdir.iterdir():
                    if entry.is_file():
                        name = entry.name
                        if name.startswith("."):
                            continue
                        rel_name = name
                        if rel_name in session_touched_files or rel_name in culprit_paths:
                            continue
                        try:
                            # If created after safe checkpoint and not in the safe commit
                            if entry.stat().st_mtime >= (rec_time - 0.5):
                                if rec_cp and not file_exists_in_commit(rec_cp, rel_name, self.store_dir):
                                    culprit_paths.append(rel_name)
                        except OSError:
                            pass
            except Exception:
                pass

        # Preserved files: files modified in session that are NOT in culprit_paths
        preserved_files = sorted([f for f in session_touched_files if f not in culprit_paths])

        if culprit_paths and rec_id:
            explanation = (
                f"Diagnosed failure culprit file(s): [{', '.join(culprit_paths)}]. "
                f"Preserving concurrent modifications in: [{', '.join(preserved_files)}]. "
                f"Recommended safe checkpoint: {rec_id}."
            )
        elif rec_id:
            explanation = f"Recommended safe checkpoint prior to failure: {rec_id}."
        else:
            explanation = "No safe checkpoint identified for recovery."

        return {
            "ok": True,
            "session_id": sess,
            "working_dir": wdir,
            "recommended_snapshot_id": rec_id,
            "culprits": [{"path": c, "confidence": 0.95} for c in culprit_paths],
            "culprit_files": culprit_paths,
            "preserved_files": preserved_files,
            "confidence": "high" if culprit_paths else "low",
            "confidence_score": 0.95 if culprit_paths else 0.5,
            "explanation": explanation,
            "retry_command": last_failed_command,
        }

    def recover(
        self,
        session_id: Optional[str] = None,
        error_summary: Optional[str] = None,
        working_dir: Optional[str] = None,
        dry_run: bool = True,
        user_approved: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Diagnose failure and preview recovery plan.
        Returns suggested_verify_command for execution through Hermes' terminal tool.
        Defaults to dry-run mode (never writes disk without user approval).
        """
        diag = self.diagnose_failure(
            session_id=session_id,
            error_text=error_summary,
            working_dir=working_dir,
        )

        culprit_files = diag.get("culprit_files") or []
        preserved_files = diag.get("preserved_files") or []
        rec_id = diag.get("recommended_snapshot_id")

        suggested_cmd = (
            diag.get("retry_command")
            or self._last_failed_commands.get(session_id or "default", "")
        )
        if isinstance(suggested_cmd, str):
            s_str = suggested_cmd.strip()
            if s_str.startswith("{") and s_str.endswith("}"):
                try:
                    parsed = json.loads(s_str)
                    if isinstance(parsed, dict) and "command" in parsed:
                        suggested_cmd = parsed["command"]
                except Exception:
                    pass
            elif s_str.startswith("command="):
                suggested_cmd = s_str.split("=", 1)[1].strip()

        restore_preview = None
        if rec_id:
            restore_preview = self.restore_snapshot(
                snapshot_id=rec_id,
                file_paths=culprit_files if culprit_files else None,
                working_dir=diag.get("working_dir") or working_dir,
                dry_run=dry_run,
                user_approved=user_approved,
            )

        is_dry_run = dry_run or not user_approved

        return {
            "ok": bool(rec_id),
            "dry_run": is_dry_run,
            "restored": not is_dry_run and bool(restore_preview and restore_preview.get("ok")),
            "auto_applied": not is_dry_run and bool(restore_preview and restore_preview.get("ok")),
            "restored_files": restore_preview.get("restored_files", culprit_files) if (restore_preview and not is_dry_run) else [],
            "pre_restore_backup_id": restore_preview.get("pre_restore_backup_id") if (restore_preview and not is_dry_run) else None,
            "unlinked_new_files": restore_preview.get("unlinked_new_files", []) if (restore_preview and not is_dry_run) else [],
            "diagnosis": diag,
            "recommended_snapshot_id": rec_id,
            "culprit_files": culprit_files,
            "preserved_files": preserved_files,
            "planned_restored_files": restore_preview.get("planned_restored_files", culprit_files) if restore_preview else culprit_files,
            "planned_unlinked_files": restore_preview.get("planned_unlinked_files", []) if restore_preview else [],
            "diff": restore_preview.get("diff") if restore_preview else None,
            "suggested_verify_command": suggested_cmd,
            "action_required": (
                f"To apply this surgical restoration, run: /tm restore {rec_id}\n"
                f"Then verify using Hermes' terminal tool: {suggested_cmd}"
                if is_dry_run
                else f"Restoration applied. Please run verification using Hermes' terminal tool: {suggested_cmd}"
            ),
            "message": (
                f"Recovery diagnosis complete. {diag.get('explanation', '')} "
                f"{'Dry-run preview: no files modified on disk.' if is_dry_run else 'Files restored to disk.'}"
            ),
        }

    def search_snapshots(
        self,
        query: str,
        session_id: Optional[str] = None,
        file_path: Optional[str] = None,
        limit: int = 20,
        working_dir: Optional[str] = None,
        **kwargs: Any,
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
            snap_id = s.get("id")
            if not snap_id:
                continue
            wdir = s.get("working_dir") or working_dir or resolve_working_dir(working_dir)
            diff_res = self.get_diff(snap_id, working_dir=wdir)
            files_structured = diff_res.get("files", [])

            matching_lines: List[Dict[str, Any]] = []
            matched_files: List[str] = []

            for f in files_structured:
                fpath = f.get("path") or f.get("file") or ""
                if file_path and file_path not in fpath:
                    continue

                chunk = f.get("diff_chunk") or f.get("patch") or ""
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

    def get_last_safe_snapshot(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get the latest file-mutating snapshot that succeeded."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM snapshots
                WHERE session_id = ? AND tool_name IN ('write_file', 'patch', 'edit_file', 'apply_patch', 'multi_patch')
                  AND status = 'ok' AND checkpoint_hash IS NOT NULL AND checkpoint_hash != ''
                ORDER BY created_at DESC LIMIT 1
                """,
                (session_id,),
            ).fetchone()
            if row:
                return dict(row)
        return None
