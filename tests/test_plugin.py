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
    assert "[🛡️ Time Machine Self-Heal Notice" in transformed
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


# ─── 8. Path Traversal Security & Newly-Created File Rollback ─────────────────

def test_path_traversal_rejection(test_ledger, temp_dir):
    snap = test_ledger.record_pre_tool(
        session_id="s_sec",
        tool_name="write_file",
        args={"path": "safe.py"},
        working_dir=str(temp_dir),
    )
    # Attempt directory traversal outside temp_dir
    res = test_ledger.restore_snapshot(
        snapshot_id=snap["id"],
        file_paths=["../../etc/passwd"],
        working_dir=str(temp_dir),
    )
    assert res["ok"] is False
    assert "escapes workspace root" in res["error"]


def test_newly_created_file_unlinked_on_restore(git_repo, test_ledger):
    wdir = git_repo["dir"]
    c1 = git_repo["c1"]

    snap = test_ledger.record_pre_tool(
        session_id="s_newfile",
        tool_name="write_file",
        args={"path": "file_a.py"},
        working_dir=str(wdir),
    )
    with test_ledger._connect() as conn:
        conn.execute("UPDATE snapshots SET checkpoint_hash = ? WHERE id = ?", (c1, snap["id"]))

    # Now create a brand-new file that NEVER existed in c1
    new_file = wdir / "unwanted_generated.tmp"
    new_file.write_text("temporary bad dump", encoding="utf-8")
    assert new_file.exists()

    # Revert file_a.py AND unwanted_generated.tmp
    res = test_ledger.restore_snapshot(
        snapshot_id=snap["id"],
        file_paths=["file_a.py", "unwanted_generated.tmp"],
        working_dir=str(wdir),
    )
    assert res["ok"] is True
    assert "unwanted_generated.tmp" in res["unlinked_new_files"]
    assert not new_file.exists()  # Cleanly removed from disk!


# ─── 9. Arbitrary A <-> B & Live Working Tree Diff ───────────────────────────

def test_arbitrary_and_working_tree_diff(git_repo, test_ledger):
    wdir = git_repo["dir"]
    c1 = git_repo["c1"]
    f1 = git_repo["f1"]

    # Make change and commit c2
    f1.write_text("print('version 2')", encoding="utf-8")
    env = os.environ.copy()
    subprocess.run(["git", "add", "."], cwd=str(wdir), env=env, check=True)
    subprocess.run(["git", "commit", "-m", "second"], cwd=str(wdir), env=env, check=True)
    c2 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(wdir), env=env, check=True, capture_output=True, text=True).stdout.strip()

    snap1 = test_ledger.record_pre_tool(session_id="s_diff", tool_name="edit", args={}, working_dir=str(wdir))
    snap2 = test_ledger.record_pre_tool(session_id="s_diff", tool_name="edit", args={}, working_dir=str(wdir))
    with test_ledger._connect() as conn:
        conn.execute("UPDATE snapshots SET checkpoint_hash = ? WHERE id = ?", (c1, snap1["id"]))
        conn.execute("UPDATE snapshots SET checkpoint_hash = ? WHERE id = ?", (c2, snap2["id"]))

    # Test arbitrary A <-> B diff
    diff_ab = test_ledger.get_diff(snapshot_id=snap2["id"], compare_to_id=snap1["id"], working_dir=str(wdir))
    assert diff_ab["ok"] is True
    assert diff_ab["mode"] == "arbitrary"
    assert any(f["path"] == "file_a.py" for f in diff_ab.get("files", []))

    # Test uncommitted live drift against WORKING_TREE
    f1.write_text("print('version 3 uncommitted')", encoding="utf-8")
    diff_wt = test_ledger.get_diff(snapshot_id=snap2["id"], compare_to_id="WORKING_TREE", working_dir=str(wdir))
    assert diff_wt["ok"] is True
    assert diff_wt["mode"] == "working_tree"
    assert "version 3 uncommitted" in diff_wt["diff"]


# ─── 10. Storage Pruning & Storage Stats ─────────────────────────────────────

def test_prune_snapshots_and_storage_stats(test_ledger, temp_dir):
    # Create 15 regular snapshots and 1 protected safety backup
    for i in range(15):
        test_ledger.record_pre_tool(
            session_id="s_prune",
            tool_name="edit_file",
            args={"index": i},
            working_dir=str(temp_dir),
        )

    prot_snap = test_ledger.record_pre_tool(
        session_id="s_prune",
        tool_name="pre_restore_safety_backup",
        args={"target": "baseline"},
        working_dir=str(temp_dir),
    )

    stats = test_ledger.get_storage_stats()
    assert stats["total_snapshots"] >= 16
    assert stats["total_sessions"] >= 1

    prune_res = test_ledger.prune_snapshots(session_id="s_prune", keep_last=5)
    assert prune_res["ok"] is True
    assert prune_res["pruned_count"] > 0

    # Ensure protected safety snapshot was NOT pruned
    remaining = test_ledger.list_snapshots(session_id="s_prune", limit=100)
    remaining_ids = [s["id"] for s in remaining]
    assert prot_snap["id"] in remaining_ids


# ─── 11. Live Demo Walkthrough Integration & Slash Command ───────────────────

def test_demo_walkthrough_e2e():
    from scripts.demo_walkthrough import run_demo
    demo_result = run_demo(quiet=True)
    assert demo_result["ok"] is True
    steps = demo_result["steps"]
    assert steps["step1_baseline"]["pytest_passed"] is True
    assert steps["step3_bad_edit"]["test_failed_as_expected"] is True
    assert steps["step5_restore"]["billing_restored"] is True
    assert steps["step5_restore"]["new_file_unlinked"] is True
    assert steps["step5_restore"]["tax_feature_preserved"] is True
    assert steps["step5_restore"]["pytest_passed"] is True


def test_slash_command_rewind_demo(test_ledger):
    class CtxStub:
        def __init__(self):
            self.command_handler = None
        def register_hook(self, *a, **kw): pass
        def register_tool(self, *a, **kw): pass
        def register_command(self, name, handler, **kw): self.command_handler = handler
        def register_system_prompt_section(self, *a, **kw): pass

    stub = CtxStub()
    tm_plugin.register(stub, ledger=test_ledger)
    out_demo = stub.command_handler("demo")
    assert "🎉 Time Machine E2E Live Demo Passed 100%!" in out_demo


# ─── 12. Autonomous Self-Healing Recovery & Diagnosis ────────────────────────

def test_diagnose_failure_and_culprit_detection(git_repo, test_ledger):
    wdir = git_repo["dir"]
    c1 = git_repo["c1"]
    f1 = git_repo["f1"]

    # Baseline safe snapshot
    snap_safe = test_ledger.record_pre_tool(
        session_id="s_diag",
        tool_name="write_file",
        args={"path": "file_a.py"},
        working_dir=str(wdir),
    )
    with test_ledger._connect() as conn:
        conn.execute("UPDATE snapshots SET checkpoint_hash = ? WHERE id = ?", (c1, snap_safe["id"]))
    test_ledger.record_post_tool(session_id="s_diag", tool_name="write_file", status="ok")

    # Agent edits file_a.py breaking something
    f1.write_text("broken syntax error", encoding="utf-8")

    # Command fails with traceback mentioning file_a.py
    test_ledger.record_pre_tool(
        session_id="s_diag",
        tool_name="terminal",
        args={"command": "pytest"},
        working_dir=str(wdir),
    )
    traceback_err = "File 'file_a.py', line 1, in <module>\nSyntaxError: invalid syntax"
    test_ledger.record_post_tool(
        session_id="s_diag",
        tool_name="terminal",
        status="error",
        self_heal_candidate=True,
        recommended_snapshot_id=snap_safe["id"],
    )

    diag = test_ledger.diagnose_failure(session_id="s_diag", error_text=traceback_err, working_dir=str(wdir))
    assert diag["ok"] is True
    assert "file_a.py" in diag["culprit_files"]
    assert diag["confidence"] == "high"
    assert diag["recommended_snapshot_id"] == snap_safe["id"]


def test_time_machine_recover_auto_apply(git_repo, test_ledger):
    wdir = git_repo["dir"]
    c1 = git_repo["c1"]
    f1 = git_repo["f1"]
    f2 = git_repo["f2"]

    # Safe snapshot at c1
    snap = test_ledger.record_pre_tool(session_id="s_rec", tool_name="edit_file", args={"path": "file_a.py"}, working_dir=str(wdir))
    with test_ledger._connect() as conn:
        conn.execute("UPDATE snapshots SET checkpoint_hash = ? WHERE id = ?", (c1, snap["id"]))
    test_ledger.record_post_tool(session_id="s_rec", tool_name="edit_file", status="ok")

    # Broken edit to file_a.py and good edit to file_b.py
    f1.write_text("BROKEN", encoding="utf-8")
    f2.write_text("GOOD PRESERVED", encoding="utf-8")

    # Command fails
    test_ledger.record_pre_tool(session_id="s_rec", tool_name="terminal", args={"command": "pytest"}, working_dir=str(wdir))
    test_ledger.record_post_tool(
        session_id="s_rec",
        tool_name="terminal",
        status="error",
        self_heal_candidate=True,
        recommended_snapshot_id=snap["id"],
    )

    # 1. auto_apply=False returns plan only
    plan = test_ledger.recover(session_id="s_rec", error_summary="file_a.py failed", auto_apply=False)
    assert plan["ok"] is True
    assert plan["auto_applied"] is False

    # 2. auto_apply=True without confirm is rejected
    res_no_conf = test_ledger.recover(session_id="s_rec", error_summary="file_a.py failed", auto_apply=True, confirm=False)
    assert res_no_conf["ok"] is False

    # 3. auto_apply=True with confirm=True executes surgical recovery
    res_rec = test_ledger.recover(session_id="s_rec", error_summary="file_a.py failed", auto_apply=True, confirm=True, working_dir=str(wdir))
    assert res_rec["ok"] is True
    assert res_rec["auto_applied"] is True
    assert "file_a.py" in res_rec["restored_files"]
    assert res_rec["pre_restore_backup_id"] is not None

    # file_a.py reverted, file_b.py preserved
    assert f1.read_text(encoding="utf-8") == "print('version 1 A')"
    assert f2.read_text(encoding="utf-8") == "GOOD PRESERVED"


def test_time_machine_recover_tool_handler(git_repo, test_ledger):
    from tools import handle_recover
    wdir = git_repo["dir"]
    c1 = git_repo["c1"]

    snap = test_ledger.record_pre_tool(session_id="s_tool_rec", tool_name="write_file", args={}, working_dir=str(wdir))
    with test_ledger._connect() as conn:
        conn.execute("UPDATE snapshots SET checkpoint_hash = ? WHERE id = ?", (c1, snap["id"]))

    res_json = handle_recover(
        {"session_id": "s_tool_rec", "error_summary": "file_a.py error", "auto_apply": False},
        ledger=test_ledger,
    )
    res = json.loads(res_json)
    assert res["ok"] is True
    assert "diagnosis" in res


def test_api_recover_and_export_endpoints(test_ledger, temp_dir):
    test_ledger.record_pre_tool(session_id="s_api", tool_name="patch", args={}, working_dir=str(temp_dir))

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Test export timeline markdown
    res_exp = client.get("/snapshots/export?session_id=s_api")
    assert res_exp.status_code == 200
    assert "Time Machine Timeline Report" in res_exp.json()["markdown"]

    # Test recover endpoint
    res_rec = client.post("/snapshots/recover", json={"session_id": "s_api", "auto_apply": False})
    assert res_rec.status_code == 200
    assert res_rec.json()["ok"] is True


def test_slash_command_recover_and_export(test_ledger, temp_dir):
    test_ledger.record_pre_tool(session_id="s_slash", tool_name="write_file", args={"path": "a.py"}, working_dir=str(temp_dir))

    class CtxStub:
        def __init__(self):
            self.command_handler = None
        def register_hook(self, *a, **kw): pass
        def register_tool(self, *a, **kw): pass
        def register_command(self, name, handler, **kw): self.command_handler = handler
        def register_system_prompt_section(self, *a, **kw): pass

    stub = CtxStub()
    tm_plugin.register(stub, ledger=test_ledger)

    out_export = stub.command_handler("export", session_id="s_slash")
    assert "Hermes Time Machine Timeline Report" in out_export


def test_search_snapshots_diff_content(git_repo, test_ledger):
    wdir = git_repo["dir"]
    c1 = git_repo["c1"]
    f1 = git_repo["f1"]
    f2 = git_repo["f2"]

    # Commit 1 snapshot
    s1 = test_ledger.record_pre_tool(session_id="s_search", tool_name="write_file", args={"path": "file_a.py"}, working_dir=str(wdir))
    with test_ledger._connect() as conn:
        conn.execute("UPDATE snapshots SET checkpoint_hash = ? WHERE id = ?", (c1, s1["id"]))

    # Commit 2: change file_a and add something to file_b
    f1.write_text("print('version 2 ALPHA')", encoding="utf-8")
    f2.write_text("print('version 2 BETA UNIQUE_MARKER')", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(wdir), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "second commit"], cwd=str(wdir), check=True, capture_output=True)
    c2 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(wdir), check=True, capture_output=True, text=True).stdout.strip()

    s2 = test_ledger.record_pre_tool(session_id="s_search", tool_name="edit_file", args={"path": "file_b.py"}, working_dir=str(wdir))
    with test_ledger._connect() as conn:
        conn.execute("UPDATE snapshots SET checkpoint_hash = ? WHERE id = ?", (c2, s2["id"]))

    # 1. Search for unique symbol in diff
    res = test_ledger.search_snapshots(query="UNIQUE_MARKER", session_id="s_search", working_dir=str(wdir))
    assert res["ok"] is True
    assert res["total_matches"] >= 1
    match0 = res["matches"][0]
    assert match0["snapshot_id"] == s2["id"]
    assert "file_b.py" in match0["matched_files"]

    # 2. Search scoped to file_path
    res_scoped = test_ledger.search_snapshots(query="version", file_path="file_a.py", session_id="s_search", working_dir=str(wdir))
    assert res_scoped["ok"] is True
    for m in res_scoped["matches"]:
        for f in m["matched_files"]:
            assert "file_a.py" in f

    # 3. Empty query error
    res_empty = test_ledger.search_snapshots(query="")
    assert res_empty["ok"] is False


def test_time_machine_search_tool_handler(git_repo, test_ledger):
    from tools import handle_search
    wdir = git_repo["dir"]
    c1 = git_repo["c1"]

    s1 = test_ledger.record_pre_tool(session_id="s_tool_search", tool_name="write_file", args={"path": "file_a.py"}, working_dir=str(wdir))
    with test_ledger._connect() as conn:
        conn.execute("UPDATE snapshots SET checkpoint_hash = ? WHERE id = ?", (c1, s1["id"]))

    res_json = handle_search({"query": "version 1", "session_id": "s_tool_search"}, ledger=test_ledger)
    res = json.loads(res_json)
    assert res["ok"] is True
    assert "total_matches" in res


def test_recover_auto_verify(git_repo, test_ledger):
    wdir = git_repo["dir"]
    c1 = git_repo["c1"]
    f1 = git_repo["f1"]

    snap = test_ledger.record_pre_tool(session_id="s_rec_verify", tool_name="edit_file", args={"path": "file_a.py"}, working_dir=str(wdir))
    with test_ledger._connect() as conn:
        conn.execute("UPDATE snapshots SET checkpoint_hash = ? WHERE id = ?", (c1, snap["id"]))
    test_ledger.record_post_tool(session_id="s_rec_verify", tool_name="edit_file", status="ok")

    # Corrupt file_a
    f1.write_text("CORRUPTED SYNTAX ERROR {{{", encoding="utf-8")

    # Terminal failure recorded
    test_ledger.record_pre_tool(session_id="s_rec_verify", tool_name="terminal", args={"command": "python -c \"import sys; sys.exit(1)\""}, working_dir=str(wdir))
    test_ledger.record_post_tool(
        session_id="s_rec_verify",
        tool_name="terminal",
        status="error",
        self_heal_candidate=True,
        recommended_snapshot_id=snap["id"],
    )

    # Recovery with passing verify_command
    res = test_ledger.recover(
        session_id="s_rec_verify",
        error_summary="syntax error in file_a.py",
        auto_apply=True,
        confirm=True,
        auto_verify=True,
        verify_command=f'"{sys.executable}" -c "import sys; sys.exit(0)"',
        working_dir=str(wdir),
    )
    assert res["ok"] is True
    assert res["auto_applied"] is True
    assert res["verification"]["passed"] is True
    assert res["verification"]["exit_code"] == 0

    # Recovery with failing verify_command
    res_fail = test_ledger.recover(
        session_id="s_rec_verify",
        error_summary="syntax error in file_a.py",
        auto_apply=True,
        confirm=True,
        auto_verify=True,
        verify_command=f'"{sys.executable}" -c "import sys; sys.exit(2)"',
        working_dir=str(wdir),
    )
    assert res_fail["ok"] is True
    assert res_fail["verification"]["passed"] is False
    assert res_fail["verification"]["exit_code"] == 2


def test_api_search_endpoint(test_ledger, temp_dir):
    test_ledger.record_pre_tool(session_id="s_api_search", tool_name="edit_file", args={"path": "main.py"}, working_dir=str(temp_dir))

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # Query with match or empty result
    res = client.get("/snapshots/search?q=main&session_id=s_api_search")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["query"] == "main"
    assert "matches" in data


def test_slash_command_search(test_ledger, temp_dir):
    test_ledger.record_pre_tool(session_id="s_slash_search", tool_name="write_file", args={"path": "calc.py"}, working_dir=str(temp_dir))

    class CtxStub:
        def __init__(self):
            self.command_handler = None
        def register_hook(self, *a, **kw): pass
        def register_tool(self, *a, **kw): pass
        def register_command(self, name, handler, **kw): self.command_handler = handler
        def register_system_prompt_section(self, *a, **kw): pass

    stub = CtxStub()
    tm_plugin.register(stub, ledger=test_ledger)

    out = stub.command_handler("search calc", session_id="s_slash_search")
    assert "search results for" in out.lower()
