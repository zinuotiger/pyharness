"""Verify installed bytes and pinned dependencies, without importing source trees."""
import hashlib
import importlib.metadata as md
import importlib.util
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parent


def verify():
    expected = (ROOT / 'runtime/harness-venv').resolve()
    if Path(sys.prefix).resolve() != expected:
        raise RuntimeError('Use this candidate runtime/harness-venv interpreter')
    if not sys.flags.isolated or not __debug__:
        raise RuntimeError('Use Python -I without -O')
    checksums = json.loads((ROOT / 'SHA256SUMS.json').read_text(encoding='utf-8'))
    for name, digest in checksums.items():
        path = (ROOT / name).resolve()
        if not path.is_relative_to(ROOT) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f'Candidate checksum mismatch: {name}')
    spec = importlib.util.find_spec('pyharness')
    if spec is None or not Path(spec.origin).resolve().is_relative_to(expected):
        raise RuntimeError('PyHarness is not imported from the installed candidate wheel')
    site = Path(spec.origin).parent.parent
    with zipfile.ZipFile(ROOT / 'wheels/pyharness-0.1.0-py3-none-any.whl') as wheel:
        for name in wheel.namelist():
            if name.startswith('pyharness/') and not name.endswith('/'):
                if (site / name).read_bytes() != wheel.read(name):
                    raise RuntimeError(f'Installed file differs from wheel: {name}')
    pins = json.loads((ROOT / 'harness-approved.json').read_text())
    versions = {d.metadata['Name'].lower().replace('_', '-'): d.version for d in md.distributions()}
    for name, version in versions.items():
        if pins.get(name) != version:
            raise RuntimeError(f'Unexpected distribution: {name}=={version}')
    info = {'executable': sys.executable, 'python': sys.version, 'import': spec.origin,
            'cwd': str(Path.cwd()), 'isolated': sys.flags.isolated,
            'sys_path': sys.path, 'distributions': versions}
    folder = ROOT / 'runtime/logs'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'installed.json').write_text(json.dumps(info, indent=2), encoding='utf-8')
    return info


if __name__ == '__main__':
    print(json.dumps(verify(), ensure_ascii=False, indent=2))
