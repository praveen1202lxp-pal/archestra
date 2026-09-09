"""Command Line Interface for Fusion Agent."""

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

from fusion_agent.config.loader import ConfigLoader
from fusion_agent.config.schema import AgentConfig, FusionConfig, OptimizationMode
from fusion_agent.core.orchestrator import FusionOrchestrator
from fusion_agent.memory.database import Database
from fusion_agent.models.deliberation import ReviewStatus
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
    print(f"{BOLD} Multi-Model Coding Agent Orchestrator (v0.1.0)     {RESET}")
    print(f"{BOLD}{CYAN}==================================================={RESET}")


def cmd_init(args) -> int:
    """Initialize a new Fusion Agent project."""
    target_dir = Path(args.dir).resolve()
    fusion_dir = target_dir / ".fusion"

    if (fusion_dir / "config.json").exists() and not args.force:
        print(f"{YELLOW}Project already initialized at: {fusion_dir}{RESET}")
        print("Use --force to overwrite configuration.")
        return 0

    project_name = args.name or target_dir.name
    goal = args.goal or "Build software using provider-agnostic multi-agent orchestration"

    config = FusionConfig.default_mock_config(project_name=project_name)
    config.project_root = str(target_dir)

    ConfigLoader.save(config, target_dir)

    # Initialize SQLite database
    db = Database(fusion_dir / "fusion.db")
    db.connect()
    db.close()

    print(f"{GREEN}{ICON_OK} Initialized Fusion Agent project: {BOLD}{project_name}{RESET}")
    print(f"  Configuration: {fusion_dir / 'config.json'}")
    print(f"  Shared Brain:  {fusion_dir / 'fusion.db'}")
    print(f"  Optimization:  {config.optimization_mode.value}")
    print(f"\nRun {BOLD}fusion status{RESET} to view configured providers.")
    return 0


def cmd_status(args) -> int:
    """Display project status, providers, and recent activity."""
    config_file = ConfigLoader.find_config_file(args.dir)
    if not config_file:
        print(f"{YELLOW}No Fusion Agent project found. Run 'fusion init' first.{RESET}")
        return 1

    config = ConfigLoader.load(config_file)
    db = Database(Path(config.project_root) / config.storage_dir / "fusion.db")
    
    print_banner()
    print(f"{BOLD}Project:{RESET}      {config.project_name}")
    print(f"{BOLD}Directory:{RESET}    {config.project_root}")
    print(f"{BOLD}Mode:{RESET}         {config.optimization_mode.value}")
    print(f"\n{BOLD}Configured Providers:{RESET}")

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
            print(f"  {status_icon} [{agent_id}] {agent_cfg.provider_name} ({agent_cfg.provider_type}) - {health.message}")
        except Exception as e:
            print(f"  {YELLOW}{ICON_FAIL}{RESET} [{agent_id}] {agent_cfg.provider_name} - Error: {e}")

    # Display recent tasks
    conn = db.connect()
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
    return 0


def cmd_run(args) -> int:
    """Run a single task through Fusion Agent."""
    config_file = ConfigLoader.find_config_file(args.dir)
    if not config_file:
        print(f"{YELLOW}No Fusion Agent project found. Initializing with defaults...{RESET}")
        cmd_init(args)
        config_file = ConfigLoader.find_config_file(args.dir)

    config = ConfigLoader.load(config_file)
    db = Database(Path(config.project_root) / config.storage_dir / "fusion.db")
    orchestrator = FusionOrchestrator(config=config, database=db)

    print(f"\n{BOLD}{CYAN}Fusion Agent{RESET}")
    print(f"{GRAY}Task: {args.task}{RESET}\n")

    def handle_status_event(event_type: str, data: dict):
        if event_type == "status":
            print(f"  {BLUE}{ICON_BULLET}{RESET} {data.get('message')}")
        elif event_type == "deliberation_step":
            print(f"    {GRAY}{ICON_ARROW} {data.get('step')}{RESET}")
        elif event_type == "routing" and args.debug:
            print(f"  {YELLOW}[DEBUG Router]{RESET} Strategy: {data.get('strategy')} | Complexity: {data.get('complexity')}")
            print(f"    Rationale: {data.get('rationale')}")

    try:
        result = orchestrator.run_task(args.task, on_status=handle_status_event)
    except Exception as exc:
        print(f"\n{BOLD}{YELLOW}[ERROR]{RESET} Fusion Agent failed to process task: {exc}\n")
        return 1

    # Output deliberation details if debug mode requested
    if args.debug:
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

    # Autonomous Edit Inspection & Mandatory User Confirmation Gate
    if getattr(result, "workspace_session", None) is not None:
        session = result.workspace_session
        verif = result.verification_result
        diff = result.diff
        review = result.review_result
        promo = PromotionEngine()

        print(f"{BOLD}{CYAN}=== ISOLATED REPOSITORY EDIT INSPECTION ==={RESET}")
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
            try:
                target_b = getattr(session, "base_branch", "master") or "master"
                prompt_msg = f"{BOLD}Apply and merge verified changes into branch '{target_b}'? [y/N]: {RESET}"
                choice = input(prompt_msg).strip().lower()
            except (KeyboardInterrupt, EOFError):
                choice = "n"

            if choice in ("y", "yes"):
                res = promo.promote(session)
                if res.success:
                    print(f"\n{BOLD}{GREEN}[SUCCESS]{RESET} {res.message}\n")
                else:
                    print(f"\n{BOLD}{RED}[PROMOTION FAILED]{RESET} {res.message}\n")
            else:
                promo.discard(session)
                print(f"\n{YELLOW}Promotion declined. Isolated worktree and branch cleanly discarded.{RESET}\n")
        else:
            promo.discard(session)
            reasons = []
            if not (verif and verif.passed):
                reasons.append("automated verification failed")
            if not (review and review.status == ReviewStatus.APPROVED):
                reasons.append("peer review was not approved")
            if not diff:
                reasons.append("no modifications generated")
            print(f"\n{RED}Changes not eligible for promotion ({', '.join(reasons)}). Isolated worktree discarded.{RESET}\n")

    return 0


def cmd_interactive(args) -> int:
    """Start an interactive REPL session with Fusion Agent."""
    print_banner()
    config_file = ConfigLoader.find_config_file(args.dir)
    if not config_file:
        print(f"{YELLOW}Initializing project in current directory...{RESET}")
        cmd_init(args)
        config_file = ConfigLoader.find_config_file(args.dir)

    config = ConfigLoader.load(config_file)
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
    common_parser.add_argument("--debug", action="store_true", help="Show internal multi-agent deliberation details")

    parser = argparse.ArgumentParser(
        prog="fusion",
        description="Fusion Agent: Provider-agnostic multi-model coding agent orchestrator.",
        parents=[common_parser],
    )

    subparsers = parser.add_subparsers(dest="command")

    # Init
    p_init = subparsers.add_parser("init", parents=[common_parser], help="Initialize a Fusion Agent project")
    p_init.add_argument("--name", help="Project name")
    p_init.add_argument("--goal", help="Project goal description")
    p_init.add_argument("--force", action="store_true", help="Force overwrite existing config")

    # Status
    subparsers.add_parser("status", parents=[common_parser], help="Show project status and provider health")

    # Run
    p_run = subparsers.add_parser("run", parents=[common_parser], help="Run a single task through Fusion Agent")
    p_run.add_argument("task", help="The programming task or question")

    # Interactive
    subparsers.add_parser("interactive", parents=[common_parser], help="Start an interactive session")

    args = parser.parse_args()

    if not args.command:
        # Default to interactive if no command given
        return cmd_interactive(args)

    if args.command == "init":
        return cmd_init(args)
    elif args.command == "status":
        return cmd_status(args)
    elif args.command == "run":
        return cmd_run(args)
    elif args.command == "interactive":
        return cmd_interactive(args)
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
