LIST_SCHEMA = {
    "name": "time_machine_list_snapshots",
    "description": "List recent Time Machine snapshots for the current session or a given session_id. Returns timestamps, tool names, and affected files.",
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Optional session ID. Defaults to current session."},
            "limit": {"type": "integer", "description": "Max snapshots to return", "default": 50},
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

ALL_SCHEMAS = [LIST_SCHEMA, DIFF_SCHEMA, RESTORE_SCHEMA, BRANCH_SCHEMA]
