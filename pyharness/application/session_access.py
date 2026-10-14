"""Session access: complete migrated implementation."""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    pass


def _session_module():
    """Application-owned session persistence surface."""
    from pyharness.application import session_store as sessions
    return sessions

_METADATA_HOSTS = frozenset({"169.254.169.254", "metadata.google.internal",
                             "100.100.100.200"})
