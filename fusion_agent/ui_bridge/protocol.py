"""Structured JSON-RPC-style protocol definitions for Fusion UI Bridge.

Defines all incoming command types, outgoing event types, request/response models,
and error codes exchanged between the desktop application and Fusion core.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class UICommand(str, Enum):
    """Supported incoming commands from desktop UI."""

    OPEN_PROJECT = "OPEN_PROJECT"
    GET_PROJECT_STATUS = "GET_PROJECT_STATUS"
    GET_PROVIDER_STATUS = "GET_PROVIDER_STATUS"
    GET_MCP_STATUS = "GET_MCP_STATUS"
    LIST_TASKS = "LIST_TASKS"
    GET_TASK = "GET_TASK"
    START_TASK = "START_TASK"
    RESUME_TASK = "RESUME_TASK"
    GET_DIFF = "GET_DIFF"
    APPROVE_TASK = "APPROVE_TASK"
    REJECT_TASK = "REJECT_TASK"
    LIST_FILES = "LIST_FILES"
    READ_FILE = "READ_FILE"
    WRITE_FILE = "WRITE_FILE"
    GET_TEST_RESULTS = "GET_TEST_RESULTS"
    GET_REVIEW_FINDINGS = "GET_REVIEW_FINDINGS"
    RUN_DOCTOR = "RUN_DOCTOR"
    INIT_PROJECT = "INIT_PROJECT"
    SAVE_CONFIG = "SAVE_CONFIG"


class UIEvent(str, Enum):
    """Structured UI events emitted by Fusion to the desktop client."""

    PROJECT_OPENED = "PROJECT_OPENED"
    TASK_STARTED = "TASK_STARTED"
    TASK_ASSESSED = "TASK_ASSESSED"
    PLAN_CREATED = "PLAN_CREATED"
    STEP_STARTED = "STEP_STARTED"
    CONTEXT_BUILT = "CONTEXT_BUILT"
    PROVIDER_STARTED = "PROVIDER_STARTED"
    PROVIDER_COMPLETED = "PROVIDER_COMPLETED"
    PATCH_CREATED = "PATCH_CREATED"
    VERIFICATION_STARTED = "VERIFICATION_STARTED"
    VERIFICATION_COMPLETED = "VERIFICATION_COMPLETED"
    REVIEW_STARTED = "REVIEW_STARTED"
    REVIEW_FINDING = "REVIEW_FINDING"
    REPAIR_STARTED = "REPAIR_STARTED"
    DIFF_READY = "DIFF_READY"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"
    TASK_RECOVERABLE = "TASK_RECOVERABLE"
    LOG_MESSAGE = "LOG_MESSAGE"


class UIErrorCode(str, Enum):
    """Standardized error codes returned by the bridge."""

    INVALID_REQUEST = "INVALID_REQUEST"
    UNKNOWN_COMMAND = "UNKNOWN_COMMAND"
    PROJECT_NOT_FOUND = "PROJECT_NOT_FOUND"
    PATH_TRAVERSAL_DENIED = "PATH_TRAVERSAL_DENIED"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    DIRTY_REPOSITORY = "DIRTY_REPOSITORY"
    TASK_NOT_FOUND = "TASK_NOT_FOUND"
    TASK_ALREADY_RUNNING = "TASK_ALREADY_RUNNING"
    PROMOTION_INELIGIBLE = "PROMOTION_INELIGIBLE"
    PROMOTION_FAILED = "PROMOTION_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass
class BridgeRequest:
    """Incoming command message from client."""

    command: str
    id: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BridgeRequest":
        return cls(
            command=data.get("command", ""),
            id=data.get("id"),
            params=data.get("params", {}),
        )


@dataclass
class BridgeResponse:
    """Outgoing response message to client."""

    id: Optional[str]
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        res: Dict[str, Any] = {
            "id": self.id,
            "success": self.success,
        }
        if self.data is not None:
            res["data"] = self.data
        if self.error is not None:
            res["error"] = self.error
        return res


@dataclass
class BridgeEvent:
    """Outgoing asynchronous event message to client."""

    event: str
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event": self.event,
            "data": self.data,
        }
