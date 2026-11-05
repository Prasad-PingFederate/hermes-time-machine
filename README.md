# hermes-time-machine

Visual Time Machine plugin for Hermes Agent.

Makes autonomous Hermes sessions fearless by giving you a visual timeline of every agent action, one-click restore, session branching, and tools so Hermes can recover itself when things go wrong.

Built for the Hermes Agent Plugin Hackathon.

## Features
- Auto-records tool calls and links them to Hermes checkpoints (or lightweight snapshots)
- Desktop status-bar shield + right-rail timeline scrubber + diff viewer
- One-click restore of workspace files
- Branch a new session from any point in history
- Agent-usable tools: `time_machine_list_snapshots`, `time_machine_diff`, `time_machine_restore`, `time_machine_branch`

## Install
```bash
hermes plugins install Prasad-PingFederate/hermes-time-machine
```

Or clone into `~/.hermes/plugins/hermes-time-machine`.

## Status
Phase 1 skeleton — hooks, ledger, basic tools, and Desktop UI stubs ready for full integration against the Hermes codebase.
