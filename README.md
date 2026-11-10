# 🛡️ hermes-time-machine

**Zero-Risk Autonomous Time Travel & Self-Healing for Hermes Agent & Desktop**  
*Built for the Nous Research Hermes Agent Hackathon*

[![Hermes Plugin Validate](https://img.shields.io/badge/hermes_plugin_validate-16%2F16_PASS-success.svg)](#official-hermes-admission-validator)
[![Version](https://img.shields.io/badge/version-0.4.1-blue.svg)](plugin.yaml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/Prasad-PingFederate/hermes-time-machine/actions/workflows/ci.yml/badge.svg)](https://github.com/Prasad-PingFederate/hermes-time-machine/actions/workflows/ci.yml)

---

## ⚡ What is Time Machine?

`hermes-time-machine` transforms Hermes Agent into a fearless, zero-risk autonomous software developer. By maintaining an isolated bare git store and SQLite ledger inside the plugin's private data directory (`$HERMES_HOME/plugin-data/time-machine/`), it provides both human operators and autonomous agents with:

- **Autonomous Self-Healing Recovery (`time_machine_recover`):** Diagnoses broken tests, identifies root-cause culprit edits from tracebacks, safely stages restoration plans in dry-run mode, and returns `suggested_verify_command` for execution through Hermes' native `terminal` tool.
- **Surgical Per-File Rollback (`time_machine_restore`):** Reverts only the broken file while preserving newly added features in other files. Never executes `git checkout -f` on the user's repository.
- **Rollback of a Rollback Safety Net:** Captures an automatic `pre_restore_safety_backup` snapshot in the plugin's bare store before any file is restored—zero data loss guarantee.
- **Human Approval Guard:** Restorations default to preview mode (`dry_run=true`) and require explicit user approval (via `/tm restore <snapshot_id>` or interactive confirmation) before modifying files on disk.
- **"What Introduced This?" Diff Search (`time_machine_search`):** Search code diffs across history to pinpoint exact snapshots and line numbers.
- **Hermes Desktop & Dashboard Surfaces:** Status bar indicator (`statusBar.right`), timeline sidebar pane (`panes`), and web dashboard tab.

---

## 🛡️ Architecture & Security Guarantees

1. **Zero Host Test Runner `subprocess.run`:**
   `time_machine_recover` never executes user verification commands directly on the host. Instead, it diagnoses the failure, stages the recovery, and returns `suggested_verify_command` in its structured payload. The model then executes the test via Hermes's own `terminal` tool under the user's configured backend (Docker, SSH, or local host) with proper user approval and environment isolation.

2. **Real Human Approval Guard:**
   The model cannot unilaterally overwrite disk files by passing flags. All restorations default to `dry_run = True`. Physical disk updates require explicit human approval via the `/tm restore <id>` slash command or interactive user confirmation.

3. **Isolated Private Storage (`$HERMES_HOME/plugin-data/time-machine/`):**
   All plugin snapshots and metadata are kept strictly in the plugin's private data directory:
   - SQLite Ledger: `$HERMES_HOME/plugin-data/time-machine/ledger.db`
   - Bare Git Store: `$HERMES_HOME/plugin-data/time-machine/store` (project refs at `refs/time-machine/{hash16}`)
   The plugin never touches or mutates `$HERMES_HOME/state.db` or core checkpoint directories.

4. **No git commands against the project repository:**
   Snapshots, restore, commit, clone, push, and pull use only `git --git-dir=<plugin store>`. The plugin never runs `git commit`, `git checkout`, `git push`, `git pull`, or `git clone` in the user's repository. `.env`, `*.pem`, `*.key`, `node_modules`, and virtualenvs are left out of the private store. `.env.example` is kept.

5. **Path Traversal & Boundary Safety:**
   All target files are strictly scoped to `ctx.session_cwd` and validated with `resolve()`. Paths traversing outside the workspace boundary are rejected.

6. **Native Conversation State Handled by Core:**
   Conversation branch creation and tree management are delegated to Hermes core's native `/rewind` and `/branch` commands.

---

## 🏛️ Repository Layout

```text
hermes-time-machine/
├── plugin.yaml          # Manifest declaring 5 tools, 4 hooks, and /tm command
├── __init__.py          # Plugin lifecycle hooks, /tm slash command, and context bridge
├── ledger.py            # SQLite ledger + private bare git store manager
├── schemas.py           # Tool schemas (5 tools with dry-run default)
├── tools.py             # Tool handlers strictly scoped to ctx.session_cwd
├── dashboard/
│   ├── manifest.json    # Declares backend FastAPI extension router
│   ├── plugin_api.py    # REST endpoints (/status, /snapshots, /diff, /restore, /recover, /search)
│   └── dist/
│       └── index.js     # Web dashboard UI bundle registered with window.__HERMES_PLUGINS__
├── desktop/
│   └── plugin.js        # Desktop SDK UI (statusBar.right chip + panes timeline)
├── scripts/
│   └── demo_walkthrough.py # Standalone reproducible walkthrough script
├── tests/
│   ├── test_plugin.py   # Unit & integration test suite
│   └── desktop_plugin.test.mjs # Automated Node.js Desktop SDK test
└── README.md
```

---

## 🔧 Tools & Commands

### 1. Agent Toolset (`tools.py`)
- `time_machine_list_snapshots`: List snapshots captured during the current session.
- `time_machine_diff`: View unified diff between a snapshot and current workspace (or between two snapshots).
- `time_machine_restore`: Preview or surgically restore files from a snapshot (defaults to `dry_run=true`).
- `time_machine_recover`: Diagnose test failures, identify culprit files, and return recovery diff + `suggested_verify_command`.
- `time_machine_search`: Search code diffs across history by keyword or symbol.

### 2. User Slash Command (`/tm`)
- `/tm` or `/tm list`: Display recent session snapshots in a clean table.
- `/tm diff <snapshot_id>`: Show unified diff of a snapshot against workspace.
- `/tm restore <snapshot_id> [--files f1,f2]`: Explicitly approve and execute a restore. Omit `--files` for a full workspace restore.
- `/tm commit <message>`: Record a named checkpoint in the private store. This is not `git commit` and does not change the project repository.
- `/tm comment <snapshot_id> <text>`: Attach a note to a snapshot in the private ledger.
- `/tm clone <snapshot_id> <new-directory> [--dry-run]`: Copy that snapshot into a new folder. This is not `git clone`. It refuses remotes, the session workspace, and any `.git` directory.
- `/tm push <file.bundle> [--dry-run]`: Write a private checkpoint bundle. This is not `git push`.
- `/tm pull <file.bundle> [--dry-run]`: Import a bundle into the private store. This is not `git pull`.
- `/tm recover`: Run self-healing diagnostic and display recommended recovery plan.
- `/tm search <query>`: Search history for commits introducing specific lines or symbols.
- `/tm export`: Export full session history formatted as Markdown.

### 3. Core Commands for Conversation State
- `/rewind`: Core Hermes command to rewind conversation turns and messages.
- `/branch`: Core Hermes command to branch the session into a new conversation tree.

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
◆ Isolation — runs in the plugin host (plugins.isolation: host)

Validation passed. (16/16 Checks Passed, 0 Errors, 0 Warnings)
```

### Official Hermes Plugin Catalog Validator
```bash
python scripts/validate_plugin_catalog.py plugin-catalog/time-machine.yaml
OK: 1 file(s) valid
```

---

## 🚀 Quick Start

### 1. Installation
Clone into your Hermes plugins directory:
```bash
git clone https://github.com/Prasad-PingFederate/hermes-time-machine.git ~/.hermes/plugins/time-machine
```

### 2. Run Tests
```bash
pytest tests/test_plugin.py -v
node --test tests/desktop_plugin.test.mjs
```
