/* Read-only smoke against the designated daily app. Never logs credentials or content. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PYHARNESS_PLAYWRIGHT_MODULE||require('node:path').join(require('node:os').homedir(),'.cache','codex-runtimes','codex-primary-runtime','dependencies','node','node_modules','playwright'));
const base='http://127.0.0.1:60460';
const dataRoot='D:/zinuotiger-workspace/temporary-runtimes/pyharness-ui-20261003-manual/data';
(async()=>{
 const token=fs.readFileSync(path.join(dataRoot,'web.token'),'utf8').trim();
 const get=async url=>{const r=await fetch(base+url,{headers:{'X-PyHarness-Token':token}});assert.equal(r.status,200,url);return r.json();};
 const sessions=await get('/api/v1/sessions'),runs=await get('/api/v1/runs');
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const context=await browser.newContext({viewport:{width:1600,height:1000},extraHTTPHeaders:{'X-PyHarness-Token':token}});
  const page=await context.newPage(),errors=[],writes=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('request',r=>{if(!['GET','HEAD','OPTIONS'].includes(r.method()))writes.push(r.method());});
  await page.goto(base+'/#runs');await page.waitForFunction(()=>!!window.PyharnessRunTrace);
  let scope='empty',nodes=0;
  const run=runs.runs.find(r=>['completed','failed','cancelled'].includes(r.status))||runs.runs[0];
  if(run){
   const data=await get('/api/v1/sessions/'+encodeURIComponent(run.session_id)+'/trajectory?run_id='+encodeURIComponent(run.run_id));
   scope='existing persistent task';nodes=data.trace.length;
   await page.locator('[data-action="run-detail"][data-id="'+run.run_id+'"]').first().click();
   await page.waitForSelector('.trace-overlay .run-trace');
   assert.equal(await page.locator('[data-trace-id]').textContent(),run.run_id);
  }else if(sessions.sessions.length){
   const sid=sessions.sessions[0].sid,data=await get('/api/v1/sessions/'+encodeURIComponent(sid)+'/trajectory');
   scope='existing persistent session';nodes=data.trace.length;
   await page.goto(base+'/classic');await page.locator('.sess[title="'+sid+'"]').click();await page.locator('#open-agent-trajectory').click();
   await page.waitForSelector('.trace-overlay .run-trace');
  }
  const screenshot=path.resolve('reports/trajectory-integration/screenshots/09-daily-app.png');
  await page.screenshot({path:screenshot,fullPage:true});
  assert.deepEqual(errors,[]);assert.deepEqual(writes,[]);
  const report={timestamp:new Date().toISOString(),url:base,checkout:'D:/zinuotiger-workspace/feature-worktrees/pyharness-platform-web-v1',scope,visible_source_nodes:nodes,session_count:sessions.sessions.length,run_count:runs.runs.length,uncaught_errors:errors.length,mutating_requests:writes.length,screenshot};
  fs.writeFileSync('reports/trajectory-integration/daily-results.json',JSON.stringify(report,null,2));
  console.log(JSON.stringify(report));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
