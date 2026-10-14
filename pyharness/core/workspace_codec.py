"""Bounded workspace wire codec shared by worker and client."""
import base64
import os
from pyharness.core.sandbox import safe_path, read_regular
from pyharness.core.tenant_settings import TenantSettingsStore
MAX_BYTES = 10 * 1048576

def pack_files(root):
    files = {}
    total = 0
    for count, entry in enumerate(root.rglob('*')):
        if count >= 1000: raise ValueError('workspace_quota')
        name = entry.relative_to(root).as_posix()
        path = safe_path(root, name)
        if path.is_dir():
            continue
        raw = read_regular(path, MAX_BYTES)
        total += len(raw)
        if total > MAX_BYTES or len(files) >= 500:
            raise ValueError('workspace_quota')
        files[name] = base64.b64encode(raw).decode('ascii')
    return files


def unpack_files(root, files):
    if not isinstance(files, dict) or len(files) > 500:
        raise ValueError('workspace_quota')
    decoded = {}
    total = 0
    for name, value in files.items():
        target = safe_path(root, name)
        raw = base64.b64decode(value, validate=True)
        total += len(raw)
        if total > MAX_BYTES:
            raise ValueError('workspace_quota')
        decoded[target] = raw
    for path, raw in decoded.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        TenantSettingsStore._atomic_write(path, raw)
        if os.name != 'nt' and os.getuid() == 0:
            os.chown(path, 65534, 65534)
            for parent in path.parents:
                if not parent.is_relative_to(root): break
                os.chown(parent, 65534, 65534)
                parent.chmod(0o700)
