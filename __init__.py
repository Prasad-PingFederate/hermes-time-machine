"""
Hermes Time Machine Plugin Entrypoint.

Registers:
- Hooks: on_session_start, pre_tool_call, post_tool_call, transform_tool_result
- Tools: time_machine_list_snapshots, time_machine_diff, time_machine_restore, time_machine_recover, time_machine_search
- Slash command: /tm (list, diff, restore, recover, search)
- Self-healing system prompt section

All snapshots stored in HERMES_HOME/plugin-data/time-machine/.
Workspace directory strictly bound to ctx.session_cwd.
Restoration defaults to dry-run and requires user approval.
Conversation rewind and session branching are handled by core /rewind and /branch.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from .ledger import (
        TimeMachineLedger,
        resolve_working_dir,
        MUTATING_TOOLS,
        FILE_MUTATING_TOOLS,
        COMMAND_TOOLS,
    )
    from .tools import register_time_machine_tools
except (ImportError, ValueError):
    from ledger import (
        TimeMachineLedger,
        resolve_working_dir,
        MUTATING_TOOLS,
        FILE_MUTATING_TOOLS,
        COMMAND_TOOLS,
    )
    from tools import register_time_machine_tools

logger = logging.getLogger(__name__)

__version__ = "0.4.1"
__plugin_name__ = "time-machine"

_last_safe_snapshot: Dict[str, str] = {}
_session_working_dirs: Dict[str, str] = {}


def _get_session_cwd(ctx: Any, session_id: Optional[str] = None) -> str:
    """Resolve session working directory strictly from ctx.session_cwd, tracked session dir, or Path.cwd()."""
    if session_id and session_id in _session_working_dirs:
        return _session_working_dirs[session_id]
    if ctx is not None:
        if callable(getattr(ctx, "session_cwd", None)):
            try:
                scwd = ctx.session_cwd()
                if scwd:
                    p = Path(scwd).expanduser().resolve()
                    if p.is_dir():
                        return str(p)
            except Exception:
                pass
        scwd = getattr(ctx, "session_cwd", None)
        if scwd and isinstance(scwd, (str, bytes)):
            try:
                p = Path(scwd).expanduser().resolve()
                if p.is_dir():
                    return str(p)
            except Exception:
                pass
    return str(Path.cwd().resolve())


def register(ctx: Any, ledger: Optional[TimeMachineLedger] = None) -> None:
    """Called by Hermes PluginManager on startup."""
    cfg = getattr(ctx, "config", {}) or {}
    snapshot_on_every_tool = bool(cfg.get("snapshot_on_every_tool", False))
    max_snapshots_per_session = int(cfg.get("max_snapshots_per_session", 100))

    def _get_ledger() -> TimeMachineLedger:
        if ledger is not None:
            return ledger
        return TimeMachineLedger(max_snapshots_per_session=max_snapshots_per_session)

    def on_session_start(
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        try:
            sess = session_id or task_id or "default"
            _last_safe_snapshot[sess] = ""
            if kwargs.get("working_dir"):
                _session_working_dirs[sess] = str(Path(kwargs["working_dir"]).expanduser().resolve())
            wdir = kwargs.get("working_dir") or _get_session_cwd(ctx, sess)
            cur_ledger = _get_ledger()
            cur_ledger.record_pre_tool(
                session_id=sess,
                tool_name="session_start",
                tool_args_summary="",
                working_dir=wdir,
                reason="session_start",
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
            should_snap = snapshot_on_every_tool or (tool_name in MUTATING_TOOLS)
            if should_snap:
                if kwargs.get("working_dir"):
                    _session_working_dirs[sess] = str(Path(kwargs["working_dir"]).expanduser().resolve())
                wdir = kwargs.get("working_dir") or _get_session_cwd(ctx, sess)
                args_summary = json.dumps(args) if isinstance(args, (dict, list)) else str(args or {})
                cur_ledger = _get_ledger()
                snap = cur_ledger.record_pre_tool(
                    session_id=sess,
                    turn_id=turn_id,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    tool_args_summary=args_summary,
                    working_dir=wdir,
                    reason=f"pre-{tool_name}",
                )
                if snap and tool_name in FILE_MUTATING_TOOLS:
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
            is_cmd_tool = tool_name in COMMAND_TOOLS
            res_str = str(result or "")

            exit_code = None
            if isinstance(result, dict):
                exit_code = result.get("exit_code", result.get("returncode"))
            elif hasattr(result, "exit_code"):
                exit_code = getattr(result, "exit_code")
            elif hasattr(result, "returncode"):
                exit_code = getattr(result, "returncode")

            if exit_code is not None:
                has_error = (int(exit_code) != 0) or (status != "ok")
            else:
                has_error = (status != "ok")
                if not has_error and is_cmd_tool:
                    has_error = bool(
                        re.search(r"\b(?:FAILED|FAILURES|Traceback \(most recent call last\)|SyntaxError)\b", res_str)
                    )

            self_heal = False
            rec_snap = None
            cur_ledger = _get_ledger()
            cmd_str = ""
            if is_cmd_tool and has_error:
                if kwargs.get("working_dir"):
                    _session_working_dirs[sess] = str(Path(kwargs["working_dir"]).expanduser().resolve())
                wdir = kwargs.get("working_dir") or _get_session_cwd(ctx, sess)
                if args:
                    cmd_str = args.get("command") or args.get("cmd") or args.get("script") or ""
                    if not isinstance(cmd_str, str):
                        cmd_str = str(cmd_str)
                cur_ledger._last_failed_commands[sess] = cmd_str
                if cmd_str:
                    try:
                        cur_ledger.remember_failed_command(sess, cmd_str)
                    except Exception as remember_exc:
                        logger.debug("remember_failed_command error: %s", remember_exc)
                diag = cur_ledger.diagnose_failure(session_id=sess, error_text=res_str, working_dir=wdir)
                rec_snap = diag.get("recommended_snapshot_id") or _last_safe_snapshot.get(sess)
                self_heal = bool(rec_snap)

            cur_ledger.record_post_tool(
                session_id=sess,
                turn_id=turn_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                duration_ms=duration_ms,
                status="error" if has_error else status,
                self_heal_candidate=self_heal,
                recommended_snapshot_id=rec_snap,
                tool_args_summary=cmd_str if cmd_str else None,
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
            is_cmd_tool = tool_name in COMMAND_TOOLS
            res_str = str(result or "")

            exit_code = None
            if isinstance(result, dict):
                exit_code = result.get("exit_code", result.get("returncode"))
            elif hasattr(result, "exit_code"):
                exit_code = getattr(result, "exit_code")
            elif hasattr(result, "returncode"):
                exit_code = getattr(result, "returncode")

            if exit_code is not None:
                has_error = (int(exit_code) != 0)
            else:
                has_error = bool(
                    re.search(r"\b(?:FAILED|FAILURES|Traceback \(most recent call last\)|SyntaxError)\b", res_str)
                )

            if is_cmd_tool and has_error:
                cur_ledger = _get_ledger()
                wdir = kwargs.get("working_dir") or _get_session_cwd(ctx, sess)
                diag = cur_ledger.diagnose_failure(session_id=sess, error_text=res_str, working_dir=wdir)
                culprits = diag.get("culprit_files", [])
                target_snap = diag.get("recommended_snapshot_id") or _last_safe_snapshot.get(sess)
                if not target_snap:
                    safe_snap = cur_ledger.get_last_safe_snapshot(sess)
                    if safe_snap and safe_snap.get("checkpoint_hash"):
                        target_snap = safe_snap.get("id")

                if target_snap:
                    culprits_desc = ", ".join(culprits) if culprits else "recently modified files"
                    pres_desc = f"• Preserved concurrent modification(s): {', '.join(diag.get('preserved_files'))}\n" if diag.get("preserved_files") else ""
                    hint = (
                        f"\n\n[🛡️ Time Machine Self-Heal Notice & Recommendation]:\n"
                        f"Command failed after recent file mutations.\n"
                        f"• Culprit file(s): {culprits_desc}\n"
                        f"{pres_desc}"
                        f"• Recommended safe checkpoint: '{target_snap}'\n"
                        f"• Diagnosis & dry-run preview:\n"
                        f"  Call `time_machine_recover()` or `time_machine_restore(snapshot_id='{target_snap}')` to view the surgical diff and plan.\n"
                        f"• To apply surgical restore to disk, ask the user to confirm or run:\n"
                        f"  `/tm restore {target_snap}`\n"
                        f"• (To rewind conversation messages, use Hermes core's `/rewind` command.)"
                    )
                    return res_str + hint
        except Exception as exc:
            logger.debug("hermes-time-machine transform_tool_result error: %s", exc)
        return result

    def handle_tm_command(raw_args: str = "", **kwargs: Any) -> str:
        """User-executed slash command /tm."""
        parts = (raw_args or "").strip().split()
        subcmd = parts[0].lower() if parts else "list"
        session_id = kwargs.get("session_id") or "default"
        session_cwd = kwargs.get("working_dir") or _session_working_dirs.get(session_id)
        cur_ledger = _get_ledger()

        if subcmd in ("list", "ls", ""):
            snaps = cur_ledger.list_snapshots(session_id=session_id, working_dir=session_cwd, limit=10)
            if not snaps and session_cwd:
                snaps = cur_ledger.list_snapshots(session_id=session_id, limit=10)
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
            lines.append("\nCommands: /tm diff <id> | /tm restore <id> [--files f1,f2] | /tm commit <message> | /tm comment <id> <text> | /tm clone <id> <dir> | /tm push <file.bundle> | /tm pull <file.bundle> | /tm recover | /tm search <query>")
            return "\n".join(lines)

        elif subcmd == "diff":
            if len(parts) < 2:
                snaps = cur_ledger.list_snapshots(session_id=session_id, working_dir=session_cwd, limit=1)
                if not snaps:
                    return "Error: No snapshot available to diff."
                target_id = snaps[0]["id"]
                compare_to_id = None
            else:
                target_id = parts[1]
                compare_to_id = parts[2] if len(parts) > 2 else None
            res = cur_ledger.get_diff(target_id, compare_to_id=compare_to_id, working_dir=session_cwd)
            if not res.get("ok"):
                return f"Error retrieving diff: {res.get('error')}"
            diff_preview = res.get("diff", "")[:1500]
            cmp_label = f" vs {compare_to_id}" if compare_to_id else ""
            return f"📄 Diff for {target_id}{cmp_label} ({res.get('total_files', 0)} files, +{res.get('total_additions', 0)}/-{res.get('total_deletions', 0)}):\n```diff\n{diff_preview}\n```"

        elif subcmd == "restore":
            if len(parts) < 2:
                return "Usage: /tm restore <snapshot_id> [--files file1,file2]"
            target_id = parts[1]
            target_files = None
            if "--files" in parts:
                files_at = parts.index("--files")
                if files_at + 1 < len(parts):
                    target_files = [item.strip() for item in parts[files_at + 1].split(",") if item.strip()]
            for p in parts[2:]:
                if p.startswith("--files="):
                    target_files = [item.strip() for item in p.split("=", 1)[1].split(",") if item.strip()]

            # Since the user explicitly typed /tm restore, this is an authorized human restore
            res = cur_ledger.restore_snapshot(
                snapshot_id=target_id,
                file_paths=target_files,
                working_dir=session_cwd,
                dry_run=False,
                user_approved=True,
            )
            if not res.get("ok"):
                return f"❌ Restore failed: {res.get('error')}"
            restored_what = f"files ({', '.join(target_files)})" if target_files else "workspace files"
            safety = f" (Safety backup captured: {res.get('pre_restore_backup_id')})" if res.get('pre_restore_backup_id') else ""
            return f"✅ Successfully restored {restored_what} from {target_id}!{safety}\n(Note: To rewind conversation messages, run Hermes core's `/rewind` command)."

        elif subcmd == "recover":
            # Recover via slash command displays diagnosis and suggested verify command
            rec_res = cur_ledger.recover(
                session_id=session_id,
                working_dir=session_cwd,
                dry_run=True,
                user_approved=False,
            )
            if not rec_res.get("ok"):
                return f"❌ Recovery diagnosis: {rec_res.get('message', 'No safe snapshot found.')}"
            diag = rec_res.get("diagnosis", {})
            return (
                f"🛡️ Time Machine Recovery Plan:\n"
                f"• Culprits: {rec_res.get('culprit_files')}\n"
                f"• Recommended Snapshot: {rec_res.get('recommended_snapshot_id')}\n"
                f"• Planned Restores: {rec_res.get('planned_restored_files')}\n"
                f"• Action: Run `/tm restore {rec_res.get('recommended_snapshot_id')}` to apply.\n"
                f"• Verification Command: {rec_res.get('suggested_verify_command') or 'pytest'}"
            )

        elif subcmd in ("search", "find"):
            if len(parts) < 2:
                return "Usage: /tm search <query> [file_path]"
            query = parts[1]
            f_filter = parts[2] if len(parts) > 2 else None
            s_res = cur_ledger.search_snapshots(
                query=query,
                session_id=session_id,
                file_path=f_filter,
                working_dir=session_cwd,
            )
            matches = s_res.get("matches", []) if isinstance(s_res, dict) else (s_res or [])
            if not matches:
                return f"🔍 No snapshots found introducing or modifying '{query}'."
            lines = [f"🔍 Search results for '{query}' ({len(matches)} snapshots matched):"]
            for m in matches:
                sid = m.get("snapshot_id", "")
                tool = m.get("tool_name", "")
                cnt = m.get("total_match_lines", m.get("match_count", 0))
                lines.append(f"• {sid} ({tool}): {cnt} diff line(s) matched")
            return "\n".join(lines)

        elif subcmd == "export":
            return cur_ledger.export_timeline_markdown(session_id=session_id)

        elif subcmd == "commit":
            message = (raw_args or "").strip()
            if message.lower().startswith("commit"):
                message = message[len("commit"):].strip()
            res = cur_ledger.manual_commit(message, session_id=session_id, working_dir=session_cwd)
            if not res.get("ok"):
                return f"❌ Commit failed: {res.get('error')}"
            return (
                f"✅ Checkpoint {res.get('snapshot_id')} recorded in the private store.\n"
                f"Message: {res.get('message')}\n"
                "The project git repository was not modified."
            )

        elif subcmd == "comment":
            rest = (raw_args or "").strip()
            if rest.lower().startswith("comment"):
                rest = rest[len("comment"):].strip()
            snap_id, _, text = rest.partition(" ")
            res = cur_ledger.set_snapshot_comment(snap_id, text)
            if not res.get("ok"):
                return f"❌ Comment failed: {res.get('error')}"
            return f"💬 Comment saved on {res.get('snapshot_id')}: {res.get('comment')}"

        elif subcmd == "clone":
            dry = "--dry-run" in parts
            tokens = [p for p in parts[1:] if p != "--dry-run"]
            if len(tokens) < 2:
                return "Usage: /tm clone <snapshot_id> <new-directory> [--dry-run]"
            res = cur_ledger.clone_snapshot(
                tokens[0],
                " ".join(tokens[1:]),
                dry_run=dry,
                user_approved=not dry,
            )
            if not res.get("ok"):
                return f"❌ Clone failed: {res.get('error')}"
            if res.get("dry_run"):
                return f"📄 Clone dry run → {res.get('destination')} ({len(res.get('planned_files') or [])} files). Re-run without --dry-run to write."
            return f"✅ Cloned snapshot into {res.get('destination')} ({res.get('files_written')} files). Project .git was not touched."

        elif subcmd == "push":
            dry = "--dry-run" in parts
            tokens = [p for p in parts[1:] if p != "--dry-run"]
            if not tokens:
                return "Usage: /tm push <file.bundle> [--dry-run]"
            res = cur_ledger.push_checkpoint_bundle(tokens[0], dry_run=dry, user_approved=not dry)
            if not res.get("ok"):
                return f"❌ Push failed: {res.get('error')}"
            if res.get("dry_run"):
                return f"📄 Push dry run would write {res.get('bundle')} (private store only, not git push)."
            return f"✅ Pushed private checkpoints to {res.get('bundle')} ({res.get('snapshots')} snapshots). Project .git was not pushed."

        elif subcmd == "pull":
            dry = "--dry-run" in parts
            tokens = [p for p in parts[1:] if p != "--dry-run"]
            if not tokens:
                return "Usage: /tm pull <file.bundle> [--dry-run]"
            res = cur_ledger.pull_checkpoint_bundle(tokens[0], dry_run=dry, user_approved=not dry)
            if not res.get("ok"):
                return f"❌ Pull failed: {res.get('error')}"
            if res.get("dry_run"):
                return f"📄 Pull dry run for {res.get('bundle')}. The project repository was not pulled."
            return f"✅ Pulled {res.get('imported_snapshots')} snapshot(s) into the private store from {res.get('bundle')}."

        elif subcmd == "demo":
            try:
                from scripts.demo_walkthrough import run_demo
                d_res = run_demo(quiet=True)
                if d_res.get("ok"):
                    return "🎉 Time Machine E2E Live Demo Passed 100%!"
                return f"❌ Demo failed: {d_res}"
            except Exception as d_err:
                return f"❌ Demo error: {d_err}"

        return "Usage: /tm [list | diff <id> | restore <id> [--files f1,f2] | commit <message> | comment <id> <text> | clone <id> <dir> | push <file.bundle> | pull <file.bundle> | recover | search <query> | export | demo]"

    # Register hooks
    ctx.register_hook("on_session_start", on_session_start)
    ctx.register_hook("pre_tool_call", on_pre_tool_call)
    ctx.register_hook("post_tool_call", on_post_tool_call)
    ctx.register_hook("transform_tool_result", on_transform_tool_result)

    # Register tools
    register_time_machine_tools(ctx, ledger=ledger)

    # Register slash command: /tm (renamed from /rewind to prevent collision with core)
    if hasattr(ctx, "register_command"):
        ctx.register_command(
            name="tm",
            handler=handle_tm_command,
            description="Hermes Time Machine: inspect timeline, diff, or restore snapshots",
            args_hint="[list|diff|restore|commit|comment|clone|push|pull|recover|search]",
        )

    # Register system prompt section
    if hasattr(ctx, "register_system_prompt_section"):
        try:
            ctx.register_system_prompt_section(
                "time_machine_self_heal",
                (
                    "## Time Machine Self-Healing & Safety Active\n"
                    "Hermes Time Machine automatically checkpoints the filesystem into its private store before file mutations.\n"
                    "If code edits cause test, syntax, or build failures, use these tools to diagnose and recover:\n"
                    "- `time_machine_recover`: diagnose failure root-cause, identify culprit files, and generate a dry-run surgical restore plan with a suggested verification command.\n"
                    "- `time_machine_restore`: preview or restore files from a snapshot (defaults to dry-run preview; modifying files on disk requires user approval via `/tm restore <snapshot_id>` or approval prompt).\n"
                    "- `time_machine_search`: search historical snapshot diffs to find which turn introduced, modified, or removed code.\n"
                    "- `time_machine_list_snapshots`: inspect captured checkpoints in the current session.\n"
                    "- `time_machine_diff`: view exact unified and structured diff of any snapshot or live working tree drift.\n"
                    "- For conversation rewind or session branching, use Hermes core commands: `/rewind` and `/branch`."
                ),
                position="after_memory",
            )
        except Exception as exc:
            logger.debug("register_system_prompt_section skipped: %s", exc)
