# 🛡️ hermes-time-machine

**Zero-Risk Autonomous Time Travel & Self-Healing for Hermes Agent**  
*Built for the Nous Research Hermes Agent Hackathon*

[![Hermes Plugin Validate](https://img.shields.io/badge/hermes_plugin_validate-100%25_PASS-success.svg)](#verification--testing)
[![Tests](https://img.shields.io/badge/pytest-7%2F7_PASS-brightgreen.svg)](#verification--testing)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

---

## 🌟 Overview

`hermes-time-machine` transforms Hermes Agent into a fearless, zero-risk autonomous software developer. By bridging Hermes's native shadow-git filesystem checkpoints (`~/.hermes/checkpoints/store/`) with session message history (`~/.hermes/state.db`) into an atomic SQLite ledger (`time_machine.db`), it provides both human operators and autonomous agents with:

- **"Rollback of a Rollback" Safety:** Automatically captures a pre-restore safety snapshot before any file rollback, making every action 100% reversible.
- **Surgical Per-File Restore:** Revert only the specific broken files (`file_paths=['src/auth.py']`) while keeping good changes untouched.
- **Autonomous Self-Healing Loop:** Directly alerts Hermes when commands or tests fail after code edits, giving the agent the context and tools to self-heal.
- **Unified & Split (Side-by-Side) Diff Viewer:** Interactive visual diffing with syntax highlighting and line numbers in Hermes Desktop.
- **Session Branching:** Fork whole new exploration sessions from historical checkpoints without losing context.
- **CLI Slash Command:** Use `/rewind [list|diff|restore]` directly from the terminal console.

---

## 🏛️ Architecture

```text
hermes-time-machine/
├── plugin.yaml          # Hermes unified plugin manifest (v2)
├── __init__.py          # Lifecycle hooks, slash command & self-heal injection
├── ledger.py            # SQLite ledger + CheckpointManager & SessionDB bridge
├── schemas.py           # OpenAI-compatible function schemas
├── tools.py             # Agent tool handlers with confirmation guards
├── dashboard/
│   ├── manifest.json    # Declares backend FastAPI extension router
│   └── plugin_api.py    # REST endpoints (/status, /snapshots, /diff, /restore, /branch)
├── desktop/
│   └── plugin.js        # Native Desktop UI via @hermes/plugin-sdk
├── tests/
│   └── test_plugin.py   # Comprehensive unit & integration tests
└── README.md
```

### 1. Dual-Bridge Ledger Engine (`ledger.py`)
- **Filesystem Bridge:** Computes deterministic project hashes matching Hermes core `_normalize_path` (`hashlib.sha256(expanduser().resolve()).hexdigest()[:16]`). Interacts directly with `refs/hermes/<hash16>` shadow-git store.
- **Structured Diff Engine:** Parses git unified diffs into structured file objects (`additions`, `deletions`, `status`, `diff_chunk`) for rich UI presentation.
- **Conversation Bridge:** Reads message watermarks from `~/.hermes/state.db` to coordinate file rollbacks with conversation history rewinds.

### 2. Autonomous Self-Healing System (`__init__.py`)
- Injects a cache-friendly system prompt section (`time_machine_self_heal`) via `ctx.register_system_prompt_section`.
- Uses `transform_tool_result` to automatically attach recovery notices whenever a terminal or test command fails immediately following file modifications.

### 3. Agent Toolset (`tools.py`)
- `time_machine_list_snapshots`: Inspect captured checkpoints in the current session.
- `time_machine_diff`: View raw and structured diffs for any turn.
- `time_machine_restore`: Surgically restore specific files (`file_paths=[...]`) or the entire workspace with confirmation guard.
- `time_machine_branch`: Fork a new exploration session from a historical checkpoint.

### 4. CLI Slash Command (`/rewind`)
- `/rewind` or `/rewind list`: Formatted table of recent session checkpoints.
- `/rewind diff [id]`: Quick diff summary in console.
- `/rewind restore <id> [--files file1,file2]`: Instant command-line rollback.

### 5. Native Hermes Desktop UI (`desktop/plugin.js`)
- **Status Bar:** Shield badge (`🛡️ Safe Mode • X snaps`), dynamically switching to amber warning (`⚠️ Self-Heal • X snaps`) upon build errors.
- **Right-Rail Timeline:** Color-coded event stream (blue for writes, purple for commands, green for safety backups, red for failures, gold for branches) with search filtering.
- **Studio Diff Inspector:** Toggle between Unified Diff and Side-by-Side (Split) Diff with per-file collapsible cards and 1-click **"Revert This File"** buttons.

---

## 🧪 Verification & Testing

### Official Hermes Admission Validator
Tested against `hermes_cli.plugin_validate` from Hermes core:
```text
Validation OK: True
[PASS] manifest: plugin.yaml parses
[PASS] manifest fields: name, version, description present
[PASS] config schema: shape valid
[PASS] requires_env: all entries UPPER_SNAKE
[PASS] loadable: entry: __init__.py, desktop/plugin.js
[PASS] capability probe: register() ran in isolation
[PASS] declared tools: matches registrations
[PASS] declared hooks: matches registrations
[PASS] built-in tool collisions: no collisions
[PASS] security scan: safe
[PASS] no core override: no runtime rebinds of Hermes core
[PASS] desktop surface: stays inside the plugin SDK surface
```

### Unit & Integration Test Suite (`pytest`)
```text
tests/test_plugin.py::test_parse_unified_diff PASSED                     [ 14%]
tests/test_plugin.py::test_ledger_record_and_self_heal PASSED            [ 28%]
tests/test_plugin.py::test_surgical_file_restore_and_safety_backup PASSED [ 42%]
tests/test_plugin.py::test_tool_handlers_and_confirmation PASSED         [ 57%]
tests/test_plugin.py::test_slash_command_rewind PASSED                    [ 71%]
tests/test_plugin.py::test_self_healing_transform_hint PASSED            [ 85%]
tests/test_plugin.py::test_dashboard_api_endpoints PASSED               [100%]

============================== 7 passed in 0.87s ==============================
```

---

## 🚀 Installation & Usage

```bash
# Install directly via Hermes CLI
hermes plugins install Prasad-PingFederate/hermes-time-machine
```

Or clone into your local Hermes plugins folder:
```bash
git clone https://github.com/Prasad-PingFederate/hermes-time-machine.git ~/.hermes/plugins/hermes-time-machine
```

---

## 📄 License
MIT License. Built for the Nous Research Hermes Agent Hackathon.
