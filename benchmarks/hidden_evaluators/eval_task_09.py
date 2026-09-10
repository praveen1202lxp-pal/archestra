"""Hidden acceptance evaluator for TASK-09: Notifier Strategy Pattern."""

import pytest
from src.notifier import NotificationDispatcher


def test_hidden_custom_strategy_registration():
    d = NotificationDispatcher()
    # Must support registering custom notification strategies
    assert hasattr(d, "register") or hasattr(d, "register_handler"), "NotificationDispatcher must implement register() strategy registration"
    reg_fn = getattr(d, "register", None) or getattr(d, "register_handler")
    reg_fn("pagerduty", lambda msg: f"PAGERDUTY: {msg}")
    assert d.dispatch("pagerduty", "Critical Alert") == "PAGERDUTY: Critical Alert"
    assert d.dispatch("email", "Report") == "EMAIL: Report"


def test_hidden_unregistered_channel_raises():
    d = NotificationDispatcher()
    with pytest.raises(ValueError):
        d.dispatch("nonexistent_channel", "Ping")
