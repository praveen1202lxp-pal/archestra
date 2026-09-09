"""Antigravity CLI Provider Adapter.

Integrates the official Google Antigravity CLI ('agy') into Fusion Agent's
provider abstraction for headless multi-model orchestration.
"""

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from fusion_agent.models.deliberation import ReviewStatus
from fusion_agent.providers.base import (
    AgentProvider,
    AgentResponse,
    ContextSnapshot,
    HealthCheckResult,
    ReviewResponse,
)
from fusion_agent.providers.capabilities import ProviderCapabilities
from fusion_agent.providers.normalizer import StructuredAgentOutput, StructuredOutputNormalizer


@dataclass
class CLIExecutionResult:
    """Detailed execution telemetry from Antigravity CLI."""
    stdout: str
    stderr: str
    exit_code: int
    duration_seconds: float
    raw_json: Optional[Dict[str, Any]] = None
    conversation_id: Optional[str] = None
    response_status: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None
    model: Optional[str] = None
    response_text: Optional[str] = None


class AntigravityCLIProvider(AgentProvider):
    """Adapter allowing Fusion Agent to orchestrate Google's Antigravity CLI ('agy')."""

    DEFAULT_TIMEOUT_SECONDS = 120.0

    # Output patterns indicating authentication or login requirements
    AUTH_ERROR_PATTERNS = [
        r"\b(not\s+logged\s+in|unauthenticated|please\s+log\s+in|auth\s+login)\b",
        r"\b(invalid\s+credentials|authentication\s+failed|token\s+expired)\b",
        r"\b(set\s+an\s+auth\s+method|run\s+'?agy'?\s+to\s+authenticate)\b",
        r"\b(sign\s+in\s+required|session\s+expired)\b",
    ]

    def __init__(
        self,
        name: str = "Antigravity CLI",
        config: Optional[Dict[str, Any]] = None,
        executable_path: Optional[str] = None,
    ):
        super().__init__(name=name, config=config)
        self.executable_path = (
            executable_path
            or (config.get("executable_path") if config else None)
            or os.getenv("ANTIGRAVITY_CLI_PATH")
            or "agy"
        )
        self.timeout = float(self.config.get("timeout_seconds", self.DEFAULT_TIMEOUT_SECONDS))

    def _resolve_executable(self) -> Optional[str]:
        """Resolve full path to 'agy' executable across PATH and standard install directories."""
        # 1. Standard PATH lookup
        found = shutil.which(self.executable_path)
        if found:
            return found

        # If a specific custom path was configured and not found, do not fall back
        if self.executable_path not in ("agy", "agy.exe"):
            return None

        # 2. Standard Windows install path: %LOCALAPPDATA%\agy\bin\agy.exe
        local_appdata = os.getenv("LOCALAPPDATA")
        if local_appdata:
            agy_win = Path(local_appdata) / "agy" / "bin" / "agy.exe"
            if agy_win.is_file():
                return str(agy_win.resolve())

        # 3. User profile .antigravity/bin or .gemini/bin
        user_profile = os.getenv("USERPROFILE") or os.getenv("HOME")
        if user_profile:
            p1 = Path(user_profile) / ".antigravity" / "bin" / ("agy.exe" if os.name == "nt" else "agy")
            if p1.is_file():
                return str(p1.resolve())
            p2 = Path(user_profile) / ".local" / "bin" / ("agy.exe" if os.name == "nt" else "agy")
            if p2.is_file():
                return str(p2.resolve())

        # 4. Global npm paths (in case installed via npm wrapper)
        appdata = os.getenv("APPDATA")
        if appdata:
            p_npm = Path(appdata) / "npm" / "agy.cmd"
            if p_npm.is_file():
                return str(p_npm.resolve())

        return None

    def initialize(self) -> bool:
        """Check executable availability."""
        exe = self._resolve_executable()
        self._is_initialized = (exe is not None)
        return self._is_initialized

    def health_check(self) -> HealthCheckResult:
        """Verify if 'agy' executable exists, runs, and is authenticated."""
        start_time = time.perf_counter()
        exe = self._resolve_executable()

        if not exe:
            install_guide = (
                "irm https://antigravity.google/cli/install.ps1 | iex"
                if os.name == "nt"
                else "curl -fsSL https://antigravity.google/cli/install.sh | bash"
            )
            return HealthCheckResult(
                healthy=False,
                message=(
                    f"Antigravity CLI executable '{self.executable_path}' was not found. "
                    f"Install on Windows via: {install_guide}"
                ),
                latency_ms=(time.perf_counter() - start_time) * 1000.0,
            )

        try:
            # Query version
            proc_ver = subprocess.run(
                [exe, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            latency = (time.perf_counter() - start_time) * 1000.0

            if proc_ver.returncode != 0:
                combined_err = f"{proc_ver.stdout} {proc_ver.stderr}".strip()
                if any(re.search(p, combined_err, re.IGNORECASE) for p in self.AUTH_ERROR_PATTERNS):
                    return HealthCheckResult(
                        healthy=False,
                        message="Antigravity CLI is installed, but requires authentication. Please run 'agy' in your terminal to finish Google authentication.",
                        latency_ms=latency,
                    )
                return HealthCheckResult(
                    healthy=False,
                    message=f"Antigravity CLI returned error on --version: {combined_err}",
                    latency_ms=latency,
                )

            version_str = proc_ver.stdout.strip().splitlines()[0] if proc_ver.stdout.strip() else "detected"

            return HealthCheckResult(
                healthy=True,
                message=f"Antigravity CLI ({version_str}) online and ready at {exe}",
                latency_ms=(time.perf_counter() - start_time) * 1000.0,
            )

        except subprocess.TimeoutExpired:
            return HealthCheckResult(
                healthy=False,
                message="Antigravity CLI timed out during health check probe.",
                latency_ms=(time.perf_counter() - start_time) * 1000.0,
            )
        except Exception as e:
            return HealthCheckResult(
                healthy=False,
                message=f"Antigravity CLI health check failed: {e}",
                latency_ms=(time.perf_counter() - start_time) * 1000.0,
            )

    def get_capabilities(self) -> ProviderCapabilities:
        """Advertise Antigravity CLI capabilities accurately.
        
        Reflects headless analysis/review execution without unrestricted
        autonomous shell or repo-write permissions in Milestone 2.
        """
        return ProviderCapabilities(
            reasoning=True,
            structured_output=True,
            streaming=False,
            tool_calling=False,
            repository_read=True,
            repository_write=False,
            shell=False,
            git=False,
            local=False,
            is_cli=True,
            context_window=1_000_000,
            estimated_cost_per_1k_input=0.0,
            estimated_cost_per_1k_output=0.0,
            preferred_task_types=["ARCHITECTURE_DESIGN", "CODE_REVIEW", "BUG_INVESTIGATION"],
        )

    def _execute_cli(self, prompt: str) -> CLIExecutionResult:
        """Execute Antigravity CLI in headless mode with JSON output preference."""
        exe = self._resolve_executable()
        if not exe:
            install_guide = (
                "irm https://antigravity.google/cli/install.ps1 | iex"
                if os.name == "nt"
                else "curl -fsSL https://antigravity.google/cli/install.sh | bash"
            )
            raise FileNotFoundError(
                f"Antigravity CLI executable '{self.executable_path}' was not found. "
                f"Install on Windows via: {install_guide}"
            )

        cmd = [exe, "-p", prompt, "--output-format", "json"]

        # Optional model flag
        model = self.config.get("model")
        if model:
            cmd.extend(["--model", model])

        # Extra flags if configured
        extra_flags = self.config.get("flags", [])
        if extra_flags:
            cmd.extend(extra_flags)

        start_time = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(
                f"Antigravity CLI timed out after {self.timeout}s while processing task."
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"Failed to execute Antigravity CLI process: {exc}") from exc

        duration_seconds = time.perf_counter() - start_time
        stdout = proc.stdout.strip() if proc.stdout else ""
        stderr = proc.stderr.strip() if proc.stderr else ""

        if proc.returncode != 0:
            combined_err = f"{stderr} {stdout}".strip()
            if any(re.search(p, combined_err, re.IGNORECASE) for p in self.AUTH_ERROR_PATTERNS):
                raise PermissionError(
                    "Antigravity CLI authentication required. Please run 'agy' in your terminal and complete Google authentication."
                )
            raise RuntimeError(
                f"Antigravity CLI failed with exit code {proc.returncode}.\nStderr: {stderr or stdout}"
            )

        if not stdout:
            raise ValueError("Antigravity CLI returned an empty response.")

        # Parse native JSON fields
        raw_json = None
        conversation_id = None
        response_status = None
        usage = None
        response_text = None

        try:
            data = json.loads(stdout)
            if isinstance(data, dict):
                raw_json = data
                conversation_id = data.get("conversation_id")
                response_status = data.get("status")
                usage = data.get("usage")
                if "duration_seconds" in data and isinstance(data["duration_seconds"], (int, float)):
                    duration_seconds = float(data["duration_seconds"])
                response_text = (
                    data.get("response")
                    or data.get("content")
                    or data.get("text")
                )
        except Exception:
            pass

        return CLIExecutionResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=proc.returncode,
            duration_seconds=duration_seconds,
            raw_json=raw_json,
            conversation_id=conversation_id,
            response_status=response_status,
            usage=usage,
            model=model,
            response_text=response_text,
        )

    def _normalize_output(self, result: CLIExecutionResult) -> StructuredAgentOutput:
        """Extract structured domain output from native Antigravity JSON or fallback."""
        payload = result.raw_json if result.raw_json else (result.response_text or result.stdout)
        return StructuredOutputNormalizer.normalize(
            payload,
            fallback_summary="Analysis completed by Antigravity CLI."
        )

    def invoke(
        self,
        prompt: str,
        context: Optional[ContextSnapshot] = None,
        **kwargs
    ) -> AgentResponse:
        """Invoke Antigravity CLI with context snapshot and structured formatting."""
        sections = [
            "You are participating as an AI software engineering provider inside Fusion Agent.",
        ]
        if context:
            sections.append(context.to_prompt_context())

        sections.append(f"### TASK\n{prompt}")
        sections.append(
            "Provide a concrete engineering analysis and solution with clear headings:\n"
            "- **Summary**\n- **Findings**\n- **Proposal**\n- **Implementation Plan**\n- **Next Action**"
        )

        final_prompt = "\n\n".join(sections)
        exec_result = self._execute_cli(final_prompt)
        structured = self._normalize_output(exec_result)
        formatted_content = structured.to_formatted_text()

        # Token metrics: only record actual tokens if the CLI exposes reliable usage
        input_tokens = None
        output_tokens = None
        if exec_result.usage and isinstance(exec_result.usage, dict):
            if "input_tokens" in exec_result.usage:
                input_tokens = int(exec_result.usage["input_tokens"])
            if "output_tokens" in exec_result.usage:
                output_tokens = int(exec_result.usage["output_tokens"])

        metadata: Dict[str, Any] = {
            "provider": "antigravity_cli",
            "executable": self.executable_path,
            "confidence": structured.confidence,
        }
        if exec_result.conversation_id:
            metadata["conversation_id"] = exec_result.conversation_id
        if exec_result.response_status:
            metadata["status"] = exec_result.response_status
        if exec_result.usage:
            metadata["usage"] = exec_result.usage
        metadata["duration_seconds"] = exec_result.duration_seconds

        return AgentResponse(
            content=formatted_content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=exec_result.duration_seconds * 1000.0,
            raw=exec_result.stdout,
            metadata=metadata,
        )

    def review(
        self,
        content: str,
        criteria: str,
        context: Optional[ContextSnapshot] = None,
        **kwargs
    ) -> ReviewResponse:
        """Execute review via Antigravity CLI."""
        sections = [
            "You are acting as an expert code and architecture reviewer inside Fusion Agent.",
            f"### CRITERIA\n{criteria}",
            f"### CONTENT UNDER REVIEW\n{content}",
        ]
        if context:
            sections.append(context.to_prompt_context())

        sections.append(
            "Evaluate this content rigorously. Begin your review with one of:\n"
            "[APPROVED]\n[NEEDS_REVISION]\n[REJECTED]\n\n"
            "Follow with specific critiques and recommended fixes."
        )

        final_prompt = "\n\n".join(sections)
        exec_result = self._execute_cli(final_prompt)
        review_text = exec_result.response_text if exec_result.response_text else exec_result.stdout

        status = StructuredOutputNormalizer.parse_review_status(review_text)

        input_tokens = None
        output_tokens = None
        if exec_result.usage and isinstance(exec_result.usage, dict):
            if "input_tokens" in exec_result.usage:
                input_tokens = int(exec_result.usage["input_tokens"])
            if "output_tokens" in exec_result.usage:
                output_tokens = int(exec_result.usage["output_tokens"])

        return ReviewResponse(
            status=status,
            comments=review_text,
            suggested_fixes=[],
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=exec_result.duration_seconds * 1000.0,
        )
