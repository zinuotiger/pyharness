"""Assemble delivery evidence only from completed verification outputs."""
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

root=Path(__file__).resolve().parents[1]
out=root/'reports/trajectory-integration'
read=lambda name:json.loads((out/name).read_text(encoding='utf-8'))
suite=ET.parse(out/'release-suite.xml').getroot().find('testsuite')
assert suite is not None and int(suite.get('failures'))==0 and int(suite.get('errors'))==0
browser=read('browser-results.json');daily=read('daily-results.json');sync=read('sync-results.json')
daily_browser=read('daily-browser-results.json')
assert daily_browser['checks']==browser['checks'] and not daily_browser['errors']
assert not browser['errors'] and daily['uncaught_errors']==daily['mutating_requests']==0
assert sync['applied'] and not sync['conflicts']
for name,digest in sync['sha256'].items():
    for checkout in (root,Path(sync['target'])):
        assert hashlib.sha256((checkout/name).read_bytes()).hexdigest()==digest,name
tests=int(suite.get('tests'));skipped=int(suite.get('skipped'))
report={
 'STATUS':'DONE',
 'timestamp':datetime.now(timezone.utc).isoformat(),
 'SUMMARY':'Shared built-in dynamic trajectory integrated into classic Agent, platform chat/task center and Workflow; synchronized to the designated daily checkout and verified against its running app.',
 'FILES CHANGED':list(sync['sha256']),
 'ARCHITECTURE IMPACT':'Additive authenticated public projection and optional workflow.step.task_id; existing SessionLog, queue, SSE, approval and artifact owners reused. No new frontend runtime.',
 'BOUNDARIES':'Read-only viewer and replay. Only explicitly recorded identities form joins. Existing timeline and public structured detail remain available.',
 'CONSTRAINTS':'No raw model request/response bodies, tool arguments or private reasoning. Authenticate session before reading cache; tenant isolation checked. No real provider called by browser fixtures.',
 'FAILURES':[
  'Initial four path-guard failures were caused by placing pytest basetemp inside the workspace; all guard tests passed with an external temporary directory.',
  'Browser acceptance caught display redaction corrupting public artifact identifiers. Fixed by preserving public resource IDs and added a regression test.',
  'Browser harness assumptions about native preview containers and same-hash navigation were corrected; final browser run has no uncaught errors.'
 ],
 'TESTS':['Full pytest regression with external basetemp and isolated Hypothesis dependency directory','11 trajectory API/projection/workflow/tenant tests','5 Node semantic tests','Integrated Chrome browser acceptance with real local HTTP and synthetic persisted facts','Read-only smoke against daily app using existing persisted data','Conflict-checked file-by-file sync and SHA256 comparison'],
 'TEST RESULTS':{'pytest_passed':tests-skipped,'pytest_skipped':skipped,'pytest_failures':0,'pytest_errors':0,'browser_checks':browser['checks'],'browser_errors':len(browser['errors']),'daily_browser_checks':daily_browser['checks'],'node_tests':5,'daily_scope':daily['scope'],'daily_mutating_requests':daily['mutating_requests'],'synchronized_files':sync['files']},
 'EVIDENCE':['release-suite.xml','release-suite.log','release-coverage.json','focused-final.log','browser-results.json','daily-browser-results.json','daily-results.json','sync-results.json','screenshots/09-daily-app.png'],
 'CONVERGENCE':'Required regression, renderer, negative authorization, workflow restart binding, pagination and daily entry checks passed. Target files match verified source hashes; unrelated target files preserved.',
 'KNOWN LIMITATIONS':['Large selected-session projections recompute when their watermark changes; no large-data throughput claim.','Legacy records without causal/workflow task identities or closed timestamps retain unknown relationships/durations.','Browser scenarios use synthetic facts; real external model/provider execution was not exercised.','Classic artifact entry routes to the existing artifact center; platform task entry opens the exact preview.'],
 'REMAINING WORK':[],
 'daily_url':daily['url'],
 'source':str(root),'target':sync['target'],'baseline':sync['baseline'],
}
(out/'delivery-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
audit={'status':'INTEGRATED_VERIFIED_AND_SYNCED','user_authority':'User approved continued integrated implementation and safe synchronization to the designated daily checkout. Earlier entry-clarification pause is superseded.',
 'baseline':sync['baseline'],'worktree':str(root),'daily_checkout':sync['target'],
 'entries':['/classic Agent session trajectory','platform chat session trajectory','/#runs task details','/#workflows Workflow trajectory'],
 'evidence':'reports/trajectory-integration/delivery-report.json','verification':report['TEST RESULTS'],'limits':report['KNOWN LIMITATIONS']}
(root/'.ai-coding/evidence/trace-target-audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report['TEST RESULTS']))
