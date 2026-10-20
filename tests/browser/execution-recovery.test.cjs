const {test}=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const canonical=v=>JSON.stringify(v,(_,x)=>x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.entries(x).sort(([a],[b])=>a<b?-1:a>b?1:0)):x);
for(const scenario of ['observe','bad-digest','wrong-execution','false-authority','withdrawal','identity-change','late-close']) {
 test('recovery plan '+scenario,async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});let release;const gate=new Promise(r=>release=r);
  try {
   const page=await browser.newPage();page.setDefaultTimeout(8000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
   const project={project_id:'p-'+'1'.repeat(16),name:'Synthetic recovery'},ref={execution_id:'ex-'+'2'.repeat(16),revision:1,digest:'a'.repeat(64),source_seq:9};
   let realm='A',deny=false,requests=0,posts=0,entered=false;
   await page.exposeFunction('mockApi',async(url,options={})=>{
    if(options.method==='POST')posts++;
    if(url==='/api/account/me')return {account:{tenant:'synthetic',user_id:realm}};
    assert(url.endsWith('/executions/'+ref.execution_id+'/recovery-plan'));requests++;
    if(scenario==='late-close'&&requests===1){entered=true;await gate;return {__error:{status:503,message:'late stale failure'}};}
    if(deny)return {__error:{status:403,message:'synthetic source permission withdrawn'}};
    const v={schema_version:1,project_id:project.project_id,execution_ref:scenario==='wrong-execution'?{...ref,execution_id:'ex-'+'3'.repeat(16)}:ref,
     execution_status:'interrupted',journals:[],artifacts:[],continuation_blockers:['restore_state_unavailable','continuation_not_implemented','unknown_effects'],index_observation:{state:'stale'},
     continuation_authorized:scenario==='false-authority',repair_authorized:false,automatic_dispatch:false,atomic_snapshot:false,restore_state_available:false,repair_implementation:'pending'};
    return {...v,plan_digest:scenario==='bad-digest'?'b'.repeat(64):crypto.createHash('sha256').update(canonical(v)).digest('hex')};
   });
   await page.route('http://127.0.0.1:8765/**',r=>r.fulfill({contentType:'text/html',body:'<main></main>'}));await page.goto('http://127.0.0.1:8765/');
   for(const name of ['workspace-input-contracts','execution-recovery'])await page.addScriptTag({path:'pyharness/ui/host-adapters/'+name+'.js'});
   await page.evaluate(({project,ref})=>{
    const api=async(url,options={})=>{const v=await mockApi(url,options);if(v?.__error)throw Object.assign(Error(v.__error.message),v.__error);return v;};
    window.project=project;window.reference=ref;window.ctrl=PyharnessRecoveryPlans.create({main:document.querySelector('main'),api,esc:s=>s});window.opening=ctrl.open(project,ref);
   },{project,ref});
   if(scenario==='late-close') {
    for(let i=0;i<200&&!entered;i++)await new Promise(r=>setTimeout(r,20));assert(entered);
    await page.keyboard.press('Escape');await page.evaluate(()=>{window.reopening=ctrl.open(project,reference);});release();
    await page.waitForFunction(()=>document.querySelector('[data-rp-status]').textContent.includes('预检已核对'));
    assert(!(await page.locator('[data-rp-status]').textContent()).includes('stale'));
   }else if(['bad-digest','wrong-execution','false-authority'].includes(scenario)) {
    await page.waitForFunction(()=>document.querySelector('[data-rp-status]').textContent.includes('预检未确认'));
    assert.equal(await page.locator('[data-rp-body]').textContent(),'');
   }else {
    await page.waitForFunction(()=>document.querySelector('[data-rp-status]').textContent.includes('预检已核对'));
    assert((await page.locator('[data-rp-body]').textContent()).includes('未确认的副作用'));
    assert.equal(await page.locator('dialog button').count(),2);
    if(scenario==='withdrawal'){deny=true;await page.locator('[data-rp-refresh]').click();await page.waitForFunction(()=>document.querySelector('[data-rp-status]').textContent.includes('预检未确认'));assert.equal(await page.locator('[data-rp-body]').textContent(),'');}
    if(scenario==='identity-change'){realm='B';await page.locator('[data-rp-refresh]').click();await page.waitForFunction(()=>document.querySelectorAll('dialog').length===0);}
   }
   assert.equal(posts,0);assert.deepEqual(errors,[]);
  }finally{release();await browser.close();}
 });
}
