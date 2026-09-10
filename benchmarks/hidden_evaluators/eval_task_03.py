"""Hidden acceptance evaluator for TASK-03: ClientConfig timeout."""

import pytest
from src.client import ClientConfig


def test_hidden_custom_timeout():
    cfg = ClientConfig("https://api.test.com", timeout_seconds=45.0)
    assert cfg.timeout_seconds == 45.0


def test_hidden_zero_timeout_raises():
    with pytest.raises(ValueError):
        ClientConfig("https://api.test.com", timeout_seconds=0.0)


def test_hidden_negative_timeout_raises():
    with pytest.raises(ValueError):
        ClientConfig("https://api.test.com", timeout_seconds=-10.0)
