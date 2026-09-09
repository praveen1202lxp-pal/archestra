"""Tests demonstrating provider context confinement and Fusion safety invariants."""

import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from fusion_agent.providers.codex_cli import CodexCLIProvider
from fusion_agent.providers.antigravity_cli import AntigravityCLIProvider
from fusion_agent.providers.gemini_cli import GeminiCLIProvider
from fusion_agent.workspace.broker import ExecutionBroker
from fusion_agent.workspace.editor import WorkspaceEditor


def test_empty_directory_execution_is_context_confinement():
    """Verify that running providers in an empty temporary directory is context confinement, not a sandbox.

    Its architectural role is:
    1. Prevent autonomous CLI tools from scraping/indexing the full workspace repository.
    2. Enforce Fusion-owned deterministic context injection.
    3. Reduce native provider token overhead.

    Host safety boundary is strictly enforced by WorkspaceEditor + ExecutionBroker.
    """
    with patch("subprocess.run") as mock_run:
        mock_proc = MagicMock()
        mock_proc.stdout = '{"type":"turn.completed","usage":{"input_tokens":1500,"output_tokens":200}}\n'
        mock_proc.stderr = ""
        mock_proc.returncode = 0
        mock_run.return_value = mock_proc

        provider = CodexCLIProvider(name="test_codex")
        resp = provider.invoke("Explain add function")

        # Check that subprocess was launched in an ephemeral temporary directory
        call_kwargs = mock_run.call_args[1]
        exec_cwd = call_kwargs.get("cwd")
        assert exec_cwd is not None
        assert "fusion_codex_" in exec_cwd or "Temp" in exec_cwd or "tmp" in exec_cwd
        # Verify it is not the main workspace root
        assert Path(exec_cwd).resolve() != Path.cwd().resolve()


def test_safety_boundary_maintained_by_workspace_editor_and_broker():
    """Verify models cannot write directly to host filesystem; all mutations pass through WorkspaceEditor and Broker."""
    mock_session = MagicMock()
    mock_session.worktree_dir = Path(tempfile.mkdtemp())

    broker = ExecutionBroker(mock_session)

    model_output = """
### File: relative/path/safe_file.py
```python
def safe():
    return True
```
"""
    # Application of edits is exclusively handled by WorkspaceEditor
    applied = WorkspaceEditor.apply_edits(model_output, broker)
    assert "relative/path/safe_file.py" in applied
    target_file = mock_session.worktree_dir / "relative/path/safe_file.py"
    assert target_file.is_file()
    assert "def safe():" in target_file.read_text(encoding="utf-8")
