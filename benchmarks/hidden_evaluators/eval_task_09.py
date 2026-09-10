"""Hidden acceptance evaluator for TASK-09: Notifier Strategy Pattern."""

import pytest
from src.notifier import NotificationDispatcher


def test_hidden_custom_strategy_registration():
    d = NotificationDispatcher()
    # Must support registering custom notification strategies
    if hasattr(d, "register"):
        d.register("pagerduty", lambda msg: f"PAGERDUTY: {msg}")
        assert d.dispatch("pagerduty", "Critical Alert") == "PAGERDUTY: Critical Alert"
    assert d.dispatch("email", "Report") == "EMAIL: Report"


def test_hidden_unregistered_channel_raises():
    d = NotificationDispatcher()
    with pytest.raises(ValueError):
        d.dispatch("nonexistent_channel", "Ping")
