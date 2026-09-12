"""One-time mechanical split of pyharness/desktop.py into a package."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "pyharness" / "desktop.py"
TARGET = ROOT / "pyharness" / "desktop"


def lines() -> list[str]:
    return SOURCE.read_text(encoding="utf-8").splitlines()


def slice_lines(data: list[str], start: int, end: int) -> str:
    return "\n".join(data[start - 1:end]) + "\n"


def main() -> None:
    data = lines()
    TARGET.mkdir(exist_ok=True)

    headers = {
        "constants.py": '''"""Desktop shell constants."""
from __future__ import annotations

''',
        "projection.py": '''"""Timeline and SSE projection helpers."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from pyharness.events import Envelope

from .constants import SSE_HEARTBEAT_S, TIMELINE_KINDS, _SENSITIVE_HINTS

log = logging.getLogger("pyharness.desktop.projection")

''',
        "net.py": '''"""Local server startup and readiness helpers."""
from __future__ import annotations

import asyncio
import socket

import uvicorn

from pyharness.errors import raise_code

''',
        "sessions.py": '''"""Desktop session surface and persistence manager."""
from __future__ import annotations

import inspect
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Optional

from pyharness.core.session import SessionLog, open_session
from pyharness.errors import raise_code
from pyharness.events import EVENT_TYPES

log = logging.getLogger("pyharness.desktop.sessions")

''',
        "app.py": '''"""Desktop FastAPI application and API handlers."""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import pathlib
import secrets
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable, Optional

from fastapi import Body, Depends, FastAPI, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from pyharness.core.approval import ApprovalProvider
from pyharness.core.task_queue import TaskQueue
from pyharness.errors import PyHError, raise_code
from pyharness.events import EVENT_TYPES, Envelope
from pyharness.events.vocab import TRANSIENT_TYPES

from .constants import (
    BRIDGE_TIMEOUT_S,
    CHANNEL,
    HOST,
    LISTEN_TIMEOUT_S,
    WARN_RATIO,
    WINDOW_HEIGHT,
    WINDOW_TITLE,
    WINDOW_WIDTH,
    _STATUS_FOR_CODE,
)
from .net import pick_free_port, run_uvicorn, wait_listening_async, wait_until_listening
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
from .sessions import (
    DesktopSessionManager,
    _await,
    _cfg_of,
    _plugin_within,
    _resolve_secret_value,
    _sessions_dir_of,
    _surface_of,
)

log = logging.getLogger("pyharness.desktop.app")

''',
        "bridge.py": '''"""pywebview JavaScript bridge."""
from __future__ import annotations

import asyncio
import inspect
import json
import time
import uuid
from typing import Any

from pyharness.errors import PyHError

from .constants import BRIDGE_TIMEOUT_S

''',
        "launcher.py": '''"""Desktop window and process launcher."""
from __future__ import annotations

import logging
import sys
import threading
from types import SimpleNamespace
from typing import Optional

from pyharness.errors import PyHError

from .app import DesktopApp
from .bridge import DesktopBridge
from .constants import HOST, WINDOW_HEIGHT, WINDOW_TITLE, WINDOW_WIDTH
from .net import pick_free_port, run_uvicorn, wait_listening_async, wait_until_listening

log = logging.getLogger("pyharness.desktop.launcher")

''',
    }

    ranges = {
        "constants.py": (97, 121),
        "projection.py": (125, 416),
        "net.py": (418, 487),
        "sessions.py": (491, 759),
        "app.py": (763, 1746),
        "bridge.py": (1748, 1821),
        "launcher.py": (1823, len(data)),
    }

    for name, (start, end) in ranges.items():
        path = TARGET / name
        path.write_text(headers[name] + slice_lines(data, start, end),
                        encoding="utf-8")

    init = '''"""PyHarness desktop shell package."""
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
'''
    (TARGET / "__init__.py").write_text(init, encoding="utf-8")
    SOURCE.unlink()


if __name__ == "__main__":
    main()
