"""Hidden acceptance evaluator for TASK-05: Billing FeeOptions backward compatibility."""

import pytest
from src.billing import FeeOptions, calculate_fee


def test_hidden_fee_options_object():
    opts = FeeOptions(rate=0.25, discount=12.5)
    assert calculate_fee(100.0, options=opts) == 12.5


def test_hidden_legacy_positional_mixed():
    # Positional args must still work unmodified
    assert calculate_fee(200.0, 0.1, 5.0) == 15.0


def test_hidden_zero_discount():
    opts = FeeOptions(rate=0.05, discount=0.0)
    assert calculate_fee(500.0, options=opts) == 25.0
