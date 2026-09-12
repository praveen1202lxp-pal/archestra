"""Clean, actionable error formatting for Fusion Agent CLI.

Converts internal operational exceptions into understandable developer messages,
avoiding raw stack traces unless debug mode is explicitly requested.
"""

import traceback
from typing import Optional

from fusion_agent.workspace.lock import TaskLockError
from fusion_agent.workspace.session import DirtyWorkingTreeError


# Terminal styling
BOLD = "\033[1m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"


def format_cli_error(exc: Exception, debug: bool = False) -> str:
    """Format an exception into a clean, actionable user message."""
    lines = []

    if isinstance(exc, DirtyWorkingTreeError):
        lines.append(f"\n{BOLD}{RED}[!] Fusion refused to start: uncommitted changes detected{RESET}")
        lines.append("")
        lines.append("Fusion Agent requires a clean repository to safely create isolated worktrees")
        lines.append("and guarantee conflict-free automated verification without risking data loss.")
        lines.append("")
        lines.append(f"{YELLOW}Action required:{RESET}")
        lines.append("  1. Commit or stash your work: 'git stash' or 'git commit -am \"wip\"'")
        lines.append("  2. Re-run your task: 'fusion run \"...\"'")
        lines.append("")

    elif isinstance(exc, TaskLockError):
        lines.append(f"\n{BOLD}{RED}[!] Workspace lock conflict{RESET}")
        lines.append("")
        lines.append(f"Another task is currently running or a prior task was not cleanly released:")
        lines.append(f"  {exc}")
        lines.append("")
        lines.append(f"{YELLOW}Action required:{RESET}")
        lines.append("  If no other Fusion process is running, inspect active tasks with 'fusion status'")
        lines.append("  or remove stale lock in .fusion/lock if the process crashed.")
        lines.append("")

    elif isinstance(exc, TimeoutError):
        lines.append(f"\n{BOLD}{YELLOW}[!] Task execution timed out{RESET}")
        lines.append("")
        lines.append(f"{exc}")
        lines.append("No unvetted changes were promoted to your repository.")
        lines.append("")
        lines.append(f"{CYAN}Tip:{RESET} You can increase timeout ceilings in .fusion/config.json under deliberation.timeout_seconds.")
        lines.append("")

    elif isinstance(exc, FileNotFoundError) and any(kw in str(exc).lower() for kw in ("codex", "agy", "antigravity", "cli")):
        lines.append(f"\n{BOLD}{RED}[!] Required provider CLI not found{RESET}")
        lines.append("")
        lines.append(f"{exc}")
        lines.append("")
        lines.append(f"{YELLOW}Action required:{RESET}")
        lines.append("  Run 'fusion doctor' to diagnose provider CLI discovery and installation paths.")
        lines.append("")

    elif isinstance(exc, PermissionError) and any(kw in str(exc).lower() for kw in ("auth", "login", "credentials", "session")):
        lines.append(f"\n{BOLD}{RED}[!] Provider authentication required{RESET}")
        lines.append("")
        lines.append(f"{exc}")
        lines.append("")
        lines.append(f"{YELLOW}Action required:{RESET}")
        lines.append("  Complete provider login or verify authentication with 'fusion doctor'.")
        lines.append("")

    else:
        lines.append(f"\n{BOLD}{RED}[ERROR]{RESET} {type(exc).__name__}: {exc}")

    if debug:
        lines.append(f"\n{BOLD}{YELLOW}--- TECHNICAL TRACEBACK (DEBUG) ---{RESET}")
        lines.append(traceback.format_exc())
        lines.append(f"{BOLD}{YELLOW}----------------------------------{RESET}")

    return "\n".join(lines)
