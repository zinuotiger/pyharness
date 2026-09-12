"""PyHarness desktop shell package."""
from __future__ import annotations

from .constants import (
    BRIDGE_TIMEOUT_S,
    CHANNEL,
    HOST,
    LISTEN_TIMEOUT_S,
    SSE_HEARTBEAT_S,
    TIMELINE_KINDS,
    WARN_RATIO,
    WINDOW_HEIGHT,
    WINDOW_TITLE,
    WINDOW_WIDTH,
)
from .projection import (
    EventStreamHub,
    StreamClient,
    TimelineNode,
    approval_node,
    derive_timeline,
    redact,
    redact_args,
    render_timeline_node,
)
from .net import (
    pick_free_port,
    run_uvicorn,
    wait_listening_async,
    wait_until_listening,
)
from .sessions import DesktopSessionManager
from .app import DesktopApp
from .bridge import DesktopBridge
from .launcher import assemble_desktop_ctx, main, run_desktop

__all__ = [
    "WINDOW_TITLE", "WINDOW_WIDTH", "WINDOW_HEIGHT", "HOST",
    "TIMELINE_KINDS", "CHANNEL", "WARN_RATIO",
    "TimelineNode", "StreamClient", "EventStreamHub", "DesktopBridge",
    "DesktopApp", "DesktopSessionManager",
    "render_timeline_node", "derive_timeline", "approval_node", "redact_args",
    "redact", "pick_free_port", "run_uvicorn", "wait_until_listening",
    "wait_listening_async", "main", "run_desktop", "assemble_desktop_ctx",
]
