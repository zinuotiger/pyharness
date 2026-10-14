"""Atomic platform configuration only; never a second execution event store."""
from __future__ import annotations
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import threading
from pyharness.persistence import _FileLock
from pyharness.core.tenant_settings import TenantSettingsStore
from pyharness.core.sandbox import fail


class PlatformStore:
    def __init__(self, root: Path):
        self.root = root
        self.lock = threading.RLock()

    @contextmanager
    def transaction(self):
        with self.lock:
            self.root.mkdir(parents=True, exist_ok=True)
            lock = _FileLock(self.root / '.platform.lock')
            if not lock.try_acquire():
                fail('platform_busy')
            try:
                path = self.root / 'configuration.json'
                data = json.loads(path.read_text('utf-8')) if path.exists() else {
                    'platform_format_version': 1, 'agents': {}, 'versions': {}, 'knowledge': {}, 'connections': {}}
                if data.get('platform_format_version') != 1:
                    fail('platform_schema_unsupported')
                # Additive migration: old configuration remains readable.
                for key in ('session_preferences', 'deprecated_versions', 'preferences'):
                    data.setdefault(key, {})
                previous = copy.deepcopy(data)
                yield data
                if data != previous:
                    TenantSettingsStore._atomic_write(path, json.dumps(data, ensure_ascii=False).encode('utf-8'))
            finally:
                lock.release()

    def read(self):
        with self.transaction() as data:
            return copy.deepcopy(data)
