"""Codex CLI Provider Adapter.

Integrates OpenAI Codex CLI ('codex') into Fusion Agent's provider abstraction
for headless multi-model orchestration using existing ChatGPT subscriptions.
"""

import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
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
from fusion_agent.providers.capabilities import (
    CapabilityStrength,
    CostTier,
    ProviderCapabilities,
)
from fusion_agent.providers.normalizer import (
    StructuredAgentOutput,
    StructuredOutputNormalizer,
)


@dataclass
class CodexExecutionResult:
    """Execution telemetry captured from Codex CLI subprocess."""
    stdout: str
    stderr: str
    exit_code: int
    duration_seconds: float
    thread_id: Optional[str] = None
    response_text: str = ""
    usage: Optional[Dict[str, Any]] = None


class CodexCLIProvider(AgentProvider):
    """Adapter allowing Fusion Agent to orchestrate OpenAI's Codex CLI ('codex')."""

    DEFAULT_TIMEOUT_SECONDS = 180.0

    # Common auth error patterns
    AUTH_ERROR_PATTERNS = [
        r"\b(not\s+logged\s+in|unauthenticated|please\s+log\s+in|codex\s+login)\b",
        r"\b(token\s+expired|session\s+expired|authentication\s+failed)\b",
        r"\b(sign\s+in\s+required|status\s+401|unauthorized\s+access|http\s+401)\b",
    ]

    def __init__(
        self,
        name: str = "Codex CLI",
        config: Optional[Dict[str, Any]] = None,
        executable_path: Optional[str] = None,
    ):
        super().__init__(name=name, config=config)
        self.executable_path = (
            executable_path
            or (config.get("executable_path") if config else None)
            or os.getenv("CODEX_CLI_PATH")
            or "codex"
        )
        self.timeout = float(self.config.get("timeout_seconds", self.DEFAULT_TIMEOUT_SECONDS))

    def _resolve_executable(self) -> Optional[str]:
        """Resolve path to 'codex' executable across PATH and standard install directories."""
        # 1. PATH lookup
        found = shutil.which(self.executable_path)
        if found:
            return found

        # If a custom path was configured and not found, do not fall back
        if self.executable_path not in ("codex", "codex.exe"):
            return None

        # 2. Check standard user profile Codex locations
        user_profile = os.getenv("USERPROFILE") or os.getenv("HOME")
        if user_profile:
            p_profile = Path(user_profile)
            candidates = [
                p_profile / ".codex" / "plugins" / ".plugin-appserver" / ("codex.exe" if os.name == "nt" else "codex"),
                p_profile / ".codex" / ".sandbox-bin" / ("codex.exe" if os.name == "nt" else "codex"),
                p_profile / ".codex" / "bin" / ("codex.exe" if os.name == "nt" else "codex"),
            ]
            for cand in candidates:
                if cand.is_file():
                    return str(cand.resolve())

        # 3. Check AppData Local OpenAI Codex installations
        local_appdata = os.getenv("LOCALAPPDATA")
        if local_appdata:
            pattern = str(Path(local_appdata) / "OpenAI" / "Codex" / "bin" / "*" / "codex.exe")
            matches = glob.glob(pattern)
            if matches:
                return matches[0]

        # 4. Check global npm directory
        appdata = os.getenv("APPDATA")
        if appdata:
            p_npm = Path(appdata) / "npm" / "codex.cmd"
            if p_npm.is_file():
                return str(p_npm.resolve())

        return None

    def initialize(self) -> bool:
        """Check executable availability."""
        exe = self._resolve_executable()
        self._is_initialized = (exe is not None)
        return self._is_initialized

    def health_check(self) -> HealthCheckResult:
        """Verify if 'codex' binary exists, runs, and is authenticated."""
        start_time = time.perf_counter()
        exe = self._resolve_executable()

        if not exe:
            return HealthCheckResult(
                healthy=False,
                message=(
                    f"Codex CLI executable '{self.executable_path}' was not found. "
                    "Please install the Codex app or CLI and ensure it is in PATH."
                ),
                latency_ms=(time.perf_counter() - start_time) * 1000.0,
            )

        try:
            # Check version
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
                return HealthCheckResult(
                    healthy=False,
                    message=f"Codex CLI returned error on --version: {combined_err}",
                    latency_ms=latency,
                )

            version_str = proc_ver.stdout.strip().splitlines()[0] if proc_ver.stdout.strip() else "detected"

            # Check login status via 'codex login status'
            proc_status = subprocess.run(
                [exe, "login", "status"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            status_text = f"{proc_status.stdout} {proc_status.stderr}".strip()

            if proc_status.returncode != 0 or any(re.search(p, status_text, re.IGNORECASE) for p in self.AUTH_ERROR_PATTERNS):
                return HealthCheckResult(
                    healthy=False,
                    message=(
                        f"Codex CLI ({version_str}) is installed, but requires authentication. "
                        "Please run 'codex login' in your terminal."
                    ),
                    latency_ms=(time.perf_counter() - start_time) * 1000.0,
                )

            return HealthCheckResult(
                healthy=True,
                message=f"Codex CLI ({version_str}) online and ready ({status_text}) at {exe}",
                latency_ms=(time.perf_counter() - start_time) * 1000.0,
            )

        except subprocess.TimeoutExpired:
            return HealthCheckResult(
                healthy=False,
                message="Codex CLI timed out during health check probe.",
                latency_ms=(time.perf_counter() - start_time) * 1000.0,
            )
        except Exception as e:
            return HealthCheckResult(
                healthy=False,
                message=f"Codex CLI health check failed: {e}",
                latency_ms=(time.perf_counter() - start_time) * 1000.0,
            )

    def get_capabilities(self) -> ProviderCapabilities:
        """Advertise Codex CLI capabilities."""
        return ProviderCapabilities(
            reasoning=True,
            structured_output=True,
            streaming=False,
            reasoning_strength=CapabilityStrength.HIGH,
            coding_strength=CapabilityStrength.EXPERT,
            review_strength=CapabilityStrength.HIGH,
            tool_calling=False,
            repository_read=True,
            repository_write=False,
            shell=False,
            git=False,
            local=False,
            is_cli=True,
            cost_tier=CostTier.SUBSCRIPTION,
            context_window=200_000,
            typical_latency_ms=22_000.0,
            native_token_baseline=15_000,
            estimated_cost_per_1k_input=0.0,  # Uses existing subscription
            estimated_cost_per_1k_output=0.0,
            preferred_task_types=["CODE_MODIFICATION", "CODE_REVIEW", "ARCHITECTURE_DESIGN", "BUG_INVESTIGATION"],
        )

    def _execute_cli(self, prompt: str, cwd: Optional[str] = None) -> CodexExecutionResult:
        """Execute Codex CLI in non-interactive headless mode."""
        exe = self._resolve_executable()
        if not exe:
            raise FileNotFoundError(
                f"Codex CLI executable '{self.executable_path}' was not found. "
                "Please install the Codex app or set CODEX_CLI_PATH."
            )

        cmd = [
            exe,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "-s", "read-only",
            "--json",
            "-",
        ]

        model = self.config.get("model")
        if model:
            cmd.extend(["--model", model])

        reasoning_effort = (
            self.config.get("reasoning_effort")
            or self.config.get("extra_params", {}).get("reasoning_effort")
            or "medium"
        )
        if reasoning_effort:
            cmd.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])

        extra_flags = self.config.get("flags", [])
        if extra_flags:
            cmd.extend(extra_flags)

        start_time = time.perf_counter()
        try:
            # Pass prompt via stdin using '-' argument for safe, unbound transmission
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                input=prompt,
                timeout=self.timeout,
                encoding="utf-8",
                errors="replace",
                check=False,
                cwd=cwd,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(
                f"Codex CLI timed out after {self.timeout}s while processing task."
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"Failed to execute Codex CLI process: {exc}") from exc

        duration_seconds = time.perf_counter() - start_time
        stdout = proc.stdout.strip() if proc.stdout else ""
        stderr = proc.stderr.strip() if proc.stderr else ""

        if proc.returncode != 0:
            combined_err = f"{stderr} {stdout}".strip()
            if any(re.search(p, combined_err, re.IGNORECASE) for p in self.AUTH_ERROR_PATTERNS):
                raise PermissionError(
                    "Codex CLI authentication required. Please run 'codex login' in your terminal."
                )
            raise RuntimeError(
                f"Codex CLI failed with exit code {proc.returncode}.\nStderr: {stderr or stdout}"
            )

        if not stdout:
            raise ValueError("Codex CLI returned an empty response.")

        # Parse JSONL events stream from stdout
        thread_id = None
        response_messages = []
        usage = None

        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                event_type = event.get("type")
                if event_type == "thread.started":
                    thread_id = event.get("thread_id")
                elif event_type == "item.completed":
                    item = event.get("item", {})
                    if item.get("type") == "agent_message" and item.get("text"):
                        response_messages.append(item["text"])
                elif event_type == "turn.completed":
                    usage = event.get("usage")
            except Exception:
                pass

        # If JSONL events contained agent messages, join them; otherwise use raw stdout
        response_text = "\n\n".join(response_messages).strip() if response_messages else stdout

        return CodexExecutionResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=proc.returncode,
            duration_seconds=duration_seconds,
            thread_id=thread_id,
            response_text=response_text,
            usage=usage,
        )

    def invoke(
        self,
        prompt: str,
        context: Optional[Any] = None,
        cwd: Optional[str] = None,
        **kwargs
    ) -> AgentResponse:
        """Invoke Codex CLI with context and structured response normalization within confined sandbox."""
        sections = [
            "You are participating as an AI software engineering provider inside Fusion Agent.",
        ]
        if context:
            if hasattr(context, "to_prompt_context"):
                try:
                    sections.append(context.to_prompt_context(include_task_requirements=False))
                except TypeError:
                    sections.append(context.to_prompt_context())
            else:
                sections.append(str(context))

        sections.append(f"### TASK\n{prompt}")

        final_prompt = "\n\n".join(sections)

        # Provider Confinement: Execute in empty temporary directory by default if cwd is not specified
        if cwd is not None:
            exec_result = self._execute_cli(final_prompt, cwd=cwd)
        else:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as empty_dir:
                exec_result = self._execute_cli(final_prompt, cwd=empty_dir)

        structured = None
        raw_text = exec_result.response_text or exec_result.stdout
        if "RESPONSE CONTRACT" in prompt or "### File:" in raw_text or '"steps":' in raw_text:
            formatted_content = raw_text
        else:
            structured = StructuredOutputNormalizer.normalize(
                exec_result.response_text,
                fallback_summary="Analysis completed by Codex CLI."
            )
            formatted_content = structured.to_formatted_text()

        # Token metrics from native usage if available
        input_tokens = None
        output_tokens = None
        cached_tokens = None
        reasoning_tokens = None
        visible_output_tokens = None

        if exec_result.usage and isinstance(exec_result.usage, dict):
            if "input_tokens" in exec_result.usage:
                input_tokens = int(exec_result.usage["input_tokens"])
            if "cached_input_tokens" in exec_result.usage:
                cached_tokens = int(exec_result.usage["cached_input_tokens"])
            if "output_tokens" in exec_result.usage:
                output_tokens = int(exec_result.usage["output_tokens"])
            if "reasoning_output_tokens" in exec_result.usage:
                reasoning_tokens = int(exec_result.usage["reasoning_output_tokens"])
            if output_tokens is not None and reasoning_tokens is not None:
                visible_output_tokens = max(0, output_tokens - reasoning_tokens)

        fusion_ctx_tokens = None
        if context and hasattr(context, "metrics"):
            fusion_ctx_tokens = context.metrics.get("fusion_context_tokens") or context.metrics.get("estimated_tokens")
        if fusion_ctx_tokens is None:
            fusion_ctx_tokens = max(1, len(final_prompt) // 4)

        metadata: Dict[str, Any] = {
            "provider": "codex_cli",
            "executable": self.executable_path,
            "confidence": structured.confidence if structured else 1.0,
        }
        if exec_result.thread_id:
            metadata["conversation_id"] = exec_result.thread_id
        if exec_result.usage:
            metadata["usage"] = exec_result.usage
        metadata["duration_seconds"] = exec_result.duration_seconds

        return AgentResponse(
            content=formatted_content,
            input_tokens=input_tokens or 0,
            output_tokens=output_tokens or 0,
            duration_ms=exec_result.duration_seconds * 1000.0,
            raw=exec_result.stdout,
            metadata=metadata,
            reasoning_tokens=reasoning_tokens,
            visible_output_tokens=visible_output_tokens,
            cached_tokens=cached_tokens,
            fusion_context_tokens=fusion_ctx_tokens,
        )

    def review(
        self,
        content: str,
        criteria: str,
        context: Optional[Any] = None,
        cwd: Optional[str] = None,
        **kwargs
    ) -> ReviewResponse:
        """Execute review via Codex CLI within confined sandbox."""
        sections = [
            "You are acting as an expert code and architecture reviewer inside Fusion Agent.",
            f"### CRITERIA\n{criteria}",
            f"### CONTENT UNDER REVIEW\n{content}",
        ]
        if context:
            if hasattr(context, "to_prompt_context"):
                sections.append(context.to_prompt_context())
            else:
                sections.append(str(context))

        sections.append(
            "Evaluate this content rigorously. Begin your review with one of:\n"
            "[APPROVED]\n[NEEDS_REVISION]\n[REJECTED]\n\n"
            "Follow with specific, concise critiques and recommended fixes."
        )

        final_prompt = "\n\n".join(sections)
        if cwd is not None:
            exec_result = self._execute_cli(final_prompt, cwd=cwd)
        else:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as empty_dir:
                exec_result = self._execute_cli(final_prompt, cwd=empty_dir)

        review_text = exec_result.response_text
        status = StructuredOutputNormalizer.parse_review_status(review_text)

        input_tokens = None
        output_tokens = None
        cached_tokens = None
        reasoning_tokens = None
        visible_output_tokens = None

        if exec_result.usage and isinstance(exec_result.usage, dict):
            if "input_tokens" in exec_result.usage:
                input_tokens = int(exec_result.usage["input_tokens"])
            if "cached_input_tokens" in exec_result.usage:
                cached_tokens = int(exec_result.usage["cached_input_tokens"])
            if "output_tokens" in exec_result.usage:
                output_tokens = int(exec_result.usage["output_tokens"])
            if "reasoning_output_tokens" in exec_result.usage:
                reasoning_tokens = int(exec_result.usage["reasoning_output_tokens"])
            if output_tokens is not None and reasoning_tokens is not None:
                visible_output_tokens = max(0, output_tokens - reasoning_tokens)

        fusion_ctx_tokens = None
        if context and hasattr(context, "metrics"):
            fusion_ctx_tokens = context.metrics.get("fusion_context_tokens") or context.metrics.get("estimated_tokens")
        if fusion_ctx_tokens is None:
            fusion_ctx_tokens = max(1, len(final_prompt) // 4)

        return ReviewResponse(
            status=status,
            comments=review_text,
            suggested_fixes=[],
            input_tokens=input_tokens or 0,
            output_tokens=output_tokens or 0,
            duration_ms=exec_result.duration_seconds * 1000.0,
            reasoning_tokens=reasoning_tokens,
            visible_output_tokens=visible_output_tokens,
            cached_tokens=cached_tokens,
            fusion_context_tokens=fusion_ctx_tokens,
        )
