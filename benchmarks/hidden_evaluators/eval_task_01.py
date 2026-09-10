"""Hidden acceptance evaluator for TASK-01: Pagination boundary."""

import pytest
from src.pagination import paginate


def test_hidden_last_page_boundary():
    items = list(range(25))
    # 1-based page 3 with page_size 10 should return [20, 21, 22, 23, 24] (5 items)
    assert paginate(items, 3, 10) == list(range(20, 25))


def test_hidden_empty_list():
    assert paginate([], 1, 10) == []


def test_hidden_single_item():
    assert paginate(["only"], 1, 10) == ["only"]
