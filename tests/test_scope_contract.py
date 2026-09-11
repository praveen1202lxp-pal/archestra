"""Unit tests for Planned Change Scope Contract (Milestone 11 Phase C-DEV)."""

from fusion_agent.models.context import CodeContext, SelectedFile
from fusion_agent.models.plan import PlanStep
from fusion_agent.workspace.scope_contract import ScopeContract, ScopeExpansionCategory


def test_scope_contract_derivation_from_prompt():
    prompt = "Fix string boundary calculation in src/string_utils.py and verify with tests."
    contract = ScopeContract.derive(task_prompt=prompt)
    assert "src/string_utils.py" in contract.expected_files
    # Inferred sister test files
    assert any("test_string_utils.py" in f for f in contract.allowed_related_files)


def test_scope_contract_requested_source_file_edit():
    prompt = "Fix boundary condition in src/auth.py"
    contract = ScopeContract.derive(task_prompt=prompt)

    allowed, reason = contract.validate_file("src/auth.py", is_new_file=False, task_prompt=prompt)
    assert allowed is True
    assert "explicitly in expected change scope" in reason


def test_scope_contract_legitimate_adjacent_implementation_file():
    prompt = "Update user model in src/models.py"
    contract = ScopeContract.derive(task_prompt=prompt)

    # Adjacent file in same directory (src/user.py)
    allowed, reason = contract.validate_file("src/user.py", is_new_file=False, task_prompt=prompt)
    assert allowed is True
    assert "Adjacent source file" in reason


def test_scope_contract_legitimate_new_unit_test():
    prompt = "Fix the validator in src/validator.py"
    contract = ScopeContract.derive(task_prompt=prompt)

    allowed, reason = contract.validate_file("tests/test_validator_edge.py", is_new_file=True, task_prompt=prompt)
    assert allowed is True
    assert "New test file" in reason


def test_scope_contract_unnecessary_docs_creation():
    prompt = "Fix the bug in src/api.py"
    contract = ScopeContract.derive(task_prompt=prompt)

    # Markdown doc not requested
    allowed, reason = contract.validate_file("README.md", is_new_file=True, task_prompt=prompt)
    assert allowed is False
    assert "Opportunistic documentation file" in reason

    allowed_notes, reason_notes = contract.validate_file("docs/release_notes.txt", is_new_file=True, task_prompt=prompt)
    assert allowed_notes is False
    assert "Opportunistic documentation file" in reason_notes


def test_scope_contract_unnecessary_dependency_file_creation():
    prompt = "Implement sorting in src/sort.py"
    contract = ScopeContract.derive(task_prompt=prompt)

    # requirements.txt not requested
    allowed, reason = contract.validate_file("requirements.txt", is_new_file=False, task_prompt=prompt)
    assert allowed is False
    assert "Opportunistic modification of dependency file" in reason

    allowed_pyproj, reason_pyproj = contract.validate_file("pyproject.toml", is_new_file=False, task_prompt=prompt)
    assert allowed_pyproj is False
    assert "Opportunistic modification of dependency file" in reason_pyproj


def test_scope_contract_multi_file_planned_feature():
    prompt = "Implement WorkerPool in src/worker_pool.py and integrate into Queue in src/queue.py"
    step1 = PlanStep(id="step_1", objective="Implement WorkerPool", expected_files=["src/worker_pool.py"])
    step2 = PlanStep(id="step_2", objective="Integrate into Queue", expected_files=["src/queue.py"])

    contract = ScopeContract.derive(task_prompt=prompt, plan_steps=[step1, step2])
    assert "src/worker_pool.py" in contract.expected_files
    assert "src/queue.py" in contract.expected_files

    allowed1, _ = contract.validate_file("src/worker_pool.py", is_new_file=True, task_prompt=prompt)
    allowed2, _ = contract.validate_file("src/queue.py", is_new_file=False, task_prompt=prompt)
    assert allowed1 is True
    assert allowed2 is True


def test_scope_contract_rejects_unrequested_modification_of_existing_test():
    prompt = "Fix token expiration logic in src/token_manager.py where tokens are prematurely invalidated."
    contract = ScopeContract.derive(task_prompt=prompt)

    # Even though tests/test_token_manager.py is in allowed_related_files due to pairing,
    # modifying an EXISTING test file without authorization must be rejected.
    allowed, reason = contract.validate_file("tests/test_token_manager.py", is_new_file=False, task_prompt=prompt)
    assert allowed is False
    assert "Opportunistic modification of existing test file" in reason


def test_scope_contract_allows_modification_of_existing_test_when_requested():
    prompt = "Fix the failing test in tests/test_token_manager.py for token expiration."
    contract = ScopeContract.derive(task_prompt=prompt)

    allowed, reason = contract.validate_file("tests/test_token_manager.py", is_new_file=False, task_prompt=prompt)
    assert allowed is True


def test_scope_contract_allows_new_test_file_creation():
    prompt = "Fix token expiration logic in src/token_manager.py"
    contract = ScopeContract.derive(task_prompt=prompt)

    # Creating a NEW test file is permitted when new_test_files_allowed is True
    allowed, reason = contract.validate_file("tests/test_new_behavior.py", is_new_file=True, task_prompt=prompt)
    assert allowed is True
    assert "New test file" in reason


# === New Generic Tests for Audited Scope Expansion ===

def test_scope_contract_expansion_unnecessary_docs_denied():
    """Unnecessary documentation creation requested via expansion is denied when docs were not requested."""
    prompt = "Fix the bug in src/api.py"
    contract = ScopeContract.derive(task_prompt=prompt)

    allowed, reason = contract.request_scope_expansion(
        rel_path="docs/architecture_notes.md",
        reason="Adding architectural overview notes for the API",
        is_new_file=True,
        task_prompt=prompt,
    )
    assert allowed is False
    assert "documentation is not a requested deliverable" in reason
    assert "docs/architecture_notes.md" not in contract.scope_expansion_reasons


def test_scope_contract_expansion_unnecessary_dependency_denied():
    """Unnecessary or cosmetic dependency modification requested via expansion is denied."""
    prompt = "Refactor logic in src/calculator.py"
    contract = ScopeContract.derive(task_prompt=prompt)

    allowed, reason = contract.request_scope_expansion(
        rel_path="requirements.txt",
        reason="Format requirements and bump minor version numbers",
        is_new_file=False,
        task_prompt=prompt,
    )
    assert allowed is False
    assert "unverified or opportunistic dependency modification" in reason
    assert "requirements.txt" not in contract.scope_expansion_reasons


def test_scope_contract_expansion_unnecessary_existing_test_modification_denied():
    """Modifying existing tests without API/interface change necessity is denied."""
    prompt = "Fix bug in src/service.py"
    contract = ScopeContract.derive(task_prompt=prompt)

    allowed, reason = contract.request_scope_expansion(
        rel_path="tests/test_service.py",
        reason="Update test assertion values to match broken implementation",
        is_new_file=False,
        task_prompt=prompt,
    )
    assert allowed is False
    assert "requires verified API/interface change necessity" in reason
    assert "tests/test_service.py" not in contract.scope_expansion_reasons


def test_scope_contract_expansion_existing_test_update_for_api_change_approved():
    """Existing test update genuinely required by an API/interface change is approved via audited expansion."""
    prompt = "Update client interface in src/service.py"
    contract = ScopeContract.derive(task_prompt=prompt)

    allowed, reason = contract.request_scope_expansion(
        rel_path="tests/test_service.py",
        reason="API signature changed: ServiceClient constructor now requires mandatory client_id parameter",
        is_new_file=False,
        task_prompt=prompt,
    )
    assert allowed is True
    assert "verified API/interface change necessitates updating" in reason
    assert "tests/test_service.py" in contract.scope_expansion_reasons
    assert "tests/test_service.py" in contract.expected_files

    # Subsequent validation also confirms the file is authorized
    v_allowed, _ = contract.validate_file("tests/test_service.py", is_new_file=False, task_prompt=prompt)
    assert v_allowed is True


def test_scope_contract_expansion_dependency_genuinely_required_approved():
    """Dependency/config file genuinely required for the requested implementation is approved via audited expansion."""
    prompt = "Implement token encryption in src/auth.py"
    contract = ScopeContract.derive(task_prompt=prompt)

    allowed, reason = contract.request_scope_expansion(
        rel_path="requirements.txt",
        reason="Required new dependency 'cryptography' needed for AES token encryption",
        is_new_file=False,
        task_prompt=prompt,
    )
    assert allowed is True
    assert "verified implementation necessity" in reason
    assert "requirements.txt" in contract.scope_expansion_reasons
    assert "requirements.txt" in contract.expected_files

    # Subsequent validation confirms authorization
    v_allowed, _ = contract.validate_file("requirements.txt", is_new_file=False, task_prompt=prompt)
    assert v_allowed is True


def test_scope_contract_expansion_migration_component_for_multi_file_feature_approved():
    """Migration or new implementation component legitimately required by a multi-file feature is approved."""
    prompt = "Add database tenant isolation feature across src/repo.py"
    contract = ScopeContract.derive(task_prompt=prompt)

    allowed, reason = contract.request_scope_expansion(
        rel_path="migrations/0003_add_tenants_table.py",
        reason="Database migration script legitimately required for tenant schema definition in multi-file feature",
        is_new_file=True,
        task_prompt=prompt,
    )
    assert allowed is True
    assert "legitimate feature component" in reason
    assert "migrations/0003_add_tenants_table.py" in contract.scope_expansion_reasons
    assert "migrations/0003_add_tenants_table.py" in contract.expected_files

    # Subsequent validation confirms authorization
    v_allowed, _ = contract.validate_file("migrations/0003_add_tenants_table.py", is_new_file=True, task_prompt=prompt)
    assert v_allowed is True
