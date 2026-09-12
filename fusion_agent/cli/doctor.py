"""Non-destructive diagnostics command for Fusion Agent.

Inspects local runtime environment, Git tooling, SQLite storage, CLI provider
discoveries, authentication states, and configuration validity without invoking
expensive generative model requests.
"""

import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional, Tuple

import fusion_agent
from fusion_agent.config.loader import ConfigLoader
from fusion_agent.config.schema import FusionConfig
from fusion_agent.providers.antigravity_cli import AntigravityCLIProvider
from fusion_agent.providers.codex_cli import CodexCLIProvider


class CheckStatus(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass
class DiagnosticCheck:
    name: str
    status: CheckStatus
    message: str
    remediation: Optional[str] = None


@dataclass
class DoctorReport:
    checks: List[DiagnosticCheck] = field(default_factory=list)

    @property
    def passed_count(self) -> int:
        return sum(1 for c in self.checks if c.status == CheckStatus.PASS)

    @property
    def warn_count(self) -> int:
        return sum(1 for c in self.checks if c.status == CheckStatus.WARN)

    @property
    def fail_count(self) -> int:
        return sum(1 for c in self.checks if c.status == CheckStatus.FAIL)

    @property
    def is_healthy(self) -> bool:
        return self.fail_count == 0


def run_doctor(project_dir: str | Path = ".", verbose: bool = False) -> DoctorReport:
    """Run non-destructive diagnostics and return a detailed report."""
    proj_path = Path(project_dir).resolve()
    report = DoctorReport()

    # 1. Fusion Version
    report.checks.append(
        DiagnosticCheck(
            name="Fusion Version",
            status=CheckStatus.PASS,
            message=f"Fusion Agent v{fusion_agent.__version__}",
        )
    )

    # 2. Python Runtime Version
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info >= (3, 11):
        report.checks.append(
            DiagnosticCheck(
                name="Python Runtime",
                status=CheckStatus.PASS,
                message=f"Python {py_ver} ({platform.python_implementation()})",
            )
        )
    else:
        report.checks.append(
            DiagnosticCheck(
                name="Python Runtime",
                status=CheckStatus.FAIL,
                message=f"Python {py_ver} is unsupported. Fusion requires Python 3.11+",
                remediation="Install Python 3.11 or higher from https://python.org",
            )
        )

    # 3. Git Tooling
    git_bin = shutil.which("git")
    if git_bin:
        try:
            res = subprocess.run([git_bin, "--version"], capture_output=True, text=True, timeout=5)
            git_ver = res.stdout.strip()
            report.checks.append(
                DiagnosticCheck(
                    name="Git Tooling",
                    status=CheckStatus.PASS,
                    message=f"{git_ver} at {git_bin}",
                )
            )
        except Exception as e:
            report.checks.append(
                DiagnosticCheck(
                    name="Git Tooling",
                    status=CheckStatus.WARN,
                    message=f"Git executable found at {git_bin} but failed probe: {e}",
                )
            )
    else:
        report.checks.append(
            DiagnosticCheck(
                name="Git Tooling",
                status=CheckStatus.FAIL,
                message="Git is not installed or not available on PATH",
                remediation="Install Git from https://git-scm.com and ensure it is on your PATH",
            )
        )

    # 4. Target Repository & Worktree Status
    if git_bin:
        try:
            is_worktree = subprocess.run(
                [git_bin, "rev-parse", "--is-inside-work-tree"],
                cwd=str(proj_path),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if is_worktree.returncode == 0 and is_worktree.stdout.strip() == "true":
                # Check status
                status_res = subprocess.run(
                    [git_bin, "status", "--porcelain"],
                    cwd=str(proj_path),
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                dirty_lines = [
                    line for line in status_res.stdout.splitlines()
                    if line.strip() and not line.strip().endswith(".fusion") and ".fusion" not in line
                ]
                if not dirty_lines:
                    report.checks.append(
                        DiagnosticCheck(
                            name="Repository State",
                            status=CheckStatus.PASS,
                            message=f"Git repository detected at {proj_path} (clean)",
                        )
                    )
                else:
                    report.checks.append(
                        DiagnosticCheck(
                            name="Repository State",
                            status=CheckStatus.WARN,
                            message=f"Git repository detected at {proj_path} with {len(dirty_lines)} uncommitted file(s)",
                            remediation="Commit or stash pending changes before running autonomous tasks",
                        )
                    )
            else:
                report.checks.append(
                    DiagnosticCheck(
                        name="Repository State",
                        status=CheckStatus.WARN,
                        message=f"{proj_path} is not inside a Git repository",
                        remediation="Run 'git init' or run 'fusion init' to initialize the workspace",
                    )
                )
        except Exception as e:
            report.checks.append(
                DiagnosticCheck(
                    name="Repository State",
                    status=CheckStatus.WARN,
                    message=f"Could not inspect repository state: {e}",
                )
            )

    # 5. Storage Directory & SQLite
    fusion_dir = proj_path / ".fusion"
    try:
        fusion_dir.mkdir(parents=True, exist_ok=True)
        # Test write and sqlite operations
        db_path = fusion_dir / "_doctor_test.db"
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS _probe (k TEXT);")
            conn.execute("INSERT INTO _probe VALUES ('ok');")
            conn.commit()
        finally:
            conn.close()
        if db_path.exists():
            db_path.unlink(missing_ok=True)

        report.checks.append(
            DiagnosticCheck(
                name="Storage & SQLite",
                status=CheckStatus.PASS,
                message=f"State directory writable at {fusion_dir} (SQLite operational)",
            )
        )
    except Exception as e:
        report.checks.append(
            DiagnosticCheck(
                name="Storage & SQLite",
                status=CheckStatus.FAIL,
                message=f"State directory is not writable at {fusion_dir}: {e}",
                remediation="Ensure write permissions on project root and .fusion/ directory",
            )
        )

    # 6. Configuration Validity
    cfg_file = ConfigLoader.find_config_file(proj_path)
    if cfg_file and cfg_file.is_file():
        try:
            cfg = ConfigLoader.load(cfg_file)
            agent_names = list(cfg.agents.keys())
            report.checks.append(
                DiagnosticCheck(
                    name="Configuration",
                    status=CheckStatus.PASS,
                    message=f"Valid config at {cfg_file} (mode: {cfg.optimization_mode.value}, agents: {', '.join(agent_names) or 'none'})",
                )
            )
        except Exception as e:
            report.checks.append(
                DiagnosticCheck(
                    name="Configuration",
                    status=CheckStatus.FAIL,
                    message=f"Configuration file at {cfg_file} is invalid: {e}",
                    remediation="Check .fusion/config.json syntax or run 'fusion init --force' to recreate",
                )
            )
    else:
        report.checks.append(
            DiagnosticCheck(
                name="Configuration",
                status=CheckStatus.WARN,
                message=f"No project config found in {proj_path}. Using hierarchical/starter defaults",
                remediation="Run 'fusion init' to create a project-specific configuration",
            )
        )

    # 7. Codex CLI Provider Check (Non-generative)
    codex_provider = CodexCLIProvider()
    codex_exe = codex_provider._resolve_executable()
    if codex_exe:
        try:
            codex_health = codex_provider.health_check()
            if codex_health.healthy:
                report.checks.append(
                    DiagnosticCheck(
                        name="Codex CLI",
                        status=CheckStatus.PASS,
                        message=codex_health.message,
                    )
                )
            else:
                report.checks.append(
                    DiagnosticCheck(
                        name="Codex CLI",
                        status=CheckStatus.WARN,
                        message=f"Codex CLI found at {codex_exe} but reported: {codex_health.message}",
                        remediation="Run 'codex login' in your terminal to complete authentication",
                    )
                )
        except Exception as e:
            report.checks.append(
                DiagnosticCheck(
                    name="Codex CLI",
                    status=CheckStatus.WARN,
                    message=f"Codex CLI found at {codex_exe} but probe failed: {e}",
                )
            )
    else:
        report.checks.append(
            DiagnosticCheck(
                name="Codex CLI",
                status=CheckStatus.WARN,
                message="Codex CLI executable ('codex') was not discovered on PATH or standard directories",
                remediation="Install Codex CLI via 'npm install -g @openai/codex' or set CODEX_CLI_PATH",
            )
        )

    # 8. Antigravity CLI Provider Check (Non-generative)
    agy_provider = AntigravityCLIProvider()
    agy_exe = agy_provider._resolve_executable()
    if agy_exe:
        try:
            agy_health = agy_provider.health_check()
            if agy_health.healthy:
                report.checks.append(
                    DiagnosticCheck(
                        name="Antigravity CLI",
                        status=CheckStatus.PASS,
                        message=agy_health.message,
                    )
                )
            else:
                report.checks.append(
                    DiagnosticCheck(
                        name="Antigravity CLI",
                        status=CheckStatus.WARN,
                        message=f"Antigravity CLI found at {agy_exe} but reported: {agy_health.message}",
                        remediation="Run 'agy' in your terminal to finish Google authentication",
                    )
                )
        except Exception as e:
            report.checks.append(
                DiagnosticCheck(
                    name="Antigravity CLI",
                    status=CheckStatus.WARN,
                    message=f"Antigravity CLI found at {agy_exe} but probe failed: {e}",
                )
            )
    else:
        # Check if Docker/WSL contains antigravity-benchmark container as alternative
        docker_bin = shutil.which("docker")
        if docker_bin:
            try:
                res = subprocess.run(["docker", "image", "inspect", "antigravity-benchmark:1.2.0"], capture_output=True, timeout=5)
                if res.returncode == 0:
                    report.checks.append(
                        DiagnosticCheck(
                            name="Antigravity CLI",
                            status=CheckStatus.PASS,
                            message="Antigravity Benchmark Container detected (antigravity-benchmark:1.2.0 via Docker)",
                        )
                    )
                else:
                    report.checks.append(
                        DiagnosticCheck(
                            name="Antigravity CLI",
                            status=CheckStatus.WARN,
                            message="Antigravity CLI ('agy') not found on host and Docker container not loaded",
                            remediation="Install Antigravity CLI via 'irm https://antigravity.google/cli/install.ps1 | iex' or set ANTIGRAVITY_CLI_PATH",
                        )
                    )
            except Exception:
                report.checks.append(
                    DiagnosticCheck(
                        name="Antigravity CLI",
                        status=CheckStatus.WARN,
                        message="Antigravity CLI ('agy') not found on PATH or standard install directories",
                        remediation="Install Antigravity CLI via 'irm https://antigravity.google/cli/install.ps1 | iex' or set ANTIGRAVITY_CLI_PATH",
                    )
                )
        else:
            report.checks.append(
                DiagnosticCheck(
                    name="Antigravity CLI",
                    status=CheckStatus.WARN,
                    message="Antigravity CLI ('agy') was not discovered on PATH",
                    remediation="Install Antigravity CLI via 'irm https://antigravity.google/cli/install.ps1 | iex' or set ANTIGRAVITY_CLI_PATH",
                )
            )

    # 9. Docker / Container Environment (Optional check)
    docker_bin = shutil.which("docker")
    if docker_bin:
        try:
            res = subprocess.run([docker_bin, "--version"], capture_output=True, text=True, timeout=5)
            report.checks.append(
                DiagnosticCheck(
                    name="Docker / Container",
                    status=CheckStatus.PASS,
                    message=f"{res.stdout.strip()} (optional container execution available)",
                )
            )
        except Exception as e:
            report.checks.append(
                DiagnosticCheck(
                    name="Docker / Container",
                    status=CheckStatus.WARN,
                    message=f"Docker executable found but returned error on probe: {e}",
                )
            )
    else:
        report.checks.append(
            DiagnosticCheck(
                name="Docker / Container",
                status=CheckStatus.PASS,
                message="Docker not detected (native host CLI execution mode will be used)",
            )
        )

    return report


def print_doctor_report(report: DoctorReport) -> None:
    """Print a clean, structured diagnostic report to the terminal."""
    BOLD = "\033[1m"
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    RESET = "\033[0m"

    print(f"\n{BOLD}Fusion Doctor{RESET}\n")

    for check in report.checks:
        if check.status == CheckStatus.PASS:
            icon = f"{GREEN}✓{RESET}"
            print(f"  {icon} {check.name:22} {check.message}")
        elif check.status == CheckStatus.WARN:
            icon = f"{YELLOW}!{RESET}"
            print(f"  {icon} {check.name:22} {check.message}")
            if check.remediation:
                print(f"    {YELLOW}→ {check.remediation}{RESET}")
        else:
            icon = f"{RED}✗{RESET}"
            print(f"  {icon} {check.name:22} {check.message}")
            if check.remediation:
                print(f"    {RED}→ {check.remediation}{RESET}")

    print(f"\n{BOLD}Summary:{RESET} {report.passed_count} passed, {report.warn_count} warnings, {report.fail_count} errors\n")
    if report.is_healthy:
        print(f"{GREEN}✓ System is ready to run Fusion Agent.{RESET}\n")
    else:
        print(f"{RED}✗ Issues detected. Resolve errors above before running tasks.{RESET}\n")
