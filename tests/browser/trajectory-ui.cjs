/* Real integrated pages and HTTP projection; fixture facts are marked synthetic. */
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const playwrightModule=process.env.PYHARNESS_PLAYWRIGHT_MODULE||path.join(require('node:os').homedir(),'.cache','codex-runtimes','codex-primary-runtime','dependencies','node','node_modules','playwright');
const {chromium}=require(playwrightModule);
const meta=JSON.parse(fs.readFileSync('tmp/trajectory-ui.json','utf8'));
if(process.argv.includes('--diagnose')){fetch('http://127.0.0.1:'+meta.port+'/api/v1/sessions/'+meta.scenarios.approval.sid+'/trajectory',{headers:{'X-PyHarness-Token':meta.token}}).then(r=>r.json()).then(d=>{console.log(JSON.stringify({status:d.status,trace:d.trace.map(n=>({type:n.type,name:n.name,status:n.status,events:n.event_seqs}))}));});}else
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 globalThis.traceQaBrowser=browser;
 const context=await browser.newContext({viewport:{width:1600,height:1000},extraHTTPHeaders:{'X-PyHarness-Token':meta.token}});
 await context.addInitScript(()=>{const Native=window.EventSource;window.__traceStreams=[];window.EventSource=class extends Native{constructor(...args){super(...args);this.__closed=false;window.__traceStreams.push(this);}close(){this.__closed=true;return super.close();}};});
 const page=await context.newPage(),errors=[],checks=[],requests=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>requests.push({method:r.method(),url:r.url()}));
 globalThis.traceQaPage=page;
 const out=path.resolve('reports/trajectory-integration/screenshots');fs.mkdirSync(out,{recursive:true});
 const check=(name,value)=>{assert.ok(value,name);checks.push(name);};
 const base='http://127.0.0.1:'+meta.port;
 const active=()=>page.evaluate(()=>window.__traceStreams.filter(s=>!s.__closed&&s.url.includes('/api/stream')).length);
 const shot=name=>page.screenshot({path:path.join(out,name+'.png'),fullPage:true});
 await page.goto(base+'/#runs');
 await page.locator('[data-action="run-detail"][data-id="'+meta.scenarios.normal.run_id+'"]').first().click();
 await page.waitForSelector('.trace-node');
 await page.waitForFunction(()=>document.querySelector('[data-trace-notice]')?.textContent.includes('已连接'));
 await page.waitForTimeout(350); // Let the initial SSE cursor catch-up settle before dragging.
 check('task center opens built-in real API viewer',await page.locator('.trace-overlay .trace-node').count()===4);
 check('private model body not exposed by public viewer',!(await page.locator('.trace-overlay').textContent()).includes('SYNTHETIC_PRIVATE_MODEL_BODY'));
 check('explicit model-tool edge',await page.locator('.trace-overlay .trace-edge').count()===1);
 await shot('01-integrated-task');
 await page.locator('[data-trace-node="'+meta.scenarios.normal.sid+'~7"]').first().count();
 const tool=page.locator('.trace-node').filter({hasText:'fs.read_file'});await tool.click();
 check('node details show public tool result',await page.locator('[data-trace-details]').textContent().then(s=>s.includes('已读取示例文件')));
 const before=await tool.getAttribute('transform'),box=await tool.boundingBox();await page.mouse.move(box.x+35,box.y+20);await page.mouse.down();await page.mouse.move(box.x+65,box.y+35,{steps:5});await page.mouse.up();
 check('node can be repositioned',before!==await tool.getAttribute('transform'));
 await page.locator('[data-trace-view="waterfall"]').click();check('waterfall shows measured calls',await page.locator('.trace-wf-bar').count()>0);check('missing duration stays explicitly unknown',await page.locator('.trace-wf-unknown').count()>0);await shot('02-integrated-waterfall');
 const postCount=requests.filter(r=>r.method==='POST').length;
 await page.locator('[data-trace-play]').click();await page.waitForTimeout(200);await page.locator('[data-trace-play]').click();await page.locator('[data-trace-scrub]').fill('0');
 check('replay never re-executes operations',requests.filter(r=>r.method==='POST').length===postCount);
 await page.locator('[data-trace-artifact]').first().click();const preview=page.locator('.settings-content pre, #dialog[open] pre');await preview.waitFor();check('related artifact opens existing integrity-checked preview',(await preview.textContent()).includes('有关任务产物内容'));
 check('artifact drill-down closes trace event subscription',await active()===0);
 await page.locator('[data-action="panel-back"]').click();
 await page.locator('[data-action="run-detail"][data-id="'+meta.scenarios.failure.run_id+'"]').first().click();await page.waitForSelector('.trace-node.failed');
 check('tool failure and run failure remain visible',(await page.locator('[data-trace-run-status]').textContent()).includes('失败'));await shot('03-integrated-failure');
 await page.locator('.trace-overlay-close').click();
 // Existing classic Agent trajectory entry, same component, session-wide scope.
 await page.goto(base+'/classic');await page.locator('.sess[title="'+meta.scenarios.approval.sid+'"]').click();await page.locator('#open-agent-trajectory').click();await page.waitForSelector('.trace-node.waiting_approval');
 check('classic Agent timeline opens session scope',await page.locator('[data-trace-id]').textContent()===meta.scenarios.approval.sid);
 check('approval points to existing approval interface',await page.locator('[data-trace-approval]').isVisible());await shot('04-classic-approval');
 const selected=await page.locator('.trace-node.selected').getAttribute('data-trace-node');
 await context.setOffline(true);await page.waitForTimeout(700);
 const response=await context.request.post(base+'/__trace_test__/approval-result');check('synthetic new persistent result accepted',response.ok());
 await context.setOffline(false);
 await page.waitForFunction(()=>[...document.querySelectorAll('.trace-node')].some(n=>n.textContent.includes('fs.write_file')&&n.classList.contains('completed')),{},{timeout:20000});
 const caughtUp=JSON.parse(await page.locator('[data-trace-json]').textContent());
 // A synthetic grant without an execution owner can also log a public error.
 // That distinct event must remain visible; only the same call must be merged.
 check('disconnect catch-up merges late result into same node',caughtUp.trace.filter(n=>n.type==='tool.call').length===1&&caughtUp.trace.find(n=>n.type==='tool.call').span_id===selected&&new Set(caughtUp.trace.map(n=>n.span_id)).size===caughtUp.trace.length);
 check('live update retains selected node',await page.locator('.trace-node.selected').getAttribute('data-trace-node')===selected);
 await page.locator('.trace-overlay-close').click();
 const baseline=await active();
 await page.locator('.sess[title="'+meta.scenarios.parallel.sid+'"]').click();await page.locator('#open-agent-trajectory').click();await page.waitForSelector('.trace-node');
 const sub=page.locator('.trace-node').filter({hasText:'子任务'}).first();await sub.click();
 await page.locator('[data-trace-child]').click();await page.waitForSelector('.trace-child-pane .run-trace');
 check('child expansion uses a different authenticated session',await page.locator('.trace-child-pane [data-trace-id]').textContent()===meta.scenarios.parallel.children[0]);
 await page.locator('.trace-overlay-body > .run-trace [data-trace-child]').first().click();check('child collapse removes child subscription',await page.locator('.trace-child-pane').count()===0);
 await page.locator('[data-trace-view="waterfall"]').click();check('parallel children have separate time lanes',await page.locator('.trace-lane').count()===3);await shot('05-parallel-children');
 await page.locator('.trace-overlay-close').click();check('parent close restores native classic subscription count',await active()===baseline);
 // Session switching must close the old observer immediately.
 await page.locator('#open-agent-trajectory').click();await page.waitForSelector('.run-trace');
 await page.evaluate(sid=>selectSession(sid),meta.scenarios.normal.sid);check('session switch removes stale observer',await page.locator('.trace-overlay').count()===0);
 await page.goto(base+'/#workflows');await page.locator('[data-action="workflow-trajectory"][data-id*="'+meta.scenarios.workflow.name+'"]').click();await page.waitForSelector('.trace-node');
 check('workflow scope shows actual failure',(await page.locator('[data-trace-run-status]').textContent()).includes('失败'));
 await page.locator('.trace-node').filter({hasText:'步骤 2：'}).first().click();
 check('workflow node has explicit run link',await page.locator('[data-trace-linked-run]').isVisible());await shot('06-workflow-scope');
 await page.locator('[data-trace-linked-run]').click();await page.waitForFunction(()=>document.querySelector('[data-trace-id]')?.textContent.endsWith('~wf-task-1'));
 check('workflow drill-down selects correct task',await page.locator('[data-trace-id]').textContent()===meta.scenarios.workflow.sid+'~wf-task-1');
 await page.locator('.trace-overlay-close').click();
 await page.locator('[data-action="workflow-trajectory"][data-id*="'+meta.scenarios.legacy.name+'"]').click();await page.waitForSelector('.trace-node');await page.locator('.trace-node').first().click();
 check('legacy workflow missing task link is shown as unknown',await page.locator('[data-trace-linked-run]').count()===0&&(await page.locator('[data-trace-details]').textContent()).includes('历史记录未关联任务'));await shot('08-legacy-workflow');await page.locator('.trace-overlay-close').click();
 await page.goto(base+'/classic');await page.locator('.sess[title="'+meta.scenarios.long.sid+'"]').click();await page.locator('#open-agent-trajectory').click();await page.waitForSelector('.trace-node');
 for(let i=0;i<5;i++){const more=page.locator('[data-trace-more]');if(!await more.isVisible())break;await more.click();await page.waitForFunction(()=>!document.querySelector('[data-trace-more]')?.disabled);}
 const raw=JSON.parse(await page.locator('[data-trace-json]').textContent());
 check('long history loads more than 500 without duplicate operation ids',raw.trace.length===520&&new Set(raw.trace.map(n=>n.span_id)).size===520);
 check('graph pagination is bounded',await page.locator('.trace-node').count()===40);
 await page.locator('[data-trace-page="next"]').click();check('graph next group navigates',await page.locator('[data-trace-page-info]').textContent().then(s=>s.startsWith('41')));
 await page.locator('[data-trace-motion]').click();check('motion can be disabled',await page.locator('.run-trace').getAttribute('class').then(s=>s.includes('trace-static')));
 await page.setViewportSize({width:390,height:844});check('mobile page does not overflow horizontally',await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await shot('07-mobile-integrated');
 if(errors.length)console.error(JSON.stringify({uncaught:errors}));
 check('integrated pages have no uncaught JavaScript errors',errors.length===0);
 fs.writeFileSync('reports/trajectory-integration/browser-results.json',JSON.stringify({mode:'Real integrated PyHarness pages, real HTTP and logs, explicitly synthetic fixture facts; no real provider',timestamp:new Date().toISOString(),checks:checks.length,passed:checks,errors,screenshots:fs.readdirSync(out).filter(name=>/^\d\d-/.test(name))},null,2));
 console.log(JSON.stringify({checks:checks.length,errors}));await browser.close();
})().catch(async e=>{if(globalThis.traceQaPage)await globalThis.traceQaPage.screenshot({path:'reports/trajectory-integration/screenshots/qa-failure.png',fullPage:true}).catch(()=>{});if(globalThis.traceQaBrowser)await globalThis.traceQaBrowser.close();console.error(e);process.exit(1);});
