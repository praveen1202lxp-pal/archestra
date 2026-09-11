"""Verification of model pinning configuration plumbing across adapters and providers."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import subprocess

from benchmarks.adapters.codex_adapter import CodexAloneAdapter
from benchmarks.adapters.agy_adapter import AntigravityAloneAdapter
from benchmarks.adapters.fusion_adapter import FusionSUTAdapter
from benchmarks.schema import BenchmarkCategory, BenchmarkTask
from fusion_agent.providers.codex_cli import CodexCLIProvider
from fusion_agent.providers.antigravity_cli import AntigravityCLIProvider


def _dummy_task() -> BenchmarkTask:
    return BenchmarkTask(
        task_id="TEST-00",
        title="Dummy test task",
        category=BenchmarkCategory.EDGE_CASE_BUG,
        prompt="Fix something in src/app.py",
        timeout_seconds=30,
    )


def test_standalone_codex_adapter_model_plumbing(tmp_path):
    """Verify CodexAloneAdapter explicitly passes --model to Codex CLI subprocess."""
    adapter = CodexAloneAdapter(is_live=True, model_id="test-pinned-codex-model")
    task = _dummy_task()

    captured_cmds = []

    def mock_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = '{"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}}\n'
        mock_proc.stderr = ""
        return mock_proc

    with patch("subprocess.run", side_effect=mock_run):
        adapter.execute(task, tmp_path)

    # Verify that at least one executed command was 'codex exec' with '--model test-pinned-codex-model'
    exec_cmds = [cmd for cmd in captured_cmds if "exec" in cmd]
    assert len(exec_cmds) > 0, "Expected codex exec command to be called"
    cmd = exec_cmds[0]
    assert "--model" in cmd
    model_idx = cmd.index("--model")
    assert cmd[model_idx + 1] == "test-pinned-codex-model"


def test_fusion_codex_provider_model_plumbing(tmp_path):
    """Verify Fusion's CodexCLIProvider explicitly passes --model to Codex CLI subprocess."""
    provider = CodexCLIProvider(config={"model": "test-fusion-codex-model"})

    captured_cmds = []

    def mock_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = '{"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}}\n'
        mock_proc.stderr = ""
        return mock_proc

    with patch("shutil.which", return_value="C:\\fake\\codex.exe"), \
         patch("subprocess.run", side_effect=mock_run):
        try:
            provider.invoke("Test prompt", cwd=str(tmp_path))
        except Exception:
            pass

    exec_cmds = [cmd for cmd in captured_cmds if "exec" in cmd]
    assert len(exec_cmds) > 0
    cmd = exec_cmds[0]
    assert "--model" in cmd
    model_idx = cmd.index("--model")
    assert cmd[model_idx + 1] == "test-fusion-codex-model"


def test_standalone_antigravity_adapter_model_plumbing(tmp_path):
    """Verify AntigravityAloneAdapter explicitly passes --model to agy CLI subprocess."""
    adapter = AntigravityAloneAdapter(is_live=True, model_id="test-pinned-agy-model")
    task = _dummy_task()

    captured_cmds = []

    def mock_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = '{"usage": {"input_tokens": 10, "output_tokens": 5}, "num_turns": 1, "response": "ok"}'
        mock_proc.stderr = ""
        return mock_proc

    with patch("subprocess.run", side_effect=mock_run):
        adapter.execute(task, tmp_path)

    container_runs = [cmd for cmd in captured_cmds if any("antigravity-benchmark" in str(c) for c in cmd)]
    assert len(container_runs) > 0, "Expected container run command"
    cmd = container_runs[0]
    assert "--model" in cmd
    model_idx = cmd.index("--model")
    assert cmd[model_idx + 1] == "test-pinned-agy-model"


def test_fusion_antigravity_provider_model_plumbing(tmp_path):
    """Verify Fusion's AntigravityCLIProvider explicitly passes --model to agy CLI subprocess."""
    provider = AntigravityCLIProvider(config={"model": "test-fusion-agy-model"})

    captured_cmds = []

    def mock_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = '{"status": "ok", "usage": {"input_tokens": 10, "output_tokens": 5}, "response": "done"}'
        mock_proc.stderr = ""
        return mock_proc

    with patch("shutil.which", return_value="C:\\fake\\agy.exe"), \
         patch("subprocess.run", side_effect=mock_run):
        try:
            provider.invoke("Test prompt", cwd=str(tmp_path))
        except Exception:
            pass

    run_cmds = [cmd for cmd in captured_cmds if any("agy" in str(c) for c in cmd)]
    assert len(run_cmds) > 0
    cmd = run_cmds[0]
    assert "--model" in cmd
    model_idx = cmd.index("--model")
    assert cmd[model_idx + 1] == "test-fusion-agy-model"


def test_fusion_adapter_model_wiring():
    """Verify FusionSUTAdapter configures codex_model_id and agy_model_id into providers."""
    adapter = FusionSUTAdapter(
        is_live=True,
        codex_model_id="pinned-codex-123",
        agy_model_id="pinned-agy-456",
    )
    assert adapter.providers is not None
    assert adapter.providers["codex"].config.get("model") == "pinned-codex-123"
    assert adapter.providers["antigravity"].config.get("model") == "pinned-agy-456"
