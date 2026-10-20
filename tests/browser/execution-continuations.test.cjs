const {test}=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const canonical=v=>JSON.stringify(v,(_,x)=>x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.entries(x).sort(([a],[b])=>a<b?-1:a>b?1:0)):x);
const hash=v=>crypto.createHash('sha256').update(canonical(v)).digest('hex');
const publicNumbers=v=>typeof v==='number'?{$number:(()=>{const b=Buffer.alloc(8);b.writeDoubleBE(v);return b.toString('hex');})()}:Array.isArray(v)?v.map(publicNumbers):v&&typeof v==='object'?Object.fromEntries(Object.entries(v).map(([k,x])=>[k,publicNumbers(x)])):v;
for(const scenario of ['submit','double-click','lost-reply-query','reload-retry','blocked','bad-digest','wrong-root','false-authority','host-confirm','identity-change','withdrawal','late-close']) {
 test('explicit continuation '+scenario,async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});let release;const gate=new Promise(r=>release=r);
  try{
   const page=await browser.newPage();page.setDefaultTimeout(8000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
   const project={project_id:'p-'+'1'.repeat(16),name:'Synthetic continuation'},ref={execution_id:'ex-'+'2'.repeat(16),revision:1,digest:'a'.repeat(64),source_seq:9};
   let realm='A',deny=false,entered=false,posts=[],committed=null,reads=0;
   const root={schema_version:1,...ref,project_id:project.project_id,boundary:scenario==='host-confirm'?'host_unrestricted':'container',status:'interrupted'};
   const plan=()=>{const p={schema_version:1,project_id:project.project_id,execution_ref:scenario==='wrong-root'?{...ref,execution_id:'ex-'+'f'.repeat(16)}:ref,
    recovery_plan_digest:'b'.repeat(64),execution_status:'interrupted',journals:[{session_id:'s-pa-'+'6'.repeat(32),observed_seq:20,prefix_digest:'6'.repeat(64)}],lineage_journals:[],journal_writer_active:false,state_available:true,
    state:{session_id:'s-pa-'+'6'.repeat(32),checkpoint_id:'ck-'+'3'.repeat(16),checkpoint_seq:11,checkpoint_digest:'7'.repeat(64),state_source_seq:12,state_digest:'c'.repeat(64),state_size:3000,
     materials_digest:'8'.repeat(64),messages_digest:'9'.repeat(64),filesystem_capture:'files_and_directories',file_count:2,directory_count:1,kv_present:false,integrity:'verified',capture_scope:'session_local_state'},
    budget:{observed_usage:{in_tokens:100,out_tokens:20,cost_est:.001},reserved_unconfirmed:{in_tokens:2000,out_tokens:4096,cost_est:.1},remaining_limits:{max_in_tokens:10000,max_out_tokens:2000,max_cost_yuan:.75,warn_ratio:.8},
     remaining_rounds:4,potential_unconfirmed_attempts:1,tool_calls_used:1,remaining_timeout_seconds:50,basis:'runtime_estimate_and_unconfirmed_request_reservation'},current_model_fingerprint:'d'.repeat(64),
    blockers:scenario==='blocked'?['unknown_effects']:[],can_continue:scenario!=='blocked',continuation_authorized:scenario==='false-authority',automatic_dispatch:false,replay_authorized:false,atomic_snapshot:false};
    return {...p,plan_digest:scenario==='bad-digest'?'0'.repeat(64):hash(publicNumbers(p))};};
   await page.exposeFunction('mockApi',async(url,o={})=>{
    if(url==='/api/account/me')return {account:{tenant:'synthetic',user_id:realm}};
    if(deny)return {__error:{status:403,message:'synthetic source withdrawal'}};
    if(url.endsWith('/continuation-plan')){reads++;if(scenario==='late-close'&&reads===1){entered=true;await gate;}return plan();}
    if(url.endsWith('/continuations')&&o.method==='POST'){
     const body=JSON.parse(o.body),key=o.headers['Idempotency-Key'];posts.push({body,key});
     committed={schema_version:1,execution_id:body.execution_id,revision:1,digest:'e'.repeat(64),source_seq:12,project_id:project.project_id,
      attempt_ref:{attempt_id:body.attempt_id,revision:1,digest:'f'.repeat(64),source_seq:11},session_id:'s-pa-'+'4'.repeat(32),task_id:'p-'+'5'.repeat(32),
      submission_key:key,request_digest:hash({operation:'execution.continue',project_id:project.project_id,command:body}),status:'completed',persisted:true,execution_authorized:true,execution_started:true,
      continuation:{execution_ref:ref,plan_digest:body.plan_digest}};
     if(['lost-reply-query','reload-retry'].includes(scenario)&&posts.length===1)return {__error:{message:'synthetic committed response lost'}};
     return committed;
    }
    if(url.includes('/continuation-submissions/by-key'))return {schema_version:1,project_id:project.project_id,submission_key:decodeURIComponent(url.split('key=')[1]),status:committed?'committed':'not_found',execution:committed,attempt:null,automatic_dispatch:false};
    if(url.endsWith('/executions/'+ref.execution_id))return root;
    if(committed&&url.endsWith('/executions/'+committed.execution_id))return committed;
    throw Error('unexpected synthetic continuation API '+url);
   });
   await page.route('http://127.0.0.1:8765/**',r=>r.fulfill({contentType:'text/html',body:'<main></main>'}));await page.goto('http://127.0.0.1:8765/');
   for(const name of ['workspace-input-contracts','execution-continuations'])await page.addScriptTag({path:'pyharness/ui/host-adapters/'+name+'.js'});
   await page.evaluate(({project,ref})=>{
    const api=async(url,o={})=>{const v=await mockApi(url,o);if(v?.__error)throw Object.assign(Error(v.__error.message),v.__error);return v;};
    window.project=project;window.reference=ref;window.ctrl=PyharnessContinuations.create({main:document.querySelector('main'),api});window.opening=ctrl.open(project,ref);
   },{project,ref});
   if(scenario==='late-close'){
    for(let i=0;i<200&&!entered;i++)await new Promise(r=>setTimeout(r,20));assert(entered);await page.keyboard.press('Escape');release();
    await page.evaluate(()=>window.reopening=ctrl.open(project,reference));await page.waitForFunction(()=>document.querySelector('[data-co-status]').textContent.includes('已只读核对'));
    assert.equal(posts.length,0);
   }else if(['bad-digest','wrong-root','false-authority'].includes(scenario)){
    await page.waitForFunction(()=>document.querySelector('[data-co-status]').textContent.includes('未确认'));assert(await page.locator('[data-co-submit]').isDisabled());assert.equal(posts.length,0);
   }else{
    await page.waitForFunction(()=>document.querySelector('[data-co-status]').textContent.includes('已只读核对'));assert.equal(posts.length,0);
    if(scenario==='blocked'){assert(await page.locator('[data-co-submit]').isDisabled());assert((await page.locator('[data-co-summary]').textContent()).includes('未确认'));}
    else if(scenario==='host-confirm'){assert(await page.locator('[data-co-submit]').isDisabled());await page.locator('[data-co-host]').check();await page.locator('[data-co-submit]').click();}
    else if(scenario==='identity-change'){realm='B';await page.locator('[data-co-refresh]').click();await page.waitForFunction(()=>document.querySelectorAll('dialog').length===0);}
    else if(scenario==='withdrawal'){deny=true;await page.locator('[data-co-refresh]').click();await page.waitForFunction(()=>document.querySelector('[data-co-status]').textContent.includes('未确认'));assert.equal(await page.locator('[data-co-result]').textContent(),'');assert(await page.locator('[data-co-submit]').isDisabled());}
    else{
     await page.locator('[data-co-submit]').evaluate(b=>{b.click();b.click();});
     if(['lost-reply-query','reload-retry'].includes(scenario)){
      await page.waitForFunction(()=>document.querySelector('[data-co-status]').textContent.includes('未确认'));
      const hints=await page.evaluate(()=>Object.keys(localStorage).filter(k=>k.startsWith('pyharness.continuation.pending.v1:')).map(k=>JSON.parse(localStorage.getItem(k))));
      assert.equal(hints.length,1);assert(!JSON.stringify(hints).includes('messages'));assert.equal(posts.length,1);
      if(scenario==='reload-retry'){await page.evaluate(()=>{ctrl.invalidate();window.reopening=ctrl.open(project,reference);});await page.waitForFunction(()=>document.querySelector('[data-co-status]').textContent.includes('已只读核对'));assert.equal(posts.length,1);await page.locator('[data-co-retry]').click();}
      else await page.locator('[data-co-query]').click();
     }
    }
    if(['submit','double-click','lost-reply-query','reload-retry','host-confirm'].includes(scenario)){
     await page.waitForFunction(()=>document.querySelector('[data-co-status]').textContent.includes('精确核对'));
     assert.equal(posts.length,scenario==='reload-retry'?2:1);
     if(posts.length===2){assert.equal(posts[0].key,posts[1].key);assert.deepEqual(posts[0].body,posts[1].body);}
    }
   }
   assert.deepEqual(errors,[]);
  }finally{release();await browser.close();}
 });
}
