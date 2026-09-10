"""Hidden acceptance evaluator for TASK-11: Resource lifecycle & socket leak prevention."""

import pytest
from src.network_client import NetworkClient


def test_hidden_retry_success_after_transient_failures():
    client = NetworkClient(failures_before_success=2)
    result = client.retry_request(max_retries=4, backoff_base=0.001)
    assert result == "response_payload"

    # Exactly 3 sockets were opened (2 failed, 1 succeeded)
    assert len(client.open_sockets) == 3
    # The two failed sockets MUST be closed
    failed_sockets = client.open_sockets[:2]
    for s in failed_sockets:
        assert s["closed"] is True, f"Resource leak detected: failed socket {s['fd']} was not closed"


def test_hidden_max_retries_exceeded_and_all_closed():
    client = NetworkClient(failures_before_success=5)
    with pytest.raises(ConnectionError):
        client.retry_request(max_retries=2, backoff_base=0.001)

    # All attempted sockets must be closed
    assert len(client.open_sockets) >= 2
    for s in client.open_sockets:
        assert s["closed"] is True, f"Resource leak detected: socket {s['fd']} was not closed after fatal error"


def test_hidden_zero_failure_immediate_success():
    client = NetworkClient(failures_before_success=0)
    result = client.retry_request(max_retries=3, backoff_base=0.001)
    assert result == "response_payload"
    assert len(client.open_sockets) == 1
