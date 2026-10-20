/* Real Chrome, synthetic API DTOs only. Real loopback is verified separately. */
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fixture=JSON.parse(fs.readFileSync('tests/browser/fixtures/workspace-inputs.json','utf8'));
for(const scenario of ['capture-manifest','refresh-manifest','task-refresh-existing','bad-manifest-source','no-confirm','bad-receipt','wrong-source','unknown','unknown-retry409','late-edit','late-refresh','late-close','late-row','stale','bad-preview','pagination']){
 test('workspace inputs '+scenario,async()=>{
  const browser=await chromium.launch({headless:true,channel:'chrome'});
  try{
   const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(5000);
   await page.route('https://workspace.test/**',r=>r.fulfill({contentType:'text/html',body:'<main></main><button id="open">open</button>'}));await page.goto('https://workspace.test/');
   for(const name of ['workspace-input-contracts','workspace-inputs']){
    const file=path.resolve('pyharness/ui/host-adapters/'+name+'.js');if(fs.existsSync(file))await page.addScriptTag({path:file});
   }
   assert.equal(await page.evaluate(()=>typeof PyharnessWorkspaceInputs),'object','persistent workspace product adapter must exist');
   await page.evaluate(async({fixture,scenario})=>{
    const copy=v=>structuredClone(v);window.f={posts:[],entered:false,done:false,snapshot:null,manifest:null,pageRequests:0,target:scenario==='task-refresh-existing'?copy(fixture.task):null};f.gate=new Promise(r=>f.release=r);
    const c=PyharnessWorkspaceContracts,sign=async row=>{delete row.receipt_digest;row.receipt_digest=await c.digest(row);return row;};
    const list=async(kind,row)=>{const value=row?await c.summary(copy(row),kind):null;return {schema_version:1,[kind]:value?[value]:[],total:value?1:0,page_count:value?1:0,has_more:false,next_cursor:null,source_watermark:10,view_digest:'f'.repeat(64)};};
    window.openWorkspace=(other=false)=>a.open({project:fixture.grant.grant_id.replace('sg-','p-'),grantId:other?'sg-aaaaaaaaaaaaaaaa':fixture.grant.grant_id,grantRevision:1,sourceSeq:3},fixture.preview.items.map(i=>i.path).join('\n'),document.querySelector('#open'));
    window.a=PyharnessWorkspaceInputs.create({main:document.querySelector('main'),api:async(url,opts)=>{
     if(url==='/api/v1/projects')return copy(fixture.project);
     if(url.endsWith('/source-grants'))return {grants:[copy(fixture.grant)],total:1};
     if(url.endsWith('/context-task-references')&&!opts)return {task_references:f.target?[copy(f.target)]:[],total:f.target?1:0};
     if(opts){
      const body=JSON.parse(opts.body);f.posts.push({url,body:opts.body,key:opts.headers?.['Idempotency-Key']});
      if(url.endsWith('/workspace-snapshot-previews')){const v=copy(fixture.preview);if(scenario==='bad-preview')v.observation_digest='0'.repeat(64);return v;}
      f.entered=true;if(scenario.startsWith('late-'))await f.gate;
      if(url.endsWith('/context-task-references')){f.target={...copy(fixture.task),product_task_id:body.product_task_id,revision:body.expected_revision+1,title:body.title,source_seq:8};return copy(f.target);}
      if(url.endsWith('/workspace-snapshots')){
       f.snapshot=copy(fixture.snapshot);f.snapshot.snapshot_id=body.snapshot_id;
       if(scenario==='wrong-source')f.snapshot.grant_id='sg-aaaaaaaaaaaaaaaa';
       await sign(f.snapshot);if(scenario==='bad-receipt')f.snapshot.receipt_digest='0'.repeat(64);
       f.done=true;
       const captures=f.posts.filter(p=>p.url.endsWith('/workspace-snapshots'));
       if(['unknown','unknown-retry409'].includes(scenario)&&captures.length===1)throw Error('synthetic unknown after fixture commit');
       if(scenario==='unknown-retry409'&&captures.length>1)throw Object.assign(Error('synthetic retry denied'),{status:409});
       return copy(f.snapshot);
      }
      if(url.endsWith('/workspace-context-manifests')){
       f.manifest=copy(fixture.manifest);f.manifest.manifest_id=body.manifest_id;f.manifest.revision=body.expected_revision+1;
       Object.assign(f.manifest.source_ref,body.source_ref);f.manifest.task_ref=body.task_ref;
       f.manifest.binding_mode=body.task_ref?'context_task_reference':'project_only';
       if(scenario==='task-refresh-existing')f.manifest.source_seq=body.expected_revision?9:7;
       if(scenario==='bad-manifest-source')f.manifest.source_ref.root_identity='0'.repeat(64);
       if(scenario==='stale'){f.manifest.status='stale';f.manifest.stale_reasons=['project_revision_changed'];f.manifest.items=[];}
       await sign(f.manifest);f.done=true;return copy(f.manifest);
      }throw Error('unexpected synthetic POST');
     }
     if(url.includes('/context-task-references/'))return copy(f.target);
     if(url.includes('/workspace-context-manifests/')){if(!f.manifest)throw Object.assign(Error('absent'),{status:403});return copy(f.manifest);}
     if(url.includes('/workspace-snapshots/')){if(!f.snapshot)throw Object.assign(Error('absent'),{status:403});return copy(f.snapshot);}
     if(url.includes('/workspace-snapshots')){
      f.pageRequests++;const v=await list('snapshots',f.snapshot);
      if(scenario==='pagination'&&!url.includes('cursor=')){v.has_more=true;v.next_cursor='synthetic-cursor';v.total=2;v.page_count=0;v.snapshots=[];}
      return v;
     }
     if(url.includes('/workspace-context-manifests'))return list('manifests',f.manifest);
     throw Error('unexpected synthetic GET '+url);
    }});await openWorkspace();
   },{fixture,scenario});
   if(scenario==='pagination'){
    await page.locator('[data-ws-action=more-snapshots]').click();assert.equal(await page.evaluate(()=>f.pageRequests),2);
   }else{
    if(scenario!=='no-confirm')await page.locator('[data-ws-read-confirm]').check();
    await page.locator('[data-ws-action=preview]').click();
    if(['no-confirm','bad-preview'].includes(scenario)){
     await page.waitForFunction(()=>!document.querySelector('[data-ws-action=preview]').disabled);
     await page.locator('[data-ws-save-confirm]').check();await page.locator('[data-ws-action=capture]').click();
     assert.equal(await page.evaluate(()=>f.posts.filter(p=>p.url.endsWith('/workspace-snapshots')).length),0);
    }else{
     await page.waitForFunction(()=>document.querySelector('[data-ws-status]').textContent.includes('可确认'));
     await page.locator('[data-ws-save-confirm]').check();await page.locator('[data-ws-action=capture]').click();await page.waitForFunction(()=>f.entered);
     if(scenario==='late-edit')await page.locator('[data-ws-paths]').fill('new.txt');
     if(scenario==='late-refresh')await page.locator('[data-ws-action=refresh]').click();
     if(['late-close','late-row'].includes(scenario)){await page.locator('#workspaceInputsDialog').press('Escape');await page.evaluate(other=>openWorkspace(other),scenario==='late-row');}
     if(scenario.startsWith('late-'))await page.evaluate(()=>f.release());
     await page.waitForFunction(()=>f.done);await page.waitForFunction(()=>!document.querySelector('[data-ws-action=capture]').disabled);
     if(scenario.startsWith('late-')){
      assert.equal(await page.locator('[data-ws-result]').textContent(),'');assert.equal(await page.locator('[data-ws-save-confirm]').isChecked(),false);
      if(['late-edit','late-refresh'].includes(scenario))assert.match(await page.locator('[data-ws-status]').textContent(),/原命令已精确读回/);
     }else if(['bad-receipt','wrong-source'].includes(scenario))assert.equal(await page.locator('[data-ws-result]').textContent(),'');
     else if(scenario==='unknown'||scenario==='unknown-retry409'){
      const initial=await page.evaluate(()=>f.posts.filter(p=>p.url.endsWith('/workspace-snapshots')));assert.equal(initial.length,1);
      if(scenario==='unknown-retry409'){
       await page.locator('[data-ws-action=retry]').click();await page.waitForFunction(()=>f.posts.filter(p=>p.url.endsWith('/workspace-snapshots')).length===2);
       await page.waitForFunction(()=>!document.querySelector('[data-ws-action=retry]').disabled);
       const rows=await page.evaluate(()=>f.posts.filter(p=>p.url.endsWith('/workspace-snapshots')));assert.deepEqual(rows[0],rows[1]);
      }
      await page.locator('[data-ws-action=reconcile]').click();await page.waitForFunction(()=>document.querySelector('[data-ws-status]').textContent.includes('持久确认'));
      assert.equal(await page.evaluate(()=>f.posts.filter(p=>p.url.endsWith('/workspace-snapshots')).length),scenario==='unknown'?1:2);
     }else{
      await page.waitForFunction(()=>document.querySelector('[data-ws-status]').textContent.includes('持久确认'));
      if(scenario==='task-refresh-existing')await page.locator('[data-ws-target]').selectOption(fixture.task.product_task_id);
      await page.locator('[data-ws-save-confirm]').check();await page.locator('[data-ws-action=manifest]').click();
      if(scenario==='bad-manifest-source'){
       await page.waitForFunction(()=>document.querySelector('[data-ws-status]').textContent.includes('结果未确认'));
       assert.equal(await page.locator('[data-ws-result]').textContent(),'');assert.equal(await page.locator('[data-ws-action=reconcile]').isDisabled(),false);assert.deepEqual(errors,[]);return;
      }
      await page.waitForFunction(()=>document.querySelector('[data-ws-result]').textContent.includes('source_ref'));
      const body=await page.evaluate(()=>JSON.parse(f.posts.at(-1).body));assert.equal(body.schema_version,2);assert.equal(Object.keys(body.source_ref).length,4);
      if(scenario==='stale')assert.match(await page.locator('[data-ws-status]').textContent(),/过期/);
      else assert.match(await page.locator('[data-ws-result]').textContent(),/tracked_plus_selected_untracked|workspace_snapshot/);
      if(scenario==='refresh-manifest'){
       const original=body.manifest_id;await page.locator('[data-ws-action=refresh]').click();
       await page.locator('[data-ws-manifests] button').click();await page.waitForFunction(()=>document.querySelector('[data-ws-result]').textContent.includes('source_ref'));
       await page.locator('[data-ws-snapshots] button').click();await page.waitForFunction(()=>document.querySelector('[data-ws-result]').textContent.includes('tracked_paths'));
       await page.locator('[data-ws-save-confirm]').check();await page.locator('[data-ws-action=manifest]').click();
       await page.waitForFunction(()=>f.posts.filter(p=>p.url.endsWith('/workspace-context-manifests')).length===2);
       const updated=await page.evaluate(()=>JSON.parse(f.posts.at(-1).body));assert.equal(updated.manifest_id,original);assert.equal(updated.expected_revision,1);
      }
      if(scenario==='task-refresh-existing'){
       const original=body.manifest_id;await page.locator('[data-ws-title]').fill('Synthetic title r2');await page.locator('[data-ws-save-confirm]').check();await page.locator('[data-ws-action=task]').click();
       await page.waitForFunction(()=>document.querySelector('[data-ws-status]').textContent.includes('任务引用持久确认'));
       await page.locator('[data-ws-save-confirm]').check();await page.locator('[data-ws-action=manifest]').click();await page.waitForFunction(()=>f.posts.filter(p=>p.url.endsWith('/workspace-context-manifests')).length===2);
       const updated=await page.evaluate(()=>JSON.parse(f.posts.at(-1).body));assert.equal(updated.manifest_id,original);assert.equal(updated.expected_revision,1);assert.equal(updated.task_ref.revision,2);
      }
     }
    }
   }
   assert.deepEqual(errors,[]);
  }finally{await browser.close();}
 });
}
