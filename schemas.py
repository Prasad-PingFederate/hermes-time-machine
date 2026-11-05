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
    "description": "Show the unified diff and metadata for a specific snapshot ID.",
    "parameters": {
        "type": "object",
        "properties": {
            "snapshot_id": {"type": "integer", "description": "Snapshot ID from the ledger"},
        },
        "required": ["snapshot_id"],
    },
}

RESTORE_SCHEMA = {
    "name": "time_machine_restore",
    "description": "Restore workspace files to the state of a given snapshot. Optionally also rewind the conversation.",
    "parameters": {
        "type": "object",
        "properties": {
            "snapshot_id": {"type": "integer"},
            "rewind_chat": {"type": "boolean", "default": False, "description": "Also rewind conversation to this point"},
            "confirm": {"type": "boolean", "default": False, "description": "Must be true to actually restore"},
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
            "snapshot_id": {"type": "integer"},
            "new_session_title": {"type": "string", "default": "Branched from Time Machine"},
        },
        "required": ["snapshot_id"],
    },
}
