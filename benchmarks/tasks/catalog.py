"""Representative Benchmark Task Catalog (12 Tasks) covering diverse engineering workflows."""

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from benchmarks.schema import BenchmarkCategory, BenchmarkTask


BENCHMARK_TASKS: List[BenchmarkTask] = [
    # 1. Simple single-file bug fix
    BenchmarkTask(
        task_id="TASK-01",
        category=BenchmarkCategory.SINGLE_FILE_BUG_FIX,
        title="Fix pagination boundary in src/pagination.py",
        prompt="Fix the pagination boundary calculation in src/pagination.py so that paginate(items, page, page_size) correctly slices pages and handles empty lists.",
        visible_preexisting_tests=["test_standard_pagination"],
        visible_reproduction_tests=[],
        visible_test_command="pytest tests/test_pagination.py",
        hidden_evaluator_module="eval_task_01.py",
        full_regression_command="pytest tests/test_pagination.py",
        required_paths=["src/pagination.py"],
        allowed_paths=["src/pagination.py"],
        forbidden_paths=["src/critical_auth.py", "config.json"],
        registered_defect_criteria=["criterion_last_page_boundary", "criterion_empty_list"],
        timeout_seconds=120.0,
    ),
    # 2. Failing unit test repair
    BenchmarkTask(
        task_id="TASK-02",
        category=BenchmarkCategory.FAILING_TEST_REPAIR,
        title="Repair NoneType handling in src/serializer.py",
        prompt="Fix failing tests in tests/test_serializer.py where serialize_user crashes with AttributeError when optional fields (email, bio) are None.",
        visible_preexisting_tests=["test_valid_user"],
        visible_reproduction_tests=["test_null_fields_repro"],
        visible_test_command="pytest tests/test_serializer.py",
        hidden_evaluator_module="eval_task_02.py",
        full_regression_command="pytest tests/test_serializer.py -k test_valid_user",
        required_paths=["src/serializer.py"],
        allowed_paths=["src/serializer.py"],
        forbidden_paths=["src/database.py"],
        timeout_seconds=120.0,
    ),
    # 3. Small feature addition
    BenchmarkTask(
        task_id="TASK-03",
        category=BenchmarkCategory.SMALL_FEATURE_ADDITION,
        title="Add timeout parameter with validation to ClientConfig in src/client.py",
        prompt="Add a timeout_seconds parameter (float, default 30.0) to ClientConfig in src/client.py. It must validate timeout_seconds > 0, raising ValueError otherwise.",
        visible_preexisting_tests=["test_client_defaults"],
        visible_reproduction_tests=[],
        visible_test_command="pytest tests/test_client.py",
        hidden_evaluator_module="eval_task_03.py",
        full_regression_command="pytest tests/test_client.py -k test_client_defaults",
        required_paths=["src/client.py"],
        allowed_paths=["src/client.py"],
        forbidden_paths=["tests/test_client.py"],
        timeout_seconds=120.0,
    ),
    # 4. Multi-file feature
    BenchmarkTask(
        task_id="TASK-04",
        category=BenchmarkCategory.MULTI_FILE_FEATURE,
        title="Implement LRUCache and integrate into Store across multiple files",
        prompt="Implement LRUCache in src/lru_cache.py conforming to CacheInterface in src/cache_interface.py, and integrate it into Store in src/store.py with max_size eviction.",
        visible_preexisting_tests=["test_store_basic"],
        visible_reproduction_tests=[],
        visible_test_command="pytest tests/test_cache.py",
        hidden_evaluator_module="eval_task_04.py",
        full_regression_command="pytest tests/test_cache.py -k test_store_basic",
        required_paths=["src/lru_cache.py", "src/store.py"],
        allowed_paths=["src/lru_cache.py", "src/store.py"],
        forbidden_paths=["src/cache_interface.py"],
        timeout_seconds=180.0,
    ),
    # 5. API/interface change
    BenchmarkTask(
        task_id="TASK-05",
        category=BenchmarkCategory.API_INTERFACE_CHANGE,
        title="Migrate calculate_fee signature to FeeOptions dataclass with backward compatibility",
        prompt="Update calculate_fee in src/billing.py to accept an optional FeeOptions dataclass, while maintaining backward compatibility for legacy positional arguments.",
        visible_preexisting_tests=["test_legacy_positional_fee"],
        visible_reproduction_tests=[],
        visible_test_command="pytest tests/test_billing.py",
        hidden_evaluator_module="eval_task_05.py",
        full_regression_command="pytest tests/test_billing.py -k test_legacy_positional_fee",
        required_paths=["src/billing.py"],
        allowed_paths=["src/billing.py"],
        forbidden_paths=["src/payment_gateway.py"],
        timeout_seconds=150.0,
    ),
    # 6. Refactor
    BenchmarkTask(
        task_id="TASK-06",
        category=BenchmarkCategory.REFACTOR,
        title="Extract identifier sanitization into src/shared_utils.py without breaking services",
        prompt="Extract duplicated identifier sanitization logic from src/user_service.py and src/item_service.py into src/shared_utils.py without changing external behavior.",
        visible_preexisting_tests=["test_services_basic"],
        visible_reproduction_tests=[],
        visible_test_command="pytest tests/test_services.py",
        hidden_evaluator_module="eval_task_06.py",
        full_regression_command="pytest tests/test_services.py",
        required_paths=["src/shared_utils.py", "src/user_service.py", "src/item_service.py"],
        allowed_paths=["src/shared_utils.py", "src/user_service.py", "src/item_service.py"],
        forbidden_paths=["tests/test_services.py"],
        timeout_seconds=150.0,
    ),
    # 7. Edge-case bug
    BenchmarkTask(
        task_id="TASK-07",
        category=BenchmarkCategory.EDGE_CASE_BUG,
        title="Fix dictionary changed size during iteration in SessionManager",
        prompt="Fix the RuntimeError in SessionManager.cleanup_expired in src/session_manager.py caused by mutating the active sessions dict during iteration.",
        visible_preexisting_tests=["test_session_lifecycle"],
        visible_reproduction_tests=[],
        visible_test_command="pytest tests/test_session_manager.py",
        hidden_evaluator_module="eval_task_07.py",
        full_regression_command="pytest tests/test_session_manager.py -k test_session_lifecycle",
        required_paths=["src/session_manager.py"],
        allowed_paths=["src/session_manager.py"],
        forbidden_paths=["src/auth.py"],
        registered_defect_criteria=["criterion_dict_size_change_during_iteration"],
        timeout_seconds=120.0,
    ),
    # 8. Test-generation task
    BenchmarkTask(
        task_id="TASK-08",
        category=BenchmarkCategory.TEST_GENERATION,
        title="Generate unit test suite for TokenBucket rate limiter",
        prompt="Write unit tests in tests/test_rate_limiter.py covering TokenBucket in src/rate_limiter.py (token consumption, replenishment, burst, and exhaustion rejection).",
        visible_preexisting_tests=[],
        visible_reproduction_tests=[],
        visible_test_command="pytest tests/test_rate_limiter.py",
        hidden_evaluator_module="eval_task_08.py",
        full_regression_command=None,
        required_paths=["tests/test_rate_limiter.py"],
        allowed_paths=["tests/test_rate_limiter.py"],
        forbidden_paths=["src/rate_limiter.py"],
        timeout_seconds=150.0,
    ),
    # 9. Architecture/design task
    BenchmarkTask(
        task_id="TASK-09",
        category=BenchmarkCategory.ARCHITECTURE_DESIGN,
        title="Refactor notification dispatcher to registered Strategy pattern",
        prompt="Refactor NotificationDispatcher in src/notifier.py to use a Strategy pattern where handlers register for notification channels ('email', 'sms', 'webhook').",
        visible_preexisting_tests=["test_legacy_dispatch"],
        visible_reproduction_tests=[],
        visible_test_command="pytest tests/test_notifier.py",
        hidden_evaluator_module="eval_task_09.py",
        full_regression_command="pytest tests/test_notifier.py -k test_legacy_dispatch",
        required_paths=["src/notifier.py"],
        allowed_paths=["src/notifier.py"],
        forbidden_paths=["tests/test_notifier.py"],
        timeout_seconds=180.0,
    ),
    # 10. Multi-step dependent task
    BenchmarkTask(
        task_id="TASK-10",
        category=BenchmarkCategory.MULTI_STEP_DEPENDENT,
        title="Add archived_at migration, model update, and repository filtering",
        prompt="Add 'archived_at' timestamp: 1) add migration SQL in src/db_schema.py, 2) add field to Record in src/models.py, 3) update list_active in src/repo.py.",
        visible_preexisting_tests=["test_standard_record_crud"],
        visible_reproduction_tests=[],
        visible_test_command="pytest tests/test_repo.py",
        hidden_evaluator_module="eval_task_10.py",
        full_regression_command="pytest tests/test_repo.py -k test_standard_record_crud",
        required_paths=["src/db_schema.py", "src/models.py", "src/repo.py"],
        allowed_paths=["src/db_schema.py", "src/models.py", "src/repo.py"],
        forbidden_paths=["src/unrelated_audit.py"],
        timeout_seconds=200.0,
    ),
    # 11. Resource lifecycle management (Neutralized)
    BenchmarkTask(
        task_id="TASK-11",
        category=BenchmarkCategory.RESOURCE_LIFECYCLE,
        title="Implement network retry with backoff and connection lifecycle cleanup",
        prompt="Implement retry_request in src/network_client.py with exponential backoff on ConnectionError, ensuring all failed socket connections are explicitly closed.",
        visible_preexisting_tests=["test_single_request"],
        visible_reproduction_tests=[],
        visible_test_command="pytest tests/test_network.py",
        hidden_evaluator_module="eval_task_11.py",
        full_regression_command="pytest tests/test_network.py -k test_single_request",
        required_paths=["src/network_client.py"],
        allowed_paths=["src/network_client.py"],
        forbidden_paths=["src/tls.py"],
        registered_defect_criteria=["criterion_socket_resource_leak_on_failure"],
        timeout_seconds=150.0,
    ),
    # 12. Issue-driven MCP task (Frozen fixture)
    BenchmarkTask(
        task_id="TASK-12",
        category=BenchmarkCategory.MCP_ISSUE_DRIVEN,
        title="Resolve markdown header parser issue described in GitHub Issue #42",
        prompt=(
            "Resolve the defect described in Issue #42:\n"
            "Issue Title: Markdown parser retains trailing '#' and whitespace in headers\n"
            "Issue Description: When parsing '### Section Header ###', parse_header in src/markdown_parser.py should "
            "return 'Section Header' without trailing '#'. Currently returns 'Section Header ###'. "
            "Fix parse_header to strip trailing '#' characters and surrounding whitespace."
        ),
        visible_preexisting_tests=["test_standard_headers"],
        visible_reproduction_tests=[],
        visible_test_command="pytest tests/test_markdown.py",
        hidden_evaluator_module="eval_task_12.py",
        full_regression_command="pytest tests/test_markdown.py -k test_standard_headers",
        required_paths=["src/markdown_parser.py"],
        allowed_paths=["src/markdown_parser.py"],
        forbidden_paths=["src/html_sanitizer.py"],
        registered_defect_criteria=["criterion_trailing_hashes_stripped"],
        timeout_seconds=150.0,
        mcp_context={
            "server_id": "github-readonly",
            "tool_name": "github.get_issue",
            "arguments": {"issue_number": 42},
            "frozen_fixture": {
                "number": 42,
                "title": "Markdown parser retains trailing '#' and whitespace in headers",
                "body": "When parsing '### Section Header ###', parse_header should return 'Section Header' without trailing '#'. Currently returns 'Section Header ###'.",
                "state": "open",
            },
        },
    ),
]


def get_task_by_id(task_id: str) -> Optional[BenchmarkTask]:
    """Look up a task definition by task_id."""
    for t in BENCHMARK_TASKS:
        if t.task_id == task_id:
            return t
    return None


def compute_task_definition_hash(task: BenchmarkTask) -> str:
    """Compute deterministic full SHA-256 hash of task definition."""
    dumped = json.dumps(task.to_dict(), sort_keys=True)
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()


def compute_hidden_evaluator_hash(task: BenchmarkTask) -> str:
    """Compute full SHA-256 hash of the decisive hidden evaluator module."""
    eval_dir = Path(__file__).parent.parent / "hidden_evaluators"
    target = eval_dir / task.hidden_evaluator_module
    if target.exists():
        content = target.read_bytes()
        return hashlib.sha256(content).hexdigest()
    return "0" * 64


def compute_benchmark_suite_hash() -> str:
    """Compute overall suite hash binding task definitions and hidden evaluators."""
    all_elements = []
    for t in BENCHMARK_TASKS:
        t_hash = compute_task_definition_hash(t)
        h_hash = compute_hidden_evaluator_hash(t)
        all_elements.append(f"{t.task_id}:{t_hash}:{h_hash}")
    combined = ":".join(sorted(all_elements))
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def setup_task_fixtures(task_id: str, target_dir: Path) -> None:
    """Populate ONLY SUT-visible baseline source and visible test files into target_dir.
    
    Hidden acceptance tests are NEVER placed into target_dir.
    """
    src_dir = target_dir / "src"
    tests_dir = target_dir / "tests"
    src_dir.mkdir(parents=True, exist_ok=True)
    tests_dir.mkdir(parents=True, exist_ok=True)

    if task_id == "TASK-01":
        (src_dir / "pagination.py").write_text(
            'def paginate(items, page, page_size):\n'
            '    """Returns items for given 1-based page number."""\n'
            '    start = (page - 1) * page_size\n'
            '    end = start + page_size - 1  # BUG: off-by-one\n'
            '    return items[start:end]\n',
            encoding="utf-8",
        )
        (tests_dir / "test_pagination.py").write_text(
            'from src.pagination import paginate\n\n'
            'def test_standard_pagination():\n'
            '    items = list(range(20))\n'
            '    res = paginate(items, 1, 5)\n'
            '    assert len(res) in (4, 5)\n',
            encoding="utf-8",
        )

    elif task_id == "TASK-02":
        (src_dir / "serializer.py").write_text(
            'def serialize_user(data):\n'
            '    return {\n'
            '        "username": data["username"].strip().lower(),\n'
            '        "email": data["email"].strip().lower(),\n'
            '        "bio": data["bio"].strip(),\n'
            '    }\n',
            encoding="utf-8",
        )
        (tests_dir / "test_serializer.py").write_text(
            'import pytest\n'
            'from src.serializer import serialize_user\n\n'
            'def test_valid_user():\n'
            '    res = serialize_user({"username": " Alice ", "email": " Alice@Ex.com ", "bio": " Dev "})\n'
            '    assert res["username"] == "alice"\n'
            '    assert res["email"] == "alice@ex.com"\n\n'
            'def test_null_fields_repro():\n'
            '    # Visible reproduction test\n'
            '    with pytest.raises(AttributeError):\n'
            '        serialize_user({"username": "Bob", "email": None, "bio": None})\n',
            encoding="utf-8",
        )

    elif task_id == "TASK-03":
        (src_dir / "client.py").write_text(
            'class ClientConfig:\n'
            '    def __init__(self, base_url: str, retries: int = 3):\n'
            '        self.base_url = base_url\n'
            '        self.retries = retries\n',
            encoding="utf-8",
        )
        (tests_dir / "test_client.py").write_text(
            'from src.client import ClientConfig\n\n'
            'def test_client_defaults():\n'
            '    cfg = ClientConfig("https://api.example.com")\n'
            '    assert cfg.base_url == "https://api.example.com"\n'
            '    assert cfg.retries == 3\n',
            encoding="utf-8",
        )

    elif task_id == "TASK-04":
        (src_dir / "cache_interface.py").write_text(
            'class CacheInterface:\n'
            '    def get(self, key: str):\n'
            '        raise NotImplementedError\n'
            '    def set(self, key: str, value):\n'
            '        raise NotImplementedError\n',
            encoding="utf-8",
        )
        (src_dir / "lru_cache.py").write_text(
            'from src.cache_interface import CacheInterface\n\n'
            'class LRUCache(CacheInterface):\n'
            '    def __init__(self, max_size: int = 2):\n'
            '        self.max_size = max_size\n'
            '        self.data = {}\n'
            '    def get(self, key: str):\n'
            '        return self.data.get(key)\n'
            '    def set(self, key: str, value):\n'
            '        self.data[key] = value\n',
            encoding="utf-8",
        )
        (src_dir / "store.py").write_text(
            'from src.lru_cache import LRUCache\n\n'
            'class Store:\n'
            '    def __init__(self, max_size: int = 2):\n'
            '        self.cache = LRUCache(max_size=max_size)\n'
            '    def save(self, key: str, val):\n'
            '        self.cache.set(key, val)\n'
            '    def load(self, key: str):\n'
            '        return self.cache.get(key)\n',
            encoding="utf-8",
        )
        (tests_dir / "test_cache.py").write_text(
            'from src.store import Store\n\n'
            'def test_store_basic():\n'
            '    s = Store()\n'
            '    s.save("a", 1)\n'
            '    assert s.load("a") == 1\n',
            encoding="utf-8",
        )

    elif task_id == "TASK-05":
        (src_dir / "billing.py").write_text(
            'from dataclasses import dataclass\n\n'
            '@dataclass\n'
            'class FeeOptions:\n'
            '    rate: float = 0.1\n'
            '    discount: float = 0.0\n\n'
            'def calculate_fee(amount: float, rate: float = 0.1, discount: float = 0.0) -> float:\n'
            '    return (amount * rate) - discount\n',
            encoding="utf-8",
        )
        (tests_dir / "test_billing.py").write_text(
            'from src.billing import calculate_fee\n\n'
            'def test_legacy_positional_fee():\n'
            '    assert calculate_fee(100.0, 0.1, 5.0) == 5.0\n',
            encoding="utf-8",
        )

    elif task_id == "TASK-06":
        (src_dir / "shared_utils.py").write_text(
            '# Extracted utilities module\n',
            encoding="utf-8",
        )
        (src_dir / "user_service.py").write_text(
            'import re\n'
            'def normalize_user_id(val: str) -> str:\n'
            '    return re.sub(r"[^a-zA-Z0-9_-]", "", val).lower().strip()\n'
            'def register_user(name: str):\n'
            '    return {"user_id": normalize_user_id(name)}\n',
            encoding="utf-8",
        )
        (src_dir / "item_service.py").write_text(
            'import re\n'
            'def normalize_item_id(val: str) -> str:\n'
            '    return re.sub(r"[^a-zA-Z0-9_-]", "", val).lower().strip()\n'
            'def register_item(sku: str):\n'
            '    return {"sku": normalize_item_id(sku)}\n',
            encoding="utf-8",
        )
        (tests_dir / "test_services.py").write_text(
            'from src.user_service import register_user\n'
            'from src.item_service import register_item\n\n'
            'def test_services_basic():\n'
            '    assert register_user("User-1")["user_id"] == "user-1"\n'
            '    assert register_item("Item-2")["sku"] == "item-2"\n',
            encoding="utf-8",
        )

    elif task_id == "TASK-07":
        (src_dir / "session_manager.py").write_text(
            'import time\n'
            'class SessionManager:\n'
            '    def __init__(self):\n'
            '        self.sessions = {}\n'
            '    def add_session(self, sid: str, ttl: float):\n'
            '        self.sessions[sid] = time.time() + ttl\n'
            '    def cleanup_expired(self):\n'
            '        now = time.time()\n'
            '        for sid, exp in self.sessions.items():\n'
            '            if exp < now:\n'
            '                del self.sessions[sid]\n',
            encoding="utf-8",
        )
        (tests_dir / "test_session_manager.py").write_text(
            'from src.session_manager import SessionManager\n\n'
            'def test_session_lifecycle():\n'
            '    sm = SessionManager()\n'
            '    sm.add_session("s1", 100)\n'
            '    assert "s1" in sm.sessions\n',
            encoding="utf-8",
        )

    elif task_id == "TASK-08":
        (src_dir / "rate_limiter.py").write_text(
            'import time\n'
            'class TokenBucket:\n'
            '    def __init__(self, capacity: int, refill_rate: float):\n'
            '        self.capacity = capacity\n'
            '        self.tokens = float(capacity)\n'
            '        self.refill_rate = refill_rate\n'
            '        self.last_update = time.time()\n'
            '    def consume(self, tokens: int = 1) -> bool:\n'
            '        now = time.time()\n'
            '        delta = now - self.last_update\n'
            '        self.tokens = min(float(self.capacity), self.tokens + delta * self.refill_rate)\n'
            '        self.last_update = now\n'
            '        if self.tokens >= tokens:\n'
            '            self.tokens -= tokens\n'
            '            return True\n'
            '        return False\n',
            encoding="utf-8",
        )
        (tests_dir / "test_rate_limiter.py").write_text(
            '# Visible placeholder file for agent-generated unit tests\n',
            encoding="utf-8",
        )

    elif task_id == "TASK-09":
        (src_dir / "notifier.py").write_text(
            'class NotificationDispatcher:\n'
            '    def dispatch(self, channel: str, message: str) -> str:\n'
            '        if channel == "email":\n'
            '            return f"EMAIL: {message}"\n'
            '        elif channel == "sms":\n'
            '            return f"SMS: {message}"\n'
            '        elif channel == "webhook":\n'
            '            return f"WEBHOOK: {message}"\n'
            '        raise ValueError(f"Unknown channel: {channel}")\n',
            encoding="utf-8",
        )
        (tests_dir / "test_notifier.py").write_text(
            'from src.notifier import NotificationDispatcher\n\n'
            'def test_legacy_dispatch():\n'
            '    d = NotificationDispatcher()\n'
            '    assert d.dispatch("email", "hi") == "EMAIL: hi"\n',
            encoding="utf-8",
        )

    elif task_id == "TASK-10":
        (src_dir / "db_schema.py").write_text(
            'SCHEMA_MIGRATIONS = [\n'
            '    "CREATE TABLE records (id TEXT PRIMARY KEY, title TEXT);",\n'
            ']\n',
            encoding="utf-8",
        )
        (src_dir / "models.py").write_text(
            'from dataclasses import dataclass\n'
            'from typing import Optional\n\n'
            '@dataclass\n'
            'class Record:\n'
            '    id: str\n'
            '    title: str\n',
            encoding="utf-8",
        )
        (src_dir / "repo.py").write_text(
            'from src.models import Record\n\n'
            'class RecordRepository:\n'
            '    def __init__(self):\n'
            '        self.items = []\n'
            '    def add(self, r: Record):\n'
            '        self.items.append(r)\n'
            '    def list_active(self):\n'
            '        return self.items\n',
            encoding="utf-8",
        )
        (tests_dir / "test_repo.py").write_text(
            'from src.models import Record\n'
            'from src.repo import RecordRepository\n\n'
            'def test_standard_record_crud():\n'
            '    repo = RecordRepository()\n'
            '    repo.add(Record(id="1", title="First"))\n'
            '    assert len(repo.list_active()) == 1\n',
            encoding="utf-8",
        )

    elif task_id == "TASK-11":
        (src_dir / "network_client.py").write_text(
            'class NetworkClient:\n'
            '    def __init__(self, failures_before_success: int = 0):\n'
            '        self.open_sockets = []\n'
            '        self.failures_remaining = failures_before_success\n\n'
            '    def close_socket(self, sock: dict):\n'
            '        sock["closed"] = True\n\n'
            '    def simulate_connection(self, fail_count: int = None):\n'
            '        sock = {"fd": len(self.open_sockets) + 1, "closed": False}\n'
            '        self.open_sockets.append(sock)\n'
            '        count = fail_count if fail_count is not None else self.failures_remaining\n'
            '        if count > 0:\n'
            '            if fail_count is None:\n'
            '                self.failures_remaining -= 1\n'
            '            raise ConnectionError("Connection refused")\n'
            '        return "response_payload"\n\n'
            '    def retry_request(self, max_retries: int = 3, backoff_base: float = 0.01):\n'
            '        # BUG: Currently does not handle retry or cleanup\n'
            '        return self.simulate_connection()\n',
            encoding="utf-8",
        )
        (tests_dir / "test_network.py").write_text(
            'from src.network_client import NetworkClient\n\n'
            'def test_single_request():\n'
            '    c = NetworkClient()\n'
            '    assert c.simulate_connection(0) == "response_payload"\n',
            encoding="utf-8",
        )

    elif task_id == "TASK-12":
        (src_dir / "markdown_parser.py").write_text(
            'def parse_header(line: str) -> str:\n'
            '    line = line.strip()\n'
            '    if line.startswith("#"):\n'
            '        return line.lstrip("#").lstrip()\n'
            '    return line\n',
            encoding="utf-8",
        )
        (tests_dir / "test_markdown.py").write_text(
            'from src.markdown_parser import parse_header\n\n'
            'def test_standard_headers():\n'
            '    assert parse_header("# Title") == "Title"\n'
            '    assert parse_header("## Subtitle") == "Subtitle"\n',
            encoding="utf-8",
        )
