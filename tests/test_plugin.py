"""
Comprehensive Unit & Integration Test Suite for hermes-time-machine (v0.4.0).
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
import time
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
)
from tools import (
    LIST_SNAPSHOTS_SCHEMA,
    DIFF_SCHEMA,
    RESTORE_SCHEMA,
    RECOVER_SCHEMA,
    SEARCH_SCHEMA,
    handle_list_snapshots,
    handle_diff,
    handle_restore,
    handle_recover,
    handle_search,
)
import __init__ as tm_plugin


class TestTimeMachineSuite(unittest.TestCase):
    """Full test suite covering Time Machine tools, ledger, security & self-heal."""

    def setUp(self):
        self.base_test_dir = Path(tempfile.mkdtemp(prefix="tm_test_"))
        self.temp_dir = self.base_test_dir / "workspace"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.plugin_data_dir = self.base_test_dir / "plugin-data" / "time-machine"
        self.plugin_data_dir.mkdir(parents=True, exist_ok=True)
        self.db_file = self.plugin_data_dir / "ledger.db"
        self.ledger = TimeMachineLedger(db_path=self.db_file, plugin_data_dir=self.plugin_data_dir)
        self.git_repo = self._init_git_repo(self.temp_dir)
        if HAS_FASTAPI:
            from dashboard.plugin_api import set_ledger
            set_ledger(self.ledger)

    def tearDown(self):
        if HAS_FASTAPI:
            from dashboard.plugin_api import set_ledger
            set_ledger(None)
        shutil.rmtree(str(self.base_test_dir), ignore_errors=True)

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
        f1 = self.git_repo["f1"]
        f2 = self.git_repo["f2"]

        f1.write_text("print('version 1 A')", encoding="utf-8")
        f2.write_text("print('version 1 B')", encoding="utf-8")

        snap = self.ledger.record_pre_tool(
            session_id="s_surg",
            tool_name="write_file",
            args={"path": "file_a.py"},
            working_dir=str(wdir),
        )

        f1.write_text("print('version 2 A BROKEN')", encoding="utf-8")
        f2.write_text("print('version 2 B GOOD')", encoding="utf-8")

        res = self.ledger.restore_snapshot(
            snapshot_id=snap["id"],
            file_paths=["file_a.py"],
            working_dir=str(wdir),
            dry_run=False,
            user_approved=True,
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

        # Default restoration acts as dry-run preview requiring user approval
        res_dry = json.loads(handle_restore({"snapshot_id": snap_id}, ledger=self.ledger))
        self.assertTrue(res_dry["ok"])
        self.assertTrue(res_dry.get("dry_run", False))
        self.assertFalse(res_dry.get("files_restored", False))

        rec_res = json.loads(handle_recover({"session_id": "s_tools"}, ledger=self.ledger))
        self.assertTrue(rec_res["ok"])
        self.assertIn("suggested_verify_command", rec_res)

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
                self.command_name = None
            def register_hook(self, *a, **kw): pass
            def register_tool(self, *a, **kw): pass
            def register_command(self, name, handler, **kw):
                self.command_name = name
                self.command_handler = handler
            def register_system_prompt_section(self, *a, **kw): pass

        stub = CtxStub()
        tm_plugin.register(stub, ledger=self.ledger)
        self.assertEqual(stub.command_name, "tm")
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
            dry_run=False,
            user_approved=True,
        )
        self.assertFalse(res["ok"])
        self.assertIn("escapes workspace root", res["error"])

    def test_newly_created_file_unlinked_on_restore(self):
        wdir = self.git_repo["dir"]
        f1 = self.git_repo["f1"]

        f1.write_text("print('version 1 A')", encoding="utf-8")
        snap = self.ledger.record_pre_tool(
            session_id="s_newfile",
            tool_name="write_file",
            args={"path": "file_a.py"},
            working_dir=str(wdir),
        )

        new_file = wdir / "unwanted_generated.tmp"
        new_file.write_text("temporary bad dump", encoding="utf-8")
        self.assertTrue(new_file.exists())

        res = self.ledger.restore_snapshot(
            snapshot_id=snap["id"],
            file_paths=["file_a.py", "unwanted_generated.tmp"],
            working_dir=str(wdir),
            dry_run=False,
            user_approved=True,
        )
        self.assertTrue(res["ok"])
        self.assertIn("unwanted_generated.tmp", res["unlinked_new_files"])
        self.assertFalse(new_file.exists())

    # ─── 9. Arbitrary A <-> B & Live Working Tree Diff ───────────────────────

    def test_arbitrary_and_working_tree_diff(self):
        wdir = self.git_repo["dir"]
        f1 = self.git_repo["f1"]

        f1.write_text("print('version 1 A')", encoding="utf-8")
        snap1 = self.ledger.record_pre_tool(session_id="s_diff", tool_name="edit_file", args={"path": "file_a.py"}, working_dir=str(wdir))

        f1.write_text("print('version 2 A')", encoding="utf-8")
        snap2 = self.ledger.record_pre_tool(session_id="s_diff", tool_name="edit_file", args={"path": "file_a.py"}, working_dir=str(wdir))

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
        f1 = self.git_repo["f1"]

        snap_safe = self.ledger.record_pre_tool(
            session_id="s_diag",
            tool_name="write_file",
            args={"path": "file_a.py"},
            working_dir=str(wdir),
        )
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
        f1 = self.git_repo["f1"]
        f2 = self.git_repo["f2"]

        f1.write_text("print('version 1 A')", encoding="utf-8")
        f2.write_text("print('version 1 B')", encoding="utf-8")

        snap = self.ledger.record_pre_tool(session_id="s_rec", tool_name="edit_file", args={"path": "file_a.py"}, working_dir=str(wdir))
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

        # 1. Default recovery runs in dry-run mode
        plan = self.ledger.recover(session_id="s_rec", error_summary="file_a.py failed", dry_run=True, user_approved=False)
        self.assertTrue(plan["ok"])
        self.assertFalse(plan.get("restored", False))
        self.assertTrue(plan.get("dry_run", False))
        self.assertIn("file_a.py", plan.get("planned_restored_files", []))
        self.assertIn("suggested_verify_command", plan)

        # 2. Approved recovery executes surgical recovery without running host test runner
        res_rec = self.ledger.recover(session_id="s_rec", error_summary="file_a.py failed", dry_run=False, user_approved=True, working_dir=str(wdir))
        self.assertTrue(res_rec["ok"])
        self.assertTrue(res_rec.get("restored", False))
        self.assertIn("file_a.py", res_rec.get("restored_files", []))
        self.assertIsNotNone(res_rec.get("pre_restore_backup_id"))
        self.assertIn("suggested_verify_command", res_rec)

        self.assertEqual(f1.read_text(encoding="utf-8"), "print('version 1 A')")
        self.assertEqual(f2.read_text(encoding="utf-8"), "GOOD PRESERVED")

    def test_time_machine_recover_tool_handler(self):
        wdir = self.git_repo["dir"]

        snap = self.ledger.record_pre_tool(session_id="s_tool_rec", tool_name="write_file", args={}, working_dir=str(wdir))

        res_json = handle_recover(
            {"session_id": "s_tool_rec", "error_summary": "file_a.py error"},
            ledger=self.ledger,
        )
        res = json.loads(res_json)
        self.assertTrue(res["ok"])
        self.assertIn("diagnosis", res)

    @unittest.skipIf(not HAS_FASTAPI, "fastapi not installed in environment")
    def test_api_recover_and_export_endpoints(self):
        from dashboard.plugin_api import set_ledger
        set_ledger(self.ledger)
        self.ledger.record_pre_tool(session_id="s_api", tool_name="patch", args={}, working_dir=str(self.temp_dir))

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        res_exp = client.get("/snapshots/export?session_id=s_api")
        self.assertEqual(res_exp.status_code, 200)
        self.assertIn("Time Machine Timeline Report", res_exp.json()["markdown"])

        res_rec = client.post("/snapshots/recover", json={"session_id": "s_api", "dry_run": True})
        self.assertEqual(res_rec.status_code, 200)
        self.assertIn("ok", res_rec.json())

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

        f1.write_text("print('version 1 ALPHA')", encoding="utf-8")
        f2.write_text("print('version 1 BETA')", encoding="utf-8")
        s1 = self.ledger.record_pre_tool(session_id="s_search", tool_name="write_file", args={"path": "file_a.py"}, working_dir=str(wdir))
        self.ledger.record_post_tool(session_id="s_search", tool_name="write_file", args={"path": "file_a.py"}, status="ok")

        f2.write_text("print('version 2 BETA UNIQUE_MARKER')", encoding="utf-8")
        s2 = self.ledger.record_pre_tool(session_id="s_search", tool_name="edit_file", args={"path": "file_b.py"}, working_dir=str(wdir))
        self.ledger.record_post_tool(session_id="s_search", tool_name="edit_file", args={"path": "file_b.py"}, status="ok")

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
        f1 = self.git_repo["f1"]
        f1.write_text("print('version 1 ALPHA')", encoding="utf-8")
        s1 = self.ledger.record_pre_tool(session_id="s_tool_search", tool_name="write_file", args={"path": "file_a.py"}, working_dir=str(wdir))
        f1.write_text("print('version 2 ALPHA')", encoding="utf-8")
        self.ledger.record_post_tool(session_id="s_tool_search", tool_name="write_file", args={"path": "file_a.py"}, status="ok")

        res_json = handle_search({"query": "version", "session_id": "s_tool_search"}, ledger=self.ledger)
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
        snap = self.ledger.record_pre_tool(session_id="s_rec_verify", tool_name="edit_file", args={"path": "file_a.py"}, working_dir=str(wdir))
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

        # 1. Recovery returns suggested_verify_command for execution by Hermes' terminal tool
        res = self.ledger.recover(
            session_id="s_rec_verify",
            error_summary="syntax error in file_a.py",
            working_dir=str(wdir),
            dry_run=False,
            user_approved=True,
        )
        self.assertTrue(res["ok"])
        self.assertTrue(res.get("restored", False))
        self.assertIn("suggested_verify_command", res)
        self.assertEqual(res["suggested_verify_command"], "pytest")
        # Ensure ledger does NOT execute host subprocesses for verification
        self.assertNotIn("verification", res)

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

    def test_security_no_host_subprocess_runner(self):
        """Fix 1: Prove verify_command is NEVER run via subprocess.run on the host."""
        ledger_code = (PLUGIN_DIR / "ledger.py").read_text(encoding="utf-8")
        self.assertNotIn("_run_verify_command", ledger_code)
        self.assertNotIn("SAFE_TEST_RUNNERS", ledger_code)
        self.assertNotIn("validate_verify_command", ledger_code)

        tools_code = (PLUGIN_DIR / "tools.py").read_text(encoding="utf-8")
        self.assertIn("suggested_verify_command", tools_code)

    def test_security_dry_run_preserves_disk_state(self):
        """Prove dry_run=True / user_approved=False previews restoration without touching disk."""
        wdir = self.git_repo["dir"]
        c1 = self.git_repo["c1"]
        f1 = self.git_repo["f1"]

        f1.write_text("print('version 1 A')", encoding="utf-8")
        snap = self.ledger.record_pre_tool(session_id="s_dry", tool_name="edit_file", args={"path": "file_a.py"}, working_dir=str(wdir))

        f1.write_text("MODIFIED_CONTENT_NEVER_LOST", encoding="utf-8")
        stray_file = wdir / "stray_scratch.tmp"
        stray_file.write_text("stray content", encoding="utf-8")

        # Call restore in dry-run mode (default, unapproved)
        preview = self.ledger.restore_snapshot(
            snapshot_id=snap["id"],
            file_paths=["file_a.py", "stray_scratch.tmp"],
            working_dir=str(wdir),
            dry_run=True,
            user_approved=False,
        )
        self.assertTrue(preview["ok"])
        self.assertTrue(preview.get("dry_run"))
        self.assertFalse(preview.get("files_restored"))
        self.assertIn("file_a.py", preview.get("planned_restored_files", []))
        self.assertIn("stray_scratch.tmp", preview.get("planned_deleted_stray_files", []))

        # Files on disk must NOT have changed!
        self.assertEqual(f1.read_text(encoding="utf-8"), "MODIFIED_CONTENT_NEVER_LOST")
        self.assertTrue(stray_file.exists())


    def test_hook_sequence_recover_preserves_good_files_and_restores_broken(self):
        """
        Bug 1 & Bug 3 Regression Test:
        Simulate real hook sequence on_session_start -> pre_tool_call -> post_tool_call.
        Proves:
        1. Modifying tax.py (good) then billing.py (bad) then running terminal (test fails)
           identifies billing.py as culprit and preserves tax.py!
        2. Innocent untracked notes.txt is NEVER deleted!
        3. Stray debug_dump.tmp created during broken turn IS unlinked!
        4. billing.py is restored and tests pass!
        """
        import __init__ as tm_plugin
        wdir = self.git_repo["dir"]
        c1 = self.git_repo["c1"]

        # Setup files in working dir
        billing_py = wdir / "billing.py"
        tax_py = wdir / "tax.py"
        test_py = wdir / "test_billing.py"
        notes_txt = wdir / "notes.txt"
        dump_tmp = wdir / "debug_dump.tmp"

        billing_py.write_text("def calculate_total(subtotal, tax): return subtotal * (1 + tax)\n", encoding="utf-8")
        tax_py.write_text("def get_tax_rate(): return 0.08\n", encoding="utf-8")
        test_py.write_text(
            "import unittest\n"
            "from billing import calculate_total\n"
            "from tax import get_tax_rate\n"
            "class TestB(unittest.TestCase):\n"
            "    def test_ok(self): self.assertEqual(calculate_total(100, get_tax_rate()), 108)\n"
            "if __name__ == '__main__': unittest.main()\n",
            encoding="utf-8",
        )
        notes_txt.write_text("My important project notes that must never be deleted!\n", encoding="utf-8")

        # Initial commit in repo
        subprocess.run(["git", "add", "billing.py", "tax.py", "test_billing.py"], cwd=str(wdir), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial baseline"], cwd=str(wdir), check=True, capture_output=True)

        class MockCtx:
            def register_tool(self, *a, **k): pass
            def register_hook(self, *a, **k): pass
            def register_slash_command(self, *a, **k): pass

        mock_ctx = MockCtx()
        registered_hooks = {}

        def reg_hook_mock(event, fn):
            registered_hooks[event] = fn

        mock_ctx.register_hook = reg_hook_mock
        tm_plugin.register(mock_ctx, ledger=self.ledger)

        sess = "s_hook_real_order"
        registered_hooks["on_session_start"](session_id=sess, working_dir=str(wdir))

        # Turn 1: Good feature edit to tax.py
        registered_hooks["pre_tool_call"](
            tool_name="edit_file",
            args={"path": "tax.py"},
            session_id=sess,
            turn_id="t1",
        )
        tax_py.write_text("def get_tax_rate(): return 0.08\ndef get_tax_name(): return 'VAT'\n", encoding="utf-8")
        subprocess.run(["git", "add", "tax.py"], cwd=str(wdir), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "good tax edit"], cwd=str(wdir), check=True, capture_output=True)
        registered_hooks["post_tool_call"](
            tool_name="edit_file",
            args={"path": "tax.py"},
            session_id=sess,
            turn_id="t1",
            status="ok",
        )

        # Turn 2: Bad edit to billing.py + stray temp file created
        registered_hooks["pre_tool_call"](
            tool_name="edit_file",
            args={"path": "billing.py"},
            session_id=sess,
            turn_id="t2",
        )
        billing_py.write_text("def calculate_total(subtotal, tax):\n    raise RuntimeError('boom')\n", encoding="utf-8")
        dump_tmp.write_text("DEBUG DUMP CRASH LOG\n", encoding="utf-8")
        subprocess.run(["git", "add", "billing.py"], cwd=str(wdir), check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "broken billing edit"], cwd=str(wdir), check=True, capture_output=True)
        registered_hooks["post_tool_call"](
            tool_name="edit_file",
            args={"path": "billing.py"},
            session_id=sess,
            turn_id="t2",
            status="ok",
        )

        # Turn 3: Run test command via terminal -> fails
        registered_hooks["pre_tool_call"](
            tool_name="terminal",
            args={"command": f'"{sys.executable}" -m unittest test_billing.py'},
            session_id=sess,
            turn_id="t3",
        )
        t_res = subprocess.run([sys.executable, "-m", "unittest", "test_billing.py"], cwd=str(wdir), capture_output=True, text=True)
        self.assertNotEqual(t_res.returncode, 0)
        self.assertIn("billing.py", t_res.stderr)
        self.assertIn("RuntimeError: boom", t_res.stderr)

        registered_hooks["post_tool_call"](
            tool_name="terminal",
            args={"command": f'"{sys.executable}" -m unittest test_billing.py'},
            result={"exit_code": t_res.returncode, "stdout": t_res.stdout, "stderr": t_res.stderr},
            session_id=sess,
            turn_id="t3",
            status="error",
        )

        # Check transform_tool_result hint
        hint = registered_hooks["transform_tool_result"](
            tool_name="terminal",
            result=t_res.stderr,
            session_id=sess,
        )
        self.assertIn("billing.py", hint)
        self.assertIn("tax.py", hint)

        # Diagnosis check
        diag = self.ledger.diagnose_failure(session_id=sess, error_text=t_res.stderr, working_dir=str(wdir))
        self.assertIn("billing.py", diag["culprit_files"])
        self.assertIn("tax.py", diag["preserved_files"])
        self.assertNotIn("notes.txt", diag["culprit_files"])

        # Execute approved recovery
        rec_res = self.ledger.recover(
            session_id=sess,
            error_summary=t_res.stderr,
            working_dir=str(wdir),
            dry_run=False,
            user_approved=True,
        )
        self.assertTrue(rec_res["ok"])
        self.assertTrue(rec_res.get("restored", False))
        self.assertIn("tax.py", rec_res.get("preserved_files", []))
        self.assertIn("suggested_verify_command", rec_res)

        # Assert disk states
        # 1. billing.py no longer has RuntimeError('boom')
        self.assertNotIn("RuntimeError('boom')", billing_py.read_text(encoding="utf-8"))
        # 2. tax.py STILL has get_tax_name (good edit preserved!)
        self.assertIn("get_tax_name", tax_py.read_text(encoding="utf-8"))
        # 3. notes.txt was NOT deleted!
        self.assertTrue(notes_txt.exists())
        self.assertEqual(notes_txt.read_text(encoding="utf-8"), "My important project notes that must never be deleted!\n")
        # 4. Stray debug_dump.tmp was unlinked!
        self.assertFalse(dump_tmp.exists())
        # 5. Model runs suggested verify command via terminal -> passes
        t_verify = subprocess.run([sys.executable, "-m", "unittest", "test_billing.py"], cwd=str(wdir), capture_output=True, text=True)
        self.assertEqual(t_verify.returncode, 0)

    def test_security_invalid_commit_aborts_without_deleting_files(self):
        """Bug 2: Invalid commit hash aborts with error and does NOT delete any workspace files."""
        wdir = self.git_repo["dir"]
        f1 = self.git_repo["f1"]
        original_content = f1.read_text(encoding="utf-8")

        res = self.ledger.restore_snapshot(
            snapshot_id="nonexistent_snap",
            file_paths=["file_a.py"],
            working_dir=str(wdir),
            commit_hash="deadbeef1234567890abcdef1234567890abcdef",
            dry_run=False,
            user_approved=True,
        )
        self.assertFalse(res["ok"])
        self.assertIn("does not exist", res["error"])
        # File must NOT have been deleted
        self.assertTrue(f1.exists())
        self.assertEqual(f1.read_text(encoding="utf-8"), original_content)

    def test_command_error_detection_exit_code_authoritative(self):
        """Bug 4: Commands with exit_code == 0 are ok even if stdout contains 'Error:' or 'exit code'."""
        import __init__ as tm_plugin
        class MockCtx:
            def register_hook(self, *a, **k): pass
            def register_tool(self, *a, **k): pass
            def register_slash_command(self, *a, **k): pass

        mock_ctx = MockCtx()
        hooks = {}
        mock_ctx.register_hook = lambda event, fn: hooks.update({event: fn})
        tm_plugin.register(mock_ctx, ledger=self.ledger)

        sess = "s_exit_code_auth"
        hooks["on_session_start"](session_id=sess)

        # Successful command that printed error handling logs
        res_dict = {
            "exit_code": 0,
            "stdout": "Testing error handling: Error: handled successfully, exit code 0 verified.",
        }
        hooks["pre_tool_call"](
            tool_name="terminal",
            args={"command": "echo test"},
            session_id=sess,
            turn_id="t_success",
        )
        hooks["post_tool_call"](
            tool_name="terminal",
            result=res_dict,
            session_id=sess,
            turn_id="t_success",
            status="ok",
        )

        snaps = self.ledger.list_snapshots(session_id=sess)
        self.assertTrue(len(snaps) > 0)
        latest = snaps[0]
        self.assertEqual(latest.get("status"), "ok")
        self.assertFalse(bool(latest.get("self_heal_candidate")))

    def test_fastapi_restore_endpoint_pydantic_list_model(self):
        """Bug 6: POST /snapshots/{id}/restore parses List[str] in Pydantic without NameError."""
        try:
            from fastapi.testclient import TestClient
            from dashboard.plugin_api import router, set_ledger
            from fastapi import FastAPI
            app = FastAPI()
            app.include_router(router)
            set_ledger(self.ledger)
            client = TestClient(app)

            snap = self.ledger.record_pre_tool(
                session_id="s_api_model",
                tool_name="edit_file",
                args={"path": "billing.py"},
                working_dir=str(self.git_repo["dir"]),
            )

            response = client.post(
                f"/snapshots/{snap['id']}/restore",
                json={"file_paths": ["billing.py"], "dry_run": True},
            )
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertTrue(isinstance(data, dict))
            self.assertTrue(data.get("ok"))
        except ImportError:
            self.skipTest("fastapi/testclient not installed in current environment")

    def test_architecture_zero_state_db_access_and_private_store(self):
        """Fix 4 & 5: Prove ledger.py never connects to state.db and keeps data in plugin-data/time-machine."""
        ledger_code = (PLUGIN_DIR / "ledger.py").read_text(encoding="utf-8")
        self.assertNotIn("state.db", ledger_code)
        self.assertNotIn("SessionDB", ledger_code)
        self.assertNotIn("time_machine_branch", (PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8"))

        # Verify storage path resolution
        self.assertIn("plugin-data", str(self.ledger.data_dir))
        self.assertEqual(self.ledger.data_dir.name, "time-machine")

    def test_security_minimal_git_env_strips_secrets(self):
        """Fix 3: Verify _minimal_git_env strips provider keys and sets safety variables."""
        from ledger import _minimal_git_env
        os.environ["OPENAI_API_KEY"] = "sk-test-secret-12345"
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test-secret-67890"
        os.environ["OPENROUTER_API_KEY"] = "sk-or-test-secret-99999"
        try:
            clean_env = _minimal_git_env({"EXTRA_VAR": "safe_val"})
            self.assertNotIn("OPENAI_API_KEY", clean_env)
            self.assertNotIn("ANTHROPIC_API_KEY", clean_env)
            self.assertNotIn("OPENROUTER_API_KEY", clean_env)
            self.assertEqual(clean_env.get("GIT_TERMINAL_PROMPT"), "0")
            self.assertEqual(clean_env.get("GIT_CONFIG_NOSYSTEM"), "1")
            self.assertEqual(clean_env.get("EXTRA_VAR"), "safe_val")
        finally:
            os.environ.pop("OPENAI_API_KEY", None)
            os.environ.pop("ANTHROPIC_API_KEY", None)
            os.environ.pop("OPENROUTER_API_KEY", None)

    @unittest.skipIf(not HAS_FASTAPI, "fastapi not installed in environment")
    def test_fastapi_all_endpoints(self):
        """Item 5: Test all FastAPI routes in dashboard/plugin_api.py."""
        from dashboard.plugin_api import router, set_ledger
        set_ledger(self.ledger)
        snap = self.ledger.record_pre_tool(
            session_id="s_api_full",
            tool_name="edit_file",
            args={"path": "calc.py"},
            working_dir=str(self.temp_dir),
        )

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        # GET /status
        r_status = client.get("/status")
        self.assertEqual(r_status.status_code, 200)
        self.assertTrue(r_status.json().get("ok"))

        # GET /snapshots
        r_list = client.get("/snapshots?session_id=s_api_full")
        self.assertEqual(r_list.status_code, 200)
        self.assertTrue(r_list.json().get("ok"))

        # GET /snapshots/search
        r_search = client.get("/snapshots/search?q=calc&session_id=s_api_full")
        self.assertEqual(r_search.status_code, 200)
        self.assertTrue(r_search.json().get("ok"))

        # POST /snapshots (manual snapshot)
        r_post_snap = client.post("/snapshots", json={"session_id": "s_api_full", "reason": "manual test"})
        self.assertEqual(r_post_snap.status_code, 200)
        self.assertTrue(r_post_snap.json().get("ok"))

        # POST /snapshots/{id}/restore
        r_restore = client.post(
            f"/snapshots/{snap['id']}/restore",
            json={"file_paths": ["calc.py"], "dry_run": True},
        )
        self.assertEqual(r_restore.status_code, 200)
        self.assertTrue(r_restore.json().get("ok"))

        # POST /snapshots/recover
        r_rec = client.post("/snapshots/recover", json={"session_id": "s_api_full", "dry_run": True})
        self.assertEqual(r_rec.status_code, 200)

        # GET /snapshots/export
        r_export = client.get("/snapshots/export?session_id=s_api_full")
        self.assertEqual(r_export.status_code, 200)
        self.assertIn("Time Machine", r_export.json().get("markdown", ""))

    def test_restore_refuses_outside_session_cwd(self):
        """Item 3: restore_snapshot must refuse to restore if snapshot workspace != active session_cwd."""
        dir_a = self.temp_dir / "workspace_a"
        dir_b = self.temp_dir / "workspace_b"
        dir_a.mkdir(parents=True, exist_ok=True)
        dir_b.mkdir(parents=True, exist_ok=True)
        (dir_a / "app.py").write_text("print('hello')", encoding="utf-8")

        snap = self.ledger.record_pre_tool(
            session_id="s_cwd_sec",
            tool_name="write_file",
            args={"path": "app.py"},
            working_dir=str(dir_a),
        )

        # Attempt to restore against different session_cwd (dir_b)
        res = self.ledger.restore_snapshot(
            snapshot_id=snap["id"],
            working_dir=str(dir_b),
            dry_run=False,
            user_approved=True,
        )
        self.assertFalse(res["ok"])
        self.assertIn("does not match active session_cwd", res.get("error", ""))

    def test_confirm_alias_cannot_bypass_approval(self):
        """Item 2: confirm=True or auto_apply=True cannot bypass user_approved=False or dry_run=True."""
        snap = self.ledger.record_pre_tool(
            session_id="s_bypass",
            tool_name="write_file",
            args={"path": "main.py"},
            working_dir=str(self.temp_dir),
        )

        res_restore = self.ledger.restore_snapshot(
            snapshot_id=snap["id"],
            working_dir=str(self.temp_dir),
            dry_run=True,
            user_approved=False,
            confirm=True,  # Attempt bypass
        )
        self.assertTrue(res_restore.get("dry_run", False))
        self.assertFalse(res_restore.get("files_restored", False))

        res_recover = self.ledger.recover(
            session_id="s_bypass",
            working_dir=str(self.temp_dir),
            dry_run=True,
            user_approved=False,
            confirm=True,
            auto_apply=True,  # Attempt bypass
        )
        self.assertTrue(res_recover.get("dry_run", False))
        self.assertFalse(res_recover.get("restored", False))

    def test_register_tool_signatures_and_command(self):
        import __init__ as tm_plugin
        class MockCtx:
            def __init__(self):
                self.tools = []
                self.commands = []
            def register_tool(self, name, toolset=None, schema=None, handler=None, description=None, **kwargs):
                assert isinstance(schema, dict) and callable(handler) and toolset == "time_machine"
                self.tools.append(name)
            def register_hook(self, *a, **k): pass
            def register_command(self, name, handler=None, description=None, args_hint=None, **kwargs):
                self.commands.append(name)
            def register_system_prompt_section(self, *a, **k): pass

        ctx = MockCtx()
        tm_plugin.register(ctx, ledger=self.ledger)
        expected_tools = [
            "time_machine_list_snapshots",
            "time_machine_diff",
            "time_machine_restore",
            "time_machine_recover",
            "time_machine_search",
        ]
        self.assertEqual(sorted(ctx.tools), sorted(expected_tools))
        self.assertIn("tm", ctx.commands)

    def test_git_option_injection_refused(self):
        """Verify snapshot ids starting with '-' or not in db/hex are refused and cannot become git options."""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            target = tmp / "pwned.txt"
            bad = "--output=" + str(target)

            # Record a real snapshot to test compare_to_id=bad and normal diff
            snap = self.ledger.record_pre_tool(
                session_id="test_inj",
                tool_name="write_file",
                working_dir=str(tmp),
                reason="test",
            )
            real_id = snap["id"]

            # 1. get_diff with bad snapshot_id
            res_diff1 = self.ledger.get_diff(snapshot_id=bad, working_dir=str(tmp))
            self.assertFalse(res_diff1.get("ok"))

            # 2. get_diff with real snapshot_id and bad compare_to_id
            res_diff2 = self.ledger.get_diff(snapshot_id=real_id, compare_to_id=bad, working_dir=str(tmp))
            self.assertFalse(res_diff2.get("ok"))

            # 3. restore_snapshot with bad snapshot_id
            res_restore = self.ledger.restore_snapshot(
                snapshot_id=bad,
                user_approved=True,
                dry_run=False,
                working_dir=str(tmp),
            )
            self.assertFalse(res_restore.get("ok"))

            # 4. Target file must not exist
            self.assertFalse(target.exists(), f"Security vulnerability: {target} was created via git option injection!")

            # 5. Normal snap_ id still diffs
            res_normal = self.ledger.get_diff(snapshot_id=real_id, working_dir=str(tmp))
            self.assertTrue(res_normal.get("ok"))

            # 6. compare_to_id='WORKING_TREE' still works
            res_wt = self.ledger.get_diff(snapshot_id=real_id, compare_to_id="WORKING_TREE", working_dir=str(tmp))
            self.assertTrue(res_wt.get("ok"))

    def test_recover_persists_failed_command_without_injected_ledger(self):
        """Fresh TimeMachineLedger() instances must still know the last failed command."""
        home = self.base_test_dir / "hermes_home_persist"
        home.mkdir(parents=True, exist_ok=True)
        old_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = str(home)
        sess = "s_persist_failed_cmd"
        try:
            class MockCtx:
                def __init__(self):
                    self.tools = {}
                    self.hooks = {}

                def register_tool(self, name=None, toolset=None, schema=None, handler=None, description=None, **kwargs):
                    self.tools[name] = handler

                def register_hook(self, event, fn, **kwargs):
                    self.hooks[event] = fn

                def register_command(self, *args, **kwargs):
                    return None

                def register_system_prompt_section(self, *args, **kwargs):
                    return None

            ctx = MockCtx()
            tm_plugin.register(ctx)  # ledger=None — a new ledger on every call
            wdir = str(self.temp_dir)
            ctx.hooks["on_session_start"](session_id=sess, working_dir=wdir)
            ctx.hooks["pre_tool_call"](
                tool_name="edit_file",
                args={"path": "file_a.py"},
                session_id=sess,
                tool_call_id="call-edit",
            )
            ctx.hooks["post_tool_call"](
                tool_name="edit_file",
                args={"path": "file_a.py"},
                session_id=sess,
                tool_call_id="call-edit",
                status="ok",
            )
            ctx.hooks["pre_tool_call"](
                tool_name="terminal",
                args={"command": "pytest -q"},
                session_id=sess,
                tool_call_id="call-term",
            )
            ctx.hooks["post_tool_call"](
                tool_name="terminal",
                args={"command": "pytest -q"},
                session_id=sess,
                tool_call_id="call-term",
                status="error",
            )

            from ledger import TimeMachineLedger, _GLOBAL_LAST_FAILED_COMMANDS

            self.assertEqual(_GLOBAL_LAST_FAILED_COMMANDS.get(sess), "pytest -q")
            # Drop the in-process cache so recovery must read SQLite via a new ledger.
            _GLOBAL_LAST_FAILED_COMMANDS.pop(sess, None)
            fresh = TimeMachineLedger()
            self.assertEqual(fresh._read_failed_command(sess), "pytest -q")

            raw = ctx.tools["time_machine_recover"]({"session_id": sess}, session_id=sess)
            res = json.loads(raw)
            self.assertEqual(res.get("suggested_verify_command"), "pytest -q")
        finally:
            from ledger import _GLOBAL_LAST_FAILED_COMMANDS
            _GLOBAL_LAST_FAILED_COMMANDS.pop(sess, None)
            if old_home is None:
                os.environ.pop("HERMES_HOME", None)
            else:
                os.environ["HERMES_HOME"] = old_home

    def test_post_tool_preserves_write_snapshot_stats(self):
        """read_file and a failing terminal must not zero out the previous write_file diff."""
        wdir = self.git_repo["dir"]
        f1 = self.git_repo["f1"]

        class MockCtx:
            def register_tool(self, *a, **k):
                return None

            def register_hook(self, *a, **k):
                return None

            def register_command(self, *a, **k):
                return None

            def register_system_prompt_section(self, *a, **k):
                return None

        hooks = {}
        ctx = MockCtx()
        ctx.register_hook = lambda event, fn, **k: hooks.update({event: fn})
        tm_plugin.register(ctx, ledger=self.ledger)

        sess = "s_preserve_stats"
        hooks["on_session_start"](session_id=sess, working_dir=str(wdir))
        hooks["pre_tool_call"](
            tool_name="write_file",
            args={"path": "file_a.py"},
            session_id=sess,
            tool_call_id="w1",
        )
        f1.write_text("print('version 2 preserved stats')\n", encoding="utf-8")
        hooks["post_tool_call"](
            tool_name="write_file",
            args={"path": "file_a.py"},
            session_id=sess,
            tool_call_id="w1",
            status="ok",
        )
        hooks["pre_tool_call"](
            tool_name="read_file",
            args={"path": "file_a.py"},
            session_id=sess,
            tool_call_id="r1",
        )
        hooks["post_tool_call"](
            tool_name="read_file",
            args={"path": "file_a.py"},
            session_id=sess,
            tool_call_id="r1",
            status="ok",
        )
        hooks["pre_tool_call"](
            tool_name="terminal",
            args={"command": "pytest -q"},
            session_id=sess,
            tool_call_id="t1",
        )
        hooks["post_tool_call"](
            tool_name="terminal",
            args={"command": "pytest -q"},
            result={"exit_code": 1, "stderr": "FAILED"},
            session_id=sess,
            tool_call_id="t1",
            status="error",
        )

        snaps = self.ledger.list_snapshots(session_id=sess, limit=20)
        write_snaps = [s for s in snaps if s.get("tool_name") == "write_file"]
        term_snaps = [s for s in snaps if s.get("tool_name") == "terminal"]
        read_snaps = [s for s in snaps if s.get("tool_name") == "read_file"]
        self.assertEqual(len(write_snaps), 1)
        self.assertEqual(len(term_snaps), 1)
        self.assertEqual(read_snaps, [])
        self.assertGreater(write_snaps[0].get("files_changed") or 0, 0)
        self.assertEqual(write_snaps[0].get("status"), "ok")
        self.assertEqual(term_snaps[0].get("status"), "error")

    def test_turn_diff_between_pre_and_post(self):
        """Edits made between pre_tool and post_tool show up in get_diff and search."""
        wdir = self.git_repo["dir"]
        f1 = self.git_repo["f1"]
        f1.write_text("print('version 1 A')\n", encoding="utf-8")
        snap = self.ledger.record_pre_tool(
            session_id="s_turn_diff",
            tool_name="write_file",
            args={"path": "file_a.py"},
            working_dir=str(wdir),
        )
        f1.write_text("print('version 2 TURN_DIFF_MARKER')\n", encoding="utf-8")
        self.ledger.record_post_tool(
            session_id="s_turn_diff",
            tool_name="write_file",
            status="ok",
        )

        diff = self.ledger.get_diff(snap["id"], working_dir=str(wdir))
        self.assertTrue(diff.get("ok"))
        self.assertEqual(diff.get("mode"), "turn_diff")
        self.assertIn("TURN_DIFF_MARKER", diff.get("diff") or "")
        self.assertTrue(any(f.get("path") == "file_a.py" for f in diff.get("files") or []))

        found = self.ledger.search_snapshots(
            query="TURN_DIFF_MARKER",
            session_id="s_turn_diff",
            working_dir=str(wdir),
        )
        self.assertTrue(found.get("ok"))
        self.assertGreaterEqual(found.get("total_matches") or 0, 1)
        ids = [m.get("snapshot_id") for m in found.get("matches") or []]
        self.assertIn(snap["id"], ids)

    def test_full_workspace_restore_removes_only_new_strays(self):
        """Full restore unlinks files created after the snapshot and keeps baseline files."""
        wdir = self.git_repo["dir"]
        notes = wdir / "notes.txt"
        notes.write_text("keep me\n", encoding="utf-8")
        envf = wdir / ".env"
        envf.write_text("SECRET_KEY=preexisting\n", encoding="utf-8")
        old = time.time() - 30
        os.utime(notes, (old, old))
        os.utime(envf, (old, old))

        self.ledger.record_pre_tool(
            session_id="s_full_stray",
            tool_name="session_start",
            working_dir=str(wdir),
            reason="session_start",
        )
        snap = self.ledger.record_pre_tool(
            session_id="s_full_stray",
            tool_name="write_file",
            args={"path": "file_a.py"},
            working_dir=str(wdir),
        )
        stray = wdir / "stray_after.tmp"
        stray.write_text("new junk\n", encoding="utf-8")
        # Touch the secret AFTER the snapshot; it must still be kept.
        os.utime(envf, None)

        preview = self.ledger.restore_snapshot(
            snapshot_id=snap["id"],
            working_dir=str(wdir),
            dry_run=True,
            user_approved=False,
        )
        self.assertTrue(preview.get("ok"))
        self.assertTrue(preview.get("dry_run"))
        self.assertIn("stray_after.tmp", preview.get("planned_unlinked_files") or [])
        self.assertNotIn("notes.txt", preview.get("planned_unlinked_files") or [])
        self.assertNotIn(".env", preview.get("planned_unlinked_files") or [])
        self.assertTrue(stray.exists())

        res = self.ledger.restore_snapshot(
            snapshot_id=snap["id"],
            working_dir=str(wdir),
            dry_run=False,
            user_approved=True,
        )
        self.assertTrue(res.get("ok"), res)
        self.assertFalse(stray.exists())
        self.assertTrue(notes.exists())
        self.assertEqual(notes.read_text(encoding="utf-8"), "keep me\n")
        self.assertTrue(envf.exists())
        self.assertIn("preexisting", envf.read_text(encoding="utf-8"))
        self.assertIsNotNone(res.get("pre_restore_backup_id"))

    def test_bare_store_excludes_dotenv_and_secrets_by_default(self):
        """Untracked .env secrets stay out of the private bare store; examples do not."""
        wdir = self.temp_dir
        (wdir / ".env").write_text("SECRET_KEY=supersecret\n", encoding="utf-8")
        (wdir / ".env.example").write_text("SECRET_KEY=changeme\n", encoding="utf-8")
        (wdir / "app.py").write_text("print('hello')\n", encoding="utf-8")
        (wdir / "node_modules").mkdir()
        (wdir / "node_modules" / "leftpad.js").write_text("module.exports = 1;\n", encoding="utf-8")

        snap = self.ledger.record_pre_tool(
            session_id="s_exclude",
            tool_name="write_file",
            args={"path": "app.py"},
            working_dir=str(wdir),
        )
        commit = snap.get("checkpoint_hash")
        self.assertTrue(commit)

        exclude_file = self.ledger.store_dir / "info" / "exclude"
        self.assertTrue(exclude_file.exists())
        exclude_text = exclude_file.read_text(encoding="utf-8")
        self.assertIn(".env", exclude_text)
        self.assertIn("!.env.example", exclude_text)

        listed = subprocess.run(
            ["git", f"--git-dir={self.ledger.store_dir}", "ls-tree", "-r", "--name-only", "--end-of-options", commit],
            capture_output=True,
            text=True,
            check=True,
        )
        names = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
        self.assertIn("app.py", names)
        self.assertIn(".env.example", names)
        self.assertNotIn(".env", names)
        self.assertFalse(any(n == "node_modules/leftpad.js" or n.startswith("node_modules/") for n in names))

        secret = subprocess.run(
            ["git", f"--git-dir={self.ledger.store_dir}", "grep", "-I", "--full-name", "supersecret", commit],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(secret.returncode, 0)
        self.assertNotIn("supersecret", secret.stdout)


if __name__ == "__main__":
    unittest.main()
