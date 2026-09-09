"""Unit tests for Orchestrator dynamic roles and budget controls (Milestone 6)."""

from pathlib import Path
from fusion_agent.config.schema import DeliberationConfig, FusionConfig, OptimizationMode
from fusion_agent.core.orchestrator import FusionOrchestrator
from fusion_agent.memory.database import Database
from fusion_agent.models.deliberation import ReviewStatus
from fusion_agent.models.task import Task
from fusion_agent.providers.capabilities import CapabilityStrength, CostTier, ProviderCapabilities
from fusion_agent.providers.mock import MockProvider


def test_orchestrator_role_reversal_execution(tmp_path):
    """Verify autonomous edit runs successfully with reversed roles (Provider B implements, Provider A reviews)."""
    # 1. Setup isolated test repo
    test_repo = tmp_path / "repo"
    test_repo.mkdir()
    (test_repo / "main.py").write_text("def hello(): return 'old'\n", encoding="utf-8")

    import subprocess
    subprocess.run(["git", "init"], cwd=test_repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "TestUser"], cwd=test_repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.local"], cwd=test_repo, check=True)
    subprocess.run(["git", "add", "."], cwd=test_repo, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=test_repo, check=True)

    # 2. Setup providers where Agent B is the implementer and Agent A is reviewer
    agent_b_resp = "### File: main.py\n```python\ndef hello(): return 'repaired_by_b'\n```"
    provider_b = MockProvider(
        name="Mock Implementer B",
        default_response=agent_b_resp,
        capabilities=ProviderCapabilities(
            coding_strength=CapabilityStrength.EXPERT,
            structured_output=True,
        ),
    )
    provider_a = MockProvider(
        name="Mock Reviewer A",
        default_review_status=ReviewStatus.APPROVED,
        default_review_comments="Patch looks great.",
        capabilities=ProviderCapabilities(
            review_strength=CapabilityStrength.HIGH,
        ),
    )

    db = Database(":memory:")
    config = FusionConfig(
        project_root=str(test_repo),
        verification_command="python -c \"import main; assert main.hello() == 'repaired_by_b'\"",
        deliberation=DeliberationConfig(max_repair_rounds=1),
    )
    orch = FusionOrchestrator(
        config=config,
        database=db,
        providers={"agent_a": provider_a, "agent_b": provider_b},
    )

    task = Task(id="test-task", title="Update hello function", description="Implement return value", project_id="test")
    delib, session, verif, diff, review = orch._run_autonomous_edit(
        task=task,
        implementer=provider_b,
        reviewer=provider_a,
    )

    try:
        assert verif.passed is True
        assert review.status == ReviewStatus.APPROVED
        assert review.reviewer_agent == "Mock Reviewer A"
        assert delib.participating_providers == ["Mock Implementer B", "Mock Reviewer A"]
        assert "repaired_by_b" in diff
    finally:
        session.teardown(delete_branch=True)


def test_orchestrator_single_agent_execution(tmp_path):
    """Verify autonomous edit runs with single agent when reviewer is None."""
    test_repo = tmp_path / "repo"
    test_repo.mkdir()
    (test_repo / "calc.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")

    import subprocess
    subprocess.run(["git", "init"], cwd=test_repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "TestUser"], cwd=test_repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.local"], cwd=test_repo, check=True)
    subprocess.run(["git", "add", "."], cwd=test_repo, check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=test_repo, check=True)

    provider_a = MockProvider(
        name="Solo Agent",
        default_response="### File: calc.py\n```python\ndef add(a, b): return a + b + 1\n```",
    )

    db = Database(":memory:")
    config = FusionConfig(
        project_root=str(test_repo),
        verification_command="python -c \"import calc; assert calc.add(1, 1) == 3\"",
        deliberation=DeliberationConfig(max_repair_rounds=1),
    )
    orch = FusionOrchestrator(
        config=config,
        database=db,
        providers={"solo": provider_a},
    )

    task = Task(id="solo-task", title="Tweak add function", description="Add 1", project_id="test")
    delib, session, verif, diff, review = orch._run_autonomous_edit(
        task=task,
        implementer=provider_a,
        reviewer=None,
    )

    try:
        assert verif.passed is True
        assert review.status == ReviewStatus.APPROVED
        assert review.reviewer_agent == "System"
        assert delib.participating_providers == ["Solo Agent"]
    finally:
        session.teardown(delete_branch=True)
