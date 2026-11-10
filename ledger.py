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

# Shared across TimeMachineLedger() instances created per hook/tool call.
# Production register() builds a fresh ledger whenever ledger=None, so a
# per-instance dict was discarded before time_machine_recover could read it.
_GLOBAL_LAST_FAILED_COMMANDS: Dict[str, str] = {}

# Default git exclude lines for the private bare store. Appended if missing;
# never overwrite custom lines a user already added to info/exclude.
_DEFAULT_GIT_EXCLUDES: Tuple[str, ...] = (
    ".git/",
    ".env",
    ".env.*",
    "!.env.example",
    "*.pem",
    "*.key",
    "node_modules/",
    ".venv/",
    "venv/",
    "__pycache__/",
    ".pytest_cache/",
)

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


def lexical_workspace_path(working_dir: str, rel_path: str) -> Path:
    """Path inside the workspace that does not follow a final symlink."""
    root = Path(working_dir).expanduser().resolve()
    raw = str(rel_path or "").replace("\\", "/").strip()
    if not raw or raw.startswith("/") or raw.startswith("~"):
        raise ValueError(f"Security Error: path {rel_path!r} escapes workspace root {root}")
    parts = Path(raw).parts
    if any(part in ("..", ".git") or part.startswith(".git") for part in parts):
        raise ValueError(f"Security Error: path {rel_path!r} escapes workspace root {root}")
    candidate = root.joinpath(*parts)
    parent = candidate.parent
    try:
        parent_resolved = parent.resolve()
    except OSError as exc:
        raise ValueError(f"Security Error: path {rel_path!r} escapes workspace root {root}") from exc
    if parent_resolved != root and root not in parent_resolved.parents:
        raise ValueError(f"Security Error: path {rel_path!r} escapes workspace root {root}")
    if candidate.is_symlink():
        return candidate
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise ValueError(f"Security Error: path {rel_path!r} escapes workspace root {root}") from exc
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"Security Error: path {rel_path!r} escapes workspace root {root}")
    return candidate


def unlink_workspace_entry(working_dir: str, rel_path: str) -> bool:
    """Unlink a file or symlink inside the workspace. Never follows a symlink to its target."""
    candidate = lexical_workspace_path(working_dir, rel_path)
    if candidate.is_symlink() or (candidate.exists() and candidate.is_file()):
        candidate.unlink()
        return True
    return False


def _is_remote_git_spec(value: str) -> bool:
    """True for git URLs and scp-style remotes. Time Machine never pushes or clones those."""
    text = (value or "").strip()
    lowered = text.lower()
    if not text:
        return False
    if "://" in text or lowered.startswith("git@") or lowered.startswith("ssh://"):
        return True
    if text in {"origin", "upstream"}:
        return True
    return False


def _command_text_from_summary(summary: str) -> str:
    """Pull a shell command out of a raw summary or a JSON tool-args blob."""
    text = (summary or "").strip()
    if not text:
        return ""
    if text.startswith("command="):
        return text.split("=", 1)[1].strip()
    if text.startswith("{") or text.startswith("["):
        try:
            parsed = json.loads(text)
        except Exception:
            return ""
        if isinstance(parsed, dict):
            cmd = parsed.get("command") or parsed.get("cmd") or parsed.get("script") or ""
            return cmd.strip() if isinstance(cmd, str) else ""
        return ""
    return text


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
            ["git", f"--git-dir={store_dir}", "cat-file", "-e", "--end-of-options", f"{commit_hash}:{norm}"],
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
        self._last_failed_commands = _GLOBAL_LAST_FAILED_COMMANDS
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS session_failures (
                    session_id TEXT PRIMARY KEY,
                    command TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS snapshot_comments (
                    snapshot_id TEXT PRIMARY KEY,
                    comment TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workspace_inventory (
                    session_id TEXT NOT NULL,
                    working_dir TEXT NOT NULL,
                    rel_path TEXT NOT NULL,
                    PRIMARY KEY (session_id, working_dir, rel_path)
                )
                """
            )
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
        self._ensure_default_excludes()
        return self.store_dir

    def _ensure_default_excludes(self) -> None:
        """Write default secret/bloat excludes without dropping custom user lines."""
        exclude_path = self.store_dir / "info" / "exclude"
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        existing = ""
        if exclude_path.exists():
            try:
                existing = exclude_path.read_text(encoding="utf-8")
            except OSError:
                existing = ""
        present = set(existing.splitlines())
        missing = [line for line in _DEFAULT_GIT_EXCLUDES if line not in present]
        if not missing:
            return
        body = existing
        if body and not body.endswith("\n"):
            body += "\n"
        body += "\n".join(missing) + "\n"
        exclude_path.write_text(body, encoding="utf-8")

    def remember_failed_command(self, session_id: str, command: str) -> None:
        """Persist the last failed command for a session across processes and instances."""
        cmd = (command or "").strip()
        if not session_id or not cmd:
            return
        self._last_failed_commands[session_id] = cmd
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO session_failures (session_id, command, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    command = excluded.command,
                    updated_at = excluded.updated_at
                """,
                (session_id, cmd, time.time()),
            )

    def _read_failed_command(self, session_id: str) -> str:
        """Return the last failed command from memory, then the SQLite ledger."""
        cached = self._last_failed_commands.get(session_id or "", "")
        if cached:
            return cached
        if not session_id:
            return ""
        try:
            with self._lock, self._connect() as conn:
                row = conn.execute(
                    "SELECT command FROM session_failures WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                if row and row["command"]:
                    return str(row["command"])
        except Exception:
            return ""
        return ""

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
        if tool_name == "session_start":
            try:
                self._record_workspace_inventory(session_id, wdir)
            except Exception as exc:
                logger.debug("workspace inventory error: %s", exc)

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
        tool_args_summary: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        summary = tool_args_summary.strip() if isinstance(tool_args_summary, str) else None
        if summary == "":
            summary = None
        if summary and tool_name in COMMAND_TOOLS and status == "error":
            try:
                self.remember_failed_command(session_id, summary)
            except Exception as exc:
                logger.debug("remember_failed_command error: %s", exc)

        with self._lock, self._connect() as conn:
            row = None
            if tool_call_id:
                row = conn.execute(
                    "SELECT id, working_dir, checkpoint_hash FROM snapshots WHERE tool_call_id = ? ORDER BY created_at DESC LIMIT 1",
                    (tool_call_id,),
                ).fetchone()

            # Never attach a post-tool update to a different tool's snapshot.
            # Prefer the still-pending pre-image for this tool, then the latest
            # row of the same tool. If nothing matches (read-only tool when
            # snapshot_on_every_tool is false), leave earlier snapshots alone.
            if not row:
                row = conn.execute(
                    """
                    SELECT id, working_dir, checkpoint_hash FROM snapshots
                    WHERE session_id = ? AND tool_name = ? AND status = 'pending'
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (session_id, tool_name),
                ).fetchone()

            if not row:
                row = conn.execute(
                    """
                    SELECT id, working_dir, checkpoint_hash FROM snapshots
                    WHERE session_id = ? AND tool_name = ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (session_id, tool_name),
                ).fetchone()

            if not row:
                return None

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
                            ["git", f"--git-dir={self.store_dir}", "diff", "--numstat", "--end-of-options", pre_hash, post_hash],
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
                    files_changed = CASE WHEN ? > 0 THEN ? ELSE files_changed END,
                    insertions = CASE WHEN ? > 0 THEN ? ELSE insertions END,
                    deletions = CASE WHEN ? > 0 THEN ? ELSE deletions END,
                    self_heal_candidate = ?,
                    recommended_snapshot_id = COALESCE(?, recommended_snapshot_id),
                    tool_args_summary = COALESCE(?, tool_args_summary)
                WHERE id = ?
                """,
                (
                    post_hash,
                    status,
                    duration_ms,
                    files_changed,
                    files_changed,
                    insertions,
                    insertions,
                    deletions,
                    deletions,
                    1 if self_heal_candidate else 0,
                    recommended_snapshot_id,
                    summary,
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
            if rows:
                ids = [r.get("id") for r in rows if r.get("id")]
                placeholders = ",".join("?" for _ in ids)
                comments = {}
                if ids:
                    for crow in conn.execute(
                        f"SELECT snapshot_id, comment FROM snapshot_comments WHERE snapshot_id IN ({placeholders})",
                        ids,
                    ).fetchall():
                        comments[crow["snapshot_id"]] = crow["comment"]
                for row in rows:
                    row["comment"] = comments.get(row.get("id"))

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

    def _resolve_candidate_hash(
        self, candidate: Any
    ) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]], Optional[str]]:
        """Validate snapshot candidate before running git.

        - Reject any candidate that startswith '-'.
        - If the candidate is a row in snapshots.id, use that row's checkpoint_hash.
        - Else accept the candidate only if re.fullmatch(r"[0-9a-f]{7,64}", candidate).
        - After taking checkpoint_hash from the database, also require that same hex regex.
        - Unknown ids (not in the db, not hex, or starting with -) return ok:false and must not run git.
        """
        if not candidate or not isinstance(candidate, str):
            return False, None, None, "Invalid snapshot candidate identifier."
        if candidate.startswith("-"):
            return False, None, None, f"Invalid identifier '{candidate}': cannot start with '-'."

        snap = None
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM snapshots WHERE id = ?", (candidate,)).fetchone()
            if row:
                snap = dict(row)
        if snap:
            c_hash = snap.get("checkpoint_hash")
            if not c_hash or not isinstance(c_hash, str) or not re.fullmatch(r"[0-9a-f]{7,64}", c_hash):
                return False, None, snap, f"Snapshot '{candidate}' has invalid or missing checkpoint hash."
            return True, c_hash, snap, None

        if re.fullmatch(r"[0-9a-f]{7,64}", candidate):
            return True, candidate, None, None

        return False, None, None, f"Snapshot '{candidate}' does not exist."

    def _commit_trees_differ(self, left: str, right: str) -> bool:
        """True when two commits in the private store have different trees."""
        def _tree(rev: str) -> Optional[str]:
            try:
                res = subprocess.run(
                    ["git", f"--git-dir={self.store_dir}", "rev-parse", "--verify", "--end-of-options", f"{rev}^{{tree}}"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    env=_minimal_git_env(),
                )
            except Exception:
                return None
            if res.returncode != 0:
                return None
            token = (res.stdout or "").strip()
            if re.fullmatch(r"[0-9a-f]{7,64}", token):
                return token
            return None

        left_tree = _tree(left)
        right_tree = _tree(right)
        if not left_tree or not right_tree:
            return False
        return left_tree != right_tree

    def _list_commit_paths(self, commit_hash: str) -> set:
        """List tracked paths in a private-store commit. Empty set on failure."""
        if not commit_hash or not re.fullmatch(r"[0-9a-f]{7,64}", commit_hash):
            return set()
        try:
            res = subprocess.run(
                ["git", f"--git-dir={self.store_dir}", "ls-tree", "-r", "--name-only", "--end-of-options", commit_hash],
                capture_output=True,
                text=True,
                timeout=15,
                env=_minimal_git_env(),
            )
        except Exception:
            return set()
        if res.returncode != 0:
            return set()
        return {line.strip().replace("\\", "/") for line in res.stdout.splitlines() if line.strip()}

    def _session_start_baseline_paths(self, session_id: Optional[str], working_dir: str) -> set:
        """Paths present in the earliest session_start commit for this workspace."""
        if not session_id:
            return set()
        resolved = str(Path(working_dir).expanduser().resolve())
        try:
            with self._lock, self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT checkpoint_hash FROM snapshots
                    WHERE session_id = ? AND tool_name = 'session_start' AND working_dir = ?
                    ORDER BY created_at ASC LIMIT 1
                    """,
                    (session_id, resolved),
                ).fetchone()
        except Exception:
            return set()
        if not row:
            return set()
        return self._list_commit_paths(row["checkpoint_hash"] or "")

    def _iter_workspace_entries(self, root: Path):
        """Yield (relative posix path, path) for files and symlinks. Does not follow links."""
        if not root.is_dir():
            return
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            base = Path(dirpath)
            kept_dirs = []
            for dirname in dirnames:
                if dirname == ".git" or dirname.startswith(".git"):
                    continue
                dir_path = base / dirname
                if dir_path.is_symlink():
                    rel = dir_path.relative_to(root).as_posix()
                    if ".git" not in Path(rel).parts:
                        yield rel, dir_path
                    continue
                kept_dirs.append(dirname)
            dirnames[:] = kept_dirs
            for name in filenames:
                path = base / name
                try:
                    rel = path.relative_to(root).as_posix()
                except ValueError:
                    continue
                if not rel or ".git" in Path(rel).parts:
                    continue
                yield rel, path

    def _record_workspace_inventory(self, session_id: str, working_dir: str) -> None:
        """Remember files that already existed at session start so restore cannot delete them."""
        root = Path(working_dir).expanduser().resolve()
        paths = [rel for rel, _path in self._iter_workspace_entries(root)]
        with self._lock, self._connect() as conn:
            conn.execute(
                "DELETE FROM workspace_inventory WHERE session_id = ? AND working_dir = ?",
                (session_id, str(root)),
            )
            conn.executemany(
                "INSERT OR REPLACE INTO workspace_inventory (session_id, working_dir, rel_path) VALUES (?, ?, ?)",
                [(session_id, str(root), rel) for rel in paths],
            )

    def _inventory_paths(self, session_id: Optional[str], working_dir: str) -> set:
        if not session_id:
            return set()
        resolved = str(Path(working_dir).expanduser().resolve())
        try:
            with self._lock, self._connect() as conn:
                rows = conn.execute(
                    "SELECT rel_path FROM workspace_inventory WHERE session_id = ? AND working_dir = ?",
                    (session_id, resolved),
                ).fetchall()
        except Exception:
            return set()
        return {row["rel_path"] for row in rows if row["rel_path"]}

    @staticmethod
    def _is_default_excluded_rel(rel: str) -> bool:
        """True for secret/bloat paths the bare store refuses to snapshot."""
        import fnmatch

        norm = rel.replace("\\", "/").lstrip("/")
        if norm == ".env.example" or norm.endswith("/.env.example"):
            return False
        base = norm.split("/")[-1]
        dir_names = {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache"}
        parts = norm.split("/")
        if any(part in dir_names for part in parts):
            return True
        patterns = (".env", ".env.*", "*.pem", "*.key")
        for pat in patterns:
            if fnmatch.fnmatch(base, pat) or fnmatch.fnmatch(norm, pat):
                return True
        return False

    def _collect_post_snapshot_strays(self, snap: Dict[str, Any], commit_hash: str, wdir: str) -> List[str]:
        """
        Files created after this snapshot that are not in commit_hash.

        Never returns .git paths, secret/bloat excludes, or paths that already
        existed in the session_start baseline commit (preexisting untracked
        files that were part of that baseline tree stay on disk).
        """
        root = Path(wdir).expanduser().resolve()
        if not root.is_dir() or not snap:
            return []
        snap_time = float(snap.get("created_at") or 0.0)
        commit_paths = self._list_commit_paths(commit_hash)
        baseline_paths = self._session_start_baseline_paths(snap.get("session_id"), str(root))
        inventory = self._inventory_paths(snap.get("session_id"), str(root))
        strays: List[str] = []
        for rel, path in self._iter_workspace_entries(root):
            if not rel or rel == ".git" or rel.startswith(".git/") or "/.git/" in rel:
                continue
            if rel in commit_paths or rel in baseline_paths or rel in inventory:
                continue
            if self._is_default_excluded_rel(rel):
                continue
            try:
                lexical_workspace_path(str(root), rel)
            except ValueError:
                continue
            try:
                mtime = path.lstat().st_mtime
            except OSError:
                continue
            if mtime < (snap_time - 0.5):
                continue
            strays.append(rel)
        return sorted(set(strays))

    def get_diff(
        self,
        snapshot_id: str,
        compare_to_id: Optional[str] = None,
        working_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Compute unified diff between snapshot and working tree or parent."""
        ok_snap, target_hash, snap, err_snap = self._resolve_candidate_hash(snapshot_id)
        if not ok_snap:
            return {
                "ok": False,
                "snapshot_id": snapshot_id,
                "working_dir": resolve_working_dir(working_dir),
                "error": err_snap or f"Invalid snapshot identifier '{snapshot_id}'.",
            }

        wdir = (
            snap["working_dir"]
            if snap and snap.get("working_dir")
            else resolve_working_dir(working_dir)
        )

        other_snap = None
        base_hash = None
        right_hash = target_hash
        mode = "parent"
        allow_show_fallback = False

        if compare_to_id == "WORKING_TREE":
            mode = "working_tree"
        elif compare_to_id:
            ok_cmp, base_hash, other_snap, err_cmp = self._resolve_candidate_hash(compare_to_id)
            if not ok_cmp:
                return {
                    "ok": False,
                    "snapshot_id": snapshot_id,
                    "compare_to_id": compare_to_id,
                    "working_dir": wdir,
                    "error": err_cmp or f"Invalid compare_to identifier '{compare_to_id}'.",
                }
            mode = "arbitrary"
            # Explicit compare_to_id keeps the historical git-show fallback.
            allow_show_fallback = True
        else:
            post_hash = snap.get("post_checkpoint_hash") if snap else None
            pre_hash = (snap.get("checkpoint_hash") if snap else None) or target_hash
            prev_row = None
            if (
                snap
                and isinstance(post_hash, str)
                and re.fullmatch(r"[0-9a-f]{7,64}", post_hash)
                and isinstance(pre_hash, str)
                and re.fullmatch(r"[0-9a-f]{7,64}", pre_hash)
                and post_hash != pre_hash
                and self._commit_trees_differ(pre_hash, post_hash)
            ):
                base_hash = pre_hash
                right_hash = post_hash
                mode = "turn_diff"
            elif snap and snap.get("session_id"):
                with self._lock, self._connect() as conn:
                    prev_found = conn.execute(
                        """
                        SELECT * FROM snapshots
                        WHERE session_id = ? AND created_at < ? AND checkpoint_hash IS NOT NULL AND checkpoint_hash != ''
                        ORDER BY created_at DESC LIMIT 1
                        """,
                        (snap["session_id"], snap["created_at"]),
                    ).fetchone()
                    if prev_found:
                        prev_row = dict(prev_found)
                if prev_row:
                    p_hash = prev_row.get("checkpoint_hash")
                    snap_hash = snap.get("checkpoint_hash") or target_hash
                    if (
                        p_hash
                        and re.fullmatch(r"[0-9a-f]{7,64}", p_hash)
                        and p_hash != snap_hash
                    ):
                        base_hash = p_hash
                        right_hash = target_hash
                        mode = "parent_snapshot"
                    else:
                        mode = "parent_snapshot"
                else:
                    # No parent snapshot: show the commit itself, not an empty diff.
                    mode = "initial"
                    allow_show_fallback = True
            else:
                mode = "initial"
                allow_show_fallback = True

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
                    ["git", f"--git-dir={store}", f"--work-tree={wdir}", "diff", "--end-of-options", target_hash],
                    capture_output=True,
                    timeout=15,
                    env=env,
                )
                if res.returncode == 0:
                    diff_text = res.stdout.decode("utf-8", "replace")
            except Exception as exc:
                logger.debug("working_tree diff error: %s", exc)
        elif base_hash and right_hash and base_hash != right_hash:
            try:
                res = subprocess.run(
                    ["git", f"--git-dir={store}", "diff", "--end-of-options", base_hash, right_hash],
                    capture_output=True,
                    timeout=15,
                    env=env,
                )
                if res.returncode == 0:
                    diff_text = res.stdout.decode("utf-8", "replace")
            except Exception as exc:
                logger.debug("arbitrary diff error: %s", exc)

        # git show dumps the entire commit. Only use it when there is no parent
        # snapshot to diff against, or when an explicit compare_to_id was given
        # (historical behavior). A turn/parent diff that is empty must stay empty.
        if not diff_text and allow_show_fallback and mode != "working_tree":
            try:
                res = subprocess.run(
                    ["git", f"--git-dir={store}", "show", "--format=", "--end-of-options", target_hash],
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

        if compare_to_id:
            compare_label = compare_to_id
        elif mode == "working_tree":
            compare_label = "WORKING_TREE"
        elif mode == "turn_diff":
            compare_label = "turn_diff"
        else:
            compare_label = "parent"

        return {
            "ok": True,
            "snapshot_id": snap["id"] if snap else snapshot_id,
            "compare_to_id": compare_label,
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
        ok_snap, resolved_commit, snap, err_snap = self._resolve_candidate_hash(snapshot_id)
        if not ok_snap:
            return {
                "ok": False,
                "snapshot_id": snapshot_id,
                "working_dir": resolve_working_dir(working_dir or kwargs.get("session_cwd")),
                "error": err_snap or f"Invalid snapshot identifier '{snapshot_id}'.",
            }
        commit_hash = resolved_commit

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

        target_files: List[str] = []
        # None means "whole workspace". An explicit list, even empty, is surgical
        # and must never fall through into a full restore.
        selection_explicit = file_paths is not None or bool(file_path and str(file_path).strip())
        if file_paths is not None:
            for raw in file_paths:
                cleaned = str(raw).strip() if raw is not None else ""
                if cleaned and cleaned != "(all files)" and cleaned not in target_files:
                    target_files.append(cleaned)
        if file_path and str(file_path).strip() and str(file_path).strip() not in target_files:
            target_files.append(str(file_path).strip())

        if selection_explicit and not target_files:
            return {
                "ok": True,
                "dry_run": bool(dry_run or not user_approved),
                "snapshot_id": snap["id"] if snap else snapshot_id,
                "checkpoint_hash": commit_hash,
                "working_dir": wdir,
                "files_restored": False,
                "planned_restored_files": [],
                "planned_unlinked_files": [],
                "planned_deleted_stray_files": [],
                "restored_files": [],
                "unlinked_new_files": [],
                "message": "No files selected. Nothing was restored.",
            }

        # Path Traversal Guard: lexical paths, so a symlink cannot retarget the write.
        validated_targets = []
        for tf in target_files:
            try:
                abs_target = lexical_workspace_path(wdir, tf)
                rel_target = str(abs_target.relative_to(Path(wdir).resolve())).replace("\\", "/")
                if abs_target.is_symlink():
                    return {
                        "ok": False,
                        "snapshot_id": snapshot_id,
                        "working_dir": wdir,
                        "error": f"Security Error: refusing to restore through symlink {tf!r}",
                    }
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
            ["git", f"--git-dir={store}", "cat-file", "-t", "--end-of-options", str(commit_hash)],
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
        planned_unlinks: List[str] = []

        # Identify files that did not exist in commit_hash (newly created files to unlink).
        # Full workspace restore also drops stray files created after this snapshot,
        # but never .git, secret/bloat excludes, or baseline session_start paths.
        if validated_targets:
            for rel_tf, abs_tf in validated_targets:
                if not file_exists_in_commit(commit_hash, rel_tf, store):
                    if abs_tf.exists():
                        planned_unlinks.append(rel_tf)
        elif snap:
            planned_unlinks = self._collect_post_snapshot_strays(snap, commit_hash, wdir)

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
                        ["git", f"--git-dir={store}", "show", "--end-of-options", f"{commit_hash}:{rel_tf}"],
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
                    try:
                        if unlink_workspace_entry(wdir, rel_tf):
                            unlinked_files.append(rel_tf)
                    except (OSError, ValueError) as rm_err:
                        errors.append(f"Could not remove {rel_tf}: {rm_err}")
        else:
            # Full restoration from store via git archive (safe, never detaches user's HEAD)
            import tarfile
            if not hasattr(tarfile, "data_filter"):
                return {
                    "ok": False,
                    "snapshot_id": snap["id"] if snap else snapshot_id,
                    "checkpoint_hash": commit_hash,
                    "working_dir": wdir,
                    "error": "tarfile.data_filter is required for safe full restore",
                }

            try:
                archive_res = subprocess.run(
                    ["git", f"--git-dir={store}", "archive", "--end-of-options", commit_hash],
                    capture_output=True,
                    timeout=30,
                    env=_minimal_git_env(),
                )
                if archive_res.returncode == 0:
                    import io
                    with tarfile.open(fileobj=io.BytesIO(archive_res.stdout)) as tf_archive:
                        tf_archive.extractall(path=wdir, filter="data")
                    restored_files.append("(all workspace files)")
                    for rel in planned_unlinks:
                        if not rel or ".git" in Path(rel).parts:
                            continue
                        try:
                            if unlink_workspace_entry(wdir, rel):
                                unlinked_files.append(rel)
                        except (OSError, ValueError) as rm_err:
                            errors.append(f"Could not remove {rel}: {rm_err}")
                else:
                    errors.append(f"Full archive extraction failed: {archive_res.stderr.decode('utf-8', 'replace')}")
            except Exception as a_exc:
                return {
                    "ok": False,
                    "snapshot_id": snap["id"] if snap else snapshot_id,
                    "checkpoint_hash": commit_hash,
                    "working_dir": wdir,
                    "error": str(a_exc),
                }

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
        last_failed_command = self._read_failed_command(sess)

        for s in snaps:
            if s.get("status") == "error" or s.get("self_heal_candidate"):
                extracted = _command_text_from_summary(s.get("tool_args_summary") or "")
                if extracted:
                    last_failed_command = extracted
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
            or self._read_failed_command(session_id or "default")
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

    def manual_commit(
        self,
        message: str,
        session_id: str = "default",
        working_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Record a named checkpoint in the private store.

        This is not `git commit` and it does not touch the project's .git.
        """
        text = (message or "").strip()
        if not text:
            return {"ok": False, "error": "A commit message is required. Usage: /tm commit <message>"}
        if _is_remote_git_spec(text):
            return {"ok": False, "error": "Refusing to treat a git remote as a commit message."}
        text = text[:500]
        wdir = resolve_working_dir(working_dir)
        before = self._user_git_head(wdir)
        snap = self.record_pre_tool(
            session_id=session_id,
            tool_name="commit",
            tool_args_summary=text,
            working_dir=wdir,
            reason=text,
        )
        self.record_post_tool(
            session_id=session_id,
            tool_name="commit",
            status="ok",
            tool_args_summary=text,
        )
        after = self._user_git_head(wdir)
        if before != after:
            return {"ok": False, "error": "Refusing to continue: the project git HEAD changed."}
        return {
            "ok": True,
            "snapshot_id": snap.get("id"),
            "checkpoint_hash": snap.get("checkpoint_hash"),
            "working_dir": wdir,
            "message": text,
            "user_git_head": after,
        }

    def set_snapshot_comment(self, snapshot_id: str, comment: str) -> Dict[str, Any]:
        """Attach a human note to a snapshot. Does not write to the project repository."""
        snap = self.get_snapshot(snapshot_id)
        if not snap:
            return {"ok": False, "error": f"Snapshot '{snapshot_id}' does not exist."}
        text = (comment or "").strip()
        if not text:
            return {"ok": False, "error": "A comment is required. Usage: /tm comment <snapshot_id> <text>"}
        text = text[:500]
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO snapshot_comments (snapshot_id, comment, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(snapshot_id) DO UPDATE SET
                    comment = excluded.comment,
                    updated_at = excluded.updated_at
                """,
                (snap["id"], text, time.time()),
            )
        return {"ok": True, "snapshot_id": snap["id"], "comment": text}

    def clone_snapshot(
        self,
        snapshot_id: str,
        destination: str,
        *,
        dry_run: bool = True,
        user_approved: bool = False,
    ) -> Dict[str, Any]:
        """
        Materialize a snapshot into a new directory.

        This is not `git clone`. It refuses remotes, the session workspace, and any .git directory.
        """
        if _is_remote_git_spec(snapshot_id) or _is_remote_git_spec(destination or ""):
            return {
                "ok": False,
                "error": "Time Machine clone copies a snapshot into a new folder. It does not run git clone.",
            }
        ok_snap, commit_hash, snap, err_snap = self._resolve_candidate_hash(snapshot_id)
        if not ok_snap or not commit_hash:
            return {"ok": False, "error": err_snap or f"Snapshot '{snapshot_id}' does not exist."}
        workspace = (snap or {}).get("working_dir") or resolve_working_dir(None)
        try:
            dest = self._validate_clone_destination(destination, workspace)
        except ValueError as exc:
            return {"ok": False, "snapshot_id": snapshot_id, "error": str(exc)}
        files = sorted(self._list_commit_paths(commit_hash))
        if dry_run or not user_approved:
            return {
                "ok": True,
                "dry_run": True,
                "snapshot_id": snap["id"] if snap else snapshot_id,
                "checkpoint_hash": commit_hash,
                "destination": str(dest),
                "planned_files": files,
                "message": "Dry run. No directory was created. Re-run /tm clone to write the folder.",
            }
        try:
            self._extract_commit_to(commit_hash, dest)
        except Exception as exc:
            return {"ok": False, "error": str(exc), "destination": str(dest)}
        return {
            "ok": True,
            "dry_run": False,
            "snapshot_id": snap["id"] if snap else snapshot_id,
            "checkpoint_hash": commit_hash,
            "destination": str(dest),
            "files_written": len(files),
            "user_git_head": self._user_git_head(workspace),
        }

    def push_checkpoint_bundle(
        self,
        destination: str,
        *,
        dry_run: bool = True,
        user_approved: bool = False,
    ) -> Dict[str, Any]:
        """Write a private-store git bundle plus snapshot JSON. Never `git push`."""
        if _is_remote_git_spec(destination or ""):
            return {
                "ok": False,
                "error": "Time Machine push writes a .bundle file. It does not git push the project repository.",
            }
        try:
            bundle = self._validate_bundle_path(destination)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        meta = bundle.with_suffix(".json")
        if dry_run or not user_approved:
            return {
                "ok": True,
                "dry_run": True,
                "bundle": str(bundle),
                "metadata": str(meta),
                "message": "Dry run. No bundle was written.",
            }
        bundle.parent.mkdir(parents=True, exist_ok=True)
        res = subprocess.run(
            ["git", f"--git-dir={self.store_dir}", "bundle", "create", "--end-of-options", str(bundle), "--all"],
            capture_output=True,
            text=True,
            timeout=30,
            env=_minimal_git_env(),
        )
        if res.returncode != 0:
            return {"ok": False, "error": (res.stderr or "git bundle create failed").strip()}
        payload = self._export_checkpoint_payload()
        meta.write_text(json.dumps(payload), encoding="utf-8")
        return {"ok": True, "dry_run": False, "bundle": str(bundle), "metadata": str(meta), "snapshots": len(payload["snapshots"])}

    def pull_checkpoint_bundle(
        self,
        source: str,
        *,
        dry_run: bool = True,
        user_approved: bool = False,
    ) -> Dict[str, Any]:
        """Fetch a private checkpoint bundle into the private store. Never `git pull`."""
        if _is_remote_git_spec(source or ""):
            return {
                "ok": False,
                "error": "Time Machine pull reads a .bundle file. It does not git pull the project repository.",
            }
        try:
            bundle = self._validate_bundle_path(source)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if not bundle.is_file():
            return {"ok": False, "error": f"Bundle not found: {bundle}"}
        verify = subprocess.run(
            ["git", f"--git-dir={self.store_dir}", "bundle", "verify", "--end-of-options", str(bundle)],
            capture_output=True,
            text=True,
            timeout=30,
            env=_minimal_git_env(),
        )
        if verify.returncode != 0:
            return {"ok": False, "error": (verify.stderr or "git bundle verify failed").strip()}
        if dry_run or not user_approved:
            return {
                "ok": True,
                "dry_run": True,
                "bundle": str(bundle),
                "message": "Dry run. The private store was not changed.",
            }
        fetched = subprocess.run(
            [
                "git",
                f"--git-dir={self.store_dir}",
                "fetch",
                "--end-of-options",
                str(bundle),
                "refs/time-machine/*:refs/tm-import/*",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=_minimal_git_env(),
        )
        if fetched.returncode != 0:
            return {"ok": False, "error": (fetched.stderr or "git fetch of bundle failed").strip()}
        imported = self._import_checkpoint_payload(bundle.with_suffix(".json"))
        return {"ok": True, "dry_run": False, "bundle": str(bundle), "imported_snapshots": imported}

    def _user_git_head(self, working_dir: str) -> Optional[str]:
        """Read HEAD from the worktree files. Never invokes git on the project repository."""
        git_dir = Path(working_dir) / ".git"
        head_file = git_dir / "HEAD"
        if not head_file.is_file():
            return None
        try:
            text = head_file.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if text.startswith("ref:"):
            ref = text.split(":", 1)[1].strip()
            if not ref or ref.startswith("/") or ".." in Path(ref).parts:
                return text
            ref_file = git_dir / ref
            try:
                if ref_file.is_file():
                    return ref_file.read_text(encoding="utf-8").strip() or text
            except OSError:
                return text
        return text or None

    def _validate_clone_destination(self, destination: str, workspace: str) -> Path:
        if not destination or not str(destination).strip():
            raise ValueError("A destination directory is required. Usage: /tm clone <snapshot_id> <directory>")
        dest = Path(destination).expanduser().resolve()
        root = Path(workspace).expanduser().resolve()
        if dest == root or root in dest.parents or dest in root.parents:
            raise ValueError("Refusing to clone into the session workspace or one of its parents.")
        if ".git" in dest.parts or dest.name == ".git":
            raise ValueError("Refusing to write inside a .git directory.")
        if (dest / ".git").exists():
            raise ValueError("Refusing to clone into an existing git repository.")
        if dest.exists() and any(dest.iterdir()):
            raise ValueError("Destination already exists and is not empty.")
        return dest

    def _validate_bundle_path(self, destination: str) -> Path:
        if not destination or not str(destination).strip():
            raise ValueError("A .bundle path is required.")
        path = Path(destination).expanduser().resolve()
        if path.suffix != ".bundle":
            raise ValueError("Bundle path must end with .bundle")
        if ".git" in path.parts:
            raise ValueError("Refusing to read or write inside a .git directory.")
        return path

    def _extract_commit_to(self, commit_hash: str, dest: Path) -> None:
        import io
        import tarfile

        if not hasattr(tarfile, "data_filter"):
            raise RuntimeError("tarfile.data_filter is required for safe extract")
        archive_res = subprocess.run(
            ["git", f"--git-dir={self.store_dir}", "archive", "--end-of-options", commit_hash],
            capture_output=True,
            timeout=30,
            env=_minimal_git_env(),
        )
        if archive_res.returncode != 0:
            raise RuntimeError(archive_res.stderr.decode("utf-8", "replace"))
        dest.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(archive_res.stdout)) as tf_archive:
            tf_archive.extractall(path=dest, filter="data")

    def _export_checkpoint_payload(self) -> Dict[str, Any]:
        with self._lock, self._connect() as conn:
            snaps = [dict(row) for row in conn.execute("SELECT * FROM snapshots").fetchall()]
            comments = [dict(row) for row in conn.execute("SELECT * FROM snapshot_comments").fetchall()]
        return {"version": 1, "snapshots": snaps, "comments": comments}

    def _import_checkpoint_payload(self, metadata: Path) -> int:
        if not metadata.is_file():
            return 0
        try:
            payload = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0
        snaps = payload.get("snapshots") if isinstance(payload, dict) else None
        comments = payload.get("comments") if isinstance(payload, dict) else None
        imported = 0
        with self._lock, self._connect() as conn:
            for snap in snaps or []:
                if not isinstance(snap, dict) or not snap.get("id"):
                    continue
                exists = conn.execute("SELECT 1 FROM snapshots WHERE id = ?", (snap["id"],)).fetchone()
                if exists:
                    continue
                conn.execute(
                    """
                    INSERT INTO snapshots (
                        id, session_id, turn_id, tool_call_id, tool_name,
                        tool_args_summary, working_dir, checkpoint_hash, post_checkpoint_hash,
                        status, reason, files_changed, insertions, deletions, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snap.get("id"),
                        snap.get("session_id") or "import",
                        snap.get("turn_id"),
                        snap.get("tool_call_id"),
                        snap.get("tool_name") or "import",
                        snap.get("tool_args_summary") or "",
                        snap.get("working_dir") or "",
                        snap.get("checkpoint_hash"),
                        snap.get("post_checkpoint_hash"),
                        snap.get("status") or "ok",
                        snap.get("reason"),
                        int(snap.get("files_changed") or 0),
                        int(snap.get("insertions") or 0),
                        int(snap.get("deletions") or 0),
                        float(snap.get("created_at") or time.time()),
                    ),
                )
                imported += 1
            for comment in comments or []:
                if not isinstance(comment, dict) or not comment.get("snapshot_id"):
                    continue
                conn.execute(
                    """
                    INSERT INTO snapshot_comments (snapshot_id, comment, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(snapshot_id) DO NOTHING
                    """,
                    (comment.get("snapshot_id"), comment.get("comment") or "", float(comment.get("updated_at") or time.time())),
                )
        return imported

