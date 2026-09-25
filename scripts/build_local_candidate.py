"""Build a unique local candidate; preserve original integration scripts/evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise RuntimeError('Integration script changed; review portable adaptation: ' + old[:80])
    return text.replace(old, new, 1)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--id', required=True)
    p.add_argument('--out-dir', required=True, type=Path,
                   help='External parent directory; the unique ID is appended')
    p.add_argument('--r1-artifacts', required=True, type=Path,
                   help='Explicit verified R1 wheel, delivery-manifest.json and constraints.txt directory')
    p.add_argument('--harness-constraints', required=True, type=Path,
                   help='Explicit name==version pins for the approved harness installation')
    args = p.parse_args()
    if not args.id.replace('-', '').replace('_', '').isalnum():
        raise ValueError('Use an alphanumeric unique candidate ID')
    pins = read_pins(args.harness_constraints)
    for required in ('fastapi', 'uvicorn'):
        if required not in pins:
            raise ValueError(f'Missing required harness pin: {required}')
    pins['pyharness'] = tomllib.loads((ROOT/'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
    dest = args.out_dir.resolve()/args.id
    if dest.is_relative_to(ROOT.resolve()):
        raise ValueError('Use an output directory outside the source repository')
    dest.mkdir(parents=True, exist_ok=False)
    wheels = dest/'wheels'
    wheels.mkdir()
    subprocess.run(['uv', 'build', '--wheel', '--out-dir', str(wheels), '--python', sys.executable], cwd=ROOT, check=True)
    previous = args.r1_artifacts.resolve()
    fixed = previous/'evleven-1.1.0-py3-none-any.whl'
    if hashlib.sha256(fixed.read_bytes()).hexdigest() != '40d5cbd5ae312b4bb3af133405807fb9764653b95145a80e4633bed17039ea2e':
        raise RuntimeError('Pinned R1 wheel changed')
    shutil.copy2(fixed, wheels/fixed.name)
    shutil.copy2(previous/'delivery-manifest.json', wheels/'delivery-manifest.json')
    shutil.copy2(previous/'constraints.txt', dest/'evleven-constraints.txt')
    for path in (ROOT/'scripts/local_candidate').iterdir():
        if path.is_file():
            shutil.copy2(path, dest/path.name)
    (dest/'harness-approved.json').write_text(json.dumps(pins, indent=2), encoding='utf-8')
    (dest/'harness-constraints.txt').write_text(''.join(f'{n}=={v}\n' for n,v in sorted(pins.items())), encoding='utf-8')
    (dest/'harness-requirements.txt').write_text(f"fastapi=={pins['fastapi']}\nuvicorn=={pins['uvicorn']}\n", encoding='utf-8')
    (dest/'evleven-requirements.txt').write_text('# Runtime dependencies are declared by the pinned R1 wheel.\n', encoding='utf-8')
    sources = {}
    for name in ('evleven_integration.py', 'evleven_r1_server.py'):
        path = ROOT/'scripts'/name
        sources[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        text = path.read_text(encoding='utf-8')
        text = replace_once(text, 'ROOT = Path(__file__).resolve().parents[1]', 'ROOT = Path(__file__).resolve().parent')
        text = replace_once(text, "BASE = ROOT / '.work/integration-evleven'", "BASE = ROOT / 'runtime'")
        if name == 'evleven_integration.py':
            text = replace_once(text, 'sys.path.insert(0, str(ROOT))', 'import runpy  # isolated installed-wheel imports; never add source paths')
            text = replace_once(text, "ROOT / 'scripts/evleven_r1_server.py'", "ROOT / 'evleven_r1_server.py'")
            text = replace_once(text, "ROOT / '.work/verification/venv'", "BASE / 'harness-venv'")
            text = replace_once(text, "default=BASE / 'trial'", "default=BASE / 'checks'")
            text = replace_once(text, 'def main():\n', "def main():\n    runpy.run_path(str(ROOT / 'integrity.py'))['verify']()\n")
            text = replace_once(text, "[sys.executable, str(Path(__file__).resolve()), '--phase'", "[sys.executable, '-I', '-B', '-X', 'utf8', str(Path(__file__).resolve()), '--phase'")
            text = text.replace('Use .work/verification/venv/Scripts/python.exe', 'Use runtime/harness-venv/Scripts/python.exe')
            text = text.replace('must stay in .work/integration-evleven', 'must stay in this candidate runtime')
        else:
            text = replace_once(text, "BASE / 'artifacts/evleven-1.1.0-py3-none-any.whl'", "ROOT / 'wheels/evleven-1.1.0-py3-none-any.whl'")
            text = replace_once(text, "BASE / 'artifacts/delivery-manifest.json'", "ROOT / 'wheels/delivery-manifest.json'")
            text = text.replace('must remain under .work/integration-evleven', 'must remain under this candidate runtime')
        (dest/name).write_text(text, encoding='utf-8')
    for mode in ('deterministic', 'local', 'external'):
        model = {'mode':mode, 'model_source':'REPLACE_WITH_EXPLICIT_MODEL_SOURCE',
            'llm':{'model':'REPLACE_WITH_SERVED_MODEL_ID',
                   'base_url':'http://127.0.0.1:8000/v1' if mode == 'local' else 'https://provider.example/v1',
                   'api_key':'env:PYHARNESS_TRIAL_MODEL_KEY', 'max_tokens':512, 'temperature':0,
                   'fallback_models':[], 'retry':{'attempts':0}, 'degrade':{'enabled':False},
                   'timeout':{'connect_s':5, 'first_token_s':20, 'total_s':30}},
            'acceptance_budget':{'suggested_max_requests':6, 'requires_separate_external_approval':True}}
        (dest/f'model-{mode}.json').write_text(json.dumps(model, indent=2), encoding='utf-8')
    record = {'head':subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT).decode().strip(),
              'source_integration_scripts':sources, 'candidate_id':args.id,
              'portable_changes':'Bundle-relative paths, isolated interpreter, installed integrity verification; core runtime unchanged.'}
    (dest/'BUILD.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    paths = sorted(set(subprocess.check_output(['git','ls-files','-co','--exclude-standard','-z'], cwd=ROOT).decode().split('\0')) - {''})
    state = {'head':record['head'], 'status':subprocess.check_output(['git','status','--short','-uall'], cwd=ROOT).decode(),
             'files':{n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in paths if (ROOT/n).is_file()}}
    (dest/'SOURCE-STATE.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    seal(dest)
    print(dest)


def seal(dest):
    manifest = {p.relative_to(dest).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(dest.rglob('*')) if p.is_file() and p.name != 'SHA256SUMS.json'
                and 'runtime' not in p.relative_to(dest).parts}
    (dest/'SHA256SUMS.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')


def read_pins(path):
    """Read explicit inputs, never infer dependencies from the builder's env."""
    pins = {}
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9_.-]*)==([A-Za-z0-9_.+!-]+)', line)
        if not match:
            raise ValueError('Expected a pinned name==version requirement')
        name = match[1].lower().replace('_', '-')
        if name in pins and pins[name] != match[2]:
            raise ValueError(f'Conflicting pin for {name}')
        pins[name] = match[2]
    return pins


if __name__ == '__main__':
    main()
