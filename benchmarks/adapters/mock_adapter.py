"""Deterministic Mock SUT Adapter for offline harness validation and dry-runs."""

import time
from pathlib import Path
from typing import Optional, Set

from benchmarks.adapters.base import AdapterRunTelemetry, BaseSUTAdapter
from benchmarks.schema import BenchmarkTask, SystemUnderTest


class MockSUTAdapter(BaseSUTAdapter):
    """Simulates agent execution offline with configurable deterministic behaviors."""

    def __init__(
        self,
        sut: SystemUnderTest = SystemUnderTest.FUSION,
        solve_tasks: Optional[Set[str]] = None,
        simulate_syntax_error: bool = False,
        simulate_forbidden_write: bool = False,
        simulate_unintended_write: bool = False,
        simulate_reviewer_defect: bool = False,
    ):
        super().__init__(sut=sut)
        self.solve_tasks = solve_tasks or set()
        self.simulate_syntax_error = simulate_syntax_error
        self.simulate_forbidden_write = simulate_forbidden_write
        self.simulate_unintended_write = simulate_unintended_write
        self.simulate_reviewer_defect = simulate_reviewer_defect

    def execute(self, task: BenchmarkTask, repo_path: Path) -> AdapterRunTelemetry:
        t0 = time.time()

        # 1. Apply reference solution if requested
        if task.task_id in self.solve_tasks:
            self._apply_reference_solution(task.task_id, repo_path)

        # 2. Fault injection simulations
        if self.simulate_syntax_error and task.required_paths:
            target = repo_path / task.required_paths[0]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("def broken_syntax(:\n    pass\n", encoding="utf-8")

        if self.simulate_forbidden_write and task.forbidden_paths:
            forbidden = repo_path / task.forbidden_paths[0]
            forbidden.parent.mkdir(parents=True, exist_ok=True)
            forbidden.write_text("# FORBIDDEN MODIFICATION\n", encoding="utf-8")

        if self.simulate_unintended_write:
            unintended = repo_path / "unintended_file.py"
            unintended.write_text("# UNINTENDED FILE\n", encoding="utf-8")

        duration = max(0.01, time.time() - t0)

        # Telemetry modeling
        native_in = 1250 if self.sut == SystemUnderTest.FUSION else 1600
        native_out = 320
        fusion_ctx = 980 if self.sut == SystemUnderTest.FUSION else None

        return AdapterRunTelemetry(
            system_under_test=self.sut,
            wall_clock_duration_seconds=duration,
            active_provider_duration_seconds=duration * 0.8,
            native_input_tokens=native_in,
            native_output_tokens=native_out,
            fusion_controlled_context_tokens=fusion_ctx,
            provider_calls_count=2 if self.sut == SystemUnderTest.FUSION else 1,
            mcp_calls_count=1 if task.mcp_context else 0,
            provider_model_id="mock-agent-v1",
            cli_version="1.0.0-mock",
            reasoning_effort="medium",
            fusion_config_hash="a1b2c3d4" if self.sut == SystemUnderTest.FUSION else None,
            reviewer_verdict="APPROVED" if not self.simulate_reviewer_defect else "CHANGES_REQUESTED",
            reviewer_found_defect=self.simulate_reviewer_defect,
            defect_in_test_passing_patch=self.simulate_reviewer_defect,
            repair_rounds=1 if self.simulate_reviewer_defect else 0,
            repair_successful=self.simulate_reviewer_defect,
            human_promotion_disposition="PROMOTED",
            recovery_events=0,
            policy_denials=0,
        )

    def _apply_reference_solution(self, task_id: str, repo_path: Path) -> None:
        """Apply working solution for task."""
        src_dir = repo_path / "src"
        tests_dir = repo_path / "tests"

        if task_id == "TASK-01":
            (src_dir / "pagination.py").write_text(
                "def paginate(items, page, page_size):\n"
                "    if page < 1 or page_size < 1:\n"
                "        raise ValueError('Page and page_size must be positive')\n"
                "    start = (page - 1) * page_size\n"
                "    end = start + page_size\n"
                "    return items[start:end]\n",
                encoding="utf-8",
            )
        elif task_id == "TASK-02":
            (src_dir / "serializer.py").write_text(
                "def serialize_user(data):\n"
                "    return {\n"
                "        'username': data['username'].strip().lower(),\n"
                "        'email': data['email'].strip().lower() if data.get('email') is not None else None,\n"
                "        'bio': data['bio'].strip() if data.get('bio') is not None else None,\n"
                "    }\n",
                encoding="utf-8",
            )
        elif task_id == "TASK-03":
            (src_dir / "client.py").write_text(
                "class ClientConfig:\n"
                "    def __init__(self, base_url: str, retries: int = 3, timeout_seconds: float = 30.0):\n"
                "        if timeout_seconds <= 0:\n"
                "            raise ValueError('timeout_seconds must be positive')\n"
                "        self.base_url = base_url\n"
                "        self.retries = retries\n"
                "        self.timeout_seconds = float(timeout_seconds)\n",
                encoding="utf-8",
            )
        elif task_id == "TASK-04":
            (src_dir / "lru_cache.py").write_text(
                "from collections import OrderedDict\n"
                "from src.cache_interface import CacheInterface\n\n"
                "class LRUCache(CacheInterface):\n"
                "    def __init__(self, max_size: int = 2):\n"
                "        self.max_size = max_size\n"
                "        self.data = OrderedDict()\n"
                "    def get(self, key: str):\n"
                "        if key not in self.data:\n"
                "            return None\n"
                "        self.data.move_to_end(key)\n"
                "        return self.data[key]\n"
                "    def set(self, key: str, value):\n"
                "        if key in self.data:\n"
                "            self.data.move_to_end(key)\n"
                "        self.data[key] = value\n"
                "        if len(self.data) > self.max_size:\n"
                "            self.data.popitem(last=False)\n",
                encoding="utf-8",
            )
            (src_dir / "store.py").write_text(
                "from src.lru_cache import LRUCache\n\n"
                "class Store:\n"
                "    def __init__(self, max_size: int = 2):\n"
                "        self.cache = LRUCache(max_size=max_size)\n"
                "    def save(self, key: str, val):\n"
                "        self.cache.set(key, val)\n"
                "    def load(self, key: str):\n"
                "        return self.cache.get(key)\n",
                encoding="utf-8",
            )
        elif task_id == "TASK-05":
            (src_dir / "billing.py").write_text(
                "from dataclasses import dataclass\n"
                "from typing import Optional\n\n"
                "@dataclass\n"
                "class FeeOptions:\n"
                "    rate: float = 0.1\n"
                "    discount: float = 0.0\n\n"
                "def calculate_fee(amount: float, rate: float = 0.1, discount: float = 0.0, options: Optional[FeeOptions] = None) -> float:\n"
                "    if options is not None:\n"
                "        return (amount * options.rate) - options.discount\n"
                "    return (amount * rate) - discount\n",
                encoding="utf-8",
            )
        elif task_id == "TASK-06":
            (src_dir / "shared_utils.py").write_text(
                "import re\n\n"
                "def sanitize_identifier(val: str) -> str:\n"
                "    return re.sub(r'[^a-zA-Z0-9_-]', '', val).lower().strip()\n",
                encoding="utf-8",
            )
            (src_dir / "user_service.py").write_text(
                "from src.shared_utils import sanitize_identifier\n\n"
                "def register_user(name: str):\n"
                "    return {'user_id': sanitize_identifier(name)}\n",
                encoding="utf-8",
            )
            (src_dir / "item_service.py").write_text(
                "from src.shared_utils import sanitize_identifier\n\n"
                "def register_item(sku: str):\n"
                "    return {'sku': sanitize_identifier(sku)}\n",
                encoding="utf-8",
            )
        elif task_id == "TASK-07":
            (src_dir / "session_manager.py").write_text(
                "import time\n\n"
                "class SessionManager:\n"
                "    def __init__(self):\n"
                "        self.sessions = {}\n"
                "    def add_session(self, sid: str, ttl: float):\n"
                "        self.sessions[sid] = time.time() + ttl\n"
                "    def cleanup_expired(self):\n"
                "        now = time.time()\n"
                "        expired_keys = [sid for sid, exp in self.sessions.items() if exp < now]\n"
                "        for sid in expired_keys:\n"
                "            del self.sessions[sid]\n",
                encoding="utf-8",
            )
        elif task_id == "TASK-08":
            (tests_dir / "test_rate_limiter.py").write_text(
                "import time\n"
                "from src.rate_limiter import TokenBucket\n\n"
                "def test_token_bucket_consumption():\n"
                "    tb = TokenBucket(capacity=5, refill_rate=10.0)\n"
                "    assert tb.consume(3) is True\n"
                "    assert tb.consume(2) is True\n"
                "    assert tb.consume(1) is False\n"
                "    time.sleep(0.12)\n"
                "    assert tb.consume(1) is True\n",
                encoding="utf-8",
            )
        elif task_id == "TASK-09":
            (src_dir / "notifier.py").write_text(
                "class NotificationDispatcher:\n"
                "    def __init__(self):\n"
                "        self._strategies = {\n"
                "            'email': lambda msg: f'EMAIL: {msg}',\n"
                "            'sms': lambda msg: f'SMS: {msg}',\n"
                "            'webhook': lambda msg: f'WEBHOOK: {msg}',\n"
                "        }\n"
                "    def register(self, channel: str, handler):\n"
                "        self._strategies[channel] = handler\n"
                "    def dispatch(self, channel: str, message: str) -> str:\n"
                "        if channel not in self._strategies:\n"
                "            raise ValueError(f'Unknown channel: {channel}')\n"
                "        return self._strategies[channel](message)\n",
                encoding="utf-8",
            )
        elif task_id == "TASK-10":
            (src_dir / "db_schema.py").write_text(
                "SCHEMA_MIGRATIONS = [\n"
                "    'CREATE TABLE records (id TEXT PRIMARY KEY, title TEXT);',\n"
                "    'ALTER TABLE records ADD COLUMN archived_at TEXT;',\n"
                "]\n",
                encoding="utf-8",
            )
            (src_dir / "models.py").write_text(
                "from dataclasses import dataclass\n"
                "from typing import Optional\n\n"
                "@dataclass\n"
                "class Record:\n"
                "    id: str\n"
                "    title: str\n"
                "    archived_at: Optional[str] = None\n",
                encoding="utf-8",
            )
            (src_dir / "repo.py").write_text(
                "from src.models import Record\n\n"
                "class RecordRepository:\n"
                "    def __init__(self):\n"
                "        self.items = []\n"
                "    def add(self, r: Record):\n"
                "        self.items.append(r)\n"
                "    def list_active(self):\n"
                "        return [r for r in self.items if r.archived_at is None]\n",
                encoding="utf-8",
            )
        elif task_id == "TASK-11":
            (src_dir / "network_client.py").write_text(
                "import time\n\n"
                "class NetworkClient:\n"
                "    def __init__(self, failures_before_success: int = 0):\n"
                "        self.open_sockets = []\n"
                "        self.failures_remaining = failures_before_success\n\n"
                "    def close_socket(self, sock: dict):\n"
                "        sock['closed'] = True\n\n"
                "    def simulate_connection(self, fail_count: int = None):\n"
                "        sock = {'fd': len(self.open_sockets) + 1, 'closed': False}\n"
                "        self.open_sockets.append(sock)\n"
                "        count = fail_count if fail_count is not None else self.failures_remaining\n"
                "        if count > 0:\n"
                "            if fail_count is None:\n"
                "                self.failures_remaining -= 1\n"
                "            raise ConnectionError('Connection refused')\n"
                "        return 'response_payload'\n\n"
                "    def retry_request(self, max_retries: int = 3, backoff_base: float = 0.01):\n"
                "        attempts = 0\n"
                "        while attempts <= max_retries:\n"
                "            try:\n"
                "                return self.simulate_connection()\n"
                "            except ConnectionError:\n"
                "                if self.open_sockets:\n"
                "                    self.close_socket(self.open_sockets[-1])\n"
                "                attempts += 1\n"
                "                if attempts > max_retries:\n"
                "                    raise\n"
                "                time.sleep(backoff_base * (2 ** (attempts - 1)))\n",
                encoding="utf-8",
            )
        elif task_id == "TASK-12":
            (src_dir / "markdown_parser.py").write_text(
                "def parse_header(line: str) -> str:\n"
                "    line = line.strip()\n"
                "    if line.startswith('#'):\n"
                "        stripped = line.lstrip('#').strip()\n"
                "        while stripped.endswith('#'):\n"
                "            stripped = stripped.rstrip('#').strip()\n"
                "        return stripped\n"
                "    return line\n",
                encoding="utf-8",
            )
