const {test}=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fixture=require('./fixtures/owned-inputs.json');
const canonical=v=>JSON.stringify(v&&typeof v==='object'?(Array.isArray(v)?v.map(x=>JSON.parse(canonical(x))):Object.fromEntries(Object.keys(v).sort().map(k=>[k,JSON.parse(canonical(v[k]))]))):v);
for(const scenario of ['submit','double-click','unknown-retry','reload-query','reload-retry','known-reject','identity-change','late-close','late-inspect','safe-text','refresh-denied','tampered-hint']){
 test(`task inbox ${scenario}`,async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  let release;const gate=new Promise(r=>release=r);
  try{
   const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(7000);
   const project={project_id:fixture.input.project_id,revision:1,name:'Synthetic inbox'};
   const task={schema_version:2,product_task_id:'pt-0123456789abcdef',revision:1,digest:'a'.repeat(64),source_seq:6,
     title:scenario==='safe-text'?'<img src=x onerror=window.syntheticXss=1>':'Synthetic task',status:'definition_ready',input_binding:{manifest_ref:fixture.input.manifest_ref,input_ref:Object.fromEntries(['input_id','revision','digest','source_seq'].map(k=>[k,fixture.input[k]]))}};
   const state={posts:[],attempt:null,realm:'A',entered:false,denyList:false};
   const fail=(status,message)=>({__error:{status,message}});
   await page.exposeFunction('mockState',()=>({entered:state.entered,posts:state.posts.length}));
   await page.exposeFunction('mockApi',async(url,options={})=>{
    if(url==='/api/account/me')return {account:{tenant:'synthetic',user_id:state.realm}};
    if(url.endsWith('/product-tasks'))return {tasks:[task,{...task,product_task_id:'pt-fedcba9876543210',status:'draft'}],total:2};
    for(const [path,key] of [['documents','documents'],['citations','citations'],['delivery-definitions','deliveries']])if(url.includes('/'+path+'?'))return {[key]:[],next_cursor:null};
    if(options.method==='POST'){
     state.posts.push({body:options.body,key:options.headers['Idempotency-Key']});
     if(scenario==='known-reject')return fail(403,'synthetic rejected');
     const selection=JSON.parse(options.body);
     state.attempt={schema_version:1,attempt_id:selection.attempt_id,revision:1,project_id:project.project_id,project_revision:1,
      selection,context:{schema_version:1,document_refs:[],citation_refs:[],delivery_ref:null},prerequisites:{task_ref:selection.task_ref,input_binding:task.input_binding},
      binding_digest:'b'.repeat(64),digest:'c'.repeat(64),source_seq:99,created_at:'2026-10-18T00:00:00Z',submission_key:options.headers['Idempotency-Key'],
      request_digest:crypto.createHash('sha256').update(canonical({operation:'run_attempt.submit',project_id:project.project_id,command:selection})).digest('hex'),
      status:'reserved',persisted:true,attempt_reserved:true,execution_authorized:false,execution_started:false,dispatch_policy:'explicit_governed_start',input_readiness:'current',stale_reasons:[]};
     if(['unknown-retry','reload-query','reload-retry','identity-change','tampered-hint'].includes(scenario)&&state.posts.length===1)return fail(503,'synthetic response lost');
     if(scenario==='late-close'){state.entered=true;await gate;}
     return state.attempt;
    }
    if(url.includes('/run-submissions/by-key?')){
     const key=new URL('http://127.0.0.1'+url).searchParams.get('key'),found=state.attempt?.submission_key===key;
     return {schema_version:1,project_id:project.project_id,submission_key:key,status:found?'committed':'not_found',attempt:found?state.attempt:null,writer_requires_reopen:false,execution_authorized:false};
    }
    if(url.includes('/run-attempts?')){
     if(state.denyList)return fail(403,'synthetic role withdrawn');
     return {attempts:state.attempt?[state.attempt]:[],next_cursor:null,total:state.attempt?1:0};
    }
    if(url.includes('/run-attempts/')){
     if(scenario==='late-inspect'){state.entered=true;await gate;return fail(503,'synthetic late failure');}
     return state.attempt;
    }
    throw Error('unexpected synthetic API '+url);
   });
   await page.route('http://127.0.0.1:8765/**',r=>r.fulfill({body:'<main></main>',contentType:'text/html'}));
   async function install(reload=false){
    if(reload)await page.reload();else await page.goto('http://127.0.0.1:8765/');
    for(const path of ['workspace-input-contracts','task-inbox'])await page.addScriptTag({path:'pyharness/ui/host-adapters/'+path+'.js'});
    await page.evaluate(project=>{
     const api=async(url,options={})=>{const value=await window.mockApi(url,options);if(value?.__error)throw Object.assign(Error(value.__error.message),value.__error);return value;};
     window.project=project;window.ctrl=window.PyharnessTaskInbox.create({main:document.querySelector('main'),api,esc:s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))});
     return ctrl.open(project,null);
    },project);
    await page.waitForFunction(()=>/记录已核对|待核对/.test(document.querySelector('[data-inbox-status]').textContent));
   }
   await install();let dialog=page.getByRole('dialog');
   await dialog.getByText('提交新尝试',{exact:true}).click();await dialog.locator('[name=task]').selectOption(task.product_task_id);
   if(scenario==='double-click')await page.evaluate(()=>{const f=document.querySelector('#inboxSubmitForm');f.requestSubmit();f.requestSubmit();});
   else await dialog.getByRole('button',{name:'提交运行尝试',exact:true}).click();
   const status=()=>dialog.locator('[data-inbox-status]');
   if(scenario==='late-close'){
    await page.waitForFunction(async()=> (await mockState()).entered);await page.keyboard.press('Escape');await page.evaluate(()=>ctrl.open(project,null));
    await page.waitForFunction(()=>document.querySelector('[data-inbox-status]').textContent.includes('待核对'));release();
    await page.waitForFunction(()=>!document.querySelector('[data-inbox-action=query]').disabled);
    assert.equal(await dialog.locator('[data-inbox-result]').textContent(),'');assert.equal(state.posts.length,1);
   }else if(['unknown-retry','reload-query','reload-retry','identity-change','tampered-hint'].includes(scenario)){
    await page.waitForFunction(()=>document.querySelector('[data-inbox-status]').textContent.includes('结果未确认'));
    const stored=await page.evaluate(()=>Object.keys(localStorage).filter(k=>k.startsWith('pyharness.inbox.pending.v1:')).map(k=>[k,localStorage.getItem(k)]));
    assert.equal(stored.length,1);assert(!stored[0][1].includes('objective'));assert(!stored[0][1].includes('Synthetic task'));
    if(scenario.startsWith('reload-')){await install(true);dialog=page.getByRole('dialog');assert.equal(state.posts.length,1);}
    if(scenario==='identity-change')state.realm='B';
    if(scenario==='tampered-hint'){
     await page.evaluate(([key,raw])=>{const hint=JSON.parse(raw);hint.digest='0'.repeat(64);localStorage.setItem(key,JSON.stringify(hint));},stored[0]);
     await install(true);dialog=page.getByRole('dialog');
    }
    await dialog.locator(`[data-inbox-action=${scenario==='reload-query'?'query':'retry'}]`).click();
    if(scenario==='identity-change'||scenario==='tampered-hint'){
     await page.waitForFunction(()=>/主体已变化|摘要不符/.test(document.querySelector('[data-inbox-status]').textContent));assert.equal(state.posts.length,1);
    }else{
     await page.waitForFunction(()=>/精确持久确认|原提交的持久记录/.test(document.querySelector('[data-inbox-status]').textContent));
     assert.equal(state.posts.length,scenario==='reload-query'?1:2);if(state.posts.length===2)assert.deepEqual(state.posts[0],state.posts[1]);
     assert.equal(await page.evaluate(()=>Object.keys(localStorage).filter(k=>k.startsWith('pyharness.inbox.pending.v1:')).length),0);
    }
   }else if(scenario==='known-reject'){
    await page.waitForFunction(()=>document.querySelector('[data-inbox-status]').textContent.includes('明确拒绝'));
    assert.equal(await dialog.locator('[name=task]').isDisabled(),false);
   }else{
    await page.waitForFunction(()=>document.querySelector('[data-inbox-status]').textContent.includes('精确持久确认'));
    assert.equal(state.posts.length,1);assert.equal(state.attempt.execution_authorized,false);
    await dialog.locator('[data-inbox-action=refresh]').click();await page.waitForFunction(()=>document.querySelector('[data-inbox-attempts] article'));
    if(scenario==='refresh-denied'){
     state.denyList=true;await dialog.locator('[data-inbox-action=refresh]').click();
     await page.waitForFunction(()=>document.querySelector('[data-inbox-status]').textContent.includes('读取失败'));
     assert.equal(await dialog.locator('[data-inbox-attempts] article').count(),0);assert.equal(await dialog.locator('[data-inbox-result]').textContent(),'');
    }
    if(scenario==='late-inspect'){
     await dialog.locator('[data-inbox-action=inspect]').click();await page.waitForFunction(async()=> (await mockState()).entered);
     await dialog.locator('[data-inbox-action=refresh]').click();await page.waitForFunction(()=>document.querySelector('[data-inbox-status]').textContent.includes('记录已核对'));
     release();await page.waitForTimeout(100);assert(!/late failure/.test(await status().textContent()));assert.equal(await dialog.locator('[data-inbox-result]').textContent(),'');
    }
    if(scenario==='safe-text'){assert.equal(await dialog.locator('img').count(),0);assert.equal(await page.evaluate(()=>window.syntheticXss),undefined);}
   }
   assert.deepEqual(errors,[]);
  }finally{release();await browser.close();}
 });
}
