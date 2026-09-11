"""Tests for No-Op Completion Guard and bounded retry mechanisms."""

from pathlib import Path
from unittest.mock import MagicMock, patch
from fusion_agent.config.schema import FusionConfig
from fusion_agent.core.orchestrator import FusionOrchestrator
from fusion_agent.memory.database import Database
from fusion_agent.models.deliberation import ReviewStatus
from fusion_agent.models.strategy import StrategyType
from fusion_agent.models.task import TaskStatus
from fusion_agent.providers.mock import MockProvider
from fusion_agent.workspace.verifier import VerificationResult


def test_edit_task_empty_patch_triggers_retry_and_succeeds(tmp_path):
    """An edit task that produces an empty patch on turn 1 triggers a retry, succeeding if turn 2 produces edits."""
    db = Database(":memory:")
    config = FusionConfig.default_mock_config(project_name="RetrySuccessTest")
    config.project_root = str(tmp_path)

    # Provider returns prose on call 1 (empty patch), then valid code block on call 2 (retry)
    responses = [
        "Here is my proposed approach to fix the bug. I suggest updating the math module.",
        "### File: src/math.py\n```python\ndef add(a, b):\n    return a + b\n```",
    ]
    call_idx = 0

    def mock_invoke(prompt, context=None):
        nonlocal call_idx
        from fusion_agent.providers.base import AgentResponse
        resp_text = responses[min(call_idx, len(responses) - 1)]
        call_idx += 1
        return AgentResponse(
            content=resp_text,
            input_tokens=100,
            output_tokens=50,
            duration_ms=150.0,
        )

    agent_1 = MockProvider(name="coder")
    agent_1.invoke = mock_invoke
    agent_2 = MockProvider(name="reviewer", default_response="[APPROVED]\nLGTM.")

    orchestrator = FusionOrchestrator(
        config=config,
        database=db,
        providers={"coder": agent_1, "reviewer": agent_2},
    )

    mock_verif = VerificationResult(
        passed=True,
        exit_code=0,
        stdout="1 passed",
        stderr="",
        duration_seconds=0.1,
        command="pytest",
    )

    diff_state = ["", "diff --git a/src/math.py b/src/math.py"]
    diff_call = 0

    def mock_get_diff(session):
        nonlocal diff_call
        d = diff_state[min(diff_call, len(diff_state) - 1)]
        diff_call += 1
        return d

    with patch("fusion_agent.workspace.session.WorkspaceSession.prepare"), \
         patch("fusion_agent.workspace.verifier.WorkspaceVerifier.run_tests", return_value=mock_verif), \
         patch("fusion_agent.workspace.verifier.WorkspaceVerifier.get_diff", side_effect=mock_get_diff):

        events = []
        result = orchestrator.run_task(
            "Fix the off-by-one error in src/math.py",
            on_status=lambda ev, data: events.append(data.get("message", "")),
        )

        assert result.task.status == TaskStatus.COMPLETED
        assert call_idx >= 2, "Expected at least 2 provider calls (initial + retry)"
        assert any("No-Op Guard" in msg for msg in events)
        assert result.diff != ""

    db.close()


def test_edit_task_with_valid_patch_accepted(tmp_path):
    """An edit task that produces a valid diff on turn 1 is accepted without retry."""
    db = Database(":memory:")
    config = FusionConfig.default_mock_config(project_name="ValidPatchTest")
    config.project_root = str(tmp_path)

    patch_output = "### File: src/math.py\n```python\ndef add(a, b):\n    return a + b\n```"
    agent_1 = MockProvider(name="coder", default_response=patch_output)
    agent_2 = MockProvider(name="reviewer", default_response="[APPROVED]\nLGTM.")

    orchestrator = FusionOrchestrator(
        config=config,
        database=db,
        providers={"coder": agent_1, "reviewer": agent_2},
    )

    mock_verif = VerificationResult(
        passed=True,
        exit_code=0,
        stdout="1 passed",
        stderr="",
        duration_seconds=0.1,
        command="pytest",
    )

    with patch("fusion_agent.workspace.session.WorkspaceSession.prepare"), \
         patch("fusion_agent.workspace.verifier.WorkspaceVerifier.run_tests", return_value=mock_verif), \
         patch("fusion_agent.workspace.verifier.WorkspaceVerifier.get_diff", return_value="diff --git a/src/math.py b/src/math.py"):

        events = []
        result = orchestrator.run_task(
            "Fix addition bug in src/math.py",
            on_status=lambda ev, data: events.append(data.get("message", "")),
        )

        assert result.task.status == TaskStatus.COMPLETED
        assert not any("No-Op Guard" in msg for msg in events)
        assert result.diff != ""

    db.close()


def test_analysis_only_task_with_empty_patch_valid(tmp_path):
    """An analysis-only task requires no diff and completes successfully with prose."""
    db = Database(":memory:")
    config = FusionConfig.default_mock_config(project_name="AnalysisTest")
    config.project_root = str(tmp_path)

    agent_1 = MockProvider(name="analyst", default_response="Analysis of architecture: clean and modular.")
    agent_2 = MockProvider(name="reviewer", default_response="Concur with analysis.")

    orchestrator = FusionOrchestrator(
        config=config,
        database=db,
        providers={"analyst": agent_1, "reviewer": agent_2},
    )

    result = orchestrator.run_task("Analyze the architecture of the payment service")
    assert result.task.status == TaskStatus.COMPLETED
    assert result.routing.task_assessment.implementation_required is False
    assert "Analysis of architecture" in result.final_answer

    db.close()


def test_existing_unrelated_tests_green_behavior_absent_empty_patch_must_not_become_no_change_required(tmp_path):
    """When existing unrelated tests pass but the requested behavior is absent and diff is empty,

    it MUST NOT become NO_CHANGE_REQUIRED; it must fail closed with NO_IMPLEMENTATION_PRODUCED.
    """
    db = Database(":memory:")
    config = FusionConfig.default_mock_config(project_name="UnrelatedTestsGreenTest")
    config.project_root = str(tmp_path)

    # Target file exists but does NOT contain the requested function 'power'
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    target_file = src_dir / "calculator.py"
    target_file.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    # Agent falsely claims no change is required because tests pass
    agent_1 = MockProvider(
        name="coder",
        default_response="All existing tests are green and passing. The repository is already correct. No change required.",
    )
    agent_2 = MockProvider(name="reviewer", default_response="[APPROVED]\nLooks good.")

    orchestrator = FusionOrchestrator(
        config=config,
        database=db,
        providers={"coder": agent_1, "reviewer": agent_2},
    )

    # Generic test suite passed, but unrelated to 'power'
    mock_verif = VerificationResult(
        passed=True,
        exit_code=0,
        stdout="5 passed in 0.05s",
        stderr="",
        duration_seconds=0.05,
        command="pytest -q",
    )

    with patch("fusion_agent.workspace.session.WorkspaceSession.prepare"), \
         patch("fusion_agent.workspace.verifier.WorkspaceVerifier.run_tests", return_value=mock_verif), \
         patch("fusion_agent.workspace.verifier.WorkspaceVerifier.get_diff", return_value=""):

        result = orchestrator.run_task("Implement def power in src/calculator.py")

        # MUST fail closed with NO_IMPLEMENTATION_PRODUCED because 'power' is absent
        assert result.task.status == TaskStatus.FAILED
        assert "NO_CHANGE_REQUIRED" not in result.final_answer
        assert "NO_IMPLEMENTATION_PRODUCED" in result.final_answer

    db.close()


def test_targeted_evidence_proves_behavior_satisfied_allows_no_change_required(tmp_path):
    """When affirmative targeted evidence proves the requested behavior is already implemented and verified,

    NO_CHANGE_REQUIRED is legitimately allowed.
    """
    db = Database(":memory:")
    config = FusionConfig.default_mock_config(project_name="TargetedEvidenceTest")
    config.project_root = str(tmp_path)

    # Target file already contains the requested implementation
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    target_file = src_dir / "calculator.py"
    target_file.write_text(
        "def add(a, b):\n    return a + b\n\ndef power(a, b):\n    return a ** b\n",
        encoding="utf-8",
    )

    agent_1 = MockProvider(
        name="coder",
        default_response="Inspection confirms def power is already implemented in src/calculator.py and verified. No change required.",
    )
    agent_2 = MockProvider(name="reviewer", default_response="[APPROVED]\nConfirmed already implemented.")

    orchestrator = FusionOrchestrator(
        config=config,
        database=db,
        providers={"coder": agent_1, "reviewer": agent_2},
    )

    # Targeted test specifically ran test_power and passed
    mock_verif = VerificationResult(
        passed=True,
        exit_code=0,
        stdout="tests/test_calculator.py::test_power PASSED\n1 passed",
        stderr="",
        duration_seconds=0.05,
        command="pytest tests/test_calculator.py -k test_power",
    )

    with patch("fusion_agent.workspace.session.WorkspaceSession.prepare"), \
         patch("fusion_agent.workspace.verifier.WorkspaceVerifier.run_tests", return_value=mock_verif), \
         patch("fusion_agent.workspace.verifier.WorkspaceVerifier.get_diff", return_value=""):

        result = orchestrator.run_task("Ensure def power is implemented in src/calculator.py")

        assert result.task.status == TaskStatus.COMPLETED
        assert "NO_CHANGE_REQUIRED" in result.final_answer

    db.close()


def test_edit_task_retry_still_empty_fails_closed(tmp_path):
    """When an edit task retry still produces an empty patch, fail closed with NO_IMPLEMENTATION_PRODUCED."""
    db = Database(":memory:")
    config = FusionConfig.default_mock_config(project_name="FailClosedTest")
    config.project_root = str(tmp_path)

    # Provider persistently produces prose only without file blocks
    agent_1 = MockProvider(name="coder", default_response="I suggest you edit src/math.py to add the function.")
    agent_2 = MockProvider(name="reviewer", default_response="Looks like only suggestions.")

    orchestrator = FusionOrchestrator(
        config=config,
        database=db,
        providers={"coder": agent_1, "reviewer": agent_2},
    )

    mock_verif = VerificationResult(
        passed=False,
        exit_code=1,
        stdout="",
        stderr="FAILED tests/test_math.py",
        duration_seconds=0.1,
        command="pytest",
    )

    with patch("fusion_agent.workspace.session.WorkspaceSession.prepare"), \
         patch("fusion_agent.workspace.verifier.WorkspaceVerifier.run_tests", return_value=mock_verif), \
         patch("fusion_agent.workspace.verifier.WorkspaceVerifier.get_diff", return_value=""):

        result = orchestrator.run_task("Implement add function in src/math.py")

        assert result.task.status == TaskStatus.FAILED
        assert "NO_IMPLEMENTATION_PRODUCED" in result.final_answer

    db.close()
