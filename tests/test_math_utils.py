"""Tests for numeric utility functions."""

import pytest

from fusion_agent.utils.math_utils import clamp


@pytest.mark.parametrize(
    ("x", "min_val", "max_val", "expected"),
    [
        (5, 0, 10, 5),
        (-5, 0, 10, 0),
        (15, 0, 10, 10),
        (0, 0, 10, 0),
        (10, 0, 10, 10),
        (-5, -10, -1, -5),
        (-20, -10, -1, -10),
        (0, -10, -1, -1),
        (3, 3, 3, 3),
        (2.5, 1.0, 4.0, 2.5),
        (0.5, 1.0, 4.0, 1.0),
        (4.5, 1.0, 4.0, 4.0),
        (2, 1.5, 3.5, 2),
        (1.25, 1, 2, 1.25),
        (float("-inf"), -10, 10, -10),
        (float("inf"), -10, 10, 10),
        (5, float("-inf"), float("inf"), 5),
        (float("-inf"), float("-inf"), float("inf"), float("-inf")),
        (float("inf"), float("-inf"), float("inf"), float("inf")),
    ],
)
def test_clamp_returns_value_within_bounds(x, min_val, max_val, expected):
    assert clamp(x, min_val, max_val) == expected


@pytest.mark.parametrize(
    ("min_val", "max_val"),
    [
        (1, 0),
        (1.1, 1.0),
        (0, -1.0),
        (float("inf"), 100),
        (0, float("-inf")),
        (float("inf"), float("-inf")),
    ],
)
def test_clamp_rejects_reversed_bounds(min_val, max_val):
    with pytest.raises(ValueError, match="min_val"):
        clamp(0, min_val, max_val)


@pytest.mark.parametrize(
    ("x", "min_val", "max_val"),
    [
        ("5", 0, 10),
        (None, 0, 10),
        (complex(1, 2), 0, 10),
        ([5], 0, 10),
        (5, "0", 10),
        (5, None, 10),
        (5, complex(0, 1), 10),
        (5, 0, "10"),
        (5, 0, None),
        (5, 0, complex(10, 1)),
    ],
)
def test_clamp_rejects_non_numeric_inputs(x, min_val, max_val):
    with pytest.raises(TypeError):
        clamp(x, min_val, max_val)


@pytest.mark.parametrize(
    ("x", "min_val", "max_val"),
    [
        (True, 0, 10),
        (False, 0, 10),
        (5, True, 10),
        (5, False, 10),
        (5, 0, True),
        (5, 0, False),
    ],
)
def test_clamp_explicitly_rejects_booleans(x, min_val, max_val):
    with pytest.raises(TypeError):
        clamp(x, min_val, max_val)


@pytest.mark.parametrize(
    ("x", "min_val", "max_val", "argument_name"),
    [
        (float("nan"), 0, 10, "x"),
        (5, float("nan"), 10, "min_val"),
        (5, 0, float("nan"), "max_val"),
    ],
)
def test_clamp_rejects_nan(x, min_val, max_val, argument_name):
    with pytest.raises(ValueError, match=argument_name):
        clamp(x, min_val, max_val)


def test_type_validation_occurs_before_bounds_validation():
    with pytest.raises(TypeError):
        clamp("invalid", 10, 0)


def test_type_validation_occurs_before_nan_validation():
    with pytest.raises(TypeError):
        clamp("invalid", float("nan"), 0)