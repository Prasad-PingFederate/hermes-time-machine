"""
Hermes Time Machine Plugin Entrypoint.
Registers pre_tool_call / post_tool_call / on_session_start hooks and the 4 time_machine_* tools.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

try:
    from .ledger import TimeMachineLedger, resolve_working_dir
    from .tools import register_time_machine_tools
except (ImportError, ValueError):
    from ledger import TimeMachineLedger, resolve_working_dir
    from tools import register_time_machine_tools

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    """Called by Hermes PluginManager on startup."""
    ledger = TimeMachineLedger()

    def on_session_start(
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        try:
            sess = session_id or task_id or "default"
            wdir = resolve_working_dir(session_id=sess)
            ledger.record_pre_tool(
                session_id=sess,
                tool_name="session_start",
                args={},
                working_dir=wdir,
            )
        except Exception as exc:
            logger.debug("hermes-time-machine on_session_start error: %s", exc)

    def on_pre_tool_call(
        tool_name: str = "",
        args: Optional[Dict[str, Any]] = None,
        task_id: Optional[str] = None,
        session_id: Optional[str] = None,
        tool_call_id: Optional[str] = None,
        turn_id: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        try:
            sess = session_id or task_id or "default"
            wdir = resolve_working_dir(args=args, session_id=sess)
            ledger.record_pre_tool(
                session_id=sess,
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                args=args or {},
                working_dir=wdir,
            )
        except Exception as exc:
            logger.debug("hermes-time-machine pre_tool_call error: %s", exc)

    def on_post_tool_call(
        tool_name: str = "",
        args: Optional[Dict[str, Any]] = None,
        result: Any = None,
        task_id: Optional[str] = None,
        session_id: Optional[str] = None,
        tool_call_id: Optional[str] = None,
        turn_id: Optional[str] = None,
        duration_ms: Optional[float] = None,
        status: str = "ok",
        **kwargs: Any,
    ) -> None:
        try:
            sess = session_id or task_id or "default"
            ledger.record_post_tool(
                session_id=sess,
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                args=args or {},
                status=status,
                duration_ms=duration_ms,
            )
        except Exception as exc:
            logger.debug("hermes-time-machine post_tool_call error: %s", exc)

    ctx.register_hook("on_session_start", on_session_start)
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)

    register_time_machine_tools(ctx, ledger=ledger)
