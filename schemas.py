"""
Tool schemas for hermes-time-machine.

Declared tools:
- time_machine_list_snapshots
- time_machine_diff
- time_machine_restore
- time_machine_recover
- time_machine_search

Workspace directory is strictly scoped to the session's recorded working directory (ctx.session_cwd).
Restoration defaults to dry-run mode and requires user approval (/tm restore <snapshot_id>).
Conversation rewind and session branching are handled by Hermes core commands (/rewind and /branch).
"""

LIST_SCHEMA = {
    "name": "time_machine_list_snapshots",
    "description": "List recent Time Machine snapshots for the current session. Returns timestamps, tool names, reasons, and affected files.",
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Optional session ID. Defaults to current session."},
            "limit": {"type": "integer", "description": "Max snapshots to return (default 50).", "default": 50},
        },
    },
}

DIFF_SCHEMA = {
    "name": "time_machine_diff",
    "description": "Show unified and structured diff for a specific snapshot ID against its parent, another snapshot, or the live working tree.",
    "parameters": {
        "type": "object",
        "properties": {
            "snapshot_id": {"type": "string", "description": "Target snapshot ID to inspect."},
            "compare_to_id": {
                "type": "string",
                "description": "Optional snapshot ID to diff against, or 'WORKING_TREE' to inspect uncommitted drift since the snapshot. Omit to diff against the previous turn.",
            },
        },
        "required": ["snapshot_id"],
    },
}

RESTORE_SCHEMA = {
    "name": "time_machine_restore",
    "description": (
        "Preview or surgically restore workspace files to the state of a given snapshot from the Time Machine store. "
        "Defaults to a dry-run preview showing planned file changes and diffs. "
        "Actual disk modifications require user approval (via slash command /tm restore <snapshot_id> or Hermes approval prompt)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "snapshot_id": {"type": "string", "description": "Snapshot ID to restore from."},
            "file_path": {"type": "string", "description": "Optional single file to restore surgically."},
            "file_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of specific file paths to restore surgically.",
            },
            "dry_run": {
                "type": "boolean",
                "default": True,
                "description": "Whether to perform a dry-run preview without modifying disk (default true).",
            },
        },
        "required": ["snapshot_id"],
    },
}

RECOVER_SCHEMA = {
    "name": "time_machine_recover",
    "description": (
        "Diagnose tool or test failures, identify culprit files mutated prior to the failure, "
        "and generate a surgical recovery plan with a preview diff. "
        "Returns a suggested verification command for the model to execute through Hermes' terminal tool."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "error_summary": {
                "type": "string",
                "description": "Optional error message, traceback, or test failure output to diagnose. If omitted, uses the last recorded command failure in the session.",
            },
            "session_id": {
                "type": "string",
                "description": "Optional session ID filter. Defaults to active session.",
            },
        },
    },
}

SEARCH_SCHEMA = {
    "name": "time_machine_search",
    "description": (
        "Search across historical snapshot diffs to find which turn introduced, modified, "
        "or removed a specific symbol, line of code, or file."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Code string, function name, symbol, or error message to search for in historical diffs.",
            },
            "session_id": {
                "type": "string",
                "description": "Optional session ID filter. Defaults to current session.",
            },
            "file_path": {
                "type": "string",
                "description": "Optional file path filter to narrow search to a specific file.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum matching snapshots to return (default 10).",
                "default": 10,
            },
        },
        "required": ["query"],
    },
}

ALL_SCHEMAS = [LIST_SCHEMA, DIFF_SCHEMA, RESTORE_SCHEMA, RECOVER_SCHEMA, SEARCH_SCHEMA]
LIST_SNAPSHOTS_SCHEMA = LIST_SCHEMA
