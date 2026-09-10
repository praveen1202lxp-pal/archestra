"""Hidden acceptance evaluator for TASK-02: NoneType Serializer handling."""

import pytest
from src.serializer import serialize_user


def test_hidden_all_optional_none():
    res = serialize_user({"username": "Charlie", "email": None, "bio": None})
    assert res["username"] == "charlie"
    assert res["email"] is None
    assert res["bio"] is None


def test_hidden_whitespace_stripping():
    res = serialize_user({"username": "  David  ", "email": "  David@test.ORG  ", "bio": "  Architect  "})
    assert res["username"] == "david"
    assert res["email"] == "david@test.org"
    assert res["bio"] == "Architect"
