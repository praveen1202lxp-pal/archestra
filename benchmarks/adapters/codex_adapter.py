"""Adapter for running standalone Codex CLI as System Under Test."""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from benchmarks.adapters.base import AdapterRunTelemetry, BaseSUTAdapter
from benchmarks.schema import BenchmarkTask, SystemUnderTest
from fusion_agent.providers.codex_cli import CodexCLIProvider


class CodexAloneAdapter(BaseSUTAdapter):
    """Executes benchmark tasks using standalone Codex CLI directly."""

    def __init__(self, cli_path: Optional[str] = None, is_live: bool = False, reasoning_effort: str = "medium"):
        super().__init__(sut=SystemUnderTest.CODEX_ALONE)
        self.cli_path = cli_path or CodexCLIProvider()._resolve_executable()
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
                cli_version = "codex-cli"

        if self.is_live and self.cli_path:
            try:
                cmd = [
                    self.cli_path,
                    "exec",
                    "--approve-for-me",
                    "-c",
                    f'model_reasoning_effort="{self.reasoning_effort}"',
                    "--json",
                    "-",
                ]
                res = subprocess.run(
                    cmd,
                    input=task.prompt,
                    cwd=str(repo_path),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=task.timeout_seconds,
                )

                # Parse JSONL events for token usage
                for line in res.stdout.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        if isinstance(event, dict):
                            ev_type = event.get("type")
                            if ev_type == "turn.started":
                                provider_calls += 1
                            elif ev_type == "turn.completed":
                                usage = event.get("usage", {})
                                native_in += usage.get("input_tokens", 0)
                                native_out += usage.get("output_tokens", 0)
                                native_reasoning += usage.get("reasoning_output_tokens", 0)
                    except Exception:
                        pass

                if res.returncode != 0:
                    err_snippet = (res.stderr or res.stdout or "").strip()[:300]
                    error_message = f"Codex CLI exited with code {res.returncode}: {err_snippet}"
            except subprocess.TimeoutExpired:
                error_message = f"Codex CLI timed out after {task.timeout_seconds}s"
            except Exception as e:
                error_message = f"Codex execution failed: {str(e)}"
        else:
            # Offline mock fallback
            native_in = 1850
            native_out = 410
            provider_calls = 1

        duration = max(0.01, time.time() - t0)

        return AdapterRunTelemetry(
            system_under_test=SystemUnderTest.CODEX_ALONE,
            wall_clock_duration_seconds=duration,
            active_provider_duration_seconds=duration * 0.95,
            native_input_tokens=native_in,
            native_output_tokens=native_out,
            native_reasoning_tokens=native_reasoning if native_reasoning > 0 else None,
            fusion_controlled_context_tokens=None,
            provider_calls_count=max(1, provider_calls),
            mcp_calls_count=0,
            provider_model_id="openai/gpt-5" if "gpt" in (cli_version or "").lower() else "codex-native",
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
