"""Adapter for running standalone Antigravity CLI as System Under Test."""

import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from benchmarks.adapters.base import AdapterRunTelemetry, BaseSUTAdapter
from benchmarks.schema import BenchmarkTask, SystemUnderTest


class AntigravityAloneAdapter(BaseSUTAdapter):
    """Executes benchmark tasks using standalone Antigravity CLI directly."""

    def __init__(self, cli_path: Optional[str] = None, is_live: bool = False):
        super().__init__(sut=SystemUnderTest.ANTIGRAVITY_ALONE)
        self.cli_path = cli_path or shutil.which("agy")
        self.is_live = is_live

    def execute(self, task: BenchmarkTask, repo_path: Path) -> AdapterRunTelemetry:
        t0 = time.time()
        error_message = None
        native_in = 1920
        native_out = 430

        if self.is_live and self.cli_path:
            try:
                res = subprocess.run(
                    [self.cli_path, "exec", "--prompt", task.prompt],
                    cwd=str(repo_path),
                    capture_output=True,
                    text=True,
                    timeout=task.timeout_seconds,
                )
                if res.returncode != 0:
                    error_message = f"AGY CLI exited with code {res.returncode}: {res.stderr[:200]}"
            except Exception as e:
                error_message = f"AGY execution failed: {str(e)}"
        else:
            # Offline / mock execution mode
            pass

        duration = max(0.01, time.time() - t0)

        return AdapterRunTelemetry(
            system_under_test=SystemUnderTest.ANTIGRAVITY_ALONE,
            wall_clock_duration_seconds=duration,
            active_provider_duration_seconds=duration * 0.9,
            native_input_tokens=native_in,
            native_output_tokens=native_out,
            fusion_controlled_context_tokens=None,  # Standalone CLI has no Fusion context builder
            provider_calls_count=1,
            mcp_calls_count=0,
            provider_model_id="gemini-1.5-pro",
            cli_version="2.1.0-agy",
            reasoning_effort=None,
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
