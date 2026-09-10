"""Hidden acceptance evaluator for TASK-01: Pagination boundary."""

import pytest
from src.pagination import paginate


def test_hidden_middle_page():
    items = list(range(25))
    # 1-based page 1 with page_size 10 should return exactly 10 items: 0..9
    assert paginate(items, 1, 10) == list(range(10))
    # 1-based page 2 with page_size 10 should return exactly 10 items: 10..19
    assert paginate(items, 2, 10) == list(range(10, 20))


def test_hidden_last_page_boundary():
    items = list(range(25))
    # 1-based page 3 with page_size 10 should return [20, 21, 22, 23, 24] (5 items)
    assert paginate(items, 3, 10) == list(range(20, 25))


def test_hidden_empty_list():
    assert paginate([], 1, 10) == []


def test_hidden_single_item():
    assert paginate(["only"], 1, 10) == ["only"]
