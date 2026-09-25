"""Small Platform CI stages; uses an existing system browser, no browser bundle."""
from __future__ import annotations
import argparse
import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.ci_verify import ROOT,environment,python_in,run,write_json

def scan(out):
    files=subprocess.check_output(['git','-c','core.quotepath=false','ls-files','--cached','--others','--exclude-standard'],cwd=ROOT,text=True,encoding='utf-8').splitlines()
    issues=[];parsed=0
    for name in sorted(set(files)):
        path=ROOT/name
        if not path.is_file():continue
        if path.suffix=='.py':ast.parse(path.read_text(encoding='utf-8-sig'),filename=name);parsed+=1
        if path.suffix not in {'.py','.md','.json','.yml','.yaml','.html','.toml'}:continue
        content=path.read_text(encoding='utf-8-sig')
        if re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',content):issues.append(name+': private key')
        public=name.startswith('reports/PYHARNESS-PLATFORM') or name=='pyharness/ui/platform.html'
        if public and (re.search(r'[A-Za-z]:[\\/](?:Users|zinuotiger-workspace)[\\/]|/home/runner/work/|/Users/',content) or re.search(r'sk-[A-Za-z0-9_-]{24,}',content)):
            issues.append(name+': public path or key')
        if name=='pyharness/ui/platform.html' and re.search(r'LENOVO|68M|lenovo@example',content,re.I):issues.append(name+': fixed demo data')
    write_json(out/'artifacts/platform-scan-summary.json',{'status':'failed' if issues else 'passed','python_files_parsed':parsed,'issues':issues,'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()})
    if issues:raise SystemExit('\n'.join(issues))
    print('Platform syntax, public-path, secret and fixed-demo-data scan passed')

def browser(out,expect):
    env=environment(out/'browser-runner')
    for name in ('PROGRAMFILES','PROGRAMFILES(X86)'):
        if name in os.environ:env[name]=os.environ[name]
    uv=shutil.which('uv')
    if not uv:raise SystemExit('uv required')
    # Only the Python driver is installed. Chrome/Edge is provided by the runner.
    run([uv,'pip','install','--python',python_in(out/'test-env'),'playwright==1.61.0'],cwd=out,env=env)
    args=[python_in(out/'test-env'),'-B',ROOT/'scripts/platform_browser_smoke.py','--output-dir',out/'browser']
    if expect:args+=['--expect-docker',expect]
    run(args,cwd=ROOT,env=env,timeout=180)
    target=out/'artifacts/browser';target.mkdir(parents=True,exist_ok=True)
    for path in (out/'browser').glob('*.png'):shutil.copy2(path,target/path.name)
    shutil.copy2(out/'browser/browser-summary.json',out/'artifacts/browser-summary.json')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('stage',choices=['scan','browser']);p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--expect-docker',choices=['available','unavailable']);args=p.parse_args();out=args.output_dir.resolve()
    if out==ROOT or out.is_relative_to(ROOT):p.error('output must be outside checkout')
    out.mkdir(parents=True,exist_ok=True)
    if args.stage=='scan':scan(out)
    else:browser(out,args.expect_docker)
if __name__=='__main__':main()
