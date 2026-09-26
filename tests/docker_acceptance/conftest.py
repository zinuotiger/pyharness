"""Explicit real Docker evidence, without temporary paths or raw logs."""
import json
from pathlib import Path

def pytest_sessionfinish(session, exitstatus):
    config=session.config
    if not config.getoption('--real-docker'): return
    target=Path(config.option.basetemp).parent.parent/'artifacts'/'docker-runtime-summary.json'
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps({'real_docker':True,'exit_code':int(exitstatus),
        'checks':getattr(config,'docker_evidence',[]),'real_model':False},indent=2)+'\n',encoding='utf-8')
