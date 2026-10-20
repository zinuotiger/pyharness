const {test}=require('node:test'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fixture=require('./fixtures/owned-inputs.json');
for(const scenario of ['draft','binding','preflight','unknown-retry','unknown-reconcile','known-reject','late-close','late-preflight','safe-text','identity-change','refresh-denied']){
 test(`formal product tasks ${scenario}`,async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  try{
   const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(6000);
   await page.route('http://127.0.0.1:8765/**',r=>r.fulfill({body:'<main></main>',contentType:'text/html'}));await page.goto('http://127.0.0.1:8765/');
   await page.addScriptTag({path:'pyharness/ui/host-adapters/workspace-input-contracts.js'});
   await page.addScriptTag({path:'pyharness/ui/host-adapters/product-tasks.js'});
   await page.evaluate(({fixture,scenario})=>{
    const clone=v=>JSON.parse(JSON.stringify(v)),f=window.f={posts:[],task:null,scenario,realm:'A',preflights:0};
    f.gate=new Promise(resolve=>f.release=resolve);
    const project={project_id:fixture.input.project_id,revision:fixture.input.project_revision,name:'Synthetic project'};
    const api=async(url,options={})=>{
     if(url==='/api/account/me')return {account:{tenant:'synthetic',user_id:f.realm}};
     if(url.includes('/workspace-inputs?'))return {inputs:[clone(fixture.input)],next_cursor:null};
     if(url.endsWith('/preflight')){
      f.preflights++;if(scenario==='late-preflight'){f.entered=true;await f.gate;}
      return {status:'prerequisites_checked',prerequisites:{task_ref:Object.fromEntries(['product_task_id','revision','digest','source_seq'].map(k=>[k,f.task[k]])),project_id:project.project_id,project_revision:project.revision,input_binding:f.task.input_binding},binding_digest:'c'.repeat(64),execution_authorized:false,attempt_reserved:false,persisted:false};
     }
     if(options.method==='POST'){
      f.posts.push({body:options.body,key:options.headers['Idempotency-Key']});
      if(scenario==='known-reject')throw Object.assign(Error('synthetic denial'),{status:403});
      const body=JSON.parse(options.body),{expected_revision,...fields}=body;
      f.task={...fields,project_id:project.project_id,revision:expected_revision+1,mode:'product_task',execution_policy:'governed-attempt-only',executable:false,attempt_reserved:false,digest:'a'.repeat(64),source_seq:7,created_at:'2026-10-18T00:00:00Z',stale_reasons:[],status:body.input_binding?'definition_ready':'draft'};
      if(['unknown-retry','unknown-reconcile','identity-change'].includes(scenario)&&f.posts.length===1)throw Object.assign(Error('synthetic unknown'),{status:503});
      if(scenario==='late-close'){f.entered=true;await f.gate;}
      return clone(f.task);
     }
     if(url.includes('/revisions/')){if(!f.task)throw Object.assign(Error('unavailable'),{status:403});return clone(f.task);}
     if(url.endsWith('/product-tasks')){
      if(f.denyList)throw Object.assign(Error('synthetic revoked'),{status:403});
      return {tasks:f.task?[clone(f.task)]:[],total:f.task?1:0};
     }
     throw Error('unexpected route '+url);
    };
    window.ctrl=window.PyharnessProductTasks.create({main:document.querySelector('main'),api,esc:s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))});
    f.project=project;ctrl.open(project,null);
   },{fixture,scenario});
   const dialog=page.getByRole('dialog');await page.waitForFunction(()=>document.querySelector('[data-pt-status]').textContent.includes('已加载'));
   await dialog.locator('[name=title]').fill(scenario==='safe-text'?'<img src=x onerror=window.syntheticXss=1>':'Synthetic formal task');
   await dialog.locator('[name=objective]').fill('Review the exact prepared input');await dialog.locator('[name=criteria]').fill('Report one result');
   if(['binding','preflight','late-preflight'].includes(scenario))await dialog.locator('[name=input]').selectOption(fixture.input.input_id);
   await dialog.getByRole('button',{name:'保存正式任务定义',exact:true}).click();
   if(scenario==='late-close'){
    await page.waitForFunction(()=>f.entered);await page.keyboard.press('Escape');await page.evaluate(()=>ctrl.open(f.project,null));
    await page.waitForFunction(()=>document.querySelector('[data-pt-status]').textContent.includes('原命令结果尚未确认'));
    await page.evaluate(()=>f.release());await page.waitForTimeout(100);assert.equal(await dialog.locator('[data-pt-result]').textContent(),'');
   }else if(['unknown-retry','unknown-reconcile','identity-change'].includes(scenario)){
    await page.waitForFunction(()=>document.querySelector('[data-pt-status]').textContent.includes('结果未确认'));
    if(scenario==='identity-change')await page.evaluate(()=>f.realm='B');
    await dialog.locator(`[data-pt-action=${scenario==='unknown-reconcile'?'reconcile':'retry'}]`).click();
    if(scenario==='identity-change'){
     await page.waitForFunction(()=>document.querySelector('[data-pt-status]').textContent.includes('主体已变化'));assert.equal(await page.evaluate(()=>f.posts.length),1);
    }else{
     await page.waitForFunction(()=>document.querySelector('[data-pt-status]').textContent.includes('精确持久确认'));
     const posts=await page.evaluate(()=>f.posts);assert.equal(posts.length,scenario==='unknown-reconcile'?1:2);if(posts.length===2)assert.deepEqual(posts[0],posts[1]);
    }
   }else if(scenario==='known-reject'){
    await page.waitForFunction(()=>document.querySelector('[data-pt-status]').textContent.includes('命令被明确拒绝'));assert.equal(await dialog.locator('[name=title]').isDisabled(),false);
   }else{
    await page.waitForFunction(()=>document.querySelector('[data-pt-status]').textContent.includes('精确持久确认'));
    const task=await page.evaluate(()=>f.task);assert.equal(task.input_binding!==null,['binding','preflight','late-preflight'].includes(scenario));assert.equal(task.executable,false);
    await dialog.locator('[data-pt-action=refresh]').click();await page.waitForFunction(()=>document.querySelector('[data-pt-rows] article'));
    if(scenario==='refresh-denied'){
     await page.evaluate(()=>f.denyList=true);await dialog.locator('[data-pt-action=refresh]').click();
     await page.waitForFunction(()=>document.querySelector('[data-pt-status]').textContent.includes('读取失败'));
     assert.equal(await dialog.locator('[data-pt-rows] article').count(),0);
     assert.equal(await dialog.locator('[name=input] option').count(),1);
     assert.equal(await dialog.locator('[data-pt-result]').textContent(),'');
    }
    if(scenario==='safe-text'){assert.equal(await dialog.locator('[data-pt-rows] img').count(),0);assert.equal(await page.evaluate(()=>window.syntheticXss),undefined);}
    if(['preflight','late-preflight'].includes(scenario)){
     await dialog.locator('[data-pt-action=preflight]').click();
     if(scenario==='late-preflight'){
      await page.waitForFunction(()=>f.entered);await dialog.locator('[data-pt-action=edit]').click();await page.evaluate(()=>f.release());await page.waitForTimeout(100);assert.equal(await dialog.locator('[data-pt-result]').textContent(),'');
     }else await page.waitForFunction(()=>document.querySelector('[data-pt-status]').textContent.includes('前置检查通过'));
    }
   }
   assert.deepEqual(errors,[]);assert.equal(await page.evaluate(()=>f.posts.length)>2,false);
  }finally{await browser.close();}
 });
}
