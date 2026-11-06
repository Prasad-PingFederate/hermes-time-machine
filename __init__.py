"""
Hermes Time Machine Plugin Entrypoint.
Registers pre_tool_call / post_tool_call / on_session_start / transform_tool_result hooks,
the 4 time_machine_* tools, /rewind slash command, and self-healing system prompt section.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

try:
    from .ledger import TimeMachineLedger, resolve_working_dir, MUTATING_TOOLS
    from .tools import register_time_machine_tools
except (ImportError, ValueError):
    from ledger import TimeMachineLedger, resolve_working_dir, MUTATING_TOOLS
    from tools import register_time_machine_tools

logger = logging.getLogger(__name__)

# Track the last known-good mutating snapshot per session for autonomous self-healing
_last_safe_snapshot: Dict[str, str] = {}


def register(ctx, ledger: Optional[TimeMachineLedger] = None) -> None:
    """Called by Hermes PluginManager on startup."""
    ledger = ledger or TimeMachineLedger()

    def on_session_start(
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        try:
            sess = session_id or task_id or "default"
            _last_safe_snapshot[sess] = ""
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
            snap = ledger.record_pre_tool(
                session_id=sess,
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                args=args or {},
                working_dir=wdir,
            )
            if snap and tool_name in MUTATING_TOOLS:
                _last_safe_snapshot[sess] = snap.get("id") or ""
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
            is_cmd_tool = tool_name in {"terminal", "bash", "execute_code", "run_command"}
            res_str = str(result or "")
            has_error = (
                status != "ok"
                or any(err in res_str for err in ("FAILED", "Traceback", "SyntaxError", "Error:", "exit code"))
            )

            self_heal = False
            rec_snap = None
            if is_cmd_tool and has_error and _last_safe_snapshot.get(sess):
                self_heal = True
                rec_snap = _last_safe_snapshot.get(sess)

            ledger.record_post_tool(
                session_id=sess,
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                args=args or {},
                status="error" if has_error else status,
                duration_ms=duration_ms,
                self_heal_candidate=self_heal,
                recommended_snapshot_id=rec_snap,
            )
        except Exception as exc:
            logger.debug("hermes-time-machine post_tool_call error: %s", exc)

    def on_transform_tool_result(
        tool_name: str = "",
        result: Any = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        **kwargs: Any,
    ) -> Any:
        try:
            sess = session_id or task_id or "default"
            rec_snap = _last_safe_snapshot.get(sess)
            if rec_snap and tool_name in {"terminal", "bash", "execute_code", "run_command"}:
                res_str = str(result or "")
                if any(err in res_str for err in ("FAILED", "Traceback", "SyntaxError", "Error:", "exit code")):
                    diag = ledger.diagnose_failure(session_id=sess, error_text=res_str)
                    culprits = diag.get("culprit_files", [])
                    culprit_repr = repr(culprits) if culprits else "None"
                    target_snap = diag.get("recommended_snapshot_id") or rec_snap
                    conf = diag.get("confidence", "high")

                    hint = (
                        f"\n\n[🛡️ Time Machine Self-Heal Notice & Recommendation]:\n"
                        f"Command failed after recent file mutations.\n"
                        f"• Identified culprit file(s): {culprits if culprits else 'recently modified workspace files'}\n"
                        f"• Recommended safe checkpoint: '{target_snap}' (Confidence: {conf})\n"
                        f"• Quick 1-call autonomous recovery:\n"
                        f"  `time_machine_recover(auto_apply=true, confirm=true)`\n"
                        f"• Or surgical restore:\n"
                        f"  `time_machine_restore(snapshot_id='{target_snap}', file_paths={culprit_repr}, confirm=true)`"
                    )
                    return res_str + hint
        except Exception as exc:
            logger.debug("hermes-time-machine transform_tool_result error: %s", exc)
        return result

    def handle_rewind_command(raw_args: str = "", **kwargs: Any) -> str:
        parts = (raw_args or "").strip().split()
        subcmd = parts[0].lower() if parts else "list"
        session_id = kwargs.get("session_id") or "default"

        if subcmd in ("list", "ls", ""):
            snaps = ledger.list_snapshots(session_id=session_id, limit=10)
            if not snaps:
                return "⏱️ No Time Machine snapshots recorded for this session yet."
            lines = ["⏱️ Time Machine Snapshots (Recent):"]
            lines.append(f"{'ID':<28} {'TOOL':<14} {'STATUS':<8} {'FILES':<6} {'DIFF'}")
            lines.append("-" * 72)
            for s in snaps:
                sid = s.get("id", "")
                tool = s.get("tool_name", "")[:13]
                st = s.get("status", "ok")[:7]
                fc = str(s.get("files_changed", 0))
                diff_stat = f"+{s.get('insertions', 0)} / -{s.get('deletions', 0)}"
                lines.append(f"{sid:<28} {tool:<14} {st:<8} {fc:<6} {diff_stat}")
            lines.append("\nCommands: /rewind diff <id> [compare_to] | /rewind restore <id> [--files f1,f2] | /rewind recover | /rewind export | /rewind demo")
            return "\n".join(lines)

        elif subcmd == "demo":
            try:
                from scripts.demo_walkthrough import run_demo
            except ImportError:
                import sys
                from pathlib import Path
                root = Path(__file__).resolve().parent
                if str(root) not in sys.path:
                    sys.path.insert(0, str(root))
                from scripts.demo_walkthrough import run_demo

            res = run_demo(ledger=ledger, quiet=True)
            if res.get("ok"):
                st = res.get("steps", {})
                b_snap = (st.get("step1_baseline") or {}).get("snapshot_id", "snap_1")
                f_snap = (st.get("step2_feature") or {}).get("snapshot_id", "snap_2")
                return (
                    f"🎉 Time Machine E2E Live Demo Passed 100%!\n"
                    f"- Step 1: Baseline project created & snapshotted ({b_snap})\n"
                    f"- Step 2: Feature 'tax.py' added & checkpointed ({f_snap})\n"
                    f"- Step 3: Buggy edit + new temporary file triggered pytest failure\n"
                    f"- Step 4: Autonomous Self-Heal recommended restore to {f_snap}\n"
                    f"- Step 5: Surgical restore reverted billing.py, unlinked temp file, preserved tax.py\n"
                    f"- Step 6: Pytest GREEN (100% pass) and net diff verified"
                )
            return f"❌ Demo walkthrough failed: {res.get('steps')}"

        elif subcmd == "diff":
            if len(parts) < 2:
                snaps = ledger.list_snapshots(session_id=session_id, limit=1)
                if not snaps:
                    return "Error: No snapshot available to diff."
                target_id = snaps[0]["id"]
                compare_to_id = None
            else:
                target_id = parts[1]
                compare_to_id = parts[2] if len(parts) > 2 else None
            res = ledger.get_diff(target_id, compare_to_id=compare_to_id)
            if not res.get("ok"):
                return f"Error retrieving diff: {res.get('error')}"
            diff_preview = res.get("diff", "")[:1500]
            cmp_label = f" vs {compare_to_id}" if compare_to_id else ""
            return f"📄 Diff for {target_id}{cmp_label} ({res.get('total_files', 0)} files, +{res.get('total_additions', 0)}/-{res.get('total_deletions', 0)}):\n```diff\n{diff_preview}\n```"

        elif subcmd == "restore":
            if len(parts) < 2:
                return "Usage: /rewind restore <snapshot_id> [--files file1,file2]"
            target_id = parts[1]
            target_files = None
            for idx, p in enumerate(parts[2:]):
                if p.startswith("--files="):
                    target_files = p.split("=", 1)[1].split(",")
                elif p == "--files" and idx + 3 < len(parts):
                    target_files = parts[idx + 3].split(",")

            res = ledger.restore_snapshot(snapshot_id=target_id, file_paths=target_files, restore_files=True)
            if not res.get("ok"):
                return f"❌ Restore failed: {res.get('error')}"
            restored_what = f"files ({', '.join(target_files)})" if target_files else "entire workspace"
            safety = f" (Safety backup saved: {res.get('pre_restore_backup_id')})" if res.get('pre_restore_backup_id') else ""
            return f"✅ Successfully restored {restored_what} to {target_id}!{safety}"

        elif subcmd == "recover":
            rec_res = ledger.recover(session_id=session_id, auto_apply=True, confirm=True)
            if not rec_res.get("ok"):
                return f"❌ Recovery failed: {rec_res.get('error')}"
            diag = rec_res.get("diagnosis", {})
            return (
                f"🛡️ Autonomous Self-Healing Executed!\n"
                f"• Culprits Reverted: {rec_res.get('restored_files')}\n"
                f"• Safe Target Checkpoint: {diag.get('recommended_snapshot_id')}\n"
                f"• Pre-Restore Safety Backup: {rec_res.get('pre_restore_backup_id')}\n"
                f"• Ready to retry command: {rec_res.get('retry_command') or 'pytest'}"
            )

        elif subcmd == "export":
            return ledger.export_timeline_markdown(session_id=session_id)

        return "Usage: /rewind [list|diff <id> [compare_to]|restore <id> [--files f1,f2]|recover|export|demo]"

    # Register hooks
    ctx.register_hook("on_session_start", on_session_start)
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_hook("transform_tool_result", on_transform_tool_result)

    # Register tools
    register_time_machine_tools(ctx, ledger=ledger)

    # Register slash command
    if hasattr(ctx, "register_command"):
        ctx.register_command(
            name="rewind",
            handler=handle_rewind_command,
            description="Hermes Time Machine: inspect timeline, diff, or restore snapshots",
            args_hint="[list|diff|restore|recover|export|demo]",
        )

    # Register system prompt section for autonomous self-healing
    if hasattr(ctx, "register_system_prompt_section"):
        try:
            ctx.register_system_prompt_section(
                "time_machine_self_heal",
                (
                    "## Time Machine Self-Healing & Safety Active\n"
                    "Hermes Time Machine automatically checkpoints the filesystem before file mutations.\n"
                    "If code edits cause test, syntax, or build failures, use these tools to diagnose and recover:\n"
                    "- `time_machine_recover`: autonomous self-healing tool! Call `time_machine_recover(auto_apply=true, confirm=true)` to diagnose culprits and surgically revert broken files in 1 step.\n"
                    "- `time_machine_list_snapshots`: inspect captured checkpoints in the current session.\n"
                    "- `time_machine_diff`: view exact unified and structured diff of any snapshot or live working tree drift.\n"
                    "- `time_machine_restore`: surgically restore files (`file_paths=['path/to/file']`) or the whole workspace (`confirm=true`).\n"
                    "- `time_machine_branch`: fork a new clean exploration session without losing context."
                ),
                position="after_memory",
            )
        except Exception as exc:
            logger.debug("register_system_prompt_section skipped: %s", exc)
