# 🛡️ hermes-time-machine

**Zero-Risk Autonomous Time Travel & Self-Healing for Hermes Agent & Desktop**  
*Built for the Nous Research Hermes Agent Hackathon*

[![Hermes Plugin Validate](https://img.shields.io/badge/hermes_plugin_validate-100%25_PASS-success.svg)](#verification--testing)
[![Tests](https://img.shields.io/badge/pytest-23%2F23_PASS-brightgreen.svg)](#verification--testing)
[![Version](https://img.shields.io/badge/version-0.4.0-blue.svg)](plugin.yaml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Demo Video Script](https://img.shields.io/badge/Demo_Script-75s_Pacing-purple.svg)](DEMO_SCRIPT.md)

---

## 🌟 Overview

`hermes-time-machine` transforms Hermes Agent into a fearless, zero-risk autonomous software developer. By bridging Hermes's native shadow-git filesystem checkpoints (`~/.hermes/checkpoints/store/`) with session message history (`~/.hermes/state.db`) into an atomic SQLite ledger (`time_machine.db`), it provides both human operators and autonomous agents with:

- **1-Call Autonomous Recovery with Auto-Verification (`time_machine_recover`):** Automatically correlates compiler/test errors with recent file edits, identifies root-cause culprits, surgically reverts only the broken files while preserving concurrent good features, unlinks newly created stray files, and re-runs the failed test suite inside the same tool call (`auto_verify=true`).
- **"What Introduced This?" Diff & History Search (`time_machine_search`):** Full-text search across historical snapshot diffs, modified files, and tool arguments to pinpoint the exact snapshot ID, turn, and `+`/`-` line numbers.
- **"Rollback of a Rollback" Safety:** Automatically captures a pre-restore safety snapshot before any file rollback, making every action 100% reversible.
- **Surgical Per-File Restore & Newly-Created File Cleanup:** Revert only the specific broken files (`file_paths=['billing.py']`) while preserving untouched features and cleanly unlinking untracked temporary files from disk without Git pathspec errors.
- **Arbitrary `A ↔ B` & Live Drift Diffing:** Compare any historical snapshot against another or inspect live uncommitted working tree drift (`compare_to_id="WORKING_TREE"`).
- **Interactive Visual Timeline Scrubber:** Horizontal step scrubber in Hermes Desktop with hover mini-diff previews, parent-child branch badges (`🌿 Forked`), 1-click **"⚡ Restore & Retry"**, and **"📸 Snapshot Now"**.
- **Storage Hygiene & Pruning:** Configurable retention (`max_snapshots_per_session: 50`) with protected baselines and safety backups.
- **Instant Markdown Timeline Export:** Export structured audit logs via `/rewind export` or the Desktop UI.
- **CLI Slash Command:** Full terminal suite `/rewind [list|diff|restore|recover|search|export|demo]`.

---

## 🏛️ Architecture

```text
hermes-time-machine/
├── plugin.yaml          # Hermes unified plugin manifest (v2, 6 tools, 4 hooks)
├── __init__.py          # Lifecycle hooks, slash command & self-heal middleware
├── ledger.py            # SQLite ledger + CheckpointManager & SessionDB bridge
├── schemas.py           # OpenAI-compatible function schemas (6 tools)
├── tools.py             # Agent tool handlers with confirmation guards
├── dashboard/
│   ├── manifest.json    # Declares backend FastAPI extension router
│   └── plugin_api.py    # REST endpoints (/status, /snapshots, /diff, /restore, /recover, /search, /export)
├── desktop/
│   └── plugin.js        # Native Desktop UI via @hermes/plugin-sdk (Scrubber, Split Diff, Studio)
├── scripts/
│   └── demo_walkthrough.py # 7-Step live end-to-end smoke test script
├── tests/
│   └── test_plugin.py   # 23 Unit & integration tests (100% green)
├── DEMO_SCRIPT.md       # Official 75s video narration & walkthrough script
├── catalog-entry.yaml   # Verified template for Nous Research plugin-catalog
└── README.md
```

### 1. Dual-Bridge Ledger Engine (`ledger.py`)
- **Filesystem Bridge:** Computes deterministic project hashes matching Hermes core `_normalize_path` (`hashlib.sha256(expanduser().resolve()).hexdigest()[:16]`). Interacts directly with `refs/hermes/<hash16>` shadow-git store.
- **Structured Diff Engine:** Parses git unified diffs into structured file objects (`additions`, `deletions`, `status`, `diff_chunk`) for rich UI presentation.
- **Conversation Bridge:** Reads message watermarks from `~/.hermes/state.db` to coordinate file rollbacks with conversation history rewinds.
- **Path Traversal Protection:** Validates that all target paths resolve strictly within the workspace root (`validate_safe_path`).

### 2. Autonomous Self-Healing System (`time_machine_recover`)
- **Root-Cause Culprit Detection:** Parses tracebacks and compiler errors to pinpoint exact files modified prior to failure.
- **1-Call Recovery & Verification:** The agent or operator executes `time_machine_recover(auto_apply=true, confirm=true, auto_verify=true)` to restore culprit files, clean up newly created temporary files, and re-run the failed test command in one atomic action without extra LLM round-trips.
- **Transform Middleware:** Automatically attaches actionable self-healing guidance directly into tool failure results.

### 3. Agent Toolset (`tools.py`)
1. `time_machine_recover`: Autonomous self-healing diagnostic, surgical recovery, and test verification tool.
2. `time_machine_search`: Search historical diffs, symbols, and +/- lines to pinpoint root-cause changes.
3. `time_machine_list_snapshots`: Inspect captured checkpoints in the current session.
4. `time_machine_diff`: View unified, structured, and arbitrary A ↔ B / live drift diffs.
5. `time_machine_restore`: Surgically restore specific files or whole workspace with safety backup.
6. `time_machine_branch`: Fork a new exploration session from a historical checkpoint.

### 4. CLI Slash Command (`/rewind`)
- `/rewind` or `/rewind list`: Formatted table of recent session checkpoints.
- `/rewind diff <id> [compare_to]`: Diff snapshot vs parent, live working tree, or other snapshot.
- `/rewind restore <id> [--files file1,file2]`: Instant surgical rollback.
- `/rewind recover`: Run 1-call autonomous diagnosis and culprit rollback.
- `/rewind search <query> [file_path]`: Search code diffs and snapshot metadata.
- `/rewind export`: Output full session history formatted as Markdown.
- `/rewind demo`: Run the reproducible 7-step live walkthrough.

### 5. Native Hermes Desktop UI (`desktop/plugin.js`)
- **Status Bar:** Dynamic 3-state badge (`🛡️ Safe Mode • X snaps`, `⚠️ Self-Heal Available • Revert Ready`, `✅ Recovered • Workspace Clean`).
- **Horizontal Timeline Scrubber:** Interactive node track (`● ─── ● ─── ● ─── ⚠️ ─── 🌿 ─── ✅`) with hover mini-previews.
- **Studio Diff Inspector:** Side-by-side split & unified diffs with line numbers and 1-click **"Revert This File Only"** headers.
- **Action Buttons:** **"📸 Snapshot"**, **"⚡ Restore & Retry"**, **"📥 Export"**, and **"Prune Old"**.

---

## 🔒 Security & Admission Disclosure

In accordance with Hermes Plugin Admission guidelines:
- **Shadow-Git Access:** `hermes-time-machine` reads and writes shadow commits to Hermes's internal checkpoint repository (`~/.hermes/checkpoints/store/refs/hermes/<hash16>`) to provide zero-overhead snapshots without cluttering user Git history.
- **State Database Access:** Reads message IDs from `~/.hermes/state.db` solely to synchronize session branch points and conversation watermarks during rewind.
- **Strict Sandbox Bounds:** All file operations validate that paths resolve strictly within `Path(workspace_dir).resolve()`. Any path traversal (`../`) outside the active workspace is blocked.
- **Zero Network Exfiltration:** No external network or telemetry calls are made; all operations are local and offline.

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
[PASS] declared tools: matches registrations (6/6 tools match 1:1)
[PASS] declared hooks: matches registrations (4/4 hooks match 1:1)
[PASS] declared middleware: matches registrations
[PASS] built-in tool collisions: no collisions
[PASS] security scan: safe
[PASS] no core override: no runtime rebinds of Hermes core
[PASS] desktop surface: stays inside the plugin SDK surface
```

### Unit & Integration Test Suite (`pytest`)
```text
tests/test_plugin.py::test_parse_unified_diff PASSED                     [  4%]
tests/test_plugin.py::test_ledger_record_and_self_heal PASSED            [  8%]
tests/test_plugin.py::test_surgical_file_restore_and_safety_backup PASSED [ 13%]
tests/test_plugin.py::test_tool_handlers_and_confirmation PASSED         [ 17%]
tests/test_plugin.py::test_slash_command_rewind PASSED                    [ 21%]
tests/test_plugin.py::test_self_healing_transform_hint PASSED            [ 26%]
tests/test_plugin.py::test_dashboard_api_endpoints PASSED                 [ 30%]
tests/test_plugin.py::test_path_traversal_rejection PASSED               [ 34%]
tests/test_plugin.py::test_newly_created_file_unlinked_on_restore PASSED [ 39%]
tests/test_plugin.py::test_arbitrary_and_working_tree_diff PASSED        [ 43%]
tests/test_plugin.py::test_prune_snapshots_and_storage_stats PASSED      [ 47%]
tests/test_plugin.py::test_demo_walkthrough_e2e PASSED                   [ 52%]
tests/test_plugin.py::test_slash_command_rewind_demo PASSED              [ 56%]
tests/test_plugin.py::test_diagnose_failure_and_culprit_detection PASSED [ 60%]
tests/test_plugin.py::test_time_machine_recover_auto_apply PASSED         [ 65%]
tests/test_plugin.py::test_time_machine_recover_tool_handler PASSED      [ 69%]
tests/test_plugin.py::test_api_recover_and_export_endpoints PASSED       [ 73%]
tests/test_plugin.py::test_slash_command_recover_and_export PASSED      [ 78%]
tests/test_plugin.py::test_search_snapshots_diff_content PASSED          [ 82%]
tests/test_plugin.py::test_time_machine_search_tool_handler PASSED       [ 86%]
tests/test_plugin.py::test_recover_auto_verify PASSED                    [ 91%]
tests/test_plugin.py::test_api_search_endpoint PASSED                    [ 95%]
tests/test_plugin.py::test_slash_command_search PASSED                   [100%]

============================= 23 passed in 39.30s =============================
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
See [DEMO_SCRIPT.md](DEMO_SCRIPT.md) for the exact step-by-step 75s video narration script.
