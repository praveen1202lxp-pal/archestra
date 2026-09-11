"""Adapter for running standalone Antigravity CLI as System Under Test."""

import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Optional

from benchmarks.adapters.base import AdapterRunTelemetry, BaseSUTAdapter
from benchmarks.schema import BenchmarkTask, SystemUnderTest
from fusion_agent.providers.antigravity_cli import AntigravityCLIProvider
from fusion_agent.workspace.editor import WorkspaceEditor


def _to_wsl_path(path: Path) -> str:
    """Convert Windows path to WSL /mnt/<drive>/... path."""
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    posix_path = resolved.as_posix()
    # Strip drive prefix e.g. "C:"
    if ":" in posix_path:
        posix_path = posix_path.split(":", 1)[1]
    return f"/mnt/{drive}{posix_path}"


class AntigravityAloneAdapter(BaseSUTAdapter):
    """Executes benchmark tasks using standalone Antigravity CLI directly."""

    def __init__(self, cli_path: Optional[str] = None, is_live: bool = False, reasoning_effort: str = "medium"):
        super().__init__(sut=SystemUnderTest.ANTIGRAVITY_ALONE)
        self.cli_path = cli_path or AntigravityCLIProvider()._resolve_executable()
        self.is_live = is_live
        self.reasoning_effort = reasoning_effort

    def execute(self, task: BenchmarkTask, repo_path: Path) -> AdapterRunTelemetry:
        t0 = time.time()
        error_message = None
        native_in = 0
        native_out = 0
        native_reasoning = 0
        provider_calls = 0
        cli_version = None
        active_provider_duration = None

        if self.cli_path:
            try:
                ver_res = subprocess.run([self.cli_path, "--version"], capture_output=True, text=True, timeout=10)
                cli_version = ver_res.stdout.strip().splitlines()[0] if ver_res.stdout else None
            except Exception:
                cli_version = "agy-cli"

        if self.is_live:
            cli_version = "1.2.0 (linux-docker)"
            trial_vol = f"agy_trial_auth_{uuid.uuid4().hex[:12]}"
            wsl_repo = _to_wsl_path(repo_path)
            try:
                # 1. Create fresh disposable auth volume cloned from immutable AUTH_SEED
                subprocess.run(
                    ["wsl", "-u", "root", "docker", "volume", "create", trial_vol],
                    check=True,
                    capture_output=True,
                    stdin=subprocess.DEVNULL,
                )
                subprocess.run(
                    [
                        "wsl", "-u", "root", "docker", "run", "--rm", "-i",
                        "-v", "antigravity_benchmark_auth:/from:ro",
                        "-v", f"{trial_vol}:/to:rw",
                        "python:3.11-slim", "cp", "-a", "/from/.", "/to/",
                    ],
                    check=True,
                    capture_output=True,
                    stdin=subprocess.DEVNULL,
                )

                # 2. Run approved antigravity-benchmark:1.2.0 container
                cmd = [
                    "wsl", "-u", "root", "docker", "run", "--rm", "-i",
                    "-e", "GIT_CONFIG_COUNT=1",
                    "-e", "GIT_CONFIG_KEY_0=safe.directory",
                    "-e", "GIT_CONFIG_VALUE_0=*",
                    "-v", f"{wsl_repo}:/workspace:rw",
                    "-v", f"{trial_vol}:/home/agy/.gemini:rw",
                    "-w", "/workspace",
                    "antigravity-benchmark:1.2.0",
                    "--dangerously-skip-permissions",
                    "--effort", self.reasoning_effort,
                    "--output-format", "json",
                    "-p", task.prompt,
                ]
                t_active_0 = time.time()
                res = subprocess.run(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=task.timeout_seconds,
                )
                active_provider_duration = max(0.01, time.time() - t_active_0)

                response_text = ""
                try:
                    data = json.loads(res.stdout)
                    if isinstance(data, dict):
                        usage = data.get("usage", {})
                        native_in = usage.get("input_tokens", 0)
                        native_out = usage.get("output_tokens", 0)
                        native_reasoning = usage.get("thinking_tokens", 0)
                        provider_calls = data.get("num_turns", 1)
                        response_text = data.get("response", "") or ""
                except Exception:
                    pass

                # If response produced markdown code blocks, write them if not already written
                if response_text:
                    extracted = WorkspaceEditor.extract_file_edits(response_text)
                    for rel_p, content in extracted:
                        dest = repo_path / rel_p
                        if not dest.exists() or dest.read_text(encoding="utf-8", errors="replace").strip() != content.strip():
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            dest.write_text(content, encoding="utf-8")

                if res.returncode != 0:
                    err_snippet = (res.stderr or res.stdout or "").strip()[:300]
                    error_message = f"AGY Container exited with code {res.returncode}: {err_snippet}"
            except subprocess.TimeoutExpired:
                error_message = f"AGY Container timed out after {task.timeout_seconds}s"
            except Exception as e:
                error_message = f"AGY Container execution failed: {str(e)}"
            finally:
                # 3. Always destroy the disposable auth volume
                try:
                    subprocess.run(
                        ["wsl", "-u", "root", "docker", "volume", "rm", "-f", trial_vol],
                        capture_output=True,
                        stdin=subprocess.DEVNULL,
                    )
                except Exception:
                    pass
        else:
            native_in = 1920
            native_out = 430
            provider_calls = 1

        duration = max(0.01, time.time() - t0)

        return AdapterRunTelemetry(
            system_under_test=SystemUnderTest.ANTIGRAVITY_ALONE,
            wall_clock_duration_seconds=duration,
            active_provider_duration_seconds=active_provider_duration if (active_provider_duration is not None) else (duration * 0.95),
            native_input_tokens=native_in,
            native_output_tokens=native_out,
            native_reasoning_tokens=native_reasoning if native_reasoning > 0 else None,
            fusion_controlled_context_tokens=None,
            provider_calls_count=max(1, provider_calls),
            mcp_calls_count=0,
            provider_model_id="gemini-2.5-pro",
            cli_version=cli_version,
            reasoning_effort=self.reasoning_effort,
            fusion_config_hash=None,
            reviewer_verdict=None,
            reviewer_found_defect=False,
            defect_in_test_passing_patch=False,
            repair_rounds=0,
            repair_successful=False,
            human_promotion_disposition=None,
            recovery_events=0,
            policy_denials=0,
            error_message=error_message,
        )
