"""Tests for FusionOrchestrator end-to-end task execution."""

from fusion_agent.config.schema import FusionConfig, OptimizationMode
from fusion_agent.core.orchestrator import FusionOrchestrator
from fusion_agent.memory.database import Database
from fusion_agent.models.task import TaskStatus
from fusion_agent.providers.mock import MockProvider


def test_orchestrator_end_to_end():
    db = Database(":memory:")
    config = FusionConfig.default_mock_config(project_name="OrchestratorTest")
    
    agent_1 = MockProvider(name="agent_1", default_response="Proposed fix for bug.")
    agent_2 = MockProvider(name="agent_2", default_response="Critique and verification.")

    orchestrator = FusionOrchestrator(
        config=config,
        database=db,
        providers={"agent_1": agent_1, "agent_2": agent_2},
    )

    events = []
    result = orchestrator.run_task(
        "Investigate deadlock between thread A and thread B",
        on_status=lambda ev, data: events.append(ev),
    )

    # Verify task completion
    assert result.final_answer is not None
    assert result.task.status == TaskStatus.COMPLETED
    assert result.routing.strategy.value in ("INDEPENDENT_INVESTIGATION", "PROPOSE_CRITIQUE_REFINE")

    # Verify persistence in SQLite
    conn = db.connect()
    task_row = conn.execute("SELECT * FROM tasks WHERE id = ?;", (result.task.id,)).fetchone()
    assert task_row is not None
    assert task_row["status"] == "COMPLETED"

    # Verify agent runs persisted
    runs = conn.execute("SELECT * FROM agent_runs WHERE task_id = ?;", (result.task.id,)).fetchall()
    assert len(runs) >= 1

    # Verify decision recorded
    decisions = conn.execute("SELECT * FROM decisions WHERE task_id = ?;", (result.task.id,)).fetchall()
    assert len(decisions) == 1
    assert "deadlock" in decisions[0]["title"].lower()

    db.close()


def test_orchestrator_context_continuity():
    """Verify that subsequent tasks receive context from earlier tasks."""
    db = Database(":memory:")
    config = FusionConfig.default_mock_config(project_name="ContinuityTest")
    
    agent = MockProvider(name="solo", default_response="Completed step.")
    orchestrator = FusionOrchestrator(config=config, database=db, providers={"solo": agent})

    # Run Task 1
    orchestrator.run_task("Task 1: Design user authentication module")

    # Run Task 2
    orchestrator.run_task("Task 2: Design password reset module")

    # Check that Task 2 received Task 1 in recent context
    context = orchestrator.state_manager.build_context_snapshot("continuitytest")
    assert "Task 1" in context.recent_context
    assert "[COMPLETED]" in context.recent_context

    db.close()


def test_orchestrator_unhealthy_primary_fallback():
    """Verify that orchestrator falls back to a healthy secondary provider."""
    db = Database(":memory:")
    config = FusionConfig.default_mock_config(project_name="FallbackTest")

    failing_primary = MockProvider(name="failing_agent", should_fail_health=True)
    healthy_secondary = MockProvider(name="fallback_agent", default_response="Fallback succeeded.")

    orchestrator = FusionOrchestrator(
        config=config,
        database=db,
        providers={"primary": failing_primary, "secondary": healthy_secondary},
    )

    events = []
    result = orchestrator.run_task(
        "Design caching layer",
        on_status=lambda ev, data: events.append(data.get("message", "")),
    )

    assert result.task.status == TaskStatus.COMPLETED
    assert "Fallback succeeded." in result.final_answer
    assert any("falling back" in ev.lower() for ev in events)
    db.close()


def test_orchestrator_all_providers_unhealthy():
    """Verify clean failure reporting when no healthy provider is available."""
    db = Database(":memory:")
    config = FusionConfig.default_mock_config(project_name="AllFailTest")

    failing_1 = MockProvider(name="failing_1", should_fail_health=True)
    failing_2 = MockProvider(name="failing_2", should_fail_health=True)

    orchestrator = FusionOrchestrator(
        config=config,
        database=db,
        providers={"p1": failing_1, "p2": failing_2},
    )

    result = orchestrator.run_task("Analyze query")
    assert result.task.status == TaskStatus.FAILED
    assert "unavailable" in result.final_answer.lower()
    db.close()


def test_orchestrator_autonomous_edit_flow(tmp_path):
    """Verify end-to-end autonomous editing loop with isolation, tests, and peer review."""
    from unittest.mock import MagicMock, patch
    from fusion_agent.models.deliberation import ReviewStatus
    from fusion_agent.models.strategy import StrategyType
    from fusion_agent.workspace.session import WorkspaceState
    from fusion_agent.workspace.verifier import VerificationResult

    db = Database(":memory:")
    config = FusionConfig.default_mock_config(project_name="EditTest")
    config.project_root = str(tmp_path)

    patch_output = "### File: src/math.py\n```python\ndef add(a, b):\n    return a + b\n```"
    agent_1 = MockProvider(name="coder", default_response=patch_output)
    agent_2 = MockProvider(name="reviewer", default_response="[APPROVED]\nLGTM, clean implementation.")

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

        result = orchestrator.run_task("Implement a math helper function in Python")

        assert result.routing.strategy == StrategyType.AUTONOMOUS_EDIT
        assert result.task.status == TaskStatus.COMPLETED
        assert result.workspace_session is not None
        assert result.verification_result.passed is True
        assert result.diff is not None
        assert result.review_result.status == ReviewStatus.APPROVED
        assert "math.py" in result.final_answer

    db.close()


def test_orchestrator_autonomous_edit_dirty_tree_refusal(tmp_path):
    """Verify strict refusal when starting autonomous edit on a dirty repository."""
    from unittest.mock import patch
    from fusion_agent.workspace.session import DirtyWorkingTreeError

    db = Database(":memory:")
    config = FusionConfig.default_mock_config(project_name="DirtyRefusalTest")
    config.project_root = str(tmp_path)

    agent_1 = MockProvider(name="coder", default_response="code")
    agent_2 = MockProvider(name="reviewer", default_response="review")
    orchestrator = FusionOrchestrator(
        config=config,
        database=db,
        providers={"coder": agent_1, "reviewer": agent_2},
    )

    with patch(
        "fusion_agent.workspace.session.WorkspaceSession.prepare",
        side_effect=DirtyWorkingTreeError("Uncommitted changes detected in repo."),
    ):
        result = orchestrator.run_task("Implement a feature in Python")

        assert result.task.status == TaskStatus.FAILED
        assert "Uncommitted changes detected" in result.final_answer

    db.close()


def test_orchestrator_repair_loop_success(tmp_path):
    """Verify that a rejected patch is repaired within the same session and approved."""
    from unittest.mock import patch
    from fusion_agent.models.deliberation import ReviewStatus
    from fusion_agent.providers.base import ReviewResponse
    from fusion_agent.workspace.verifier import VerificationResult

    db = Database(":memory:")
    config = FusionConfig.default_mock_config(project_name="RepairSuccessTest")
    config.project_root = str(tmp_path)
    config.deliberation.max_repair_rounds = 2

    def coder_response(prompt, context=None):
        if "PEER REVIEW CRITIQUE" in prompt:
            return "### File: src/math.py\n```python\ndef divide(a, b):\n    if b == 0:\n        raise ZeroDivisionError('cannot divide by zero')\n    return a / b\n```"
        return "### File: src/math.py\n```python\ndef divide(a, b):\n    return a / b\n```"

    review_count = 0
    def reviewer_review(content, criteria, context=None):
        nonlocal review_count
        review_count += 1
        if review_count == 1:
            return ReviewResponse(
                status=ReviewStatus.NEEDS_REVISION,
                comments="[NEEDS_REVISION] Missing zero division error handling.",
                suggested_fixes=["Add check for b == 0"],
                input_tokens=100,
                output_tokens=20,
            )
        return ReviewResponse(
            status=ReviewStatus.APPROVED,
            comments="[APPROVED] Zero division check added properly.",
            suggested_fixes=[],
            input_tokens=120,
            output_tokens=15,
        )

    agent_1 = MockProvider(name="coder")
    agent_1.response_generator = coder_response

    agent_2 = MockProvider(name="reviewer")
    agent_2.review_generator = reviewer_review

    orchestrator = FusionOrchestrator(
        config=config,
        database=db,
        providers={"coder": agent_1, "reviewer": agent_2},
    )

    mock_verif = VerificationResult(
        passed=True,
        exit_code=0,
        stdout="tests passed",
        stderr="",
        duration_seconds=0.1,
        command="pytest",
    )

    with patch("fusion_agent.workspace.session.WorkspaceSession.prepare"), \
         patch("fusion_agent.workspace.verifier.WorkspaceVerifier.run_tests", return_value=mock_verif), \
         patch("fusion_agent.workspace.verifier.WorkspaceVerifier.get_diff", return_value="diff --git a/src/math.py b/src/math.py"):

        result = orchestrator.run_task("Implement divide function in Python")

        assert result.task.status == TaskStatus.COMPLETED
        assert result.review_result.status == ReviewStatus.APPROVED
        assert len(result.deliberation.reviews) == 2
        assert len(result.deliberation.proposals) == 2
        assert result.deliberation.rounds_executed == 2

        # Check stage metrics
        stages = [sm["stage"] for sm in result.deliberation.stage_metrics]
        assert "Initial Implementation" in stages
        assert "Review Round 1" in stages
        assert "Repair Round 1" in stages
        assert "Review Round 2" in stages

        # Check database records
        conn = db.connect()
        runs = conn.execute("SELECT * FROM agent_runs WHERE task_id = ?;", (result.task.id,)).fetchall()
        assert len(runs) >= 4

    db.close()


def test_orchestrator_repair_loop_exceeds_max_rounds(tmp_path):
    """Verify that loop terminates at max_repair_rounds if revisions are continuously rejected."""
    from unittest.mock import patch
    from fusion_agent.models.deliberation import ReviewStatus
    from fusion_agent.workspace.verifier import VerificationResult

    db = Database(":memory:")
    config = FusionConfig.default_mock_config(project_name="RepairExceedTest")
    config.project_root = str(tmp_path)
    config.deliberation.max_repair_rounds = 2

    agent_1 = MockProvider(name="coder", default_response="### File: src/math.py\n```python\nx = 1\n```")
    agent_2 = MockProvider(
        name="reviewer",
        default_review_status=ReviewStatus.NEEDS_REVISION,
        default_review_comments="[NEEDS_REVISION] Code is still insufficient.",
    )

    orchestrator = FusionOrchestrator(
        config=config,
        database=db,
        providers={"coder": agent_1, "reviewer": agent_2},
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
         patch("fusion_agent.workspace.verifier.WorkspaceVerifier.get_diff", return_value="diff"):

        result = orchestrator.run_task("Write Python code for feature")

        # Stopped after 2 repair rounds (1 initial review + 2 repair reviews = 3 reviews total)
        assert result.review_result.status == ReviewStatus.NEEDS_REVISION
        assert len(result.deliberation.reviews) == 3
        assert len(result.deliberation.proposals) == 3
        assert "Repair rounds executed: 2" in result.final_answer

    db.close()


def test_orchestrator_repair_loop_bounded_feedback(tmp_path):
    """Verify that reviewer feedback passed into the repair prompt is truncated to max_feedback_chars."""
    from unittest.mock import patch
    from fusion_agent.models.deliberation import ReviewStatus
    from fusion_agent.workspace.verifier import VerificationResult

    db = Database(":memory:")
    config = FusionConfig.default_mock_config(project_name="BoundFeedbackTest")
    config.project_root = str(tmp_path)
    config.deliberation.max_repair_rounds = 1
    config.deliberation.max_feedback_chars = 50

    long_critique = "A" * 500
    agent_1 = MockProvider(name="coder", default_response="### File: src/math.py\n```python\nx = 1\n```")
    agent_2 = MockProvider(
        name="reviewer",
        default_review_status=ReviewStatus.NEEDS_REVISION,
        default_review_comments=long_critique,
    )

    orchestrator = FusionOrchestrator(
        config=config,
        database=db,
        providers={"coder": agent_1, "reviewer": agent_2},
    )

    mock_verif = VerificationResult(
        passed=True,
        exit_code=0,
        stdout="",
        stderr="",
        duration_seconds=0.1,
        command="pytest",
    )

    with patch("fusion_agent.workspace.session.WorkspaceSession.prepare"), \
         patch("fusion_agent.workspace.verifier.WorkspaceVerifier.run_tests", return_value=mock_verif), \
         patch("fusion_agent.workspace.verifier.WorkspaceVerifier.get_diff", return_value="diff"):

        result = orchestrator.run_task("Implement bounded feedback test in Python")

        assert len(agent_1.invocations) == 2
        repair_prompt = agent_1.invocations[1]["prompt"]
        assert "PEER REVIEW CRITIQUE" in repair_prompt
        assert "A" * 30 in repair_prompt
        assert "A" * 50 not in repair_prompt

    db.close()


def test_orchestrator_repair_loop_call_limit(tmp_path):
    """Verify that repair loop respects max_provider_calls hard ceiling."""
    from unittest.mock import patch
    from fusion_agent.models.deliberation import ReviewStatus
    from fusion_agent.workspace.verifier import VerificationResult

    db = Database(":memory:")
    config = FusionConfig.default_mock_config(project_name="CallLimitTest")
    config.project_root = str(tmp_path)
    config.deliberation.max_repair_rounds = 5
    config.deliberation.max_provider_calls = 2

    agent_1 = MockProvider(name="coder", default_response="### File: src/math.py\n```python\nx = 1\n```")
    agent_2 = MockProvider(
        name="reviewer",
        default_review_status=ReviewStatus.NEEDS_REVISION,
        default_review_comments="[NEEDS_REVISION] Needs fix",
    )

    orchestrator = FusionOrchestrator(
        config=config,
        database=db,
        providers={"coder": agent_1, "reviewer": agent_2},
    )

    mock_verif = VerificationResult(
        passed=True,
        exit_code=0,
        stdout="",
        stderr="",
        duration_seconds=0.1,
        command="pytest",
    )

    with patch("fusion_agent.workspace.session.WorkspaceSession.prepare"), \
         patch("fusion_agent.workspace.verifier.WorkspaceVerifier.run_tests", return_value=mock_verif), \
         patch("fusion_agent.workspace.verifier.WorkspaceVerifier.get_diff", return_value="diff"):

        result = orchestrator.run_task("Implement feature for call limits in Python")

        assert len(agent_1.invocations) == 1
        assert len(agent_2.reviews) == 1
        assert len(result.deliberation.reviews) == 1
        assert "Repair rounds executed: 0" in result.final_answer

    db.close()


