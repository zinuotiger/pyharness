"""Drive the actual installed terminal entry; keep evidence separate from the bundle."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import uuid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    candidate, evidence = args.candidate.resolve(), args.evidence.resolve()
    repo = Path(__file__).resolve().parents[1]
    if not candidate.is_relative_to(repo/'.work/release-candidate') or not evidence.is_relative_to(repo/'.work/release-candidate'):
        raise ValueError('Acceptance paths must stay in this repository release-candidate workspace')
    evidence.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ)
    for key in list(env):
        if key.startswith('PH_') or any(w in key.upper() for w in ('API_KEY','SECRET','TOKEN','PROXY')):
            env.pop(key, None)
    env.update(PYTHONPATH='', PYTHONUTF8='1')
    for key in ('TEMP', 'TMP', 'HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'XDG_CACHE_HOME'):
        path = evidence/key
        path.mkdir()
        env[key] = str(path)
    powershell = shutil.which('powershell.exe')
    python = str(candidate/'runtime/harness-venv/Scripts/python.exe')
    records = []

    def run(label, command, stdin=None, expected=0, extra_env=None):
        start = time.monotonic()
        proc = subprocess.run(command, cwd=evidence, env=env | (extra_env or {}), input=stdin,
                              capture_output=True, text=True, encoding='utf-8', timeout=120)
        (evidence/f'{label}.log').write_text(proc.stdout+'\nSTDERR:\n'+proc.stderr, encoding='utf-8')
        records.append({'case':label, 'command':command, 'cwd':str(evidence), 'stdin':stdin,
                        'exit_code':proc.returncode, 'seconds':round(time.monotonic()-start,3)})
        (evidence/'commands.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
        assert proc.returncode == expected, (label, proc.returncode, proc.stderr[-2000:])
        return proc.stdout+proc.stderr

    start_cmd = [powershell, '-NoProfile', '-File', str(candidate/'start.ps1')]
    phrase = '合成独立安装 蓝色纸鹤 TOKEN' + uuid.uuid4().hex
    first = run('interactive-A', start_cmd,
                f'save {phrase}\nyes\nread last\nyes\ndeny last\naudit\nsave 拒绝保存 {uuid.uuid4().hex}\nno\nquit\n')
    assert 'MCP sent before approval=0' in first and 'DENIED before MCP send: 0 calls' in first
    assert phrase in first and 'NOT EXECUTED / ERROR' in first and 'owned MCP exit=0' in first
    second = run('interactive-B', start_cmd, 'read last\nyes\nsearch TOKEN\nyes\nhistory\nquit\n')
    assert phrase in second and 'owned MCP exit=0' in second
    sessions = sorted((candidate/'runtime/user/logs').glob('*/session-*.json'), key=lambda p:p.stat().st_mtime)
    assert len(sessions) >= 2
    a, b = [json.loads(p.read_text(encoding='utf-8')) for p in sessions[-2:]]
    assert a['sid'] != b['sid']
    assert b['requests'][0]['params']['name'] == 'memory_read'
    assert all(phrase not in json.dumps(q['params'], ensure_ascii=False) for q in b['requests'])
    result = json.loads(b['requests'][0]['result']['content'][0]['text'])
    assert result['memory']['content'] == phrase
    assert not any(q['params'].get('name') == 'memory_delete' for q in a['requests'])
    assert len([q for q in a['requests'] if q['params'].get('name') == 'memory_create']) == 1
    (evidence/'cross-session.json').write_text(json.dumps({'a':a, 'b':b,
        'expected_sha256':hashlib.sha256(phrase.encode()).hexdigest(), 'memory_id':result['memory']['id']}, ensure_ascii=False, indent=2), encoding='utf-8')
    for session in (a,b):
        assert session['mcp_exit_code'] == 0
        port = int(session['url'].rsplit(':',1)[1])
        with socket.socket() as sock:
            assert sock.connect_ex(('127.0.0.1',port)) != 0
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, session['mcp_pid'])
        if handle:
            status = ctypes.c_ulong()
            try:
                assert ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(status))
                assert status.value != 259, 'Owned MCP process still running'
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
    missing = evidence/'missing-install'
    missing.mkdir()
    shutil.copy2(candidate/'start.ps1', missing/'start.ps1')
    run('missing-install', [powershell,'-NoProfile','-File',str(missing/'start.ps1')], expected=1)
    prefix = [python, '-I', '-B', '-X', 'utf8', str(candidate/'model_preflight.py')]
    run('model-deterministic', prefix+[str(candidate/'model-deterministic.json')])
    run('model-missing', prefix+[str(candidate/'model-local.json')], expected=2)
    cfg = json.loads((candidate/'model-local.json').read_text())
    cfg.update(model_source='SYNTHETIC PREFLIGHT ONLY; no running model')
    cfg['llm']['model'] = 'synthetic-preflight-only'
    config = evidence/'model-test.json'
    config.write_text(json.dumps(cfg), encoding='utf-8')
    dummy = 'synthetic-preflight-' + uuid.uuid4().hex
    output = run('model-static-only', prefix+[str(config)], extra_env={'PYHARNESS_TRIAL_MODEL_KEY':dummy})
    assert dummy not in output and '"network_requests": 0' in output
    cfg['llm']['base_url'] = 'https://example.invalid/v1'
    config.write_text(json.dumps(cfg), encoding='utf-8')
    run('model-nonloopback-rejected', prefix+[str(config)], expected=2)
    cfg['llm']['base_url'] = 'http://127.0.0.1:8000/v1'
    cfg['llm']['api_key'] = dummy
    config.write_text(json.dumps(cfg), encoding='utf-8')
    output = run('model-literal-secret-rejected', prefix+[str(config)], expected=2)
    assert dummy not in output
    with (evidence/'lock-owner.log').open('w',encoding='utf-8') as output:
        owner = subprocess.Popen(start_cmd,cwd=evidence,env=env,stdin=subprocess.PIPE,
            stdout=output,stderr=subprocess.STDOUT,text=True,encoding='utf-8')
        try:
            deadline=time.monotonic()+30
            while 'READY session=' not in (evidence/'lock-owner.log').read_text(encoding='utf-8'):
                assert owner.poll() is None and time.monotonic()<deadline
                time.sleep(.1)
            rejected = run('second-writer-rejected',start_cmd,'quit\n',expected=1)
            assert 'already open' in rejected
        finally:
            owner.communicate('quit\n',timeout=30)
            assert owner.returncode == 0
    records.append({'case':'cross-process-lock-owner-cleanup','exit_code':owner.returncode})
    (evidence/'commands.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'PASS: {len(records)} installed terminal/preflight checks; {evidence}')


if __name__ == '__main__':
    main()
