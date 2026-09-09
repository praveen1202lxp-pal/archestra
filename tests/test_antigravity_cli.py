"""Unit tests for AntigravityCLIProvider adapter."""

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fusion_agent.models.deliberation import ReviewStatus
from fusion_agent.providers.antigravity_cli import AntigravityCLIProvider
from fusion_agent.providers.base import ContextSnapshot
from fusion_agent.providers.registry import ProviderRegistry


def test_antigravity_cli_detection_not_installed():
    """Missing executable returns unhealthy status with installation guidance."""
    with patch("shutil.which", return_value=None), \
         patch.object(Path, "is_file", return_value=False):
        provider = AntigravityCLIProvider(name="test_agy", executable_path="agy")
        assert provider.initialize() is False

        health = provider.health_check()
        assert health.healthy is False
        assert "not found" in health.message.lower()
        assert "irm https://antigravity.google/cli/install.ps1 | iex" in health.message


def test_antigravity_cli_detection_localappdata_resolution():
    """Executable is discovered in %LOCALAPPDATA%\\agy\\bin\\agy.exe if not on PATH."""
    def fake_is_file(self):
        return "agy" in str(self) and "agy.exe" in str(self)

    with patch("shutil.which", return_value=None), \
         patch.object(Path, "is_file", autospec=True, side_effect=fake_is_file):
        provider = AntigravityCLIProvider(name="test_agy")
        exe = provider._resolve_executable()
        assert exe is not None
        assert "agy.exe" in exe


def test_antigravity_cli_custom_executable_path():
    """Configured custom executable path is respected."""
    with patch("shutil.which", return_value="D:\\custom\\bin\\agy.exe"):
        provider = AntigravityCLIProvider(
            name="test_agy",
            config={"executable_path": "D:\\custom\\bin\\agy.exe"}
        )
        assert provider._resolve_executable() == "D:\\custom\\bin\\agy.exe"
        assert provider.initialize() is True


def test_antigravity_cli_health_check_healthy():
    """Version query returns version string and healthy result."""
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "1.1.28\n"
    mock_proc.stderr = ""

    with patch("shutil.which", return_value="C:\\fake\\agy.exe"), \
         patch("subprocess.run", return_value=mock_proc):
        provider = AntigravityCLIProvider(name="test_agy")
        health = provider.health_check()
        assert health.healthy is True
        assert "1.1.28" in health.message
        assert "online and ready" in health.message


def test_antigravity_cli_health_check_auth_failure():
    """Version failure with auth keywords reports authentication required."""
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = ""
    mock_proc.stderr = "Error: unauthenticated. Please log in before continuing."

    with patch("shutil.which", return_value="C:\\fake\\agy.exe"), \
         patch("subprocess.run", return_value=mock_proc):
        provider = AntigravityCLIProvider(name="test_agy")
        health = provider.health_check()
        assert health.healthy is False
        assert "requires authentication" in health.message
        assert "run 'agy'" in health.message


def test_antigravity_cli_health_check_timeout():
    """Timeout during health check returns unhealthy."""
    with patch("shutil.which", return_value="C:\\fake\\agy.exe"), \
         patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["agy"], timeout=10)):
        provider = AntigravityCLIProvider(name="test_agy")
        health = provider.health_check()
        assert health.healthy is False
        assert "timed out" in health.message


def test_antigravity_cli_capabilities():
    """Accurately advertises reasoning and headless analysis capabilities."""
    provider = AntigravityCLIProvider(name="test_agy")
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
    assert caps.context_window == 1_000_000
    assert caps.is_free_or_local() is True


def test_antigravity_cli_invoke_native_json_success():
    """Parses native Antigravity CLI JSON output with usage and metadata."""
    native_json = """
    {
        "conversation_id": "test-conv-123",
        "status": "SUCCESS",
        "response": "Identified memory leak in connection pool.\\n- Pool does not reap idle connections\\n- Recommend LRU eviction",
        "duration_seconds": 2.45,
        "num_turns": 1,
        "usage": {
            "input_tokens": 1200,
            "output_tokens": 450,
            "total_tokens": 1650
        }
    }
    """
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = native_json
    mock_proc.stderr = ""

    with patch("shutil.which", return_value="C:\\fake\\agy.exe"), \
         patch("subprocess.run", return_value=mock_proc):
        provider = AntigravityCLIProvider(name="test_agy")
        context = ContextSnapshot(permanent_context="Project Alpha", current_context="Memory leak")
        response = provider.invoke("Analyze connection pool leak", context=context)

        assert "Identified memory leak" in response.content
        assert response.input_tokens == 1200
        assert response.output_tokens == 450
        assert response.duration_ms == 2450.0
        assert response.metadata["conversation_id"] == "test-conv-123"
        assert response.metadata["status"] == "SUCCESS"
        assert response.metadata["provider"] == "antigravity_cli"
        assert response.metadata["usage"]["total_tokens"] == 1650


def test_antigravity_cli_invoke_structured_schema_in_response():
    """Parses structured JSON nested inside native response string."""
    nested_json = {
        "conversation_id": "test-conv-456",
        "status": "SUCCESS",
        "response": '{"summary": "Refactor database router", "proposal": "Implement round-robin routing", "findings": ["High contention on primary node"], "implementation_plan": ["Add replica pool", "Route read queries"], "confidence": 0.99}',
        "duration_seconds": 1.5,
        "usage": {"input_tokens": 800, "output_tokens": 200}
    }
    import json
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = json.dumps(nested_json)
    mock_proc.stderr = ""

    with patch("shutil.which", return_value="C:\\fake\\agy.exe"), \
         patch("subprocess.run", return_value=mock_proc):
        provider = AntigravityCLIProvider(name="test_agy")
        response = provider.invoke("Refactor database router")

        assert "Refactor database router" in response.content
        assert "Implement round-robin routing" in response.content
        assert "High contention on primary node" in response.content
        assert response.metadata["confidence"] == 0.99


def test_antigravity_cli_invoke_markdown_fenced_json():
    """Parses markdown fenced JSON block fallback."""
    raw_output = """
    Here is the architecture proposal:
    ```json
    {
        "summary": "Decouple queue consumer",
        "proposal": "Use async event bus to isolate tasks",
        "findings": ["Direct coupling causes cascade timeouts"],
        "confidence": 0.92
    }
    ```
    """
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = raw_output
    mock_proc.stderr = ""

    with patch("shutil.which", return_value="C:\\fake\\agy.exe"), \
         patch("subprocess.run", return_value=mock_proc):
        provider = AntigravityCLIProvider(name="test_agy")
        response = provider.invoke("Decouple consumer")

        assert "Decouple queue consumer" in response.content
        assert "Use async event bus" in response.content
        assert response.metadata["confidence"] == 0.92


def test_antigravity_cli_invoke_plain_text_fallback():
    """Falls back gracefully to plain text line analysis."""
    raw_output = "Modular architecture is recommended.\n- Separation of concerns\n- Testability\n- Deployability"
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = raw_output
    mock_proc.stderr = ""

    with patch("shutil.which", return_value="C:\\fake\\agy.exe"), \
         patch("subprocess.run", return_value=mock_proc):
        provider = AntigravityCLIProvider(name="test_agy")
        response = provider.invoke("Recommend architecture")

        assert "Modular architecture is recommended" in response.content
        assert "Separation of concerns" in response.content


def test_antigravity_cli_invoke_missing_executable():
    """Invocation fails with FileNotFoundError if executable is not resolved."""
    with patch("shutil.which", return_value=None), \
         patch.object(Path, "is_file", return_value=False):
        provider = AntigravityCLIProvider(name="test_agy", executable_path="nonexistent_agy")
        with pytest.raises(FileNotFoundError) as exc_info:
            provider.invoke("Hello")
        assert "was not found" in str(exc_info.value)
        assert "install.ps1" in str(exc_info.value)


def test_antigravity_cli_invoke_auth_failure():
    """Invocation raises PermissionError when authentication error is detected."""
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = ""
    mock_proc.stderr = "Error: not logged in. Please run 'agy' to authenticate."

    with patch("shutil.which", return_value="C:\\fake\\agy.exe"), \
         patch("subprocess.run", return_value=mock_proc):
        provider = AntigravityCLIProvider(name="test_agy")
        with pytest.raises(PermissionError) as exc_info:
            provider.invoke("Test task")
        assert "authentication required" in str(exc_info.value).lower()
        assert "run 'agy'" in str(exc_info.value)


def test_antigravity_cli_invoke_non_zero_exit():
    """Invocation raises RuntimeError with process stderr on command failure."""
    mock_proc = MagicMock()
    mock_proc.returncode = 2
    mock_proc.stdout = ""
    mock_proc.stderr = "Fatal error: failed to resolve workspace path"

    with patch("shutil.which", return_value="C:\\fake\\agy.exe"), \
         patch("subprocess.run", return_value=mock_proc):
        provider = AntigravityCLIProvider(name="test_agy")
        with pytest.raises(RuntimeError) as exc_info:
            provider.invoke("Test task")
        assert "exit code 2" in str(exc_info.value)
        assert "failed to resolve workspace path" in str(exc_info.value)


def test_antigravity_cli_invoke_timeout():
    """Invocation raises TimeoutError on subprocess timeout."""
    with patch("shutil.which", return_value="C:\\fake\\agy.exe"), \
         patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["agy"], timeout=90.0)):
        provider = AntigravityCLIProvider(name="test_agy")
        with pytest.raises(TimeoutError) as exc_info:
            provider.invoke("Test task")
        assert f"timed out after {provider.timeout}s" in str(exc_info.value)


def test_antigravity_cli_invoke_empty_response():
    """Invocation raises ValueError when CLI returns blank stdout."""
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "   \n"
    mock_proc.stderr = ""

    with patch("shutil.which", return_value="C:\\fake\\agy.exe"), \
         patch("subprocess.run", return_value=mock_proc):
        provider = AntigravityCLIProvider(name="test_agy")
        with pytest.raises(ValueError) as exc_info:
            provider.invoke("Test task")
        assert "empty response" in str(exc_info.value)


def test_antigravity_cli_review():
    """Review evaluates content and returns appropriate status."""
    raw_output = "APPROVED: The proposed architectural changes meet all requirements."
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = raw_output
    mock_proc.stderr = ""

    with patch("shutil.which", return_value="C:\\fake\\agy.exe"), \
         patch("subprocess.run", return_value=mock_proc) as mock_run:
        provider = AntigravityCLIProvider(name="test_agy")
        review = provider.review("sample proposal", criteria="check correctness")
        assert review.status == ReviewStatus.APPROVED
        assert "APPROVED" in review.comments
        assert mock_run.call_args.kwargs.get("cwd") is not None


def test_antigravity_cli_review_sandboxed_cwd():
    """Verify review executes in an ephemeral directory, not repository cwd."""
    raw_output = "[NEEDS_REVISION] Add boundary check for negative index."
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = raw_output
    mock_proc.stderr = ""

    with patch("shutil.which", return_value="C:\\fake\\agy.exe"), \
         patch("subprocess.run", return_value=mock_proc) as mock_run:
        provider = AntigravityCLIProvider(name="test_agy")
        review = provider.review("diff content", criteria="review diff")
        assert review.status == ReviewStatus.NEEDS_REVISION
        passed_cwd = mock_run.call_args.kwargs.get("cwd")
        assert passed_cwd is not None
        assert os.path.isabs(passed_cwd)


def test_antigravity_cli_registry_registration():
    """Provider is registered and instantiable via ProviderRegistry."""
    assert "antigravity_cli" in ProviderRegistry.list_available()
    provider = ProviderRegistry.create(
        name="primary_agent",
        provider_type="antigravity_cli",
        config={"timeout_seconds": 45.0}
    )
    assert isinstance(provider, AntigravityCLIProvider)
    assert provider.name == "primary_agent"
    assert provider.timeout == 45.0
