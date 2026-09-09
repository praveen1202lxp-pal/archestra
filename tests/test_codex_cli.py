"""Unit tests for CodexCLIProvider adapter."""

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fusion_agent.models.deliberation import ReviewStatus
from fusion_agent.providers.base import ContextSnapshot
from fusion_agent.providers.codex_cli import CodexCLIProvider
from fusion_agent.providers.registry import ProviderRegistry


def test_codex_cli_detection_not_installed():
    """Missing executable returns unhealthy status with helpful guidance."""
    with patch("shutil.which", return_value=None), \
         patch.object(Path, "is_file", return_value=False), \
         patch("glob.glob", return_value=[]):
        provider = CodexCLIProvider(name="test_codex", executable_path="codex")
        assert provider.initialize() is False

        health = provider.health_check()
        assert health.healthy is False
        assert "not found" in health.message.lower()


def test_codex_cli_detection_profile_resolution():
    """Discovers codex in user profile directory if not in PATH."""
    def fake_is_file(self):
        return ".codex" in str(self) and "codex.exe" in str(self)

    with patch("shutil.which", return_value=None), \
         patch.object(Path, "is_file", autospec=True, side_effect=fake_is_file):
        provider = CodexCLIProvider(name="test_codex")
        exe = provider._resolve_executable()
        assert exe is not None
        assert "codex.exe" in exe


def test_codex_cli_custom_executable_path():
    """Configured custom executable path is respected."""
    with patch("shutil.which", return_value="C:\\custom\\codex.exe"):
        provider = CodexCLIProvider(
            name="test_codex",
            config={"executable_path": "C:\\custom\\codex.exe"}
        )
        assert provider._resolve_executable() == "C:\\custom\\codex.exe"
        assert provider.initialize() is True


def test_codex_cli_health_check_healthy():
    """Version query and login status return healthy result."""
    proc_ver = MagicMock()
    proc_ver.returncode = 0
    proc_ver.stdout = "codex-cli 0.153.0-alpha.5\n"
    proc_ver.stderr = ""

    proc_login = MagicMock()
    proc_login.returncode = 0
    proc_login.stdout = "Logged in using ChatGPT\n"
    proc_login.stderr = ""

    with patch("shutil.which", return_value="C:\\fake\\codex.exe"), \
         patch("subprocess.run", side_effect=[proc_ver, proc_login]):
        provider = CodexCLIProvider(name="test_codex")
        health = provider.health_check()
        assert health.healthy is True
        assert "0.153.0" in health.message
        assert "Logged in using ChatGPT" in health.message


def test_codex_cli_health_check_auth_failure():
    """Unauthenticated login status reports authentication required."""
    proc_ver = MagicMock()
    proc_ver.returncode = 0
    proc_ver.stdout = "codex-cli 0.153.0-alpha.5\n"
    proc_ver.stderr = ""

    proc_login = MagicMock()
    proc_login.returncode = 1
    proc_login.stdout = ""
    proc_login.stderr = "Not logged in. Please run codex login."

    with patch("shutil.which", return_value="C:\\fake\\codex.exe"), \
         patch("subprocess.run", side_effect=[proc_ver, proc_login]):
        provider = CodexCLIProvider(name="test_codex")
        health = provider.health_check()
        assert health.healthy is False
        assert "requires authentication" in health.message
        assert "codex login" in health.message


def test_codex_cli_health_check_timeout():
    """Timeout during health check returns unhealthy result."""
    with patch("shutil.which", return_value="C:\\fake\\codex.exe"), \
         patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["codex"], timeout=10)):
        provider = CodexCLIProvider(name="test_codex")
        health = provider.health_check()
        assert health.healthy is False
        assert "timed out" in health.message


def test_codex_cli_capabilities():
    """Accurately advertises reasoning and headless analysis capabilities."""
    provider = CodexCLIProvider(name="test_codex")
    caps = provider.get_capabilities()

    assert caps.reasoning is True
    assert caps.structured_output is True
    assert caps.is_cli is True
    assert caps.streaming is False
    assert caps.tool_calling is False
    assert caps.repository_read is True
    assert caps.repository_write is False
    assert caps.shell is False
    assert caps.git is False
    assert caps.context_window == 200_000
    assert caps.is_free_or_local() is True


def test_codex_cli_invoke_jsonl_stream_success():
    """Parses JSONL events from codex exec including usage and thread_id."""
    jsonl_output = "\n".join([
        '{"type":"thread.started","thread_id":"thread-abc-123"}',
        '{"type":"turn.started"}',
        '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"Identified bottleneck in cache eviction.\\n- High contention\\n- Recommend sharding"}}',
        '{"type":"turn.completed","usage":{"input_tokens":1500,"output_tokens":250,"total_tokens":1750}}'
    ])

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = jsonl_output
    mock_proc.stderr = ""

    with patch("shutil.which", return_value="C:\\fake\\codex.exe"), \
         patch("subprocess.run", return_value=mock_proc):
        provider = CodexCLIProvider(name="test_codex")
        context = ContextSnapshot(permanent_context="Project Beta", current_context="Cache contention")
        response = provider.invoke("Analyze cache contention", context=context)

        assert "Identified bottleneck" in response.content
        assert response.input_tokens == 1500
        assert response.output_tokens == 250
        assert response.metadata["conversation_id"] == "thread-abc-123"
        assert response.metadata["provider"] == "codex_cli"
        assert response.metadata["usage"]["total_tokens"] == 1750


def test_codex_cli_invoke_markdown_fenced_json():
    """Extracts structured fields from markdown code blocks in message."""
    import json
    agent_message = (
        "```json\n"
        "{\n"
        '    "summary": "Introduce read replicas",\n'
        '    "proposal": "Offload read queries to async replicas",\n'
        '    "findings": ["Primary DB at 95% CPU"],\n'
        '    "confidence": 0.96\n'
        "}\n"
        "```"
    )
    jsonl_output = json.dumps({
        "type": "item.completed",
        "item": {"type": "agent_message", "text": agent_message}
    })

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = jsonl_output
    mock_proc.stderr = ""

    with patch("shutil.which", return_value="C:\\fake\\codex.exe"), \
         patch("subprocess.run", return_value=mock_proc):
        provider = CodexCLIProvider(name="test_codex")
        response = provider.invoke("Scale database")

        assert "Introduce read replicas" in response.content
        assert "Offload read queries" in response.content
        assert response.metadata["confidence"] == 0.96


def test_codex_cli_invoke_plain_text_fallback():
    """Falls back gracefully to plain text line analysis."""
    jsonl_output = '{"type":"item.completed","item":{"type":"agent_message","text":"Proposed lock-free queue architecture.\\n- Use atomics\\n- Use CAS loops"}}'

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = jsonl_output
    mock_proc.stderr = ""

    with patch("shutil.which", return_value="C:\\fake\\codex.exe"), \
         patch("subprocess.run", return_value=mock_proc):
        provider = CodexCLIProvider(name="test_codex")
        response = provider.invoke("Design queue")

        assert "Proposed lock-free queue" in response.content


def test_codex_cli_invoke_missing_executable():
    """Invocation raises FileNotFoundError if binary cannot be resolved."""
    with patch("shutil.which", return_value=None), \
         patch.object(Path, "is_file", return_value=False), \
         patch("glob.glob", return_value=[]):
        provider = CodexCLIProvider(name="test_codex", executable_path="nonexistent_codex")
        with pytest.raises(FileNotFoundError) as exc_info:
            provider.invoke("Task")
        assert "was not found" in str(exc_info.value)


def test_codex_cli_invoke_auth_failure():
    """Invocation raises PermissionError when authentication error is reported."""
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = ""
    mock_proc.stderr = "Error: unauthenticated. Please log in before proceeding."

    with patch("shutil.which", return_value="C:\\fake\\codex.exe"), \
         patch("subprocess.run", return_value=mock_proc):
        provider = CodexCLIProvider(name="test_codex")
        with pytest.raises(PermissionError) as exc_info:
            provider.invoke("Task")
        assert "authentication required" in str(exc_info.value).lower()
        assert "codex login" in str(exc_info.value)


def test_codex_cli_invoke_non_zero_exit():
    """Invocation raises RuntimeError on non-zero exit code."""
    mock_proc = MagicMock()
    mock_proc.returncode = 2
    mock_proc.stdout = ""
    mock_proc.stderr = "Internal server connection failed."

    with patch("shutil.which", return_value="C:\\fake\\codex.exe"), \
         patch("subprocess.run", return_value=mock_proc):
        provider = CodexCLIProvider(name="test_codex")
        with pytest.raises(RuntimeError) as exc_info:
            provider.invoke("Task")
        assert "exit code 2" in str(exc_info.value)


def test_codex_cli_invoke_timeout():
    """Invocation raises TimeoutError on process timeout."""
    with patch("shutil.which", return_value="C:\\fake\\codex.exe"), \
         patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["codex"], timeout=90.0)):
        provider = CodexCLIProvider(name="test_codex")
        with pytest.raises(TimeoutError) as exc_info:
            provider.invoke("Task")
        assert f"timed out after {provider.timeout}s" in str(exc_info.value)


def test_codex_cli_invoke_empty_response():
    """Invocation raises ValueError when CLI produces empty output."""
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "   \n"
    mock_proc.stderr = ""

    with patch("shutil.which", return_value="C:\\fake\\codex.exe"), \
         patch("subprocess.run", return_value=mock_proc):
        provider = CodexCLIProvider(name="test_codex")
        with pytest.raises(ValueError) as exc_info:
            provider.invoke("Task")
        assert "empty response" in str(exc_info.value)


def test_codex_cli_review():
    """Review evaluates content and returns appropriate status."""
    jsonl_output = '{"type":"item.completed","item":{"type":"agent_message","text":"APPROVED: Architecture conforms to best practices."}}'
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = jsonl_output
    mock_proc.stderr = ""

    with patch("shutil.which", return_value="C:\\fake\\codex.exe"), \
         patch("subprocess.run", return_value=mock_proc) as mock_run:
        provider = CodexCLIProvider(name="test_codex")
        review = provider.review("sample proposal", criteria="check correctness")
        assert review.status == ReviewStatus.APPROVED
        assert "APPROVED" in review.comments
        # Verify review executed with an isolated working directory
        assert mock_run.call_args.kwargs.get("cwd") is not None


def test_codex_cli_review_sandboxed_cwd():
    """Verify review executes in an ephemeral directory, not repository cwd."""
    jsonl_output = '{"type":"item.completed","item":{"type":"agent_message","text":"[NEEDS_REVISION] Missing null check"}}'
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = jsonl_output
    mock_proc.stderr = ""

    with patch("shutil.which", return_value="C:\\fake\\codex.exe"), \
         patch("subprocess.run", return_value=mock_proc) as mock_run:
        provider = CodexCLIProvider(name="test_codex")
        review = provider.review("diff content", criteria="review diff")
        assert review.status == ReviewStatus.NEEDS_REVISION
        passed_cwd = mock_run.call_args.kwargs.get("cwd")
        assert passed_cwd is not None
        assert os.path.isabs(passed_cwd)



def test_codex_cli_registry_registration():
    """Provider is registered and instantiable via ProviderRegistry."""
    assert "codex_cli" in ProviderRegistry.list_available()
    provider = ProviderRegistry.create(
        name="secondary_agent",
        provider_type="codex_cli",
        config={"timeout_seconds": 60.0}
    )
    assert isinstance(provider, CodexCLIProvider)
    assert provider.name == "secondary_agent"
    assert provider.timeout == 60.0
