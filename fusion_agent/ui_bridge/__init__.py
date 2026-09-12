"""Fusion UI Bridge package.

Provides a thin, well-defined bridge connecting desktop IDE interfaces
(Angular / Tauri) to the Python Fusion Agent control plane without duplicating
orchestration logic.
"""

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

__all__ = [
    "BridgeEvent",
    "BridgeRequest",
    "BridgeResponse",
    "PathTraversalError",
    "UIBridgeHandler",
    "UIBridgeServer",
    "UICommand",
    "UIErrorCode",
    "UIEvent",
]
