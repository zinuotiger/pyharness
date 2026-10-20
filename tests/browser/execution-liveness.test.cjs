const {test}=require('node:test'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
for(const scenario of ['approval','expired','inactive','cold','offline','offline-control','offline-identity','service-error','revoked','invalid-observation']) {
 test('execution liveness '+scenario,async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  try {
   const page=await browser.newPage();page.setDefaultTimeout(8000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
   const project={project_id:'p-'+'1'.repeat(16),name:'Synthetic liveness'};
   const attempt={attempt_id:'ra-'+'2'.repeat(16),revision:1,digest:'a'.repeat(64),source_seq:7};
   const ref={execution_id:'ex-'+'3'.repeat(16),revision:1,digest:'b'.repeat(64),source_seq:8};
   let fail=false,posts=0;
   await page.exposeFunction('mockApi',async(path,options={})=>{
    if(options.method==='POST')posts++;
    if(fail&&(scenario==='offline-control'&&path.endsWith('/execution-control')||scenario==='offline-identity'&&path==='/api/account/me'))return {__error:{message:'synthetic whole-service disconnection'}};
    if(path==='/api/account/me')return {account:{tenant:'synthetic',user_id:'A'}};
    if(path.endsWith('/execution-control'))return {schema_version:1,control_only:true,project_id:project.project_id,attempt_ref:attempt,status:'authorized',execution_ref:ref,cancellation:null};
    if(path.endsWith('/executions/'+ref.execution_id)) {
     if(fail)return {__error:{message:'synthetic observation failure',...({'service-error':{status:500},revoked:{status:403}}[scenario]||{})}};
     const c={schema_version:1,service_status:'connected',execution_owner:scenario==='cold'?'absent':'live',activity:scenario==='expired'?'heartbeat_expired':scenario==='inactive'?'heartbeat_unavailable':scenario==='cold'?'interrupted':'waiting_approval',
      heartbeat:{scope:'service_event_loop',state:scenario==='expired'?'expired':scenario==='inactive'?'inactive':scenario==='cold'?'not_observed':'fresh'},approval_waits_total:scenario==='cold'?0:1,approval_coverage:'complete',replay_authorized:scenario==='invalid-observation',automatic_dispatch:false};
     return {...ref,project_id:project.project_id,attempt_ref:attempt,status:scenario==='cold'?'interrupted':'running',business_acceptance:'not_evaluated',unknown_effects:false,result_summary:'Private exact body',artifacts:[],control_observation:c};
    }
    throw Error('unexpected synthetic API '+path);
   });
   await page.route('http://127.0.0.1:8765/**',r=>r.fulfill({contentType:'text/html',body:'<main></main>'}));await page.goto('http://127.0.0.1:8765/');
   for(const name of ['workspace-input-contracts','executions'])await page.addScriptTag({path:'pyharness/ui/host-adapters/'+name+'.js'});
   await page.evaluate(async({project,attempt})=>{
    const api=async(path,options={})=>{const v=await mockApi(path,options);if(v?.__error)throw Object.assign(Error(v.__error.message),v.__error);return v;};
    window.ctrl=PyharnessExecutions.create({main:document.querySelector('main'),api,esc:s=>s});await ctrl.open(project,attempt.attempt_id);
   },{project,attempt});
   if(['offline','offline-control','offline-identity','service-error','revoked'].includes(scenario)) {
    fail=true;await page.locator('[data-ex-action=refresh]').click();await page.waitForFunction(()=>/不可读取|操作未确认/.test(document.querySelector('[data-ex-status]').textContent));
    assert.equal(await page.locator('[data-ex-answer]').textContent(),'');assert.equal(await page.locator('[data-ex-summary]').textContent(),'');
    assert.equal(await page.locator('[data-ex-action=stop]').isDisabled(),scenario==='offline-control');
   }
   const text=await page.locator('[data-ex-health]').textContent();
   const expected={approval:'等待原工具审批',expired:'服务心跳过期',inactive:'心跳监视已停止',cold:'没有这个执行的本地执行者',offline:'服务失联','offline-control':'服务失联','offline-identity':'服务失联', 'service-error':'服务返回错误',revoked:'读取权限不足','invalid-observation':'运行观测暂不可用'}[scenario];
   assert(text.includes(expected),text);assert.equal(posts,0);assert.deepEqual(errors,[]);
   await page.locator('[data-ex-action=close]').click();assert.equal(await page.locator('dialog').count(),0);
  }finally{await browser.close();}
 });
}
