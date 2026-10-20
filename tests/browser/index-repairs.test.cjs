const {test}=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const canonical=v=>JSON.stringify(v,(_,x)=>x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.entries(x).sort(([a],[b])=>a<b?-1:a>b?1:0)):x);
const hash=v=>crypto.createHash('sha256').update(canonical(v)).digest('hex');
for(const scenario of ['submit','double-click','reload-query','reload-retry','started-query','limit','security','identity-change','late-close','withdrawal','tampered-hint','wrong-scope']) {
 test('bounded index repair '+scenario,async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});let release;const gate=new Promise(r=>release=r);
  try {
   const page=await browser.newPage();page.setDefaultTimeout(8000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
   const project={project_id:'p-'+'1'.repeat(16),name:'Synthetic index repair'},ref={execution_id:'ex-'+'2'.repeat(16),revision:1,digest:'a'.repeat(64),source_seq:9};
   const journals=[{session_id:'s-pa-'+'3'.repeat(32),observed_seq:13,prefix_digest:'b'.repeat(64)}],scope='c'.repeat(64);
   const fault=hash({operation:'index.repair',execution_ref:ref,journals,index_scope_digest:scope});
   const state={realm:'A',posts:[],rows:[],index:'stale',deny:false,entered:false,mutations:0};
   const fail=(status,message)=>({__error:{status,message}});
   function plan(){const p={schema_version:1,project_id:project.project_id,execution_ref:ref,execution_status:'interrupted',journals,journals_truncated:false,artifacts:[],artifacts_truncated:false,
    unknown_effects:false,continuation_blockers:scenario==='security'?['security_refusal','restore_state_unavailable']:['restore_state_unavailable'],
    index_observation:{state:state.index,scope_digest:scope},continuation_authorized:false,repair_authorized:false,automatic_dispatch:false,atomic_snapshot:false,restore_state_available:false,repair_implementation:'available'};return {...p,plan_digest:hash(p)};}
   function receipt(body,key,phase='succeeded'){return {schema_version:1,project_id:project.project_id,execution_ref:ref,repair_id:body.repair_id,revision:phase==='started'?1:2,phase,attempt:state.rows.length+1,
    plan_digest:body.plan_digest,source_seq:20+state.rows.length,digest:hash({body,key,phase}),request_digest:hash({operation:'execution.index_repair',project_id:project.project_id,command:body}),
    fault_digest:fault,submission_key:key,persisted:true,automatic_dispatch:false,continuation_authorized:false,index_observation:phase==='started'?null:{state:phase==='succeeded'?'current':'stale',scope_digest:scope}};}
   if(scenario==='limit')for(let i=1;i<=2;i++)state.rows.push(receipt({schema_version:1,repair_id:'er-'+String(i).repeat(16),execution_ref:ref,plan_digest:plan().plan_digest},'old-failed-'+i,'failed'));
   await page.exposeFunction('mockApi',async(url,options={})=>{
    if(url==='/api/account/me')return {account:{tenant:'synthetic',user_id:state.realm}};
    if(state.deny)return fail(403,'synthetic source withdrawn');
    if(url.endsWith('/recovery-plan'))return plan();
    if(url.includes('/index-repair-submissions/by-key?')){const key=new URL('http://local'+url).searchParams.get('key'),r=state.rows.find(r=>r.submission_key===key);return {schema_version:1,project_id:project.project_id,submission_key:key,status:r?'committed':'not_found',repair:r||null};}
    if(url.endsWith('/index-repairs')){
     if(options.method!=='POST')return {schema_version:1,project_id:project.project_id,execution_ref:scenario==='wrong-scope'?{...ref,execution_id:'ex-'+'4'.repeat(16)}:ref,repairs:state.rows,total:state.rows.length,truncated:false};
     const body=JSON.parse(options.body),key=options.headers['Idempotency-Key'];state.posts.push({body,key});const old=state.rows.find(r=>r.submission_key===key);if(old)return old;
     assert(state.rows.length<2);const r=receipt(body,key,scenario==='started-query'?'started':'succeeded');state.rows.push(r);state.mutations++;
     if(r.phase==='succeeded')state.index='current';
     if(['reload-query','reload-retry','identity-change','tampered-hint'].includes(scenario))return fail(503,'synthetic committed repair reply lost');
     if(scenario==='late-close'){state.entered=true;await gate;}return r;
    }
    throw Error('unexpected synthetic API '+url);
   });
   await page.route('http://127.0.0.1:8765/**',r=>r.fulfill({contentType:'text/html',body:'<main></main>'}));
   async function install(reload=false){
    if(reload)await page.reload();else await page.goto('http://127.0.0.1:8765/');
    for(const name of ['workspace-input-contracts','index-repairs'])await page.addScriptTag({path:'pyharness/ui/host-adapters/'+name+'.js'});
    await page.evaluate(async({project,ref})=>{const api=async(url,options={})=>{const v=await mockApi(url,options);if(v?.__error)throw Object.assign(Error(v.__error.message),v.__error);return v;};
     window.project=project;window.reference=ref;window.ctrl=PyharnessIndexRepairs.create({main:document.querySelector('main'),api,esc:s=>s});await ctrl.open(project,ref);
    },{project,ref});
   }
   await install();assert.equal(state.posts.length,0);
   const button=a=>page.locator('[data-ir-'+a+']');
   if(['limit','security','wrong-scope'].includes(scenario)){assert(await button('submit').isDisabled());assert.equal(state.posts.length,0);}
   else if(scenario==='withdrawal'){state.deny=true;await button('refresh').click();await page.waitForFunction(()=>document.querySelector('[data-ir-status]').textContent.includes('操作未确认'));assert.equal(await page.locator('[data-ir-rows]').textContent(),'');assert(await button('submit').isDisabled());}
   else {
    if(scenario==='double-click')await button('submit').evaluate(b=>{b.click();b.click();});else await button('submit').click();
    if(scenario==='late-close'){
     for(let i=0;i<200&&!state.entered;i++)await new Promise(r=>setTimeout(r,20));assert(state.entered);await page.keyboard.press('Escape');await page.evaluate(()=>ctrl.open(project,reference));release();
     await page.waitForFunction(()=>document.querySelector('[data-ir-status]').textContent.includes('已只读核对'));assert.equal(state.posts.length,1);
    }else if(['reload-query','reload-retry','identity-change','tampered-hint'].includes(scenario)){
     await page.waitForFunction(()=>document.querySelector('[data-ir-status]').textContent.includes('操作未确认'));
     const hints=await page.evaluate(()=>Object.keys(localStorage).filter(k=>k.startsWith('pyharness.index-repair.pending.v1:')).map(k=>[k,localStorage.getItem(k)]));
     assert.equal(hints.length,1);assert(!hints[0][1].includes('journals'));assert(!hints[0][1].includes('index_scope'));
     if(scenario==='identity-change'){state.realm='B';await button('retry').click();await page.waitForFunction(()=>document.querySelectorAll('dialog').length===0);}
     else {
      if(scenario==='tampered-hint')await page.evaluate(([key,raw])=>{const h=JSON.parse(raw);h.request_digest='0'.repeat(64);localStorage.setItem(key,JSON.stringify(h));},hints[0]);
      await install(true);assert.equal(state.posts.length,1);await button(scenario==='reload-retry'||scenario==='tampered-hint'?'retry':'query').click();
      await page.waitForFunction(t=>document.querySelector('[data-ir-status]').textContent.includes(t),scenario==='tampered-hint'?'摘要不符':'精确核对');
      if(scenario==='reload-retry'){assert.equal(state.posts.length,2);assert.deepEqual(state.posts[0],state.posts[1]);}else assert.equal(state.posts.length,1);
     }
    }else if(scenario==='started-query'){
     await page.waitForFunction(()=>document.querySelector('[data-ir-status]').textContent.includes('结果仍未知'));assert(await button('submit').isDisabled());await button('query').click();await page.waitForFunction(()=>document.querySelector('[data-ir-status]').textContent.includes('结果仍未知'));assert.equal(state.posts.length,1);
    }else{await page.waitForFunction(()=>document.querySelector('[data-ir-status]').textContent.includes('精确核对'));assert.equal(state.posts.length,1);assert(await button('submit').isDisabled());}
    assert.equal(state.mutations,1);
   }
   assert.deepEqual(errors,[]);
  }finally{release();await browser.close();}
 });
}
