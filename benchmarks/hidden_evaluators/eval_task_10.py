"""Hidden acceptance evaluator for TASK-10: Multi-step dependent task."""

import pytest
from src.db_schema import SCHEMA_MIGRATIONS
from src.models import Record
from src.repo import RecordRepository


def test_hidden_migration_contains_archived_at():
    all_sql = " ".join(SCHEMA_MIGRATIONS).lower()
    assert "archived_at" in all_sql


def test_hidden_record_archived_at_field():
    r_active = Record(id="1", title="Active Item")
    assert getattr(r_active, "archived_at", None) is None

    r_archived = Record(id="2", title="Old Item", archived_at="2026-01-01T00:00:00Z")
    assert r_archived.archived_at == "2026-01-01T00:00:00Z"


def test_hidden_list_active_filters_archived():
    repo = RecordRepository()
    r1 = Record(id="1", title="Active 1")
    r2 = Record(id="2", title="Archived", archived_at="2026-01-01")
    r3 = Record(id="3", title="Active 2")
    repo.add(r1)
    repo.add(r2)
    repo.add(r3)

    active = repo.list_active()
    active_ids = [r.id for r in active]
    assert "1" in active_ids
    assert "3" in active_ids
    assert "2" not in active_ids
