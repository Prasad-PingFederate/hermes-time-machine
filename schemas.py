LIST_SCHEMA = {
    "name": "time_machine_list_snapshots",
    "description": "List recent Time Machine snapshots for the current session or a given session_id. Returns timestamps, tool names, and affected files.",
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Optional session ID. Defaults to current session."},
            "limit": {"type": "integer", "description": "Max snapshots to return", "default": 50},
            "working_dir": {"type": "string", "description": "Optional working directory override."},
        },
    },
}

DIFF_SCHEMA = {
    "name": "time_machine_diff",
    "description": "Show unified and structured diff for a specific snapshot ID against its parent, another snapshot, or the live working tree.",
    "parameters": {
        "type": "object",
        "properties": {
            "snapshot_id": {"type": "string", "description": "Target snapshot ID to inspect"},
            "compare_to_id": {
                "type": "string",
                "description": "Optional snapshot ID to diff against, or 'WORKING_TREE' to inspect uncommitted drift since the snapshot. Omit to diff against the previous turn.",
            },
            "working_dir": {"type": "string", "description": "Optional working directory override"},
        },
        "required": ["snapshot_id"],
    },
}

RESTORE_SCHEMA = {
    "name": "time_machine_restore",
    "description": "Restore workspace files to the state of a given snapshot. Supports surgical single/multi-file restore without rewinding conversation, or full workspace rollback.",
    "parameters": {
        "type": "object",
        "properties": {
            "snapshot_id": {"type": "string", "description": "Snapshot ID or checkpoint hash to restore"},
            "file_path": {"type": "string", "description": "Optional single file to restore"},
            "file_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of specific file paths to restore surgically",
            },
            "rewind_conversation": {"type": "boolean", "default": False, "description": "Also rewind conversation to this point"},
            "confirm": {"type": "boolean", "default": True, "description": "Must be true to apply restore"},
            "working_dir": {"type": "string", "description": "Optional working directory override"},
        },
        "required": ["snapshot_id"],
    },
}

BRANCH_SCHEMA = {
    "name": "time_machine_branch",
    "description": "Create a new session branched from a snapshot (files restored + conversation forked).",
    "parameters": {
        "type": "object",
        "properties": {
            "snapshot_id": {"type": "string"},
            "branch_title": {"type": "string", "default": "Branched from Time Machine"},
            "restore_files": {"type": "boolean", "default": True},
            "working_dir": {"type": "string", "description": "Optional working directory override"},
        },
        "required": ["snapshot_id"],
    },
}

RECOVER_SCHEMA = {
    "name": "time_machine_recover",
    "description": (
        "Autonomous self-healing diagnostic and recovery tool. Analyzes recent failed tool results "
        "or test tracebacks, identifies culprit files edited before the failure, optionally "
        "surgically restores them, and automatically re-verifies test suites in one step."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "error_summary": {
                "type": "string",
                "description": "Optional error message, traceback, or test failure output to diagnose. If omitted, uses the last recorded command failure in the session.",
            },
            "auto_apply": {
                "type": "boolean",
                "description": "If true, immediately applies surgical restoration of identified culprit files to the pre-error state.",
                "default": False,
            },
            "confirm": {
                "type": "boolean",
                "description": "Confirmation guard required when auto_apply is true (default true).",
                "default": True,
            },
            "verify_command": {
                "type": "string",
                "description": "Optional test or build command to run after restoration to verify the fix (e.g. 'pytest -q'). Defaults to the last failed command.",
            },
            "auto_verify": {
                "type": "boolean",
                "description": "Whether to automatically execute verify_command after restore to confirm green tests (default true).",
                "default": True,
            },
            "session_id": {
                "type": "string",
                "description": "Optional session ID filter. Defaults to active session.",
            },
            "working_dir": {
                "type": "string",
                "description": "Optional working directory override.",
            },
        },
    },
}

SEARCH_SCHEMA = {
    "name": "time_machine_search",
    "description": (
        "Search across historical snapshot diffs and file edits to find which turn "
        "introduced, modified, or removed a specific symbol, line of code, or file."
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
            "working_dir": {
                "type": "string",
                "description": "Optional working directory override.",
            },
        },
        "required": ["query"],
    },
}

ALL_SCHEMAS = [LIST_SCHEMA, DIFF_SCHEMA, RESTORE_SCHEMA, BRANCH_SCHEMA, RECOVER_SCHEMA, SEARCH_SCHEMA]
