"""Unit tests for Hermes Time Machine plugin."""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Ensure hermes-time-machine root is on sys.path
PLUGIN_DIR = Path(__file__).resolve().parent.parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ledger import TimeMachineLedger
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
def test_ledger(temp_dir):
    db_file = temp_dir / "time_machine.db"
    return TimeMachineLedger(db_path=db_file)


def test_ledger_record_and_list(test_ledger, temp_dir):
    test_ledger.record_pre_tool(
        session_id="test_sess_01",
        tool_name="write_file",
        args={"path": "hello.txt", "content": "world"},
        working_dir=str(temp_dir),
        tool_call_id="call_1",
    )
    test_ledger.record_post_tool(
        session_id="test_sess_01",
        tool_name="write_file",
        args={"path": "hello.txt"},
        status="ok",
        tool_call_id="call_1",
    )

    snaps = test_ledger.list_snapshots(session_id="test_sess_01")
    assert len(snaps) >= 1
    s = snaps[0]
    assert s["tool_name"] == "write_file"
    assert s["session_id"] == "test_sess_01"


def test_tool_schemas():
    for schema in (LIST_SNAPSHOTS_SCHEMA, DIFF_SCHEMA, RESTORE_SCHEMA, BRANCH_SCHEMA):
        assert "name" in schema
        assert "description" in schema
        assert "parameters" in schema
        assert schema["parameters"]["type"] == "object"


def test_tool_handlers(test_ledger, temp_dir):
    test_ledger.record_pre_tool(
        session_id="sess_handlers",
        tool_name="patch",
        args={"patch": "test"},
        working_dir=str(temp_dir),
    )
    snaps = test_ledger.list_snapshots(session_id="sess_handlers")
    assert len(snaps) >= 1
    snap_id = snaps[0]["id"]

    # 1. List handler
    res = json.loads(handle_list_snapshots({"session_id": "sess_handlers"}, ledger=test_ledger))
    assert res.get("ok") is True
    assert len(res.get("snapshots", [])) >= 1

    # 2. Diff handler
    diff_res = json.loads(handle_diff({"snapshot_id": snap_id}, ledger=test_ledger))
    assert "ok" in diff_res

    # 3. Restore handler without confirm should reject
    restore_res = json.loads(handle_restore({"snapshot_id": snap_id, "confirm": False}, ledger=test_ledger))
    assert restore_res.get("ok") is False
    assert "ok" in restore_res

    # 4. Branch handler
    branch_res = json.loads(handle_branch({"snapshot_id": snap_id, "branch_title": "Test Branch"}, ledger=test_ledger))
    assert branch_res.get("ok") is True
    assert "new_session_id" in branch_res


def test_dashboard_api(test_ledger):
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    # 1. Status
    res = client.get("/status")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True

    # 2. Snapshots
    res = client.get("/snapshots")
    assert res.status_code == 200


def test_plugin_register():
    class MockContext:
        def __init__(self):
            self.hooks = []
            self.tools = []

        def register_hook(self, name, callback, **kwargs):
            self.hooks.append(name)

        def register_tool(self, name, *args, **kwargs):
            self.tools.append(name)

    ctx = MockContext()
    tm_plugin.register(ctx)

    assert "on_session_start" in ctx.hooks
    assert "pre_tool_call" in ctx.hooks
    assert "post_tool_call" in ctx.hooks
    assert "time_machine_list_snapshots" in ctx.tools
    assert "time_machine_diff" in ctx.tools
    assert "time_machine_restore" in ctx.tools
    assert "time_machine_branch" in ctx.tools
