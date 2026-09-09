"""Comprehensive regression tests for CodeContext quality and efficiency."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fusion_agent.models.context import (
    CodeContext,
    ContextBudgetConfig,
    ContextSliceType,
    OmissionManifest,
    SelectedFile,
    SymbolReference,
)
from fusion_agent.repository.indexer import RepositoryIndex, RepositoryIndexer, SymbolDefinition
from fusion_agent.repository.selector import MatchConfidence, RelevantFileSelector
from fusion_agent.repository.budgeter import ContextBudgeter
from fusion_agent.providers.codex_cli import CodexCLIProvider


def test_benchmark_prompt_zero_state_context(tmp_path):
    """Verify that absent target files in absent directory yield minimal zero-state context without engine pollution."""
    indexer = RepositoryIndexer()
    index = indexer.index_project(".")

    # Simulate target files being absent from index (e.g. at the start of a new-file task)
    index.file_tree.pop("fusion_agent/utils/math_utils.py", None)
    index.file_tree.pop("tests/test_math_utils.py", None)

    benchmark_prompt = (
        "Implement a robust clamp function in fusion_agent/utils/math_utils.py that clamps a number x "
        "between min_val and max_val. The function must raise ValueError if min_val > max_val, and raise "
        "TypeError if inputs are not numeric int or float. Crucial edge case: In Python, bool is a subclass "
        "of int (isinstance(True, int) is True). The function must explicitly reject booleans with TypeError. "
        "Write comprehensive unit tests in tests/test_math_utils.py."
    )

    selector = RelevantFileSelector(index)
    ranked, symbol_refs = selector.select_relevant_files(benchmark_prompt)

    budgeter = ContextBudgeter()
    ctx = budgeter.build_code_context(
        task_requirements=benchmark_prompt,
        ranked_candidates=ranked,
        symbol_refs=symbol_refs,
        index=index,
    )

    selected_paths = [sf.path for sf in ctx.selected_files]

    # Must NOT select unrelated core orchestrator / deliberation / config engine files
    assert "fusion_agent/core/orchestrator.py" not in selected_paths
    assert "fusion_agent/config/schema.py" not in selected_paths
    assert "fusion_agent/providers/mock.py" not in selected_paths
    assert "fusion_agent/models/deliberation.py" not in selected_paths
    assert "fusion_agent/models/task.py" not in selected_paths

    # Context should be zero or strictly minimal
    assert len(selected_paths) == 0


def test_explicit_existing_path_selection():
    """Explicit existing file path in prompt is matched with EXPLICIT_PATH confidence."""
    indexer = RepositoryIndexer()
    index = indexer.index_project(".")

    prompt = "Review and refactor fusion_agent/workspace/broker.py to improve error handling"
    selector = RelevantFileSelector(index)
    ranked, _ = selector.select_relevant_files(prompt)

    matched = next((c for c in ranked if c.rel_path == "fusion_agent/workspace/broker.py"), None)
    assert matched is not None
    assert matched.confidence == MatchConfidence.EXPLICIT_PATH
    assert matched.score >= 100.0


def test_exact_symbol_selection_and_symbol_slice():
    """Exact symbol match is selected with EXACT_SYMBOL confidence and rendered as SYMBOL_SLICE."""
    indexer = RepositoryIndexer()
    index = indexer.index_project(".")

    prompt = "Update ExecutionBroker to support custom process timeouts"
    selector = RelevantFileSelector(index)
    ranked, symbol_refs = selector.select_relevant_files(prompt)

    broker_candidate = next((c for c in ranked if "broker.py" in c.rel_path), None)
    assert broker_candidate is not None
    assert broker_candidate.confidence == MatchConfidence.EXACT_SYMBOL

    budgeter = ContextBudgeter()
    ctx = budgeter.build_code_context(
        task_requirements=prompt,
        ranked_candidates=ranked,
        symbol_refs=symbol_refs,
        index=index,
    )

    broker_file = next((sf for sf in ctx.selected_files if "broker.py" in sf.path), None)
    assert broker_file is not None
    assert broker_file.slice_type == ContextSliceType.SYMBOL_SLICE
    assert "SYMBOL_SLICE" in broker_file.content
    assert "ExecutionBroker" in broker_file.content


def test_sibling_convention_discovery_in_existing_directory():
    """When a new file is requested in an existing directory, sibling files are discovered as SIBLING_CONVENTION."""
    indexer = RepositoryIndexer()
    index = indexer.index_project(".")

    # fusion_agent/workspace exists, but new_helper.py does not
    prompt = "Create a helper class in fusion_agent/workspace/new_helper.py"
    selector = RelevantFileSelector(index)
    ranked, symbol_refs = selector.select_relevant_files(prompt)

    sibling_paths = [c.rel_path for c in ranked if c.confidence == MatchConfidence.SIBLING_CONVENTION]
    # Sibling files in fusion_agent/workspace (e.g. broker.py, editor.py, session.py)
    assert any("fusion_agent/workspace/" in p for p in sibling_paths)


def test_no_graph_expansion_from_weak_lexical_matches():
    """Weak lexical matches must not trigger recursive graph expansion or test associations."""
    index = RepositoryIndex(root_path=".")
    index.file_tree = {
        "src/unrelated.py": MagicMock(language="python", is_test=False),
        "src/core_engine.py": MagicMock(language="python", is_test=False),
        "tests/test_unrelated.py": MagicMock(language="python", is_test=True),
    }
    index.dependency_graph = {
        "src/unrelated.py": {"src/core_engine.py"},
    }
    index.test_associations = {
        "src/unrelated.py": {"tests/test_unrelated.py"},
    }

    # Prompt only contains a weak keyword match in unrelated.py
    selector = RelevantFileSelector(index)
    with patch.object(index, "search_keywords", return_value=[("src/unrelated.py", 10, "keyword_match")]):
        ranked, _ = selector.select_relevant_files("Do some work with keyword_match in nonexistent/target.py")

    ranked_paths = [c.rel_path for c in ranked]
    # Dependency src/core_engine.py and test tests/test_unrelated.py must NOT be expanded
    assert "src/core_engine.py" not in ranked_paths
    assert "tests/test_unrelated.py" not in ranked_paths


def test_compact_omission_manifest_formatting():
    """Omission manifest renders compact summary for low-confidence files rather than listing each one."""
    omissions = OmissionManifest()
    # 50 low-confidence files omitted
    omissions.omitted_low_confidence_count = 50
    # 1 explicit target file omitted due to policy
    omissions.omitted_files.append({
        "path": "secret_config.json",
        "reason": "Excluded by secret policy",
        "high_priority": True,
    })

    ctx = CodeContext(
        task_requirements="Test task",
        omissions=omissions,
    )
    rendered = ctx.to_prompt_context(include_task_requirements=True)

    assert "Omitted target file `secret_config.json`: Excluded by secret policy" in rendered
    assert "Omitted 50 lower-confidence repository candidates because they exceeded relevance/context thresholds." in rendered
    # Should not have 50 separate bullet points
    assert rendered.count("- Omitted") == 2


def test_prompt_deduplication_in_provider_adapter():
    """Verifies that task requirements appear exactly once in the final provider prompt."""
    task_desc = "Implement a robust clamp function with type checks and boolean rejection."
    ctx = CodeContext(task_requirements=task_desc)

    provider = CodexCLIProvider(name="test_codex")

    captured_prompt = None
    def mock_exec(prompt, cwd=None):
        nonlocal captured_prompt
        captured_prompt = prompt
        return MagicMock(stdout="### File: a.py\n```python\n# code\n```", stderr="", exit_code=0, duration_seconds=0.1, thread_id=None, response_text="", usage=None)

    with patch.object(provider, "_execute_cli", side_effect=mock_exec):
        provider.invoke(prompt=f"Task: {task_desc}\n\nRESPONSE CONTRACT:\nFormat code...", context=ctx)

    assert captured_prompt is not None
    # The task text should appear exactly ONCE in captured_prompt
    count = captured_prompt.count(task_desc)
    assert count == 1, f"Expected task description to appear exactly once, but appeared {count} times:\n{captured_prompt}"
