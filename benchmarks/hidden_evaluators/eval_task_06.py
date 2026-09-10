"""Hidden acceptance evaluator for TASK-06: Shared utilities refactoring."""

import pytest
import src.shared_utils
from src.item_service import register_item
from src.user_service import register_user


def test_hidden_shared_import_exists():
    # Verify shared_utils contains a sanitization/normalization function
    exported = dir(src.shared_utils)
    assert any("sanitize" in name or "normalize" in name for name in exported)


def test_hidden_special_characters_handling():
    res_user = register_user("Admin!@#$$%^&*()")
    assert res_user["user_id"] == "admin"
    res_item = register_item("SKU*99--00!!")
    assert res_item["sku"] == "sku99--00"
