# 🛡️ hermes-time-machine

**Zero-Risk Autonomous Time Travel & Self-Healing for Hermes Agent & Desktop**  
*Built for the Nous Research Hermes Agent Hackathon*

[![Hermes Plugin Validate](https://img.shields.io/badge/hermes_plugin_validate-16%2F16_PASS-success.svg)](#official-hermes-admission-validator)
[![Tests](https://img.shields.io/badge/pytest-26%2F26_PASS-brightgreen.svg)](#unit--integration-test-suite-pytest)
[![Unittest](https://img.shields.io/badge/unittest-26%2F26_PASS-brightgreen.svg)](#standard-library-unittest-compatibility)
[![Version](https://img.shields.io/badge/version-0.3.0-blue.svg)](plugin.yaml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Demo Video Script](https://img.shields.io/badge/Demo_Script-75s_Pacing-purple.svg)](DEMO_SCRIPT.md)

---

## ⚡ Why Time Machine Over Built-In `/rollback`?

While Hermes Agent includes basic `/rollback` support, `hermes-time-machine` elevates autonomous development into a **self-healing, zero-risk, surgical engineering platform**:

| Capability | Built-In `/rollback` | `hermes-time-machine` |
| :--- | :--- | :--- |
| **Recovery Workflow** | Multi-turn manual reset | **1-Call Autonomous Recovery (`time_machine_recover`):** Diagnoses culprit files from tracebacks, reverts only broken files, unlinks stray temp files, and re-verifies the test suite inside the same tool call. |
| **File Granularity** | Blunt whole-repo rollback | **Surgical Per-File Rollback:** Reverts broken `billing.py` while preserving newly written `tax.py` feature additions. |
| **Safety Net** | Overwrites disk state | **Rollback of a Rollback:** Captures automatic `pre_restore_safety_backup` before modifying disk state—zero data loss guarantee. |
| **Inspection & Control** | Blind rollback | **First-Class Dry-Run:** `confirm=false` or `dry_run=true` returns structured preview of affected files without touching disk. |
| **Desktop Experience** | Text-only CLI | **Interactive Visual Timeline Scrubber:** Horizontal step scrubber in Hermes Desktop with split diffs, branch badges (`🌿 Forked`), and 1-click **"⚡ Restore & Retry"**. |
| **Investigation** | Manual git diff inspection | **"What Introduced This?" Diff Search (`time_machine_search`):** Search code diffs across history to pinpoint exact snapshots and line numbers. |

---

## 🛡️ Rock-Solid Security & Control Guarantees

1. **Zero Command Injection (`shell=False`):**
   Auto-verification uses strict `shlex.split(verify_command)` and executes exclusively via `subprocess.run(args, shell=False)`. Shell chaining and redirection operators (`;`, `&&`, `||`, `|`, `` ` ``, `$(`, `>`, `<`, `\n`, `\r`) are blocked by security policy.
2. **Safe Test Runner Allowlist:**
   Verification commands are restricted to standard test and build runners (`pytest`, `python -m`, `npm`, `pnpm`, `yarn`, `bun`, `cargo`, `go`, `ruff`, `flake8`, `mypy`, `vitest`, `jest`). Generic `python` scripts without `-m` are rejected.
3. **Zero Private Core Imports (Rule 9 Compliant):**
   The plugin strictly uses public plugin APIs and native shadow-git plumbing (`refs/hermes/<hash16>`). Zero imports of internal modules (e.g. `tools.checkpoint_manager`). Passes `hermes plugins validate` with **0 errors and 0 Rule 9 warnings**.
4. **Path Traversal Prevention:**
   All target files are strictly validated against workspace boundaries (`validate_safe_path`). Any attempt to traverse outside the workspace root (`../`) is blocked.
5. **Zero External Secrets or Network Exfiltration:**
   Operates 100% offline and locally. Declares `requires_env: []` with zero external telemetry or network calls.

---

## 🌟 Overview

`hermes-time-machine` transforms Hermes Agent into a fearless, zero-risk autonomous software developer. By bridging Hermes's native shadow-git filesystem checkpoints (`~/.hermes/checkpoints/store/`) with session message history (`~/.hermes/state.db`) into an atomic SQLite ledger (`time_machine.db`), it provides both human operators and autonomous agents with:

- **1-Call Autonomous Recovery with Auto-Verification (`time_machine_recover`):** Automatically correlates compiler/test errors with recent file edits, identifies root-cause culprits, surgically reverts only the broken files while preserving concurrent good features, unlinks newly created stray files, and re-runs the failed test suite inside the same tool call (`auto_verify=true`).
- **"What Introduced This?" Diff & History Search (`time_machine_search`):** Full-text search across historical snapshot diffs, modified files, and tool arguments to pinpoint the exact snapshot ID, turn, and `+`/`-` line numbers.
- **"Rollback of a Rollback" Safety:** Automatically captures a pre-restore safety snapshot before any file rollback, making every action 100% reversible.
- **Surgical Per-File Restore & Newly-Created File Cleanup:** Revert only the specific broken files (`file_paths=['billing.py']`) while preserving untouched features and cleanly unlinking untracked temporary files from disk.
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
├── ledger.py            # SQLite ledger + shadow-git & state.db bridge
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
│   └── test_plugin.py   # 26 Unit & integration tests (100% green: unittest + pytest)
├── DEMO_SCRIPT.md       # Official 75s video narration & walkthrough script
├── catalog-entry.yaml   # Verified template for Nous Research plugin-catalog
└── README.md
```

### 1. Agent Toolset (`tools.py`)
1. `time_machine_recover`: Autonomous self-healing diagnostic, surgical recovery, and test verification tool.
2. `time_machine_search`: Search historical diffs, symbols, and +/- lines to pinpoint root-cause changes.
3. `time_machine_list_snapshots`: Inspect captured checkpoints in the current session.
4. `time_machine_diff`: View unified, structured, and arbitrary A ↔ B / live drift diffs.
5. `time_machine_restore`: Surgically restore specific files or whole workspace with safety backup.
6. `time_machine_branch`: Fork a new exploration session from a historical checkpoint.

### 2. CLI Slash Command (`/rewind`)
- `/rewind` or `/rewind list`: Formatted table of recent session checkpoints.
- `/rewind diff <id> [compare_to]`: Diff snapshot vs parent, live working tree, or other snapshot.
- `/rewind restore <id> [--files file1,file2]`: Instant surgical rollback.
- `/rewind recover`: Run 1-call autonomous diagnosis and culprit rollback.
- `/rewind search <query> [file_path]`: Search code diffs and snapshot metadata.
- `/rewind export`: Output full session history formatted as Markdown.
- `/rewind demo`: Run the reproducible 7-step live walkthrough.

### 3. Native Hermes Desktop UI (`desktop/plugin.js`)
- **Status Bar:** Dynamic 3-state badge (`🛡️ Safe Mode • X snaps`, `⚠️ Self-Heal Available • Revert Ready`, `✅ Recovered • Workspace Clean`).
- **Horizontal Timeline Scrubber:** Interactive node track (`● ─── ● ─── ● ─── ⚠️ ─── 🌿 ─── ✅`) with hover mini-previews.
- **Studio Diff Inspector:** Side-by-side split & unified diffs with line numbers and 1-click **"Revert This File Only"** headers.
- **Action Buttons:** **"📸 Snapshot"**, **"⚡ Restore & Retry"**, **"📥 Export"**, and **"Prune Old"**.

---

## 🔒 Security & Admission Disclosure

In accordance with Hermes Plugin Admission guidelines:
- **Shadow-Git Access:** `hermes-time-machine` reads and writes shadow commits to Hermes's internal checkpoint repository (`~/.hermes/checkpoints/store/refs/hermes/<hash16>`) via native git plumbing to provide zero-overhead snapshots without cluttering user Git history.
- **State Database Access:** Reads session records from `~/.hermes/state.db` solely to synchronize session branch points and conversation watermarks during rewind.
- **Strict Sandbox Bounds:** All file operations validate that paths resolve strictly within `Path(workspace_dir).resolve()`.
- **Zero Network Exfiltration:** No external network or telemetry calls are made; all operations are local and offline.

---

## 🧪 Verification & Testing

### Official Hermes Admission Validator
Tested against `hermes plugins validate` from Hermes core:
```text
✓ manifest — plugin.yaml parses
✓ manifest fields — name, version, description present
✓ requires_hermes — spec '>=0.21.0' parses
✓ config schema — shape valid
✓ requires_env — all entries UPPER_SNAKE
✓ oauth — not declared
✓ loadable — entry: __init__.py, desktop/plugin.js
✓ python dependencies — none declared
✓ capability probe — register() ran in isolation
✓ declared tools — matches registrations
✓ declared hooks — matches registrations
✓ declared middleware — matches registrations
✓ built-in tool collisions — no collisions
✓ security scan — safe
✓ no core override — no runtime rebinds of Hermes core
✓ desktop surface — stays inside the plugin SDK surface

Validation passed. (16/16 Checks Passed, 0 Errors, 0 Warnings)
```

### Official Hermes Plugin Doctor
```text
Plugin Doctor: C:\Users\prasad.dammai\Hermes-time-machine
  manifest: time-machine 0.3.0 (standalone)
  OK: runtime discovery, manifest parsing, import, and registration passed
  registrations: 6 tool(s), 4 hook(s)
```

### Unit & Integration Test Suite (`pytest` & `unittest`)
All 26 tests pass 100% cleanly under both standard library `unittest` (zero external dependencies) and `pytest`:
```text
tests/test_plugin.py::test_api_recover_and_export_endpoints PASSED       [  3%]
tests/test_plugin.py::test_api_search_endpoint PASSED                    [  7%]
tests/test_plugin.py::test_arbitrary_and_working_tree_diff PASSED        [ 11%]
tests/test_plugin.py::test_dashboard_api_endpoints PASSED               [ 15%]
tests/test_plugin.py::test_demo_walkthrough_e2e PASSED                   [ 19%]
tests/test_plugin.py::test_diagnose_failure_and_culprit_detection PASSED [ 23%]
tests/test_plugin.py::test_ledger_record_and_self_heal PASSED            [ 26%]
tests/test_plugin.py::test_newly_created_file_unlinked_on_restore PASSED [ 30%]
tests/test_plugin.py::test_parse_unified_diff PASSED                     [ 34%]
tests/test_plugin.py::test_path_traversal_rejection PASSED               [ 38%]
tests/test_plugin.py::test_prune_snapshots_and_storage_stats PASSED      [ 42%]
tests/test_plugin.py::test_recover_auto_verify_safe_allowlist PASSED     [ 46%]
tests/test_plugin.py::test_search_snapshots_diff_content PASSED          [ 50%]
tests/test_plugin.py::test_security_dry_run_preserves_disk_state PASSED [ 53%]
tests/test_plugin.py::test_security_verify_command_allowlist_and_injection_blocks PASSED [ 57%]
tests/test_plugin.py::test_security_zero_private_core_imports PASSED     [ 61%]
tests/test_plugin.py::test_self_healing_transform_hint PASSED            [ 65%]
tests/test_plugin.py::test_slash_command_recover_and_export PASSED       [ 69%]
tests/test_plugin.py::test_slash_command_rewind PASSED                    [ 73%]
tests/test_plugin.py::test_slash_command_rewind_demo PASSED              [ 76%]
tests/test_plugin.py::test_slash_command_search PASSED                   [ 80%]
tests/test_plugin.py::test_surgical_file_restore_and_safety_backup PASSED [ 84%]
tests/test_plugin.py::test_time_machine_recover_auto_apply PASSED         [ 88%]
tests/test_plugin.py::test_time_machine_recover_tool_handler PASSED      [ 92%]
tests/test_plugin.py::test_time_machine_search_tool_handler PASSED       [ 96%]
tests/test_plugin.py::test_tool_handlers_and_confirmation PASSED         [100%]

======================== 26 passed in 73.24s (0:01:13) ========================
```

---

## 🚀 Quick Start

### 1. Installation
Clone into your Hermes plugins directory:
```bash
git clone https://github.com/Prasad-PingFederate/hermes-time-machine.git ~/.hermes/plugins/time-machine
```

### 2. Run Live Walkthrough
```bash
python ~/.hermes/plugins/time-machine/scripts/demo_walkthrough.py
```
Or in chat:
```text
/rewind demo
```

### 3. Demo Video Guide
See [DEMO_SCRIPT.md](DEMO_SCRIPT.md) for the exact step-by-step 75s video narration script.
