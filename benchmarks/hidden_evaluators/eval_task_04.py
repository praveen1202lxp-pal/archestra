"""Hidden acceptance evaluator for TASK-04: LRUCache eviction."""

import pytest
from src.store import Store


def test_hidden_lru_eviction_order():
    s = Store(max_size=3)
    s.save("k1", 1)
    s.save("k2", 2)
    s.save("k3", 3)
    # Access k1 and k2 so k3 becomes the least recently used
    assert s.load("k1") == 1
    assert s.load("k2") == 2
    # Add k4 -> should evict k3
    s.save("k4", 4)
    assert s.load("k1") == 1
    assert s.load("k2") == 2
    assert s.load("k4") == 4
    assert s.load("k3") is None
