"""Gemini CLI Provider Adapter.

Integrates the installed Gemini CLI tool into Fusion Agent's provider abstraction
without requiring direct API billing or third-party SDK dependencies.
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


class GeminiCLIProvider(AgentProvider):
    """Adapter allowing Fusion Agent to drive the external Gemini CLI."""

    DEFAULT_TIMEOUT_SECONDS = 60.0

    # Common phrases in CLI output indicating missing authentication
    AUTH_ERROR_PATTERNS = [
        r"\b(not\s+logged\s+in|unauthenticated|please\s+log\s+in|auth\s+login)\b",
        r"\b(invalid\s+credentials|authentication\s+failed|token\s+expired)\b",
        r"\b(set\s+an\s+auth\s+method|set\s+GEMINI_API_KEY|api\s+key\s+not\s+found)\b",
    ]

    def __init__(
        self,
        name: str = "Gemini CLI",
        config: Optional[Dict[str, Any]] = None,
        executable_path: Optional[str] = None,
    ):
        super().__init__(name=name, config=config)
        self.executable_path = (
            executable_path
            or (config.get("executable_path") if config else None)
            or os.getenv("GEMINI_CLI_PATH")
            or "gemini"
        )
        self.timeout = float(self.config.get("timeout_seconds", self.DEFAULT_TIMEOUT_SECONDS))

    def _resolve_executable(self) -> Optional[str]:
        """Find the full path to the gemini executable across PATH, local project, and global npm."""
        # 1. Standard PATH lookup
        found = shutil.which(self.executable_path)
        if found:
            return found

        # If a specific custom path was configured and not found, do not fall back
        if self.executable_path != "gemini":
            return None

        # 2. Local node_modules/.bin lookup in project root or current working directory
        candidates = [
            Path("node_modules/.bin/gemini.cmd"),
            Path("node_modules/.bin/gemini.exe"),
            Path("node_modules/.bin/gemini"),
        ]

        # 3. Common global npm directories on Windows
        appdata = os.getenv("APPDATA")
        if appdata:
            candidates.append(Path(appdata) / "npm" / "gemini.cmd")
            candidates.append(Path(appdata) / "npm" / "gemini")

        localappdata = os.getenv("LOCALAPPDATA")
        if localappdata:
            candidates.append(Path(localappdata) / "npm" / "gemini.cmd")
            candidates.append(Path(localappdata) / "npm" / "gemini")

        for candidate in candidates:
            if candidate.is_file():
                return str(candidate.resolve())

        return None

    def initialize(self) -> bool:
        """Check availability and initialize the provider."""
        exe = self._resolve_executable()
        self._is_initialized = (exe is not None)
        return self._is_initialized

    def health_check(self) -> HealthCheckResult:
        """Verify if the Gemini CLI binary exists, runs, and is authenticated."""
        start_time = time.perf_counter()
        exe = self._resolve_executable()

        if not exe:
            return HealthCheckResult(
                healthy=False,
                message=(
                    f"Gemini CLI executable '{self.executable_path}' was not found in PATH or node_modules. "
                    "Install it via 'npm install -g @google/gemini-cli' or set GEMINI_CLI_PATH."
                ),
                latency_ms=(time.perf_counter() - start_time) * 1000.0,
            )

        try:
            # Query version to verify executable works
            proc_ver = subprocess.run(
                [exe, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            latency = (time.perf_counter() - start_time) * 1000.0

            # Check if version call reported an auth error
            combined_ver = f"{proc_ver.stdout} {proc_ver.stderr}".strip()
            if any(re.search(p, combined_ver, re.IGNORECASE) for p in self.AUTH_ERROR_PATTERNS):
                return HealthCheckResult(
                    healthy=False,
                    message=(
                        "Gemini CLI is installed, but requires authentication. "
                        "Please run 'gemini' in your terminal and select 'Login with Google' (or set GEMINI_API_KEY)."
                    ),
                    latency_ms=latency,
                )

            if proc_ver.returncode != 0:
                return HealthCheckResult(
                    healthy=False,
                    message=f"Gemini CLI returned non-zero code ({proc_ver.returncode}) on --version: {proc_ver.stderr.strip()}",
                    latency_ms=latency,
                )

            version_str = proc_ver.stdout.strip().splitlines()[0] if proc_ver.stdout.strip() else "detected"

            # Probe auth state via quick non-interactive probe
            proc_auth = subprocess.run(
                [exe, "-p", "ping", "--output-format", "text", "--skip-trust"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            combined_probe = f"{proc_auth.stdout} {proc_auth.stderr}".strip()

            if any(re.search(p, combined_probe, re.IGNORECASE) for p in self.AUTH_ERROR_PATTERNS):
                return HealthCheckResult(
                    healthy=False,
                    message=(
                        f"Gemini CLI ({version_str}) is installed, but requires authentication. "
                        "Please run 'gemini' in your terminal and select 'Login with Google' (or set GEMINI_API_KEY)."
                    ),
                    latency_ms=(time.perf_counter() - start_time) * 1000.0,
                )

            if proc_auth.returncode != 0:
                return HealthCheckResult(
                    healthy=False,
                    message=f"Gemini CLI ({version_str}) probe error ({proc_auth.returncode}): {proc_auth.stderr.strip() or proc_auth.stdout.strip()}",
                    latency_ms=(time.perf_counter() - start_time) * 1000.0,
                )

            return HealthCheckResult(
                healthy=True,
                message=f"Gemini CLI ({version_str}) online and ready at {exe}",
                latency_ms=(time.perf_counter() - start_time) * 1000.0,
            )

        except subprocess.TimeoutExpired:
            return HealthCheckResult(
                healthy=False,
                message="Gemini CLI timed out during health check probe.",
                latency_ms=(time.perf_counter() - start_time) * 1000.0,
            )
        except Exception as e:
            return HealthCheckResult(
                healthy=False,
                message=f"Gemini CLI health check failed: {e}",
                latency_ms=(time.perf_counter() - start_time) * 1000.0,
            )

    def get_capabilities(self) -> ProviderCapabilities:
        """Advertise realistic Gemini CLI capabilities."""
        return ProviderCapabilities(
            reasoning=True,
            structured_output=True,
            streaming=False,
            tool_calling=True,
            repository_read=True,
            repository_write=True,
            shell=True,
            git=True,
            local=False,
            is_cli=True,
            context_window=1_000_000,
            estimated_cost_per_1k_input=0.0,  # Uses user subscription / free CLI access
            estimated_cost_per_1k_output=0.0,
            preferred_task_types=["BUG_INVESTIGATION", "ARCHITECTURE_DESIGN", "CODE_MODIFICATION"],
        )

    def _execute_cli(self, prompt: str, cwd: Optional[str] = None) -> str:
        """Execute the Gemini CLI subprocess safely in headless mode with timeout and error handling."""
        exe = self._resolve_executable()
        if not exe:
            raise FileNotFoundError(
                f"Gemini CLI executable '{self.executable_path}' not found in PATH or node_modules. "
                "Please install Gemini CLI via 'npm install -g @google/gemini-cli' or configure executable_path."
            )

        # Build command: gemini -p "<prompt>" --skip-trust --output-format text
        cmd = [exe, "-p", prompt, "--skip-trust", "--output-format", "text"]

        model = self.config.get("model")
        if model:
            cmd.extend(["-m", model])

        # Allow passing custom flags
        extra_flags = self.config.get("flags", [])
        if extra_flags:
            cmd.extend(extra_flags)

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                encoding="utf-8",
                errors="replace",
                check=False,
                cwd=cwd,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(
                f"Gemini CLI timed out after {self.timeout} seconds while processing task."
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"Failed to execute Gemini CLI process: {exc}") from exc

        stdout = proc.stdout.strip() if proc.stdout else ""
        stderr = proc.stderr.strip() if proc.stderr else ""

        # Check for authentication failures
        combined_err = f"{stdout} {stderr}"
        if any(re.search(p, combined_err, re.IGNORECASE) for p in self.AUTH_ERROR_PATTERNS):
            raise PermissionError(
                "Gemini CLI authentication required. Please run 'gemini auth login' or configure your credentials."
            )

        if proc.returncode != 0:
            raise RuntimeError(
                f"Gemini CLI failed with exit code {proc.returncode}.\nStderr: {stderr or stdout}"
            )

        if not stdout:
            raise ValueError("Gemini CLI returned an empty response.")

        return stdout

    def _parse_structured_output(self, raw_output: str) -> StructuredAgentOutput:
        """Parse raw model output into StructuredAgentOutput with robust fallback."""
        return StructuredOutputNormalizer.normalize(
            raw_output,
            fallback_summary="Analysis completed by Gemini CLI."
        )

    def invoke(
        self,
        prompt: str,
        context: Optional[Any] = None,
        cwd: Optional[str] = None,
        **kwargs
    ) -> AgentResponse:
        """Invoke Gemini CLI with structured context and instructions within confined sandbox."""
        import tempfile
        start_time = time.perf_counter()

        # Build comprehensive structured prompt
        full_prompt_sections = []
        full_prompt_sections.append(
            "You are participating as an AI software engineering provider inside Fusion Agent.\n"
            "Analyze the task and provide a rigorous, concrete engineering proposal."
        )

        if context:
            if hasattr(context, "to_prompt_context"):
                try:
                    full_prompt_sections.append(context.to_prompt_context(include_task_requirements=False))
                except TypeError:
                    full_prompt_sections.append(context.to_prompt_context())
            else:
                full_prompt_sections.append(str(context))

        full_prompt_sections.append(f"### USER TASK\n{prompt}")

        # Request structured JSON format
        full_prompt_sections.append(
            "Respond in clear Markdown. If possible, structure your output with:\n"
            "- **Summary**: 1-2 sentence core conclusion\n"
            "- **Findings**: Key observations / root cause hypotheses\n"
            "- **Proposal**: Concrete technical architecture or implementation\n"
            "- **Implementation Plan**: Step-by-step numbered steps\n"
            "- **Next Action**: Recommended immediate step"
        )

        final_prompt = "\n\n".join(full_prompt_sections)

        if cwd is not None:
            raw_output = self._execute_cli(final_prompt, cwd=cwd)
        else:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as empty_dir:
                raw_output = self._execute_cli(final_prompt, cwd=empty_dir)

        structured = self._parse_structured_output(raw_output)
        formatted_content = structured.to_formatted_text()

        duration_ms = (time.perf_counter() - start_time) * 1000.0

        return AgentResponse(
            content=formatted_content,
            input_tokens=len(final_prompt.split()),
            output_tokens=len(formatted_content.split()),
            duration_ms=duration_ms,
            raw=raw_output,
            metadata={
                "provider": "gemini_cli",
                "executable": self.executable_path,
                "confidence": structured.confidence,
            },
            fusion_context_tokens=max(1, len(final_prompt) // 4),
        )

    def review(
        self,
        content: str,
        criteria: str,
        context: Optional[Any] = None,
        cwd: Optional[str] = None,
        **kwargs
    ) -> ReviewResponse:
        """Ask Gemini CLI to perform a code/architecture review within confined sandbox."""
        import tempfile
        start_time = time.perf_counter()

        review_prompt_sections = [
            "You are acting as an expert code and architecture reviewer inside Fusion Agent.",
            f"### CRITERIA\n{criteria}",
            f"### CONTENT UNDER REVIEW\n{content}",
        ]
        if context:
            if hasattr(context, "to_prompt_context"):
                review_prompt_sections.append(context.to_prompt_context())
            else:
                review_prompt_sections.append(str(context))

        review_prompt_sections.append(
            "Evaluate this content rigorously. Start your review with one of:\n"
            "[APPROVED] - If the work is sound and ready to proceed.\n"
            "[NEEDS_REVISION] - If specific fixes or improvements are required.\n"
            "[REJECTED] - If the solution is fundamentally flawed.\n\n"
            "Follow with concrete review comments and specific suggestions."
        )

        final_prompt = "\n\n".join(review_prompt_sections)
        if cwd is not None:
            raw_output = self._execute_cli(final_prompt, cwd=cwd)
        else:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as empty_dir:
                raw_output = self._execute_cli(final_prompt, cwd=empty_dir)

        # Parse review status
        status = StructuredOutputNormalizer.parse_review_status(raw_output)

        duration_ms = (time.perf_counter() - start_time) * 1000.0

        return ReviewResponse(
            status=status,
            comments=raw_output,
            suggested_fixes=[],
            input_tokens=len(final_prompt.split()),
            output_tokens=len(raw_output.split()),
            duration_ms=duration_ms,
            fusion_context_tokens=max(1, len(final_prompt) // 4),
        )
