import sys
from pathlib import Path

# Ensure demo calculator src is discoverable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from src.calculator import add, divide, multiply, subtract


def test_add():
    assert add(2, 3) == 5
    assert add(-1, 1) == 0
    assert add(0.5, 1.5) == 2.0


def test_subtract():
    assert subtract(5, 3) == 2
    assert subtract(0, 5) == -5


def test_multiply():
    assert multiply(3, 4) == 12
    assert multiply(-2, 3) == -6
    assert multiply(0, 100) == 0


def test_divide():
    assert divide(10, 2) == 5.0
    assert divide(7, 2) == 3.5
    with pytest.raises(ZeroDivisionError):
        divide(10, 0)
