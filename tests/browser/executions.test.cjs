const {test}=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const canonical=v=>JSON.stringify(v,(_,x)=>x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.entries(x).sort(([a],[b])=>a<b?-1:a>b?1:0)):x);
for(const scenario of ['start','double-click','lost-reply-query','reload-retry','cancel-lost-reply','source-withdrawn','identity-change','late-close','late-preview','safe-text','host-confirm','selection-change','tampered-hint','query-not-found']) {
 test('execution dialog '+scenario,async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});let release;const gate=new Promise(r=>release=r);
  try {
   const page=await browser.newPage();page.setDefaultTimeout(8000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
   const project={project_id:'p-'+'1'.repeat(16),name:'Synthetic execution'};
   const attempt={attempt_id:'ra-'+'2'.repeat(16),revision:1,digest:'a'.repeat(64),source_seq:7};
   const agent={agent_id:'a-'+'3'.repeat(32),version:1,sha256:'b'.repeat(64)};
   const state={realm:'A',posts:[],execution:null,cancellation:null,deny:false,entered:false};
   const fail=(status,message)=>({__error:{status,message}});
   const digest=(op,body)=>crypto.createHash('sha256').update(canonical({operation:op,project_id:project.project_id,command:body})).digest('hex');
   await page.exposeFunction('mockApi',async(path,options={})=>{
    if(path==='/api/account/me')return {account:{tenant:'synthetic',user_id:state.realm}};
    if(path==='/api/v1/agents')return {agents:[{agent_id:agent.agent_id,status:'published'}]};
    if(path.endsWith('/versions'))return {versions:[{...agent,snapshot:{status:'published',name:'<img src=x onerror=window.syntheticXss=1>'}},
     {...agent,version:2,sha256:'d'.repeat(64),snapshot:{status:'published',name:'Second'}}]};
    if(path.endsWith('/execution-control'))return {schema_version:1,control_only:true,project_id:project.project_id,attempt_ref:attempt,
     status:state.execution?'authorized':'not_authorized',execution_ref:state.execution?Object.fromEntries(['execution_id','revision','digest','source_seq'].map(k=>[k,state.execution[k]])):null,cancellation:state.cancellation};
    if(path.endsWith('/execution-previews')){
     const body=JSON.parse(options.body);
     if(scenario==='late-preview'){state.entered=true;await gate;}
     return {schema_version:1,project_id:project.project_id,...body,boundary:scenario==='host-confirm'?'host_unrestricted':'disabled',confirmation_digest:'c'.repeat(64),status:'confirmation_required'};
    }
    if(options.method==='POST'&&path.endsWith('/executions')) {
     const body=JSON.parse(options.body),key=options.headers['Idempotency-Key'];state.posts.push({path,body,key});
     state.execution={schema_version:1,project_id:project.project_id,submission_key:key,request_digest:digest('execution.start',body),
      execution_id:body.execution_id,revision:1,digest:'e'.repeat(64),source_seq:10,attempt_ref:body.attempt_ref,agent_ref:body.agent_ref,persisted:true,execution_authorized:true};
     if(scenario==='late-close'){state.entered=true;await gate;}
     if(['lost-reply-query','reload-retry','tampered-hint','query-not-found','identity-change'].includes(scenario)&&state.posts.length===1)return fail(503,'synthetic reply lost');
     return state.execution;
    }
    if(options.method==='POST'&&path.endsWith('/execution-cancellations')) {
     const body=JSON.parse(options.body),key=options.headers['Idempotency-Key'];state.posts.push({path,body,key});
     state.cancellation={...body,project_id:project.project_id,revision:1,source_seq:12,digest:'f'.repeat(64),submission_key:key,request_digest:digest('execution.cancel',body),persisted:true,execution_stopped:false};
     if(scenario==='cancel-lost-reply')return fail(503,'synthetic stop reply lost');return state.cancellation;
    }
    if(path.includes('/execution-submissions/by-key?'))return {schema_version:1,project_id:project.project_id,submission_key:new URL('http://local'+path).searchParams.get('key'),status:scenario==='query-not-found'?'not_found':'committed',execution:scenario==='query-not-found'?null:state.execution};
    if(path.includes('/cancellation-submissions/by-key?'))return {schema_version:1,project_id:project.project_id,submission_key:new URL('http://local'+path).searchParams.get('key'),status:'committed',cancellation:state.cancellation};
    if(path.includes('/executions/')) {
     if(state.deny)return fail(403,'synthetic source withdrawn');
     return {...state.execution,status:'completed',execution_reason:'finished',unknown_effects:true,result_summary:'<img src=x onerror=window.syntheticXss=1>',business_acceptance:'not_evaluated',artifacts:[{filename:'<script>window.syntheticXss=1</script>',integrity:'mismatch',download_allowed:false}]};
    }
    throw Error('unexpected synthetic API '+path);
   });
   await page.route('http://127.0.0.1:8765/**',r=>r.fulfill({contentType:'text/html',body:'<main></main>'}));
   async function install(reload=false) {
    if(reload)await page.reload();else await page.goto('http://127.0.0.1:8765/');
    for(const name of ['workspace-input-contracts','executions'])await page.addScriptTag({path:'pyharness/ui/host-adapters/'+name+'.js'});
    await page.evaluate(async({project,attempt})=>{
     const api=async(path,options={})=>{const value=await mockApi(path,options);if(value?.__error)throw Object.assign(Error(value.__error.message),value.__error);return value;};
     window.project=project;window.attempt=attempt;window.ctrl=PyharnessExecutions.create({main:document.querySelector('main'),api,esc:s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))});
     await ctrl.open(project,attempt.attempt_id);
    },{project,attempt});
   }
   const action=name=>page.locator('[data-ex-action='+name+']');
   const status=()=>page.locator('[data-ex-status]').textContent();
   async function preview(){await page.locator('[data-ex-agent]').selectOption('0');await action('preview').click();await page.waitForFunction(()=>document.querySelector('[data-ex-status]').textContent.includes('预览完成'));}
   await install();assert.equal(state.posts.length,0);await page.locator('[data-ex-agent]').selectOption('0');
   if(scenario==='late-preview') {
    const click=action('preview').click();await click;
    await page.waitForFunction(async()=>document.querySelector('[data-ex-action=preview]').disabled);
    await page.keyboard.press('Escape');await page.evaluate(()=>ctrl.open(project,attempt.attempt_id));release();
    await page.waitForFunction(()=>document.querySelector('[data-ex-agent] option[value="0"]'));
    assert.equal(await page.locator('[data-ex-preview]').textContent(),'');assert.equal(state.posts.length,0);
   }else {
    await preview();
    if(scenario==='selection-change') {
     await page.locator('[data-ex-agent]').selectOption('1');assert(await action('start').isDisabled());assert.equal(state.posts.length,0);
    }else {
     if(scenario==='host-confirm') {
      await action('start').click();assert((await status()).includes('明确勾选'));assert.equal(state.posts.length,0);await page.locator('[data-ex-host]').check();
     }
     if(scenario==='double-click')await page.evaluate(()=>{const b=document.querySelector('[data-ex-action=start]');b.click();b.click();});else await action('start').click();
     if(scenario==='late-close') {
      await page.waitForFunction(()=>document.querySelector('[data-ex-hints]').textContent.includes('开始待核对键'));
      await page.keyboard.press('Escape');await page.evaluate(()=>ctrl.open(project,attempt.attempt_id));release();
      await page.waitForFunction(()=>document.querySelector('[data-ex-summary]').textContent.includes('completed'));
      assert.equal(await page.locator('[data-ex-preview]').textContent(),'');assert.equal(state.posts.length,1);
     }else if(['lost-reply-query','reload-retry','tampered-hint','query-not-found','identity-change'].includes(scenario)) {
      await page.waitForFunction(()=>document.querySelector('[data-ex-status]').textContent.includes('操作未确认'));
      const hints=await page.evaluate(()=>Object.keys(localStorage).filter(k=>k.startsWith('pyharness.execution.pending.v1:')).map(k=>[k,localStorage.getItem(k)]));
      assert.equal(hints.length,1);assert(!hints[0][1].includes('result_summary'));assert(!hints[0][1].includes('<img'));
      if(scenario==='tampered-hint'){await page.evaluate(([key,raw])=>{const r=JSON.parse(raw);r.digest='0'.repeat(64);localStorage.setItem(key,JSON.stringify(r));},hints[0]);}
      if(['reload-retry','tampered-hint'].includes(scenario))await install(true);
      if(scenario==='identity-change')state.realm='B';
      await action(scenario==='reload-retry'||scenario==='tampered-hint'?'retry-start':'query-start').click();
      if(scenario==='identity-change'){await page.waitForFunction(()=>document.querySelectorAll('dialog').length===0);assert.equal(state.posts.length,1);}
      else if(scenario==='tampered-hint'){await page.waitForFunction(()=>document.querySelector('[data-ex-status]').textContent.includes('摘要不符'));assert.equal(state.posts.length,1);}
      else if(scenario==='query-not-found'){await page.waitForFunction(()=>document.querySelector('[data-ex-status]').textContent.includes('尚未找到'));assert.equal(state.posts.length,1);assert(await action('start').isDisabled());}
      else {await page.waitForFunction(()=>document.querySelector('[data-ex-summary]').textContent.includes('completed'));assert.equal(state.posts.length,scenario==='reload-retry'?2:1);if(state.posts.length===2)assert.deepEqual(state.posts[0],state.posts[1]);}
     }else {
      await page.waitForFunction(()=>document.querySelector('[data-ex-summary]').textContent.includes('completed'));assert.equal(state.posts.length,1);
      if(scenario==='source-withdrawn') {
       state.deny=true;await action('refresh').click();await page.waitForFunction(()=>document.querySelector('[data-ex-status]').textContent.includes('不可读取'));
       assert.equal(await page.locator('[data-ex-result]').textContent(),'');assert.equal(await page.locator('[data-ex-summary]').textContent(),'');assert.equal(await action('stop').isDisabled(),false);
      }
      if(['source-withdrawn','cancel-lost-reply'].includes(scenario)) {
       await action('stop').click();
       if(scenario==='cancel-lost-reply'){await page.waitForFunction(()=>document.querySelector('[data-ex-status]').textContent.includes('操作未确认'));await install(true);assert.equal(state.posts.length,2);await action('query-cancel').click();}
       await page.waitForFunction(()=>document.querySelector('[data-ex-stop]').textContent.includes('尚未停止'));assert.equal(state.posts.length,2);
       assert(await action('stop').isDisabled());assert(!(await page.locator('[data-ex-stop]').textContent()).includes('实际执行已停'));
      }
      if(scenario==='safe-text'){assert.equal(await page.evaluate(()=>window.syntheticXss),undefined);assert.equal(await page.locator('dialog img,dialog script').count(),0);assert((await page.locator('[data-ex-result]').textContent()).includes('<img'));}
     }
    }
   }
   assert.deepEqual(errors,[]);
  }finally{release();await browser.close();}
 });
}
