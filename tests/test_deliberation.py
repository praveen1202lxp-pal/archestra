"""Tests for DeliberationEngine collaboration workflows."""

from fusion_agent.config.schema import DeliberationConfig
from fusion_agent.core.deliberation import DeliberationEngine
from fusion_agent.models.deliberation import ReviewStatus
from fusion_agent.models.strategy import StrategyType
from fusion_agent.models.task import Complexity, Task, TaskType
from fusion_agent.providers.mock import MockProvider


def test_deliberation_direct():
    engine = DeliberationEngine()
    agent = MockProvider(name="solo_agent", default_response="Solo execution result.")
    task = Task(id="t1", project_id="p1", title="Quick query", description="")

    result = engine.run(StrategyType.DIRECT, task, primary_agent=agent)
    assert result.strategy_used == StrategyType.DIRECT.value
    assert "Solo execution result." in result.synthesized_output
    assert len(result.proposals) == 1
    assert result.rounds_executed == 1


def test_deliberation_execute_and_review():
    engine = DeliberationEngine(DeliberationConfig(max_rounds=2))
    agent_a = MockProvider(name="coder", default_response="Initial code.")
    agent_b = MockProvider(
        name="reviewer",
        default_review_status=ReviewStatus.NEEDS_REVISION,
        default_review_comments="Add input validation.",
    )
    task = Task(id="t2", project_id="p1", title="Write validator", description="")

    events = []
    result = engine.run(
        StrategyType.EXECUTE_AND_REVIEW,
        task,
        primary_agent=agent_a,
        secondary_agent=agent_b,
        on_event=lambda ev, data: events.append(ev),
    )

    assert result.strategy_used == StrategyType.EXECUTE_AND_REVIEW.value
    assert len(result.reviews) == 1
    assert result.reviews[0].status == ReviewStatus.NEEDS_REVISION
    # Because revision was requested, round 2 repair occurred
    assert result.rounds_executed == 2
    assert "coder" in result.participating_providers
    assert "reviewer" in result.participating_providers
    assert len(events) > 0


def test_deliberation_propose_critique_refine():
    engine = DeliberationEngine()
    agent_a = MockProvider(name="architect", default_response="Proposed architecture spec.")
    agent_b = MockProvider(name="critic", default_response="Identified bottleneck in disk I/O.")
    task = Task(id="t3", project_id="p1", title="Design pipeline", description="", task_type=TaskType.ARCHITECTURE_DESIGN)

    result = engine.run(StrategyType.PROPOSE_CRITIQUE_REFINE, task, primary_agent=agent_a, secondary_agent=agent_b)
    assert result.strategy_used == StrategyType.PROPOSE_CRITIQUE_REFINE.value
    assert len(result.proposals) == 1
    assert len(result.critiques) == 1
    assert result.rounds_executed == 3


def test_deliberation_independent_investigation():
    engine = DeliberationEngine()
    agent_a = MockProvider(name="investigator_1", default_response="Hypothesis 1: Memory leak.")
    agent_b = MockProvider(name="investigator_2", default_response="Hypothesis 2: Unclosed socket handle.")
    task = Task(id="t4", project_id="p1", title="Investigate leak", description="", task_type=TaskType.BUG_INVESTIGATION)

    result = engine.run(StrategyType.INDEPENDENT_INVESTIGATION, task, primary_agent=agent_a, secondary_agent=agent_b)
    assert result.strategy_used == StrategyType.INDEPENDENT_INVESTIGATION.value
    assert len(result.proposals) == 2
    assert "investigator_1" in result.participating_providers
    assert "investigator_2" in result.participating_providers
