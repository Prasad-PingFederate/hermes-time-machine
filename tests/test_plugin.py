"""
Comprehensive Unit & Integration Test Suite for hermes-time-machine (Phase 2).
Covers:
  - Structured unified diff parsing
  - Surgical single/multi-file restore
  - Pre-restore safety snapshot creation ("rollback of a rollback")
  - Self-healing hook & hint injection
  - Slash command /rewind dispatch
  - FastAPI dashboard endpoints
  - Plugin registration compliance
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ledger import TimeMachineLedger, parse_unified_diff
from tools import (
    LIST_SNAPSHOTS_SCHEMA,
    DIFF_SCHEMA,
    RESTORE_SCHEMA,
    BRANCH_SCHEMA,
    handle_list_snapshots,
    handle_diff,
    handle_restore,
    handle_branch,
)
from dashboard.plugin_api import router
import __init__ as tm_plugin


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp(prefix="tm_test_")
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def git_repo(temp_dir):
    """Create a temporary git repository with initial commits."""
    env = os.environ.copy()
    subprocess.run(["git", "init"], cwd=str(temp_dir), env=env, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(temp_dir), env=env, check=True)
    subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=str(temp_dir), env=env, check=True)

    f1 = temp_dir / "file_a.py"
    f2 = temp_dir / "file_b.py"
    f1.write_text("print('version 1 A')", encoding="utf-8")
    f2.write_text("print('version 1 B')", encoding="utf-8")

    subprocess.run(["git", "add", "."], cwd=str(temp_dir), env=env, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(temp_dir), env=env, check=True, capture_output=True)
    c1 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(temp_dir), env=env, check=True, capture_output=True, text=True).stdout.strip()

    return {"dir": temp_dir, "c1": c1, "f1": f1, "f2": f2}


@pytest.fixture
def test_ledger(temp_dir):
    db_file = temp_dir / "time_machine.db"
    return TimeMachineLedger(db_path=db_file)


# ─── 1. Structured Diff Parsing ──────────────────────────────────────────────

def test_parse_unified_diff():
    sample_diff = """diff --git a/src/auth.py b/src/auth.py
index 0000000..1111111 100644
--- a/src/auth.py
+++ b/src/auth.py
@@ -1,3 +1,5 @@
-old line
+new line 1
+new line 2
+new line 3
diff --git a/test.txt b/test.txt
new file mode 100644
--- /dev/null
+++ b/test.txt
@@ -0,0 +1 @@
+created
"""
    files = parse_unified_diff(sample_diff)
    assert len(files) == 2

    f1 = files[0]
    assert f1["path"] == "src/auth.py"
    assert f1["status"] == "modified"
    assert f1["additions"] == 3
    assert f1["deletions"] == 1
    assert "@@ -1,3 +1,5 @@" in f1["diff_chunk"]

    f2 = files[1]
    assert f2["path"] == "test.txt"
    assert f2["status"] == "added"
    assert f2["additions"] == 1
    assert f2["deletions"] == 0


# ─── 2. Ledger Recording & Self-Healing Flag ─────────────────────────────────

def test_ledger_record_and_self_heal(test_ledger, temp_dir):
    # Record mutating tool
    snap1 = test_ledger.record_pre_tool(
        session_id="s_heal",
        tool_name="write_file",
        args={"path": "main.py"},
        working_dir=str(temp_dir),
    )
    assert snap1 is not None
    s1_id = snap1["id"]

    test_ledger.record_post_tool(
        session_id="s_heal",
        tool_name="write_file",
        args={"path": "main.py"},
        status="ok",
    )

    # Next tool is a command that fails
    test_ledger.record_pre_tool(
        session_id="s_heal",
        tool_name="terminal",
        args={"command": "pytest"},
        working_dir=str(temp_dir),
    )
    test_ledger.record_post_tool(
        session_id="s_heal",
        tool_name="terminal",
        args={"command": "pytest"},
        status="error",
        self_heal_candidate=True,
        recommended_snapshot_id=s1_id,
    )

    snaps = test_ledger.list_snapshots(session_id="s_heal")
    assert len(snaps) >= 2
    latest = snaps[0]
    assert latest["tool_name"] == "terminal"
    assert latest["self_heal_candidate"] == 1
    assert latest["recommended_snapshot_id"] == s1_id


# ─── 3. Pre-Restore Safety Snapshot & Surgical File Restore ──────────────────

def test_surgical_file_restore_and_safety_backup(git_repo, test_ledger):
    wdir = git_repo["dir"]
    c1 = git_repo["c1"]
    f1 = git_repo["f1"]
    f2 = git_repo["f2"]

    # Modify both files
    f1.write_text("print('version 2 A BROKEN')", encoding="utf-8")
    f2.write_text("print('version 2 B GOOD')", encoding="utf-8")

    # Record snapshot at c1
    snap = test_ledger.record_pre_tool(
        session_id="s_surg",
        tool_name="write_file",
        args={"path": "file_a.py"},
        working_dir=str(wdir),
    )
    # Associate c1 commit
    with test_ledger._connect() as conn:
        conn.execute("UPDATE snapshots SET checkpoint_hash = ? WHERE id = ?", (c1, snap["id"]))

    # Surgically restore only file_a.py
    res = test_ledger.restore_snapshot(
        snapshot_id=snap["id"],
        file_paths=["file_a.py"],
        working_dir=str(wdir),
    )

    assert res["ok"] is True
    assert res["files_restored"] is True
    assert res["pre_restore_backup_id"] is not None  # Auto safety snapshot captured!

    # file_a.py was reverted to version 1
    assert f1.read_text(encoding="utf-8") == "print('version 1 A')"
    # file_b.py was preserved in version 2
    assert f2.read_text(encoding="utf-8") == "print('version 2 B GOOD')"


# ─── 4. Tool Handlers & Confirmation Guard ───────────────────────────────────

def test_tool_handlers_and_confirmation(test_ledger, temp_dir):
    snap = test_ledger.record_pre_tool(
        session_id="s_tools",
        tool_name="patch",
        args={"patch": "test"},
        working_dir=str(temp_dir),
    )
    snap_id = snap["id"]

    # Diff
    diff_res = json.loads(handle_diff({"snapshot_id": snap_id}, ledger=test_ledger))
    assert "ok" in diff_res

    # Restore rejected when confirm=False
    res_no_confirm = json.loads(handle_restore({"snapshot_id": snap_id, "confirm": False}, ledger=test_ledger))
    assert res_no_confirm["ok"] is False
    assert "confirm" in res_no_confirm["error"].lower()

    # Branch
    b_res = json.loads(handle_branch({"snapshot_id": snap_id, "branch_title": "Phase 2 Branch"}, ledger=test_ledger))
    assert b_res["ok"] is True
    assert "new_session_id" in b_res


# ─── 5. Slash Command /rewind ────────────────────────────────────────────────

def test_slash_command_rewind(test_ledger, temp_dir):
    test_ledger.record_pre_tool(
        session_id="s_cmd",
        tool_name="edit_file",
        args={"path": "api.py"},
        working_dir=str(temp_dir),
    )

    # Test /rewind list
    class CtxStub:
        def __init__(self):
            self.command_handler = None
            self.hooks = []
            self.tools = []
        def register_hook(self, name, cb, **kw): self.hooks.append(name)
        def register_tool(self, name, *a, **kw): self.tools.append(name)
        def register_command(self, name, handler, **kw): self.command_handler = handler
        def register_system_prompt_section(self, *a, **kw): pass

    stub = CtxStub()
    tm_plugin.register(stub, ledger=test_ledger)
    assert stub.command_handler is not None

    out_list = stub.command_handler("list", session_id="s_cmd")
    assert "Time Machine Snapshots" in out_list

    out_diff = stub.command_handler("diff", session_id="s_cmd")
    assert "Diff for" in out_diff or "Error" in out_diff


# ─── 6. Self-Healing Hint Injection ──────────────────────────────────────────

def test_self_healing_transform_hint(temp_dir):
    class CtxStub:
        def __init__(self):
            self.hooks = {}
        def register_hook(self, name, cb, **kw): self.hooks[name] = cb
        def register_tool(self, *a, **kw): pass
        def register_command(self, *a, **kw): pass
        def register_system_prompt_section(self, *a, **kw): pass

    stub = CtxStub()
    tm_plugin.register(stub)

    # Simulate session start and file mutation
    stub.hooks["on_session_start"](session_id="s_hint")
    stub.hooks["pre_tool_call"](tool_name="write_file", args={"path": "x.py"}, session_id="s_hint")
    stub.hooks["post_tool_call"](tool_name="write_file", args={"path": "x.py"}, status="ok", session_id="s_hint")

    # Command fails with pytest output
    fail_output = "================ FAILURES ================\nAssertionError: test failed"
    transformed = stub.hooks["transform_tool_result"](
        tool_name="terminal",
        result=fail_output,
        session_id="s_hint",
    )
    assert "[🛡️ Time Machine Self-Heal Notice]" in transformed
    assert "time_machine_restore" in transformed


# ─── 7. FastAPI Dashboard Router ─────────────────────────────────────────────

def test_dashboard_api_endpoints(test_ledger):
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Status
    res = client.get("/status")
    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert "snapshot_count" in res.json()

    # Snapshots list
    res = client.get("/snapshots")
    assert res.status_code == 200
    assert "snapshots" in res.json()
