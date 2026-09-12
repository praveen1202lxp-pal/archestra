"""Simple Calculator module for Fusion Agent demo walkthrough."""

from typing import Union

Number = Union[int, float]


def add(a: Number, b: Number) -> Number:
    """Return sum of a and b."""
    return a + b


def subtract(a: Number, b: Number) -> Number:
    """Return difference of a and b."""
    return a - b


def multiply(a: Number, b: Number) -> Number:
    """Return product of a and b."""
    return a * b


def divide(a: Number, b: Number) -> float:
    """Return quotient of a divided by b.

    Raises:
        ZeroDivisionError: If b is zero.
    """
    if b == 0:
        raise ZeroDivisionError("Cannot divide by zero.")
    return a / b
