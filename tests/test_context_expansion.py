"""Tests for bounded context expansion (CONTEXT_INSUFFICIENT protocol)."""

from unittest.mock import patch
from pathlib import Path

from fusion_agent.config.schema import FusionConfig
from fusion_agent.core.orchestrator import FusionOrchestrator
from fusion_agent.memory.database import Database
from fusion_agent.models.deliberation import ReviewStatus
from fusion_agent.models.task import Task, TaskStatus
from fusion_agent.providers.mock import MockProvider
from fusion_agent.providers.normalizer import StructuredOutputNormalizer
from fusion_agent.workspace.verifier import VerificationResult


def test_parse_context_expansion_request():
    """Verify normalizer correctly extracts files and reasons from CONTEXT_INSUFFICIENT signal."""
    output = """
Some reasoning before realization...
CONTEXT_INSUFFICIENT
- need_file: fusion_agent/providers/base.py (reason: need AgentResponse definition)
- need_file: fusion_agent/models/deliberation.py (reason: need ReviewStatus enum)
"""
    req = StructuredOutputNormalizer.parse_context_expansion_request(output)
    assert req is not None
    assert len(req.requested_files) == 2
    assert "fusion_agent/providers/base.py" in req.requested_files
    assert "fusion_agent/models/deliberation.py" in req.requested_files
    assert "AgentResponse definition" in req.reason


def test_bounded_context_expansion_round_limit(tmp_path):
    """Verify orchestrator executes at most 1 bounded expansion round when model emits CONTEXT_INSUFFICIENT."""
    (tmp_path / "calc.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")
    (tmp_path / "helpers.py").write_text("def log_add(): pass\n", encoding="utf-8")

    db = Database(":memory:")
    config = FusionConfig.default_mock_config(project_name="ExpansionTest")
    config.project_root = str(tmp_path)
    config.deliberation.max_context_expansion_rounds = 1
    config.deliberation.max_expansion_files = 2

    invocation_count = 0

    def mock_coder(prompt, context=None):
        nonlocal invocation_count
        invocation_count += 1
        if invocation_count == 1:
            # First invocation: model indicates context is insufficient and requests helpers.py
            return "CONTEXT_INSUFFICIENT\n- need_file: helpers.py (reason: need helper function signature)"
        # Second invocation (with expanded context): model provides patch
        return "### File: calc.py\n```python\nimport helpers\ndef add(a, b): return a + b\n```"

    coder = MockProvider(name="coder")
    coder.response_generator = mock_coder
    reviewer = MockProvider(
        name="reviewer",
        default_review_status=ReviewStatus.APPROVED,
        default_review_comments="[APPROVED] Looks good.",
    )

    orch = FusionOrchestrator(
        config=config,
        database=db,
        providers={"coder": coder, "reviewer": reviewer},
    )

    mock_verif = VerificationResult(
        passed=True,
        exit_code=0,
        stdout="passed",
        stderr="",
        duration_seconds=0.1,
        command="pytest",
    )

    with patch("fusion_agent.workspace.session.WorkspaceSession.prepare"), \
         patch("fusion_agent.workspace.verifier.WorkspaceVerifier.run_tests", return_value=mock_verif), \
         patch("fusion_agent.workspace.verifier.WorkspaceVerifier.get_diff", return_value="diff --git a/calc.py b/calc.py"):
        result = orch.run_task("Improve add in calc.py")

        assert result.task.status == TaskStatus.COMPLETED
        # Must have invoked coder twice (initial + 1 bounded expansion retry)
        assert invocation_count == 2
        # Check that the second invocation received helpers.py in its CodeContext
        second_call_ctx = coder.invocations[1]["context"]
        assert any(sf.path == "helpers.py" for sf in second_call_ctx.selected_files)


def test_context_expansion_rejects_secrets(tmp_path):
    """Verify model cannot request sensitive or excluded files via expansion."""
    (tmp_path / "main.py").write_text("def run(): pass\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=12345\n", encoding="utf-8")

    db = Database(":memory:")
    config = FusionConfig.default_mock_config(project_name="SecretExpansionTest")
    config.project_root = str(tmp_path)
    config.deliberation.max_context_expansion_rounds = 1

    invocation_count = 0

    def mock_coder(prompt, context=None):
        nonlocal invocation_count
        invocation_count += 1
        if invocation_count == 1:
            # Model attempts to request .env
            return "CONTEXT_INSUFFICIENT\n- need_file: .env (reason: need secrets)"
        return "### File: main.py\n```python\ndef run(): pass\n```"

    coder = MockProvider(name="coder")
    coder.response_generator = mock_coder
    reviewer = MockProvider(name="reviewer", default_review_status=ReviewStatus.APPROVED)

    orch = FusionOrchestrator(
        config=config,
        database=db,
        providers={"coder": coder, "reviewer": reviewer},
    )

    mock_verif = VerificationResult(passed=True, exit_code=0, stdout="passed", stderr="", duration_seconds=0.1, command="pytest")

    with patch("fusion_agent.workspace.session.WorkspaceSession.prepare"), \
         patch("fusion_agent.workspace.verifier.WorkspaceVerifier.run_tests", return_value=mock_verif), \
         patch("fusion_agent.workspace.verifier.WorkspaceVerifier.get_diff", return_value="diff"):
        result = orch.run_task("Task needing secrets")

        assert result.task.status == TaskStatus.COMPLETED
        # Even if retry occurred, .env MUST NOT be in selected_files
        for inv in coder.invocations:
            ctx = inv.get("context")
            if ctx and hasattr(ctx, "selected_files"):
                assert not any(sf.path == ".env" for sf in ctx.selected_files)
