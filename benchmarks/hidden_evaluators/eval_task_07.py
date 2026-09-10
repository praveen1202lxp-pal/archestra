"""Hidden acceptance evaluator for TASK-07: SessionManager mutation during iteration."""

import pytest
from src.session_manager import SessionManager


def test_hidden_concurrent_cleanup_no_error():
    sm = SessionManager()
    sm.add_session("exp1", -10)
    sm.add_session("exp2", -5)
    sm.add_session("exp3", -1)
    sm.add_session("active", 500)
    # Must not raise RuntimeError
    sm.cleanup_expired()
    assert "exp1" not in sm.sessions
    assert "exp2" not in sm.sessions
    assert "exp3" not in sm.sessions
    assert "active" in sm.sessions


def test_hidden_empty_sessions_cleanup():
    sm = SessionManager()
    sm.cleanup_expired()
    assert len(sm.sessions) == 0
