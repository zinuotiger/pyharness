"""Launch only the pinned, installed R1 wheel in an isolated stdio process.

Stdout belongs exclusively to MCP. No evleven business function is imported here.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import runpy
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / '.work/integration-evleven'
WHEEL_SHA256 = '40d5cbd5ae312b4bb3af133405807fb9764653b95145a80e4633bed17039ea2e'


def verify_install():
    expected = BASE / 'evleven-venv'
    if Path(sys.prefix).resolve() != expected.resolve():
        raise RuntimeError(f'Use the isolated R1 interpreter: {expected}')
    wheel = BASE / 'artifacts/evleven-1.1.0-py3-none-any.whl'
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != WHEEL_SHA256:
        raise RuntimeError('R1 wheel checksum mismatch')
    spec = importlib.util.find_spec('evleven')
    if spec is None or not Path(spec.origin).resolve().is_relative_to(expected.resolve()):
        raise RuntimeError('evleven import is not from the isolated installed wheel')
    installed = Path(spec.origin).parent
    with zipfile.ZipFile(wheel) as package:
        for name in package.namelist():
            if name.startswith('evleven/') and name.endswith('.py'):
                if (installed.parent / name).read_bytes() != package.read(name):
                    raise RuntimeError(f'Installed R1 source mismatch: {name}')
    manifest_path = BASE / 'artifacts/delivery-manifest.json'
    if hashlib.sha256(manifest_path.read_bytes()).hexdigest() != '73b0614848d54546d4d1cf89e6f37649a9126e516eec0d2dc9b67ca1a07bdc80':
        raise RuntimeError('R1 delivery manifest checksum mismatch')
    manifest = json.loads(manifest_path.read_text())
    versions = {d.metadata['Name'].lower().replace('_', '-'): d.version
                for d in importlib.metadata.distributions()}
    approved = {k.lower().replace('_', '-'): v
                for k, v in manifest['installed_distributions'].items()}
    for name, version in versions.items():
        if approved.get(name) != version:
            raise RuntimeError(f'Unpinned dependency: {name}=={version}')
    return {'import': spec.origin, 'prefix': sys.prefix, 'python': sys.version,
            'wheel_sha256': WHEEL_SHA256, 'distributions': versions}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    if not __debug__:
        raise RuntimeError('Do not run with -O')
    info = verify_install()
    data = args.data_dir.resolve()
    if not data.is_relative_to(BASE.resolve()):
        raise RuntimeError('R1 data must remain under .work/integration-evleven')
    data.mkdir(parents=True, exist_ok=True)
    # Sanitize even when launched manually, not only through StdioTransport.
    keep = {'PATH', 'PATHEXT', 'COMSPEC', 'SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR',
            'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE', 'OS'}
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    for key, folder in {'HOME': 'home', 'USERPROFILE': 'home', 'APPDATA': 'home/appdata',
                        'LOCALAPPDATA': 'home/local', 'TEMP': 'tmp', 'TMP': 'tmp',
                        'XDG_CACHE_HOME': 'cache', 'HF_HOME': 'cache/huggingface'}.items():
        path = data / folder
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    env.update(PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', HF_HUB_OFFLINE='1')
    os.environ.clear()
    os.environ.update(env)
    sys.dont_write_bytecode = True
    os.chdir(data)
    info.update(pid=os.getpid(), cwd=str(data), database=str(data / 'memory.db'))
    if args.verify:
        print(json.dumps(info))
        return
    print(json.dumps({'R1_PROCESS': info}), file=sys.stderr, flush=True)
    logs = data / 'logs'
    logs.mkdir(exist_ok=True)
    (logs / f'process-{os.getpid()}.json').write_text(json.dumps(info, indent=2), encoding='utf-8')
    sys.argv = ['evleven', '--db-path', str(data / 'memory.db'), '--mode', 'mcp',
                '--log-level', 'INFO']
    runpy.run_module('evleven', run_name='__main__')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'R1 startup failed: {type(exc).__name__}: {exc}', file=sys.stderr)
        sys.exit(1)
