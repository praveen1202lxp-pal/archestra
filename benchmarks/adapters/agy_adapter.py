"""Adapter for running standalone Antigravity CLI as System Under Test."""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from benchmarks.adapters.base import AdapterRunTelemetry, BaseSUTAdapter
from benchmarks.schema import BenchmarkTask, SystemUnderTest
from fusion_agent.providers.antigravity_cli import AntigravityCLIProvider
from fusion_agent.workspace.editor import WorkspaceEditor


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

        if self.cli_path:
            try:
                ver_res = subprocess.run([self.cli_path, "--version"], capture_output=True, text=True, timeout=10)
                cli_version = ver_res.stdout.strip().splitlines()[0] if ver_res.stdout else None
            except Exception:
                cli_version = "agy-cli"

        if self.is_live and self.cli_path:
            try:
                cmd = [
                    self.cli_path,
                    "--add-dir", str(repo_path),
                    "-p", task.prompt,
                    "--effort", self.reasoning_effort,
                    "--dangerously-skip-permissions",
                    "--output-format", "json",
                ]
                res = subprocess.run(
                    cmd,
                    cwd=str(repo_path),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=task.timeout_seconds,
                )

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
                    error_message = f"AGY CLI exited with code {res.returncode}: {err_snippet}"
            except subprocess.TimeoutExpired:
                error_message = f"AGY CLI timed out after {task.timeout_seconds}s"
            except Exception as e:
                error_message = f"AGY execution failed: {str(e)}"
        else:
            native_in = 1920
            native_out = 430
            provider_calls = 1

        duration = max(0.01, time.time() - t0)

        return AdapterRunTelemetry(
            system_under_test=SystemUnderTest.ANTIGRAVITY_ALONE,
            wall_clock_duration_seconds=duration,
            active_provider_duration_seconds=duration * 0.95,
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
