"""Tests for GeminiCLIProvider adapter."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from fusion_agent.models.deliberation import ReviewStatus
from fusion_agent.providers.base import ContextSnapshot
from fusion_agent.providers.gemini_cli import GeminiCLIProvider, StructuredAgentOutput
from fusion_agent.providers.registry import ProviderRegistry


def test_gemini_cli_detection_not_installed():
    with patch("shutil.which", return_value=None):
        provider = GeminiCLIProvider(name="test_gemini", executable_path="nonexistent_gemini")
        assert provider.initialize() is False

        health = provider.health_check()
        assert health.healthy is False
        assert "not found in PATH" in health.message


def test_gemini_cli_detection_installed_and_healthy():
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "gemini-cli version 0.59.0\n"
    mock_proc.stderr = ""

    with patch("shutil.which", return_value="C:\\fake\\path\\gemini.exe"), \
         patch("subprocess.run", return_value=mock_proc):
        provider = GeminiCLIProvider(name="test_gemini")
        assert provider.initialize() is True

        health = provider.health_check()
        assert health.healthy is True
        assert "0.59.0" in health.message


def test_gemini_cli_detection_auth_required():
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = ""
    mock_proc.stderr = "Error: please log in or run 'gemini auth login' to continue."

    with patch("shutil.which", return_value="C:\\fake\\path\\gemini.exe"), \
         patch("subprocess.run", return_value=mock_proc):
        provider = GeminiCLIProvider(name="test_gemini")
        health = provider.health_check()
        assert health.healthy is False
        assert "requires authentication" in health.message
        assert "Login with Google" in health.message or "GEMINI_API_KEY" in health.message


def test_gemini_cli_capabilities():
    provider = GeminiCLIProvider(name="test_gemini")
    caps = provider.get_capabilities()
    assert caps.reasoning is True
    assert caps.is_cli is True
    assert caps.repository_read is True
    assert caps.repository_write is True
    assert caps.shell is True
    assert caps.git is True
    assert caps.context_window == 1_000_000
    assert caps.is_free_or_local() is True


def test_gemini_cli_invoke_structured_json():
    json_response = """
    {
        "summary": "Identified circular wait condition in worker mutex.",
        "proposal": "Reorder lock acquisitions so Lock A precedes Lock B.",
        "implementation_plan": ["Refactor worker.cpp", "Add mutex order assertions"],
        "findings": ["Thread 1 holds A, waits for B", "Thread 2 holds B, waits for A"],
        "confidence": 0.98,
        "recommended_next_action": "Apply lock ordering patch"
    }
    """
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = json_response
    mock_proc.stderr = ""

    with patch("shutil.which", return_value="C:\\fake\\gemini.exe"), \
         patch("subprocess.run", return_value=mock_proc):
        provider = GeminiCLIProvider(name="test_gemini")
        context = ContextSnapshot(permanent_context="Project Info", current_context="Fix deadlock")
        response = provider.invoke("Fix deadlock", context=context)

        assert "circular wait condition" in response.content
        assert "Reorder lock acquisitions" in response.content
        assert "Thread 1 holds A" in response.content
        assert response.output_tokens > 0
        assert response.metadata["confidence"] == 0.98


def test_gemini_cli_invoke_markdown_json_block():
    markdown_response = """
    Here is the architecture analysis:
    ```json
    {
        "summary": "Non-blocking queue design using atomic CAS.",
        "proposal": "Use std::atomic<Node*> with Michael-Scott queue algorithm.",
        "findings": ["Lock-based approach suffers from contention under 32 threads."],
        "confidence": 0.95
    }
    ```
    """
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = markdown_response
    mock_proc.stderr = ""

    with patch("shutil.which", return_value="C:\\fake\\gemini.exe"), \
         patch("subprocess.run", return_value=mock_proc):
        provider = GeminiCLIProvider(name="test_gemini")
        response = provider.invoke("Design lock-free queue")

        assert "Non-blocking queue design" in response.content
        assert "Michael-Scott queue" in response.content


def test_gemini_cli_invoke_fallback_plain_prose():
    prose_response = (
        "I analyzed the repository.\n"
        "- The thread pool queue has unbounded growth.\n"
        "- Task cancellation lacks synchronization.\n"
        "We should add backpressure to the queue."
    )
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = prose_response
    mock_proc.stderr = ""

    with patch("shutil.which", return_value="C:\\fake\\gemini.exe"), \
         patch("subprocess.run", return_value=mock_proc):
        provider = GeminiCLIProvider(name="test_gemini")
        response = provider.invoke("Review thread pool")

        assert "I analyzed the repository" in response.content
        assert "unbounded growth" in response.content


def test_gemini_cli_invoke_timeout():
    with patch("shutil.which", return_value="C:\\fake\\gemini.exe"), \
         patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["gemini"], timeout=5)):
        provider = GeminiCLIProvider(name="test_gemini", config={"timeout_seconds": 5})
        with pytest.raises(TimeoutError) as exc_info:
            provider.invoke("Heavy task")
        assert "timed out" in str(exc_info.value)


def test_gemini_cli_invoke_empty_response():
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "   \n  "
    mock_proc.stderr = ""

    with patch("shutil.which", return_value="C:\\fake\\gemini.exe"), \
         patch("subprocess.run", return_value=mock_proc):
        provider = GeminiCLIProvider(name="test_gemini")
        with pytest.raises(ValueError) as exc_info:
            provider.invoke("Task")
        assert "empty response" in str(exc_info.value)


def test_gemini_cli_invoke_auth_error():
    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = ""
    mock_proc.stderr = "Authentication failed: Not logged in. Please run 'gemini auth login'."

    with patch("shutil.which", return_value="C:\\fake\\gemini.exe"), \
         patch("subprocess.run", return_value=mock_proc):
        provider = GeminiCLIProvider(name="test_gemini")
        with pytest.raises(PermissionError) as exc_info:
            provider.invoke("Task")
        assert "gemini auth login" in str(exc_info.value)


def test_gemini_cli_invoke_process_error():
    mock_proc = MagicMock()
    mock_proc.returncode = 2
    mock_proc.stdout = ""
    mock_proc.stderr = "Fatal error: unexpected flag --unknown"

    with patch("shutil.which", return_value="C:\\fake\\gemini.exe"), \
         patch("subprocess.run", return_value=mock_proc):
        provider = GeminiCLIProvider(name="test_gemini")
        with pytest.raises(RuntimeError) as exc_info:
            provider.invoke("Task")
        assert "exit code 2" in str(exc_info.value)


def test_gemini_cli_review():
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "[APPROVED] Architecture looks modular and conforms to requirements."
    mock_proc.stderr = ""

    with patch("shutil.which", return_value="C:\\fake\\gemini.exe"), \
         patch("subprocess.run", return_value=mock_proc):
        provider = GeminiCLIProvider(name="test_gemini")
        rev = provider.review("diff content", criteria="Check style")
        assert rev.status == ReviewStatus.APPROVED
        assert "Architecture looks modular" in rev.comments


def test_gemini_cli_registry_integration():
    registered = ProviderRegistry.list_available()
    assert "gemini_cli" in registered

    prov = ProviderRegistry.create(
        name="gemini_agent",
        provider_type="gemini_cli",
        config={"executable_path": "custom_gemini"},
    )
    assert isinstance(prov, GeminiCLIProvider)
    assert prov.name == "gemini_agent"
    assert prov.executable_path == "custom_gemini"
