"""Narrow, conflict-checked synchronization to the user-designated daily checkout."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid

SOURCE=Path(__file__).resolve().parents[1]
TARGET=Path('D:/zinuotiger-workspace/feature-worktrees/pyharness-platform-web-v1').resolve()
BASE='7b1bdeab76cff23a040c94002d2bfb33b0291a06'
FILES=[
 'pyharness/application/trajectory.py','pyharness/application/platform_service.py',
 'pyharness/application/platform_workflow.py','pyharness/application/service.py',
 'pyharness/core/workflow.py','pyharness/desktop/platform_api.py','pyharness/events/payload.py',
 'pyharness/ui/index.html','pyharness/ui/platform.html','pyharness/ui/host-adapters/run-trace.js',
 'pyharness/ui/theme-kit/styles/run-trace.css','tests/unit/test_trajectory.py',
 'tests/browser/run-trace.test.cjs','tests/browser/trajectory-ui.cjs','tests/browser/trajectory_server.py',
 'docs/architecture/platform-web-v1.md','docs/EVENT-SCHEMA.md','docs/specs/events.py.md',
]
def git(*args):
    return subprocess.run(['git','-C',str(TARGET),*args],capture_output=True,check=False)
def digest(raw):return hashlib.sha256(raw).hexdigest()
def contained(root,name):
    path=(root/name).resolve()
    if os.path.commonpath([str(root),str(path)])!=str(root):raise RuntimeError('Path containment failed: '+name)
    return path
def main():
    args=argparse.ArgumentParser();args.add_argument('--apply',action='store_true');options=args.parse_args()
    if git('rev-parse','HEAD').stdout.decode().strip()!=BASE:raise RuntimeError('Daily checkout HEAD differs; no files written.')
    plan=[];conflicts=[]
    for name in FILES:
        source=contained(SOURCE,name);target=contained(TARGET,name)
        new=source.read_bytes();old=target.read_bytes() if target.exists() else None
        tracked=git('ls-files','--error-unmatch','--',name).returncode==0
        changed=tracked and git('diff','--quiet','HEAD','--',name).returncode!=0
        if old!=new and (changed or not tracked and old is not None):conflicts.append(name)
        plan.append((name,old,new))
    report={'source':str(SOURCE),'target':str(TARGET),'baseline':BASE,'files':len(plan),'conflicts':conflicts,'applied':False}
    out=SOURCE/'reports/trajectory-integration/sync-results.json'
    out.parent.mkdir(parents=True,exist_ok=True)
    if conflicts:
        out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        raise RuntimeError('No writes: conflicting files '+', '.join(conflicts))
    if options.apply:
        backups=SOURCE/'tmp/trajectory-sync-backup';backups.mkdir(parents=True,exist_ok=True)
        written=[]
        try:
            for name,old,new in plan:
                target=contained(TARGET,name)
                current=target.read_bytes() if target.exists() else None
                if current!=old:raise RuntimeError('Concurrent target change: '+name)
                if old==new:continue
                if old is not None:
                    backup=contained(backups,name);backup.parent.mkdir(parents=True,exist_ok=True);backup.write_bytes(old)
                target.parent.mkdir(parents=True,exist_ok=True)
                staging=target.with_name(target.name+'.trajectory-'+uuid.uuid4().hex+'.tmp')
                staging.write_bytes(new);os.replace(staging,target);written.append((name,old,new))
            report['applied']=True
        except BaseException:
            for name,old,new in reversed(written):
                target=contained(TARGET,name)
                if target.read_bytes()!=new:continue
                if old is None:target.unlink()
                else:target.write_bytes(old)
            raise
    report['sha256']={name:digest(new) for name,old,new in plan}
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='sha256'},ensure_ascii=False))
if __name__=='__main__':main()
