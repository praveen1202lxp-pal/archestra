"""Command Line Interface for Fusion Agent."""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

import fusion_agent
from fusion_agent.cli.doctor import print_doctor_report, run_doctor
from fusion_agent.cli.errors import format_cli_error
from fusion_agent.config.loader import ConfigLoader
from fusion_agent.config.schema import AgentConfig, FusionConfig, OptimizationMode
from fusion_agent.core.orchestrator import FusionOrchestrator
from fusion_agent.memory.database import Database
from fusion_agent.models.deliberation import ReviewStatus
from fusion_agent.models.task import PromotionDisposition
from fusion_agent.providers.registry import ProviderRegistry
from fusion_agent.workspace.promotion import PromotionEngine


# Configure UTF-8 stdout/stderr on Windows if possible
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Terminal styling & cross-platform glyphs
BOLD = "\033[1m"
GREEN = "\033[92m"
RED = "\033[91m"
BLUE = "\033[94m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
GRAY = "\033[90m"
RESET = "\033[0m"

# Safe glyphs
ICON_OK = "[OK]"
ICON_FAIL = "[X]"
ICON_BULLET = "*"
ICON_ARROW = "->"


def print_banner():
    print(f"{BOLD}{CYAN}==================================================={RESET}")
    print(f"{BOLD}{CYAN}               FUSION AGENT                        {RESET}")
    print(f"{BOLD} Multi-Model Coding Agent Orchestrator (v{fusion_agent.__version__}) {RESET}")
    print(f"{BOLD}{CYAN}==================================================={RESET}")


def cmd_init(args) -> int:
    """Initialize a new Fusion Agent project in the target directory."""
    target_dir = Path(args.dir).resolve()
    fusion_dir = target_dir / ".fusion"

    if (fusion_dir / "config.json").exists() and not getattr(args, "force", False):
        print(f"{YELLOW}Project already initialized at: {fusion_dir}{RESET}")
        print("Use --force to overwrite configuration.")
        return 0

    project_name = getattr(args, "name", None) or target_dir.name

    # Create detected starter configuration
    config = FusionConfig.default_starter_config(project_name=project_name)
    config.project_root = str(target_dir)

    ConfigLoader.save(config, target_dir)

    # Initialize SQLite database
    db = Database(fusion_dir / "fusion.db")
    db.connect()
    db.close()

    # Update or create .gitignore to protect transient runtime state while keeping project config trackable
    gitignore_path = target_dir / ".gitignore"
    gitignore_msg = ""
    runtime_ignore_rules = (
        "\n# Fusion Agent runtime state (database, logs, worktrees, locks)\n"
        ".fusion/*.db\n"
        ".fusion/*.db-wal\n"
        ".fusion/*.db-shm\n"
        ".fusion/*.log\n"
        ".fusion/logs/\n"
        ".fusion/worktrees/\n"
        ".fusion/temp/\n"
        ".fusion/locks/\n"
        ".fusion/cache/\n"
    )

    if gitignore_path.exists():
        content = gitignore_path.read_text(encoding="utf-8", errors="replace")
        # If legacy whole-directory ignore '.fusion/' is present, replace it so .fusion/config.json is trackable
        if re.search(r"^\.fusion/?\s*$", content, flags=re.MULTILINE):
            new_content = re.sub(
                r"(?:# Fusion Agent (?:local|runtime) state\s*\n)?^\.fusion/?\s*\n?",
                runtime_ignore_rules.lstrip("\n"),
                content,
                flags=re.MULTILINE,
            )
            gitignore_path.write_text(new_content, encoding="utf-8")
            gitignore_msg = " (updated .gitignore to allow tracking .fusion/config.json)"
        elif ".fusion/*.db" not in content:
            with open(gitignore_path, "a", encoding="utf-8") as f:
                f.write(runtime_ignore_rules)
            gitignore_msg = " (added .fusion runtime ignores to .gitignore)"
    elif (target_dir / ".git").exists():
        with open(gitignore_path, "w", encoding="utf-8") as f:
            f.write(runtime_ignore_rules.lstrip("\n"))
        gitignore_msg = " (created .gitignore with .fusion runtime ignores)"

    print(f"{GREEN}{ICON_OK} Initialized Fusion Agent project: {BOLD}{project_name}{RESET}")
    print(f"  Configuration: {fusion_dir / 'config.json'}")
    print(f"  Shared Brain:  {fusion_dir / 'fusion.db'}")
    print(f"  Optimization:  {config.optimization_mode.value}{gitignore_msg}")
    print(f"\nNext steps:")
    print(f"  1. Run {BOLD}fusion doctor{RESET} to verify provider CLI installations")
    print(f"  2. Run {BOLD}fusion providers{RESET} to inspect provider health")
    print(f"  3. Run {BOLD}fusion run \"Your coding task\"{RESET} to execute work")
    return 0


def cmd_doctor(args) -> int:
    """Run non-destructive diagnostics on environment, tooling, and providers."""
    report = run_doctor(project_dir=args.dir, verbose=getattr(args, "verbose", False))
    print_doctor_report(report)
    return 0 if report.is_healthy else 1


def cmd_providers(args) -> int:
    """List configured providers and test their availability non-destructively."""
    config = ConfigLoader.load_hierarchical(project_dir=args.dir)

    print(f"\n{BOLD}Configured Providers for {config.project_name}:{RESET}\n")

    if not config.agents:
        print("  No providers configured in project or user configuration.")
        print(f"  Run '{BOLD}fusion init{RESET}' to generate a starter configuration.")
        return 0

    all_ready = True
    for agent_id, agent_cfg in config.agents.items():
        try:
            prov = ProviderRegistry.create(
                name=agent_cfg.provider_name,
                provider_type=agent_cfg.provider_type,
                config=agent_cfg.to_dict(),
            )
            prov.initialize()
            health = prov.health_check()
            status_icon = f"{GREEN}{ICON_OK}{RESET}" if health.healthy else f"{YELLOW}{ICON_FAIL}{RESET}"
            model_info = f" [model: {agent_cfg.model}]" if agent_cfg.model else ""
            print(f"  {status_icon} [{agent_id}] {agent_cfg.provider_name} ({agent_cfg.provider_type}){model_info}")
            print(f"     Status: {health.message} ({health.latency_ms:.1f}ms)")
            if not health.healthy:
                all_ready = False
        except Exception as e:
            print(f"  {RED}{ICON_FAIL}{RESET} [{agent_id}] {agent_cfg.provider_name} - Error: {e}")
            all_ready = False

    print("")
    return 0 if all_ready else 1


def cmd_status(args) -> int:
    """Display project status, providers summary, and recent activity."""
    config_file = ConfigLoader.find_config_file(args.dir)
    if not config_file:
        print(f"{YELLOW}No Fusion Agent project found in {args.dir}. Run 'fusion init' first.{RESET}")
        return 1

    config = ConfigLoader.load(config_file)
    db = Database(Path(config.project_root) / config.storage_dir / "fusion.db")

    print_banner()
    print(f"{BOLD}Project:{RESET}      {config.project_name}")
    print(f"{BOLD}Directory:{RESET}    {config.project_root}")
    print(f"{BOLD}Mode:{RESET}         {config.optimization_mode.value}")
    print(f"{BOLD}Verification:{RESET} {config.verification_command or 'default (pytest)'}")

    # Configured providers summary
    print(f"\n{BOLD}Configured Providers:{RESET}")
    for agent_id, agent_cfg in config.agents.items():
        model_str = f" ({agent_cfg.model})" if agent_cfg.model else ""
        print(f"  - {agent_id}: {agent_cfg.provider_name}{model_str}")

    # Display recoverable tasks
    try:
        conn = db.connect()
        rec_tasks = conn.execute(
            "SELECT id, title, status, last_checkpoint_sha FROM tasks WHERE status IN ('RUNNING', 'INTERRUPTED', 'RECOVERABLE', 'RESUMING') ORDER BY created_at DESC LIMIT 10;"
        ).fetchall()
        if rec_tasks:
            print(f"\n{BOLD}{YELLOW}Recoverable Tasks:{RESET}")
            for rt in rec_tasks:
                chk = f"checkpoint: {rt['last_checkpoint_sha'][:8]}" if rt['last_checkpoint_sha'] else "no checkpoint"
                print(f"  - [{rt['status']}] {BOLD}{rt['id']}{RESET}: {rt['title']} ({chk})")
            print(f"  Run {BOLD}fusion resume <task-id>{RESET} to resume execution.")

        # Display recent tasks
        tasks = conn.execute(
            "SELECT title, status, selected_strategy, created_at FROM tasks ORDER BY created_at DESC LIMIT 5;"
        ).fetchall()

        print(f"\n{BOLD}Recent Tasks in Memory:{RESET}")
        if not tasks:
            print("  (No tasks recorded yet)")
        else:
            for t in tasks:
                print(f"  - [{t['status']}] {t['title']} ({t['selected_strategy'] or 'N/A'})")

        db.close()
    except Exception:
        pass

    return 0


def cmd_config(args) -> int:
    """Inspect or validate active configuration."""
    project_dir = getattr(args, "dir", ".") or "."
    cfg_file = ConfigLoader.find_config_file(project_dir)

    if getattr(args, "path", False):
        if cfg_file:
            print(str(cfg_file.resolve()))
            return 0
        else:
            print(f"{YELLOW}No project config found. User config: {ConfigLoader.get_user_config_path()}{RESET}")
            return 1

    try:
        config = ConfigLoader.load_hierarchical(project_dir=project_dir)
    except Exception as exc:
        print(format_cli_error(exc, debug=getattr(args, "debug", False)))
        return 1

    if getattr(args, "validate", False):
        print(f"{GREEN}✓ Configuration is valid (project: {config.project_name}, mode: {config.optimization_mode.value}){RESET}")
        return 0

    if getattr(args, "get", None):
        key = args.get
        val = getattr(config, key, None)
        if val is not None:
            if isinstance(val, (dict, list)):
                print(json.dumps(val, indent=2))
            else:
                print(val)
            return 0
        else:
            print(f"{RED}Unknown configuration key: {key}{RESET}")
            return 1

    # Default: display safe redacted JSON
    safe_data = config.to_safe_dict()
    print(f"\n{BOLD}Active Fusion Configuration ({config.project_name}):{RESET}")
    print(json.dumps(safe_data, indent=2))
    print("")
    return 0


def _inspect_and_promote(result, orchestrator: FusionOrchestrator, args) -> int:
    """Inspect isolated repository edits and present the mandatory promotion confirmation gate."""
    if getattr(result, "workspace_session", None) is not None:
        session = result.workspace_session
        verif = result.verification_result
        diff = result.diff
        review = result.review_result
        promo = PromotionEngine()

        print(f"\n{BOLD}{CYAN}=== ISOLATED REPOSITORY EDIT INSPECTION ==={RESET}")
        print(f"Task Branch:  {session.task_branch}")
        print(f"Base Commit:  {session.base_commit[:8] if session.base_commit else 'unknown'}")

        if verif:
            v_color = GREEN if verif.passed else RED
            print(f"Verification: {v_color}{'PASSED' if verif.passed else 'FAILED'}{RESET} (exit code {verif.exit_code}, {verif.duration_seconds:.2f}s)")
            if verif.stderr:
                print(f"{YELLOW}Test Stderr:{RESET}\n{verif.stderr}")

        if review:
            r_color = GREEN if review.status == ReviewStatus.APPROVED else RED
            print(f"Peer Review:  {r_color}[{review.status.value}]{RESET} by {review.reviewer_agent}")
            print(f"{BOLD}Comments:{RESET}\n{review.comments}")

        if hasattr(result, "deliberation") and result.deliberation and hasattr(result.deliberation, "reviews") and len(result.deliberation.reviews) > 1:
            print(f"\n{BOLD}Review History ({len(result.deliberation.reviews)} rounds):{RESET}")
            for i, r in enumerate(result.deliberation.reviews, 1):
                rc = GREEN if r.status == ReviewStatus.APPROVED else RED
                print(f"  Round {i}: {rc}[{r.status.value}]{RESET} by {r.reviewer_agent}")

        if hasattr(result, "deliberation") and result.deliberation and hasattr(result.deliberation, "stage_metrics") and result.deliberation.stage_metrics:
            print(f"\n{BOLD}Per-Provider Stages:{RESET}")
            for sm in result.deliberation.stage_metrics:
                s_in = f"{sm['input_tokens']:,}" if sm.get('input_tokens') is not None else "unavailable"
                s_out = f"{sm['output_tokens']:,}" if sm.get('output_tokens') is not None else "unavailable"
                print(f"  - {BOLD}{sm['stage']}{RESET} ({sm['provider']}): {sm['duration_ms']:.1f} ms | in: {s_in}, out: {s_out}")

        if diff:
            print(f"\n{BOLD}{YELLOW}--- GENERATED UNIFIED DIFF ---{RESET}")
            print(diff)
            print(f"{BOLD}{YELLOW}------------------------------{RESET}\n")
        else:
            print(f"\n{YELLOW}Notice: No file modifications detected in isolated worktree.{RESET}\n")

        # Confirmation Gate: Requires tests passed, peer review approved, and non-empty diff
        eligible = (
            (verif is not None and verif.passed)
            and (review is not None and review.status == ReviewStatus.APPROVED)
            and bool(diff)
        )
        if eligible:
            if getattr(args, "no_promote", False):
                choice = "n"
            else:
                try:
                    prompt_msg = f"{BOLD}Apply verified changes? [y/N]: {RESET}"
                    choice = input(prompt_msg).strip().lower()
                except (KeyboardInterrupt, EOFError):
                    choice = "n"

            if choice in ("y", "yes"):
                res = promo.promote(session)
                if res.success:
                    orchestrator.state_manager.update_task_promotion(result.task.id, PromotionDisposition.PROMOTED)
                    print(f"\n{BOLD}{GREEN}[SUCCESS]{RESET} {res.message}\n")
                else:
                    orchestrator.state_manager.update_task_promotion(result.task.id, PromotionDisposition.BLOCKED)
                    print(f"\n{BOLD}{RED}[PROMOTION FAILED]{RESET} {res.message}\n")
            else:
                promo.discard(session)
                orchestrator.state_manager.update_task_promotion(result.task.id, PromotionDisposition.DECLINED)
                print(f"\n{YELLOW}Promotion declined. Isolated worktree and branch cleanly discarded.{RESET}\n")
        else:
            promo.discard(session)
            orchestrator.state_manager.update_task_promotion(result.task.id, PromotionDisposition.BLOCKED)
            reasons = []
            if not (verif and verif.passed):
                reasons.append("automated verification failed")
            if not (review and review.status == ReviewStatus.APPROVED):
                reasons.append("peer review was not approved")
            if not diff:
                reasons.append("no modifications generated")
            print(f"\n{RED}Changes not eligible for promotion ({', '.join(reasons)}). Isolated worktree discarded.{RESET}\n")

    return 0


def cmd_run(args) -> int:
    """Run a single task through Fusion Agent."""
    config_file = ConfigLoader.find_config_file(args.dir)
    if not config_file:
        print(f"{YELLOW}No Fusion Agent project found. Initializing with detected defaults...{RESET}")
        cmd_init(args)
        config_file = ConfigLoader.find_config_file(args.dir)

    try:
        if config_file:
            config = ConfigLoader.load(config_file)
        else:
            config = ConfigLoader.load_hierarchical(project_dir=args.dir)
    except Exception as exc:
        print(format_cli_error(exc, debug=getattr(args, "debug", False)))
        return 1

    db = Database(Path(config.project_root) / config.storage_dir / "fusion.db")
    orchestrator = FusionOrchestrator(config=config, database=db)

    print(f"\n{BOLD}{CYAN}Fusion Agent{RESET}")
    print(f"{GRAY}Task: {args.task}{RESET}\n")

    def handle_status_event(event_type: str, data: dict):
        if event_type == "status":
            print(f"  {BLUE}{ICON_BULLET}{RESET} {data.get('message')}")
        elif event_type == "deliberation_step":
            print(f"    {GRAY}{ICON_ARROW} {data.get('step')}{RESET}")
        elif event_type == "routing_decision":
            roles = data.get("role_assignments", {})
            impl = roles.get("implementer") or data.get("primary")
            rev = roles.get("reviewer") or data.get("secondary")
            print(f"  {CYAN}{ICON_ARROW} Strategy: {data.get('strategy')} | Implementer: {impl} | Reviewer: {rev or 'None'}{RESET}")
            if data.get("task_assessment") and getattr(args, "debug", False):
                ass = data["task_assessment"]
                print(f"    {GRAY}Assessment: {ass.get('task_type')} | Complexity: {ass.get('complexity')} | Scope: {ass.get('estimated_scope')} | Risk: {ass.get('review_risk')}{RESET}")
            if data.get("rationale") and getattr(args, "debug", False):
                print(f"    {GRAY}Rationale: {data.get('rationale')}{RESET}")
        elif event_type == "routing" and getattr(args, "debug", False):
            print(f"  {YELLOW}[DEBUG Router]{RESET} Strategy: {data.get('strategy')} | Complexity: {data.get('complexity')}")
            print(f"    Rationale: {data.get('rationale')}")

    try:
        result = orchestrator.run_task(args.task, on_status=handle_status_event)
    except Exception as exc:
        print(format_cli_error(exc, debug=getattr(args, "debug", False)))
        return 1

    # Output deliberation details if debug mode requested
    if getattr(args, "debug", False):
        print(f"\n{BOLD}{YELLOW}--- DELIBERATION INSPECTION (DEBUG) ---{RESET}")
        print(f"Strategy:    {result.deliberation.strategy_used}")
        print(f"Providers:   {', '.join(result.deliberation.participating_providers)}")
        print(f"Rounds:      {result.deliberation.rounds_executed}")
        print(f"Duration:    {result.deliberation.duration_ms:.1f} ms")
        in_tok_str = f"{result.deliberation.total_input_tokens:,}" if result.deliberation.total_input_tokens is not None else "unavailable"
        out_tok_str = f"{result.deliberation.total_output_tokens:,}" if result.deliberation.total_output_tokens is not None else "unavailable"
        print(f"Input Toks:  {in_tok_str}")
        print(f"Output Toks: {out_tok_str}")
        if hasattr(result, "context") and result.context and result.context.metrics:
            m = result.context.metrics
            print(f"Context:     Permanent: {m.get('permanent_chars', 0)}c | Current: {m.get('current_chars', 0)}c | Recent: {m.get('recent_chars', 0)}c | Peer: {m.get('peer_chars', 0)}c | Est. Tokens: ~{m.get('estimated_tokens', 0)}")
        if hasattr(result.deliberation, "stage_metrics") and result.deliberation.stage_metrics:
            print(f"\n{BOLD}Per-Provider Stages:{RESET}")
            for sm in result.deliberation.stage_metrics:
                s_in = f"{sm['input_tokens']:,}" if sm['input_tokens'] is not None else "unavailable"
                s_out = f"{sm['output_tokens']:,}" if sm['output_tokens'] is not None else "unavailable"
                print(f"  - {BOLD}{sm['stage']}{RESET} ({sm['provider']}): {sm['duration_ms']:.1f} ms | in: {s_in}, out: {s_out}")
        for prop in result.deliberation.proposals:
            print(f"\n{BOLD}[Proposal from {prop.agent_name}]{RESET}\n{prop.content}")
        for crit in result.deliberation.critiques:
            print(f"\n{BOLD}[Critique by {crit.reviewer_agent}]{RESET}\n{crit.content}")
        for rev in result.deliberation.reviews:
            print(f"\n{BOLD}[Review by {rev.reviewer_agent} - {rev.status.value}]{RESET}\n{rev.comments}")
        print(f"{BOLD}{YELLOW}---------------------------------------{RESET}\n")

    # Unified Single Agent Response
    print(f"\n{BOLD}{GREEN}Fusion:{RESET}")
    print(f"{result.final_answer}\n")

    return _inspect_and_promote(result, orchestrator, args)


def cmd_resume(args) -> int:
    """Resume an interrupted task."""
    config_file = ConfigLoader.find_config_file(args.dir)
    if not config_file:
        print(f"{YELLOW}No Fusion Agent project found. Run 'fusion init' first.{RESET}")
        return 1

    try:
        config = ConfigLoader.load(config_file)
    except Exception as exc:
        print(format_cli_error(exc, debug=getattr(args, "debug", False)))
        return 1

    db = Database(Path(config.project_root) / config.storage_dir / "fusion.db")
    orchestrator = FusionOrchestrator(config=config, database=db)

    print(f"\n{BOLD}{CYAN}Fusion Agent — Resuming Task{RESET}")
    print(f"{GRAY}Task ID: {args.task_id}{RESET}\n")

    def handle_status_event(event_type: str, data: dict):
        if event_type == "status":
            print(f"  {BLUE}{ICON_BULLET}{RESET} {data.get('message')}")
        elif event_type == "deliberation_step":
            print(f"    {GRAY}{ICON_ARROW} {data.get('step')}{RESET}")
        elif event_type == "routing_decision":
            roles = data.get("role_assignments", {})
            impl = roles.get("implementer") or data.get("primary")
            rev = roles.get("reviewer") or data.get("secondary")
            print(f"  {CYAN}{ICON_ARROW} Strategy: {data.get('strategy')} | Implementer: {impl} | Reviewer: {rev or 'None'}{RESET}")

    try:
        result = orchestrator.resume_task(args.task_id, on_status=handle_status_event)
    except Exception as exc:
        print(format_cli_error(exc, debug=getattr(args, "debug", False)))
        return 1

    # Unified Single Agent Response
    print(f"\n{BOLD}{GREEN}Fusion:{RESET}")
    print(f"{result.final_answer}\n")

    return _inspect_and_promote(result, orchestrator, args)


def cmd_mcp(args) -> int:
    """Manage and inspect Model Context Protocol (MCP) servers and tools."""
    config_file = ConfigLoader.find_config_file(args.dir)
    if config_file:
        config = ConfigLoader.load(config_file)
    else:
        config = ConfigLoader.load_hierarchical(project_dir=args.dir)
    from fusion_agent.mcp.registry import MCPServerRegistry

    registry = MCPServerRegistry()
    if hasattr(config, "mcp_servers") and config.mcp_servers:
        for s_id, s_cfg in config.mcp_servers.items():
            registry.register_server(s_cfg)

    action = getattr(args, "mcp_action", "list") or "list"

    if action == "list":
        print(f"\n{BOLD}Configured MCP Servers:{RESET}")
        servers = registry.list_servers()
        if not servers:
            print("  No MCP servers configured in .fusion/config.json.")
            return 0
        for s in servers:
            status_color = GREEN if s.enabled else GRAY
            print(f"  {status_color}{ICON_BULLET}{RESET} {BOLD}{s.server_id}{RESET} ({s.display_name or 'unnamed'})")
            print(f"     Transport: {s.transport.value} | Executable: {s.command} {' '.join(s.args)}")
            print(f"     Enabled: {s.enabled} | Timeout: {s.timeout_seconds}s")
        return 0

    elif action == "tools":
        server_id = args.server_id
        server_cfg = registry.get_server_config(server_id)
        if not server_cfg:
            print(f"{RED}{ICON_FAIL} Server '{server_id}' not found in registry.{RESET}")
            return 1

        print(f"\n{BOLD}Tools for MCP Server '{server_id}':{RESET}")
        tools = registry.list_tools(server_id)
        if not tools:
            print("  No tools registered or discovered for this server.")
            return 0
        for t in tools:
            caps_str = ", ".join(c.value for c in t.capabilities)
            print(f"  {CYAN}{ICON_BULLET}{RESET} {BOLD}{t.tool_name}{RESET} [{caps_str}]")
            print(f"     Side-effect: {t.side_effect.value} | Sensitivity: {t.sensitivity.value}")
            print(f"     Supports Idempotency: {t.supports_idempotency}")
        return 0

    elif action == "health":
        print(f"\n{BOLD}MCP Server Health Checks:{RESET}")
        servers = registry.list_servers()
        if not servers:
            print("  No MCP servers configured.")
            return 0
        from fusion_agent.mcp.models import ServerHealthState
        all_healthy = True
        for s in servers:
            status, latency, err = registry.check_health(s.server_id)
            if status == ServerHealthState.HEALTHY:
                print(f"  {GREEN}{ICON_OK}{RESET} {BOLD}{s.server_id}{RESET}: Healthy ({latency:.1f}ms)")
            else:
                print(f"  {RED}{ICON_FAIL}{RESET} {BOLD}{s.server_id}{RESET}: {status.value} ({err or 'Check failed'})")
                all_healthy = False
        return 0 if all_healthy else 1

    return 0


def cmd_studio_bridge(args) -> int:
    """Start the JSON-RPC stdio UI bridge server for Fusion Studio desktop app."""
    from fusion_agent.ui_bridge.server import UIBridgeServer
    server = UIBridgeServer(initial_project_root=args.dir)
    server.run()
    return 0


def cmd_interactive(args) -> int:
    """Start an interactive REPL session with Fusion Agent."""
    print_banner()
    config_file = ConfigLoader.find_config_file(args.dir)
    if not config_file:
        print(f"{YELLOW}Initializing project in current directory...{RESET}")
        cmd_init(args)
        config_file = ConfigLoader.find_config_file(args.dir)

    if config_file:
        config = ConfigLoader.load(config_file)
    else:
        config = ConfigLoader.load_hierarchical(project_dir=args.dir)
    db = Database(Path(config.project_root) / config.storage_dir / "fusion.db")
    orchestrator = FusionOrchestrator(config=config, database=db)

    print(f"Project: {BOLD}{config.project_name}{RESET} | Mode: {BOLD}{config.optimization_mode.value}{RESET}")
    print(f"Type {BOLD}exit{RESET} or {BOLD}quit{RESET} to finish.\n")

    while True:
        try:
            user_input = input(f"{BOLD}fusion>{RESET} ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit", "q"):
                print("Goodbye.")
                break

            args.task = user_input
            cmd_run(args)
        except (KeyboardInterrupt, EOFError):
            print("\nSession ended.")
            break

    return 0


def main():
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument("--dir", default=".", help="Project root directory (default: current dir)")
    common_parser.add_argument("--debug", action="store_true", help="Show internal multi-agent deliberation and tracebacks")

    parser = argparse.ArgumentParser(
        prog="fusion",
        description="Fusion Agent: Provider-agnostic multi-model coding agent orchestrator.",
        parents=[common_parser],
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"Fusion Agent v{fusion_agent.__version__}",
        help="Show Fusion Agent version and exit",
    )

    subparsers = parser.add_subparsers(dest="command")

    # Init
    p_init = subparsers.add_parser("init", parents=[common_parser], help="Initialize a Fusion Agent project in the repository")
    p_init.add_argument("--name", help="Project name")
    p_init.add_argument("--goal", help="Project goal description")
    p_init.add_argument("--force", action="store_true", help="Force overwrite existing config")

    # Doctor
    p_doctor = subparsers.add_parser("doctor", parents=[common_parser], help="Check system environment, tooling, and provider availability")
    p_doctor.add_argument("--verbose", action="store_true", help="Show detailed diagnostic output")

    # Providers
    subparsers.add_parser("providers", parents=[common_parser], help="List configured providers and verify their health")

    # Status
    subparsers.add_parser("status", parents=[common_parser], help="Show project status, mode, and recent task memory")

    # Config
    p_config = subparsers.add_parser("config", parents=[common_parser], help="Inspect or validate active project configuration")
    p_config.add_argument("--validate", action="store_true", help="Validate active configuration syntax and schema")
    p_config.add_argument("--path", action="store_true", help="Print path of the resolved configuration file")
    p_config.add_argument("--get", metavar="KEY", help="Get a specific configuration value (e.g. optimization_mode)")

    # Run
    p_run = subparsers.add_parser("run", parents=[common_parser], help="Run a single coding task through Fusion Agent")
    p_run.add_argument("task", help="The programming task or instruction")
    p_run.add_argument("--no-promote", action="store_true", help="Do not promote changes to base repository")

    # Resume
    p_resume = subparsers.add_parser("resume", parents=[common_parser], help="Resume an interrupted Fusion Agent task")
    p_resume.add_argument("task_id", help="The task ID to resume")
    p_resume.add_argument("--no-promote", action="store_true", help="Do not promote changes to base repository")

    # Interactive
    subparsers.add_parser("interactive", parents=[common_parser], help="Start an interactive session")

    # Studio Bridge
    subparsers.add_parser("studio-bridge", parents=[common_parser], help="Start stdio JSON-RPC UI bridge server for Fusion Studio")

    # MCP
    p_mcp = subparsers.add_parser("mcp", parents=[common_parser], help="Manage and inspect MCP tool servers")
    mcp_sub = p_mcp.add_subparsers(dest="mcp_action")
    mcp_sub.add_parser("list", parents=[common_parser], help="List configured MCP servers")
    p_mcp_tools = mcp_sub.add_parser("tools", parents=[common_parser], help="List tools for a specific MCP server")
    p_mcp_tools.add_argument("server_id", help="The server ID to inspect")
    mcp_sub.add_parser("health", parents=[common_parser], help="Check connectivity to all MCP servers")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    if args.command == "init":
        return cmd_init(args)
    elif args.command == "doctor":
        return cmd_doctor(args)
    elif args.command == "providers":
        return cmd_providers(args)
    elif args.command == "status":
        return cmd_status(args)
    elif args.command == "config":
        return cmd_config(args)
    elif args.command == "run":
        return cmd_run(args)
    elif args.command == "resume":
        return cmd_resume(args)
    elif args.command == "interactive":
        return cmd_interactive(args)
    elif args.command == "studio-bridge":
        return cmd_studio_bridge(args)
    elif args.command == "mcp":
        return cmd_mcp(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
