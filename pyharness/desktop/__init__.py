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
from .sessions import DesktopSessionManager


def __getattr__(name):
    """Web extras are loaded only by Web entry points, never by native/base."""
    from importlib import import_module
    modules = {'DesktopApp': 'app', 'DesktopBridge': 'bridge',
               'assemble_desktop_ctx':'launcher', 'main':'launcher', 'run_desktop':'launcher',
               'pick_free_port':'net', 'run_uvicorn':'net',
               'wait_listening_async':'net', 'wait_until_listening':'net'}
    if name not in modules: raise AttributeError(name)
    value = getattr(import_module(f'{__name__}.{modules[name]}'), name)
    globals()[name] = value
    return value

__all__ = [
    "WINDOW_TITLE", "WINDOW_WIDTH", "WINDOW_HEIGHT", "HOST",
    "TIMELINE_KINDS", "CHANNEL", "WARN_RATIO",
    "BRIDGE_TIMEOUT_S", "LISTEN_TIMEOUT_S", "SSE_HEARTBEAT_S",
    "TimelineNode", "StreamClient", "EventStreamHub", "DesktopBridge",
    "DesktopApp", "DesktopSessionManager",
    "render_timeline_node", "derive_timeline", "approval_node", "redact_args",
    "redact", "pick_free_port", "run_uvicorn", "wait_until_listening",
    "wait_listening_async", "main", "run_desktop", "assemble_desktop_ctx",
]
