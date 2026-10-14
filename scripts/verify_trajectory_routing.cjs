/* Supplemental real-page clicks; synthetic test server only, no approval decision. */
const fs=require('node:fs'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PYHARNESS_PLAYWRIGHT_MODULE||require('node:path').join(require('node:os').homedir(),'.cache','codex-runtimes','codex-primary-runtime','dependencies','node','node_modules','playwright'));
(async()=>{
 const m=JSON.parse(fs.readFileSync('tmp/trajectory-routing-ui.json','utf8'));
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const c=await browser.newContext({viewport:{width:1600,height:1000},extraHTTPHeaders:{'X-PyHarness-Token':m.token}});
  const p=await c.newPage(),errors=[],writes=[],checks=[];
  p.on('pageerror',e=>errors.push(e.message));p.on('request',r=>{if(!['GET','HEAD','OPTIONS'].includes(r.method()))writes.push({method:r.method(),path:new URL(r.url()).pathname});});
  const base='http://127.0.0.1:'+m.port,sid=m.scenarios.approval.sid;
  await p.goto(base+'/');await p.locator('[data-session="'+sid+'"]').click();
  if(!await p.locator('#runInspector').isVisible())await p.locator('[data-action="toggle-inspector"]').click();
  await p.locator('[data-action="session-trajectory"]').click();await p.waitForSelector('.trace-node.waiting_approval');
  assert.equal(await p.locator('[data-trace-id]').textContent(),sid);checks.push('platform chat inspector opens same-session dynamic trajectory');
  await p.locator('[data-trace-approval]').click();await p.waitForFunction(()=>location.hash==='#approvals'&&!document.querySelector('.trace-overlay'));
  await p.waitForSelector('.settings-content h1');assert.ok((await p.locator('.settings-content').textContent()).includes('审批中心'));checks.push('approval locator navigates to existing platform approval center and closes viewer');
  await p.screenshot({path:'reports/trajectory-integration/screenshots/10-existing-approval-center.png',fullPage:true});
  await p.goto(base+'/classic');await p.locator('.sess[title="'+sid+'"]').click();await p.locator('#open-agent-trajectory').click();await p.waitForSelector('.trace-node.waiting_approval');
  await p.locator('[data-trace-approval]').click();await p.waitForSelector('#modal-bg.show');assert.equal(await p.locator('.trace-overlay').count(),0);checks.push('classic approval locator reveals existing native approval dialog');
  assert.deepEqual(errors,[]);
  // Native chat readiness uses POST for an observational query, and classic
  // initialization creates an empty session. Neither submits work or decides approval.
  const setupPaths=new Set(['/api/v1/chat/execution-readiness','/api/sessions']);
  const operationRequests=writes.filter(r=>!setupPaths.has(r.path));
  assert.deepEqual(operationRequests,[]);checks.push('routing has no uncaught errors, execution submissions or approval decisions');
  const report={timestamp:new Date().toISOString(),mode:'Isolated test data only; real live ApprovalProvider, real integrated pages and HTTP; synthetic tool call with no executor or model',checks:checks.length,passed:checks,errors,execution_or_decision_requests:operationRequests.length,native_initialization_requests:writes};
  fs.writeFileSync('reports/trajectory-integration/routing-results.json',JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
