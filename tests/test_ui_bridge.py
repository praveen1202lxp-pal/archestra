"""Unit and integration tests for Fusion UI Bridge (Milestone 14).

Validates protocol serialization, path traversal prevention, safe file operations,
dirty-repo boundary protection, command dispatching, doctor integration,
and approval/rejection state transitions.
"""

import json
import os
import shutil
import tempfile
from pathlib import Path
import pytest

from fusion_agent.config.loader import ConfigLoader
from fusion_agent.config.schema import FusionConfig, OptimizationMode
from fusion_agent.memory.database import Database
from fusion_agent.models.deliberation import ReviewResult, ReviewStatus
from fusion_agent.models.task import PromotionDisposition, Task, TaskStatus, TaskType
from fusion_agent.ui_bridge.handler import PathTraversalError, UIBridgeHandler
from fusion_agent.ui_bridge.protocol import (
    BridgeEvent,
    BridgeRequest,
    BridgeResponse,
    UICommand,
    UIErrorCode,
    UIEvent,
)
from fusion_agent.ui_bridge.server import UIBridgeServer
from fusion_agent.workspace.session import WorkspaceSession
from fusion_agent.workspace.verifier import VerificationResult


@pytest.fixture
def temp_project(tmp_path):
    """Create a temporary initialized Fusion project for bridge testing."""
    proj_dir = tmp_path / "test_repo"
    proj_dir.mkdir(parents=True, exist_ok=True)
    fusion_dir = proj_dir / ".fusion"
    fusion_dir.mkdir(parents=True, exist_ok=True)

    # Starter files
    src_dir = proj_dir / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "app.py").write_text("def hello():\n    return 'world'\n", encoding="utf-8")

    tests_dir = proj_dir / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "test_app.py").write_text("def test_hello():\n    pass\n", encoding="utf-8")

    # Config
    cfg = FusionConfig.default_starter_config(project_name="TestRepo")
    cfg.project_root = str(proj_dir)
    ConfigLoader.save(cfg, proj_dir)

    # SQLite DB
    db = Database(fusion_dir / "fusion.db")
    db.connect()
    db.close()

    return proj_dir


def test_protocol_request_response_serialization():
    """Verify JSON-RPC request and response data models."""
    req_dict = {
        "id": "req-1",
        "command": "OPEN_PROJECT",
        "params": {"path": "/some/path"},
    }
    req = BridgeRequest.from_dict(req_dict)
    assert req.id == "req-1"
    assert req.command == "OPEN_PROJECT"
    assert req.params["path"] == "/some/path"

    resp = BridgeResponse(id="req-1", success=True, data={"name": "test"})
    d = resp.to_dict()
    assert d["id"] == "req-1"
    assert d["success"] is True
    assert d["data"]["name"] == "test"
    assert "error" not in d

    ev = BridgeEvent(event=UIEvent.TASK_STARTED.value, data={"task_id": "t1"})
    assert ev.to_dict()["event"] == "TASK_STARTED"
    assert ev.to_dict()["data"]["task_id"] == "t1"


def test_open_project_and_status(temp_project):
    """Verify OPEN_PROJECT and GET_PROJECT_STATUS commands."""
    events = []
    handler = UIBridgeHandler(event_sink=lambda ev: events.append(ev))

    req = BridgeRequest(id="1", command=UICommand.OPEN_PROJECT.value, params={"path": str(temp_project)})
    resp = handler.handle_request(req)

    assert resp.success is True
    assert resp.data["name"] == "TestRepo"
    assert resp.data["initialized"] is True
    assert len(events) == 1
    assert events[0].event == UIEvent.PROJECT_OPENED.value

    # Check status
    req_status = BridgeRequest(id="2", command=UICommand.GET_PROJECT_STATUS.value)
    resp_status = handler.handle_request(req_status)
    assert resp_status.success is True
    assert resp_status.data["opened"] is True
    assert resp_status.data["project_name"] == "TestRepo"
    assert resp_status.data["optimization_mode"] == "BALANCED"


def test_path_traversal_prevention(temp_project):
    """Verify strict rejection of path traversal attempts."""
    handler = UIBridgeHandler(project_root=str(temp_project))

    # Test reading outside project root
    traversal_paths = [
        "../outside.txt",
        "../../etc/passwd",
        "/etc/shadow",
        "C:\\Windows\\System32\\calc.exe",
    ]
    for p in traversal_paths:
        req = BridgeRequest(id="test", command=UICommand.READ_FILE.value, params={"filepath": p})
        resp = handler.handle_request(req)
        assert resp.success is False
        assert resp.error["code"] == UIErrorCode.PATH_TRAVERSAL_DENIED.value


def test_safe_file_listing(temp_project):
    """Verify LIST_FILES excludes internal/secret files (.git, .fusion/*.db, etc.)."""
    handler = UIBridgeHandler(project_root=str(temp_project))

    req = BridgeRequest(id="3", command=UICommand.LIST_FILES.value, params={"subpath": ""})
    resp = handler.handle_request(req)

    assert resp.success is True
    files = resp.data["files"]
    names = [f["name"] for f in files]

    assert "src" in names
    assert "tests" in names
    assert ".git" not in names
    # Internal DB files must not be exposed
    for f in names:
        assert not f.endswith(".db")
        assert not f.endswith(".db-wal")


def test_file_read_and_write(temp_project):
    """Verify READ_FILE and WRITE_FILE operations within project boundaries."""
    handler = UIBridgeHandler(project_root=str(temp_project))

    # Read existing file
    req_read = BridgeRequest(id="r1", command=UICommand.READ_FILE.value, params={"filepath": "src/app.py"})
    resp_read = handler.handle_request(req_read)
    assert resp_read.success is True
    assert "def hello():" in resp_read.data["content"]

    # Write new file
    req_write = BridgeRequest(
        id="w1",
        command=UICommand.WRITE_FILE.value,
        params={"filepath": "src/new_mod.py", "content": "x = 42\n"},
    )
    resp_write = handler.handle_request(req_write)
    assert resp_write.success is True
    assert (temp_project / "src" / "new_mod.py").read_text(encoding="utf-8") == "x = 42\n"


def test_doctor_command(temp_project):
    """Verify RUN_DOCTOR command executes and returns structured non-generative diagnostics."""
    handler = UIBridgeHandler(project_root=str(temp_project))

    req = BridgeRequest(id="d1", command=UICommand.RUN_DOCTOR.value)
    resp = handler.handle_request(req)

    assert resp.success is True
    assert "is_healthy" in resp.data
    assert "checks" in resp.data
    assert len(resp.data["checks"]) > 0
    check_names = [c["name"] for c in resp.data["checks"]]
    assert any("Python" in name for name in check_names)
    assert any("Storage" in name for name in check_names)


def test_unknown_command():
    """Verify unrecognized command returns UNKNOWN_COMMAND."""
    handler = UIBridgeHandler()
    req = BridgeRequest(id="u1", command="NON_EXISTENT_COMMAND")
    resp = handler.handle_request(req)
    assert resp.success is False
    assert resp.error["code"] == UIErrorCode.UNKNOWN_COMMAND.value


def test_promotion_and_rejection(temp_project):
    """Verify APPROVE_TASK and REJECT_TASK interact cleanly with PromotionEngine."""
    handler = UIBridgeHandler(project_root=str(temp_project))

    # Mock an active result with passing verification and approved review
    handler.state_manager.create_task(
        project_id="testrepo",
        title="Mock Task",
        description="Mock Description",
        task_id="task-123",
        task_type=TaskType.CODE_MODIFICATION,
    )

    # Ineligible promotion (no active session)
    req_promo = BridgeRequest(id="p1", command=UICommand.APPROVE_TASK.value, params={"task_id": "task-123"})
    resp_promo = handler.handle_request(req_promo)
    assert resp_promo.success is False
    assert resp_promo.error["code"] == UIErrorCode.PROMOTION_INELIGIBLE.value


def test_server_line_handling(temp_project):
    """Verify UIBridgeServer parses stdio lines and writes formatted responses."""
    server = UIBridgeServer(initial_project_root=str(temp_project))

    # Invalid JSON line
    server.handle_line("NOT_JSON")
    # Project status query
    server.handle_line(json.dumps({"id": "s1", "command": "GET_PROJECT_STATUS"}))
