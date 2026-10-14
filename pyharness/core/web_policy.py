"""Web policy: complete migrated implementation."""
from __future__ import annotations
import logging


log = logging.getLogger("pyharness.tool_web")

SEARCH_PER_SESSION: int = 20

SEARCH_RESULT_CHARS: int = 8000

FETCH_MAX_CHARS: int = 32768

RAW_HTML_MAX: int = 2_097_152

HTTP_TIMEOUT: int = 15

CONNECT_TIMEOUT: int = 10

REDIRECT_MAX: int = 3

BINARY_SAMPLE: int = 8192

_MAX_MD: int = 2_097_152

_MAX_REDIRECT_ADVICE: str = "重定向超过 3 跳,已停止"
