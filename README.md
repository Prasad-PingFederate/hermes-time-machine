# 🛡️ hermes-time-machine

**Zero-Risk Autonomous Time Travel & Self-Healing for Hermes Agent**  
*Built for the Nous Research Hermes Agent Hackathon*

[![Hermes Plugin Validate](https://img.shields.io/badge/hermes_plugin_validate-100%25_PASS-success.svg)](#verification--testing)
[![Tests](https://img.shields.io/badge/pytest-18%2F18_PASS-brightgreen.svg)](#verification--testing)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Demo Video Script](https://img.shields.io/badge/Demo_Script-<90s_Walkthrough-purple.svg)](DEMO_SCRIPT.md)

---

## 🌟 Overview

`hermes-time-machine` transforms Hermes Agent into a fearless, zero-risk autonomous software developer. By bridging Hermes's native shadow-git filesystem checkpoints (`~/.hermes/checkpoints/store/`) with session message history (`~/.hermes/state.db`) into an atomic SQLite ledger (`time_machine.db`), it provides both human operators and autonomous agents with:

- **Autonomous Self-Healing Engine (`time_machine_recover`):** Automatically correlates compiler/test errors with recent file edits, identifies root-cause culprits, and performs 1-step surgical rollback (`auto_apply=true`).
- **"Rollback of a Rollback" Safety:** Automatically captures a pre-restore safety snapshot before any file rollback, making every action 100% reversible.
- **Surgical Per-File Restore & Newly-Created File Cleanup:** Revert only the specific broken files (`file_paths=['billing.py']`) while preserving untouched features and cleanly unlinking untracked temporary files from disk.
- **Arbitrary `A ↔ B` & Live Drift Diffing:** Compare any historical snapshot against another or inspect live uncommitted working tree drift (`compare_to_id="WORKING_TREE"`).
- **Interactive Visual Timeline Scrubber:** Horizontal step scrubber in Hermes Desktop with hover mini-diff previews, 1-click **"⚡ Restore & Retry"**, and **"📸 Snapshot Now"**.
- **Storage Hygiene & Pruning:** Configurable retention (`max_snapshots_per_session: 50`) with protected baselines and safety backups.
- **Instant Markdown Timeline Export:** Export structured audit logs via `/rewind export` or the Desktop UI.
- **CLI Slash Command:** Full terminal suite `/rewind [list|diff|restore|recover|export|demo]`.

---

## 🏛️ Architecture

```text
hermes-time-machine/
├── plugin.yaml          # Hermes unified plugin manifest (v2)
├── __init__.py          # Lifecycle hooks, slash command & self-heal middleware
├── ledger.py            # SQLite ledger + CheckpointManager & SessionDB bridge
├── schemas.py           # OpenAI-compatible function schemas (5 tools)
├── tools.py             # Agent tool handlers with confirmation guards
├── dashboard/
│   ├── manifest.json    # Declares backend FastAPI extension router
│   └── plugin_api.py    # REST endpoints (/status, /snapshots, /diff, /restore, /recover, /export)
├── desktop/
│   └── plugin.js        # Native Desktop UI via @hermes/plugin-sdk (Scrubber, Split Diff, Studio)
├── scripts/
│   └── demo_walkthrough.py # 6-Step live end-to-end smoke test script
├── tests/
│   └── test_plugin.py   # 18 Unit & integration tests (100% green)
├── DEMO_SCRIPT.md       # Official <90s video narration & walkthrough script
└── README.md
```

### 1. Dual-Bridge Ledger Engine (`ledger.py`)
- **Filesystem Bridge:** Computes deterministic project hashes matching Hermes core `_normalize_path` (`hashlib.sha256(expanduser().resolve()).hexdigest()[:16]`). Interacts directly with `refs/hermes/<hash16>` shadow-git store.
- **Structured Diff Engine:** Parses git unified diffs into structured file objects (`additions`, `deletions`, `status`, `diff_chunk`) for rich UI presentation.
- **Conversation Bridge:** Reads message watermarks from `~/.hermes/state.db` to coordinate file rollbacks with conversation history rewinds.
- **Path Traversal Protection:** Validates that all target paths resolve strictly within the workspace root (`validate_safe_path`).

### 2. Autonomous Self-Healing System (`time_machine_recover`)
- **Root-Cause Culprit Detection:** Parses tracebacks and compiler errors to pinpoint exact files modified prior to failure.
- **1-Call Recovery:** The agent or operator executes `time_machine_recover(auto_apply=true, confirm=true)` to restore culprit files and clean up newly created temporary files in one atomic action.
- **Transform Middleware:** Automatically attaches actionable self-healing guidance directly into tool failure results.

### 3. Agent Toolset (`tools.py`)
1. `time_machine_recover`: Autonomous self-healing diagnostic and recovery tool.
2. `time_machine_list_snapshots`: Inspect captured checkpoints in the current session.
3. `time_machine_diff`: View unified, structured, and arbitrary A ↔ B / live drift diffs.
4. `time_machine_restore`: Surgically restore specific files or whole workspace with safety backup.
5. `time_machine_branch`: Fork a new exploration session from a historical checkpoint.

### 4. CLI Slash Command (`/rewind`)
- `/rewind` or `/rewind list`: Formatted table of recent session checkpoints.
- `/rewind diff <id> [compare_to]`: Diff snapshot vs parent, live working tree, or other snapshot.
- `/rewind restore <id> [--files file1,file2]`: Instant surgical rollback.
- `/rewind recover`: Run 1-call autonomous diagnosis and culprit rollback.
- `/rewind export`: Output full session history formatted as Markdown.
- `/rewind demo`: Run the reproducible 6-step live walkthrough.

### 5. Native Hermes Desktop UI (`desktop/plugin.js`)
- **Status Bar:** Dynamic 3-state badge (`🛡️ Safe Mode • X snaps`, `⚠️ Self-Heal Available • Revert Ready`, `✅ Recovered • Workspace Clean`).
- **Horizontal Timeline Scrubber:** Interactive node track (`● ─── ● ─── ● ─── ⚠️ ─── ✅`) with hover mini-previews.
- **Studio Diff Inspector:** Side-by-side split & unified diffs with line numbers and 1-click **"Revert This File Only"** headers.
- **Action Buttons:** **"📸 Snapshot"**, **"⚡ Restore & Retry"**, **"📥 Export"**, and **"Prune Old"**.

---

## 🧪 Verification & Testing

### Official Hermes Admission Validator
Tested against `hermes_cli.plugin_validate` from Hermes core:
```text
Validation OK: True (Exit Code: 0)
[PASS] manifest: plugin.yaml parses
[PASS] manifest fields: name, version, description present
[PASS] requires_hermes: not declared
[PASS] config schema: shape valid
[PASS] requires_env: all entries UPPER_SNAKE
[PASS] oauth: not declared
[PASS] loadable: entry: __init__.py, desktop/plugin.js
[PASS] python dependencies: none declared
[PASS] capability probe: register() ran in isolation
[PASS] declared tools: matches registrations (5/5 tools match 1:1)
[PASS] declared hooks: matches registrations (4/4 hooks match 1:1)
[PASS] declared middleware: matches registrations
[PASS] built-in tool collisions: no collisions
[PASS] security scan: safe
[PASS] no core override: no runtime rebinds of Hermes core
[PASS] desktop surface: stays inside the plugin SDK surface
```

### Unit & Integration Test Suite (`pytest`)
```text
tests/test_plugin.py::test_parse_unified_diff PASSED                     [  5%]
tests/test_plugin.py::test_ledger_record_and_self_heal PASSED            [ 11%]
tests/test_plugin.py::test_surgical_file_restore_and_safety_backup PASSED [ 16%]
tests/test_plugin.py::test_tool_handlers_and_confirmation PASSED         [ 22%]
tests/test_plugin.py::test_slash_command_rewind PASSED                    [ 27%]
tests/test_plugin.py::test_self_healing_transform_hint PASSED            [ 33%]
tests/test_plugin.py::test_dashboard_api_endpoints PASSED                 [ 38%]
tests/test_plugin.py::test_path_traversal_rejection PASSED               [ 44%]
tests/test_plugin.py::test_newly_created_file_unlinked_on_restore PASSED [ 50%]
tests/test_plugin.py::test_arbitrary_and_working_tree_diff PASSED        [ 55%]
tests/test_plugin.py::test_prune_snapshots_and_storage_stats PASSED      [ 61%]
tests/test_plugin.py::test_demo_walkthrough_e2e PASSED                   [ 66%]
tests/test_plugin.py::test_slash_command_rewind_demo PASSED              [ 72%]
tests/test_plugin.py::test_diagnose_failure_and_culprit_detection PASSED [ 77%]
tests/test_plugin.py::test_time_machine_recover_auto_apply PASSED         [ 83%]
tests/test_plugin.py::test_time_machine_recover_tool_handler PASSED      [ 88%]
tests/test_plugin.py::test_api_recover_and_export_endpoints PASSED       [ 94%]
tests/test_plugin.py::test_slash_command_recover_and_export PASSED      [100%]

============================= 18 passed in 33.78s =============================
```

---

## 🚀 Quick Start

### 1. Installation
Clone into your Hermes plugins directory:
```bash
git clone https://github.com/Prasad-PingFederate/hermes-time-machine.git ~/.hermes/plugins/hermes-time-machine
```

### 2. Run Live Walkthrough
```bash
python ~/.hermes/plugins/hermes-time-machine/scripts/demo_walkthrough.py
```
Or in chat:
```text
/rewind demo
```

### 3. Demo Video Guide
See [DEMO_SCRIPT.md](DEMO_SCRIPT.md) for the exact step-by-step <90s video narration script.
