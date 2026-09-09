"""Numeric utility functions."""

import math


def clamp(x: int | float, min_val: int | float, max_val: int | float) -> int | float:
    """Clamp ``x`` to the inclusive range from ``min_val`` to ``max_val``.

    Raises:
        TypeError: If any argument is not an int or float, or is a boolean.
        ValueError: If any argument is NaN or ``min_val`` exceeds ``max_val``.
    """
    for name, value in (
        ("x", x),
        ("min_val", min_val),
        ("max_val", max_val),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{name} must be an int or float")
        if isinstance(value, float) and math.isnan(value):
            raise ValueError(f"{name} must not be NaN")

    if min_val > max_val:
        raise ValueError("min_val must not be greater than max_val")

    if x < min_val:
        return min_val
    if x > max_val:
        return max_val
    return x