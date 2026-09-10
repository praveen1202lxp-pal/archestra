"""Workspace and isolation subsystem for safe autonomous repository editing."""

from fusion_agent.workspace.broker import ExecutionBroker
from fusion_agent.workspace.checkpoint import CheckpointManager
from fusion_agent.workspace.editor import WorkspaceEditor
from fusion_agent.workspace.promotion import PromotionEngine, PromotionResult
from fusion_agent.workspace.session import DirtyWorkingTreeError, WorkspaceSession, WorkspaceState
from fusion_agent.workspace.verifier import VerificationResult, WorkspaceVerifier

__all__ = [
    "DirtyWorkingTreeError",
    "WorkspaceSession",
    "WorkspaceState",
    "ExecutionBroker",
    "WorkspaceEditor",
    "WorkspaceVerifier",
    "VerificationResult",
    "PromotionEngine",
    "PromotionResult",
    "CheckpointManager",
]

