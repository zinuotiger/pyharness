const {test}=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const canonical=v=>JSON.stringify(v,(_,x)=>x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.entries(x).sort(([a],[b])=>a<b?-1:a>b?1:0)):x);
const hash=v=>crypto.createHash('sha256').update(canonical(v)).digest('hex');
for(const scenario of ['submit','double-click','reload-query','reload-retry','not-found','identity-change','late-close','late-refresh','safe-text','role-denied','tampered-hint','stale-conflict','draft-conflict','other-execution']) {
 test('execution correction '+scenario,async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});let release;const gate=new Promise(r=>release=r);
  try {
   const page=await browser.newPage();page.setDefaultTimeout(8000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
   const project={project_id:'p-'+'1'.repeat(16),name:'Synthetic correction'};
   const ref={execution_id:'ex-'+'2'.repeat(16),revision:1,digest:'a'.repeat(64),source_seq:7},other={...ref,execution_id:'ex-'+'3'.repeat(16)};
   const text=scenario==='safe-text'?'<img src=x onerror=window.correctionXss=1>':'Private supplement 中文, preserve this exact text.';
   const state={realm:'A',rows:[],posts:[],deny:false,entered:false,delayRefresh:false};
   const fail=(status,message)=>({__error:{status,message}});
   function record(body,key){return {schema_version:1,project_id:project.project_id,correction_id:body.correction_id,revision:body.expected_revision+1,execution_ref:body.execution_ref,text:body.text,
    digest:hash(body),source_seq:50+body.expected_revision,request_digest:hash({operation:'execution.correct',project_id:project.project_id,command:body}),submission_key:key,persisted:true,application_status:'pending'};}
   const rows=()=>state.rows.map(r=>({...r,application_status:r.revision<state.rows.length?'superseded':'pending'}));
   const external=()=>{const b={schema_version:1,correction_id:'cr-'+'9'.repeat(16),expected_revision:state.rows.length,execution_ref:ref,text:'Other window server revision'};state.rows.push(record(b,'other-window-'+state.rows.length));};
   await page.exposeFunction('mockApi',async(url,options={})=>{
    if(url==='/api/account/me')return {account:{tenant:'synthetic',user_id:state.realm}};
    if(url.includes('/correction-submissions/by-key?')){
     const key=new URL('http://local'+url).searchParams.get('key'),r=rows().find(r=>r.submission_key===key);
     return {schema_version:1,project_id:project.project_id,submission_key:key,status:r?'committed':'not_found',correction:r||null};
    }
    if(url.endsWith('/corrections')) {
     if(options.method==='POST') {
      const body=JSON.parse(options.body),key=options.headers['Idempotency-Key'];state.posts.push({body,key});
      const old=state.rows.find(r=>r.submission_key===key);if(old)return old;
      if(scenario==='stale-conflict'&&state.posts.length===1){external();return fail(409,'synthetic version conflict');}
      if(body.expected_revision!==state.rows.length)return fail(409,'synthetic expected revision conflict');
      if(scenario==='not-found'&&state.posts.length===1)return fail(503,'synthetic reply unknown before commit');
      const r=record(body,key);state.rows.push(r);
      if(['reload-query','reload-retry','identity-change','tampered-hint','other-execution'].includes(scenario)&&state.posts.length===1)return fail(503,'synthetic committed reply lost');
      if(scenario==='late-close'){state.entered=true;await gate;}
      return r;
     }
     if(state.deny)return fail(403,'synthetic source permission withdrawn');
     if(state.delayRefresh){state.delayRefresh=false;state.entered=true;await gate;return fail(503,'synthetic late refresh failure');}
     const selected=url.includes(other.execution_id)?other:ref,items=selected===other?[]:rows();
     return {schema_version:1,project_id:project.project_id,execution_id:selected.execution_id,execution_ref:selected,corrections:items,total:items.length,current_revision:items.length,can_submit:true};
    }
    throw Error('unexpected synthetic API '+url);
   });
   await page.route('http://127.0.0.1:8765/**',r=>r.fulfill({contentType:'text/html',body:'<main></main>'}));
   async function install(reload=false,reference=ref){
    if(reload)await page.reload();else await page.goto('http://127.0.0.1:8765/');
    for(const name of ['workspace-input-contracts','execution-corrections'])await page.addScriptTag({path:'pyharness/ui/host-adapters/'+name+'.js'});
    await page.evaluate(async({project,reference})=>{
     const api=async(url,options={})=>{const v=await mockApi(url,options);if(v?.__error)throw Object.assign(Error(v.__error.message),v.__error);return v;};
     window.project=project;window.reference=reference;window.ctrl=PyharnessExecutionCorrections.create({main:document.querySelector('main'),api,esc:s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))});await ctrl.open(project,reference);
    },{project,reference});
   }
   const button=a=>page.locator('[data-cr-action='+a+']');
   async function entered(){for(let i=0;i<200&&!state.entered;i++)await new Promise(r=>setTimeout(r,20));assert(state.entered);}
   await install();assert.equal(state.posts.length,0);await page.locator('[data-cr-text]').fill(text);
   if(scenario==='late-refresh') {
    state.delayRefresh=true;await button('refresh').click();await entered();await page.keyboard.press('Escape');await page.evaluate(()=>ctrl.open(project,reference));release();
    await page.waitForFunction(()=>document.querySelector('[data-cr-status]').textContent.includes('已核对'));
    assert(!(await page.locator('[data-cr-status]').textContent()).includes('late refresh'));assert.equal(state.posts.length,0);
   }else if(scenario==='draft-conflict') {
    external();await button('refresh').click();await page.waitForFunction(()=>document.querySelector('[data-cr-version]').textContent.includes('r1'));
    assert.equal(await page.locator('[data-cr-text]').inputValue(),text);assert(await button('submit').isDisabled());
    await button('adopt').click();assert.equal(state.posts.length,0);await button('submit').click();
    await page.waitForFunction(()=>document.querySelector('[data-cr-status]').textContent.includes('精确核对'));
    assert.equal(state.posts[0].body.expected_revision,1);assert.equal(state.posts[0].body.text,text);
   }else {
    if(scenario==='double-click')await button('submit').evaluate(b=>{b.click();b.click();});else await button('submit').click();
    if(scenario==='late-close') {
     await entered();await page.keyboard.press('Escape');await page.evaluate(()=>ctrl.open(project,reference));release();
     await page.waitForFunction(()=>document.querySelector('[data-cr-status]').textContent.includes('已核对'));
     assert.equal(state.posts.length,1);assert(!(await page.locator('[data-cr-status]').textContent()).includes('精确核对'));
    }else if(['reload-query','reload-retry','not-found','identity-change','tampered-hint','stale-conflict','other-execution'].includes(scenario)) {
     await page.waitForFunction(()=>document.querySelector('[data-cr-status]').textContent.includes('操作未确认'));
     const hints=await page.evaluate(()=>Object.keys(localStorage).filter(k=>k.startsWith('pyharness.correction.pending.v1:')).map(k=>[k,localStorage.getItem(k)]));
     assert.equal(hints.length,1);assert(!hints[0][1].includes(text));assert(!hints[0][1].includes('"text":'));assert(await button('submit').isDisabled());
     if(scenario==='identity-change') {
      state.realm='B';await button('retry').click();await page.waitForFunction(()=>document.querySelectorAll('dialog').length===0);assert.equal(state.posts.length,1);
     }else if(scenario==='other-execution') {
      await install(true,other);assert.equal(await page.locator('[data-cr-hint]').textContent(),'');assert.equal(state.posts.length,1);assert.equal(await button('submit').isDisabled(),false);
     }else if(scenario==='stale-conflict') {
      await button('refresh').click();await page.waitForFunction(()=>document.querySelector('[data-cr-version]').textContent.includes('r1'));await button('abandon').click();
      await page.waitForFunction(()=>document.querySelector('[data-cr-status]').textContent.includes('已弃置'));
      assert.equal(state.posts.length,1);assert.equal(await page.locator('[data-cr-text]').inputValue(),text);
      await button('submit').click();await page.waitForFunction(()=>document.querySelector('[data-cr-status]').textContent.includes('精确核对'));
      assert.equal(state.posts[1].body.expected_revision,1);assert.notEqual(state.posts[0].key,state.posts[1].key);
     }else {
      if(scenario==='tampered-hint'){await page.evaluate(([key,raw])=>{const h=JSON.parse(raw);h.request_digest='0'.repeat(64);localStorage.setItem(key,JSON.stringify(h));},hints[0]);}
      if(scenario!=='not-found'){await install(true);assert.equal(state.posts.length,1);assert.equal(await page.locator('[data-cr-text]').inputValue(),'');}
      if(['reload-retry','tampered-hint'].includes(scenario))await page.locator('[data-cr-text]').fill(text);
      await button(scenario==='reload-retry'||scenario==='tampered-hint'?'retry':'query').click();
      if(scenario==='tampered-hint') {await page.waitForFunction(()=>document.querySelector('[data-cr-status]').textContent.includes('摘要不符'));assert.equal(state.posts.length,1);}
      else if(scenario==='not-found') {
       await page.waitForFunction(()=>document.querySelector('[data-cr-status]').textContent.includes('尚未找到'));assert(await button('submit').isDisabled());
       await button('retry').click();await page.waitForFunction(()=>document.querySelector('[data-cr-status]').textContent.includes('精确核对'));
       assert.equal(state.posts.length,2);assert.deepEqual(state.posts[0],state.posts[1]);assert.equal(state.rows.length,1);
      }else {await page.waitForFunction(()=>document.querySelector('[data-cr-status]').textContent.includes('精确核对'));assert.equal(state.posts.length,scenario==='reload-retry'?2:1);assert.equal(state.rows.length,1);if(state.posts.length===2)assert.deepEqual(state.posts[0],state.posts[1]);}
     }
    }else {
     await page.waitForFunction(()=>document.querySelector('[data-cr-status]').textContent.includes('精确核对'));assert.equal(state.posts.length,1);assert.equal(state.rows.length,1);
     if(scenario==='safe-text'){assert.equal(await page.evaluate(()=>window.correctionXss),undefined);assert.equal(await page.locator('dialog img,dialog script').count(),0);assert((await page.locator('[data-cr-rows]').textContent()).includes('<img'));}
     if(scenario==='role-denied'){state.deny=true;await button('refresh').click();await page.waitForFunction(()=>document.querySelector('[data-cr-status]').textContent.includes('withdrawn'));assert.equal(await page.locator('[data-cr-rows]').textContent(),'');assert.equal(await page.locator('[data-cr-text]').inputValue(),'');assert(await button('submit').isDisabled());}
    }
   }
   assert.deepEqual(errors,[]);
  }finally{release();await browser.close();}
 });
}
