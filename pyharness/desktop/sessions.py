"""Compatibility module; session persistence is owned by the application layer."""
import sys
from pyharness.application import session_store
sys.modules[__name__] = session_store
