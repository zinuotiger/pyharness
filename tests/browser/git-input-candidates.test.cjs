/* Real Chrome, synthetic API responses; real loopback chain recorded separately. */
const {test}=require('node:test'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fixture=JSON.parse(fs.readFileSync('tests/browser/fixtures/git-input-candidates.json','utf8'));
for(const scenario of ['select','no-auto','partial','bad-digest','wrong-source','bad-path','view-change','pagination','late-close','late-refresh','late-edit','late-apply-edit','expiry']){
 test('Git candidate metadata '+scenario,async()=>{
  const browser=await chromium.launch({headless:true,channel:'chrome'});
  try{
   const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(5000);
   await page.route('https://workspace.test/**',r=>r.fulfill({contentType:'text/html',body:'<main></main><button id="open">open</button>'}));await page.goto('https://workspace.test/');
   for(const name of ['workspace-input-contracts','git-input-candidates','workspace-inputs'])await page.addScriptTag({path:path.resolve('pyharness/ui/host-adapters/'+name+'.js')});
   await page.evaluate(async({fixture,scenario})=>{
    const copy=x=>structuredClone(x),c=PyharnessWorkspaceContracts;window.f={posts:[],calls:0,entered:false,done:false};f.gate=new Promise(r=>f.release=r);
    f.validations=0;const validate=c.inventory;c.inventory=async(...args)=>{const result=await validate(...args);f.validations++;return result;};
    window.a=PyharnessWorkspaceInputs.create({main:document.querySelector('main'),api:async(url,opts)=>{
     if(url==='/api/v1/projects')return copy(fixture.project);
     if(url.endsWith('/source-grants'))return {grants:[copy(fixture.grant)],total:1};
     if(url.endsWith('/context-task-references'))return {task_references:[],total:0};
     if(!opts){const kind=url.includes('/workspace-snapshots')?'snapshots':'manifests';return {schema_version:1,[kind]:[],total:0,page_count:0,has_more:false,next_cursor:null,source_watermark:3,view_digest:'f'.repeat(64)};}
     const body=JSON.parse(opts.body);f.posts.push({url,body});
     if(!url.endsWith('/git-input-candidates'))throw Error('unexpected capture or content preview');
     f.calls++;f.entered=true;if((scenario.startsWith('late-')&&scenario!=='late-apply-edit'&&f.calls===1)||(scenario==='late-apply-edit'&&f.calls===2))await f.gate;
     f.done=true;if(scenario==='expiry')throw Object.assign(Error('synthetic expiry'),{status:403});
     const value=copy(fixture.candidates),start=body.cursor?Number(body.cursor.split(':')[2])+1:0;
     value.ignored_requested=body.include_ignored;value.items=value.items.slice(start,start+body.limit);value.page_count=value.items.length;
     if(scenario==='view-change'&&f.calls>1)value.view_digest='b'.repeat(64);
     value.has_more=start+value.page_count<value.total;value.next_cursor=value.has_more?`1:${body.limit}:${start+value.page_count-1}:${value.view_digest}`:null;
     if(scenario==='wrong-source')value.project_id='p-aaaaaaaaaaaaaaaa';
     if(scenario==='bad-path')value.items[0].path='../escape';
     delete value.page_digest;value.page_digest=await c.digest(value);if(scenario==='bad-digest')value.page_digest='0'.repeat(64);return value;
    }});
    window.openCandidates=()=>document.querySelector('[data-ws-action=candidates]').click();
    await a.open({project:fixture.candidates.project_id,grantId:fixture.grant.grant_id,grantRevision:fixture.grant.revision,sourceSeq:fixture.grant.source_seq},'',document.querySelector('#open'));
   },{fixture,scenario});
   await page.locator('[data-ws-read-confirm]').check();await page.locator('[data-ws-save-confirm]').check();await page.locator('[data-ws-action=candidates]').click();
   assert.equal(await page.evaluate(()=>f.posts.length),0,'opening never reads or captures');
   assert.equal(await page.locator('[data-ws-read-confirm]').isChecked(),false);assert.equal(await page.locator('[data-ws-save-confirm]').isChecked(),false);
   if(scenario==='pagination')await page.locator('[data-gic-limit]').fill('1');
   await page.locator('[data-gic-action=read]').click();
   if(scenario.startsWith('late-')&&scenario!=='late-apply-edit'){
    await page.waitForFunction(()=>f.entered);
    if(scenario==='late-close'){await page.locator('#gitInputCandidatesDialog').press('Escape');await page.evaluate(()=>openCandidates());}
    else if(scenario==='late-refresh')await page.locator('[data-gic-action=refresh]').click();
    else await page.locator('[data-gic-limit]').fill('1');
    await page.evaluate(()=>f.release());await page.waitForFunction(()=>f.done);
    assert.equal(await page.locator('[data-gic-rows] input').count(),0);assert.equal(await page.locator('[data-gic-action=read]').isDisabled(),false);
   }else if(['bad-digest','wrong-source','bad-path','expiry'].includes(scenario)){
    await page.waitForFunction(()=>document.querySelector('[data-gic-status]').textContent.includes('被拒绝'));
    assert.equal(await page.locator('[data-gic-rows] input').count(),0);
   }else{
    await page.waitForFunction(()=>document.querySelector('[data-gic-rows] input'));
    assert.equal(await page.locator('[data-gic-rows] input:checked').count(),0,'no automatic selections');
    if(scenario==='no-auto'){
     await page.locator('[data-gic-action=close]').click();assert.equal(await page.locator('[data-ws-paths]').inputValue(),'');
    }else if(scenario==='partial'){
     await page.locator('[data-gic-rows] label').filter({hasText:'new.txt'}).locator('input').check();await page.locator('[data-gic-action=apply]').click();
     assert.match(await page.locator('[data-gic-status]').textContent(),/全部可用tracked/);assert.equal(await page.evaluate(()=>f.calls),1);
    }else{
     if(scenario==='pagination'){
      for(let n=0;n<fixture.candidates.total;n++){
       const row=fixture.candidates.items[n];if(row.required)await page.locator('[data-gic-rows] input').check();
       if(n<fixture.candidates.total-1){await page.locator('[data-gic-action=next]').click();await page.waitForFunction(index=>document.querySelector('[data-gic-rows] input')?.dataset.gicIndex===String(index),n+1);}
      }
     }else for(const row of fixture.candidates.items.filter(i=>i.required))await page.locator(`[data-gic-index="${row.entry_index}"]`).check();
     await page.locator('[data-gic-action=apply]').click();
     if(scenario==='late-apply-edit'){
      await page.waitForFunction(()=>f.calls===2);
      const row=fixture.candidates.items.find(i=>i.required);await page.locator(`[data-gic-index="${row.entry_index}"]`).uncheck();
      await page.evaluate(()=>f.release());await page.waitForFunction(()=>f.validations===2);
      assert.equal(await page.locator('#gitInputCandidatesDialog').count(),1,'edited selection invalidates late apply');
      assert.equal(await page.locator('[data-gic-action=apply]').isDisabled(),false);
      await page.locator('[data-gic-action=apply]').click();assert.match(await page.locator('[data-gic-status]').textContent(),/全部可用tracked/);
     }else if(scenario==='view-change'){
      await page.waitForFunction(()=>document.querySelector('[data-gic-status]').textContent.includes('来源变化'));assert.equal(await page.locator('[data-gic-rows] input').count(),0);
     }else{
      await page.waitForFunction(()=>!document.querySelector('#gitInputCandidatesDialog'));
      assert.deepEqual((await page.locator('[data-ws-paths]').inputValue()).split('\n'),fixture.candidates.items.filter(i=>i.required).map(i=>i.path));
      assert.equal(await page.locator('[data-ws-read-confirm]').isChecked(),false);assert.equal(await page.locator('[data-ws-save-confirm]').isChecked(),false);
      assert.match(await page.locator('[data-ws-status]').textContent(),/仍须正文安全预览/);
     }
    }
   }
   assert.equal(await page.evaluate(()=>f.posts.some(p=>!p.url.endsWith('/git-input-candidates'))),false);assert.deepEqual(errors,[]);
  }finally{await browser.close();}
 });
}
