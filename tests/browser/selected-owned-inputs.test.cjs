const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fixture=JSON.parse(fs.readFileSync('tests/browser/fixtures/owned-inputs.json','utf8'));
const old=fixture.manifest,oldRef=old.source_ref;
fixture.binding={project:old.project_id,grant:oldRef.grant_id,grantRevision:oldRef.grant_revision};
fixture.manifest={schema_version:1,manifest_id:old.manifest_id,revision:old.revision,project_id:old.project_id,project_revision:old.project_revision,
 grant_id:oldRef.grant_id,grant_revision:oldRef.grant_revision,snapshot_id:'ss-0123456789abcdef',snapshot_revision:1,snapshot_digest:oldRef.digest,snapshot_source_seq:oldRef.source_seq,
 task_ref:null,policy:old.policy,items:[{input_index:0,path:'unchanged.txt',size_bytes:11,content_digest:'a'.repeat(64)}],total_size_bytes:11,file_count:1,digest:old.digest,source_seq:old.source_seq,
 created_at:old.created_at,status:'current',stale_reasons:[],binding_mode:'project_only',executable:false};
const cm=fixture.manifest,source={source_kind:'source_snapshot',snapshot_id:cm.snapshot_id,revision:1,digest:cm.snapshot_digest,source_seq:cm.snapshot_source_seq,
 grant_id:cm.grant_id,grant_revision:cm.grant_revision,grant_source_seq:oldRef.grant_source_seq,root_identity:oldRef.root_identity,confirmation_digest:oldRef.confirmation_digest};
fixture.descriptor={schema_version:1,status:'selected_inputs_observed',project_id:cm.project_id,project_revision:cm.project_revision,
 manifest_ref:Object.fromEntries(['manifest_id','revision','digest','source_seq'].map(k=>[k,cm[k]])),source_ref:source,task_ref:null,
 files:cm.items.map(i=>({...i,native_mode:438,applied_mode:384})),deletions:[],total_size_bytes:11,policy:'private-selected-copy-v1',file_count:1,deletion_count:0,persisted:false,executable:false};
fixture.input={...fixture.input,schema_version:2,source_ref:source,files:fixture.descriptor.files,deletions:[],total_size_bytes:11,file_count:1,deletion_count:0,policy:'private-selected-copy-v1'};
for(const scenario of ['prepare','default','bad-digest','wrong-source','wrong-mode','readback-different','late-close','late-refresh','unknown','same-body-retry','unknown-retry409','unknown-retry403','expiry','stale','list','late-command-edit']){
 test(`selected owned inputs ${scenario}`,async()=>{
  const browser=await chromium.launch({headless:true,channel:'chrome'});
  try{
   const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(6000);
   // Give WebCrypto a secure loopback origin without any real server or external request.
   await page.route('http://127.0.0.1:8765/**',route=>route.fulfill({body:'<main></main>',contentType:'text/html'}));await page.goto('http://127.0.0.1:8765/');
   await page.addScriptTag({path:'pyharness/ui/host-adapters/workspace-input-contracts.js'});
   await page.addScriptTag({path:'pyharness/ui/host-adapters/selected-input-contracts.js'});
   await page.addScriptTag({path:'pyharness/ui/host-adapters/owned-inputs.js'});
   await page.evaluate(async({fixture,scenario})=>{
    const clone=v=>JSON.parse(JSON.stringify(v)),c=window.PyharnessWorkspaceContracts;
    const f=window.f={fixture,scenario,calls:[],posts:[],receipt:null,done:false};f.gate=new Promise(resolve=>f.release=resolve);
    const seal=async v=>{delete v.receipt_digest;v.receipt_digest=await c.digest(v);return v;};
    await seal(fixture.input);
    const api=async(url,options={})=>{
     f.calls.push(url);
     if(url.endsWith('/input-descriptor')){const d=clone(fixture.descriptor);d.descriptor_digest=await c.digest(d);return d;}
     if(url.includes('/context-manifests/')){
      if(scenario.startsWith('late-')&&!['late-command-edit'].includes(scenario)){f.entered=true;await f.gate;f.done=true;}
      const cm=clone(fixture.manifest);if(scenario==='stale'){cm.status='stale';cm.stale_reasons=['project_revision_changed'];cm.items=[];await seal(cm);}return cm;
     }
     if(url.includes('/workspace-snapshots/'))return clone(fixture.snapshot);
     if(options.method==='POST'){
      f.posts.push({body:options.body,key:options.headers['Idempotency-Key']});const body=JSON.parse(options.body);
      let v=clone(fixture.input);v.input_id=body.input_id;await seal(v);f.receipt=v;
      if(scenario==='late-command-edit'){f.entered=true;await f.gate;f.done=true;}
      if(scenario==='expiry')throw Object.assign(Error('synthetic denied'),{status:403});
      if(['unknown','same-body-retry'].includes(scenario)&&f.posts.length===1)throw Object.assign(Error('synthetic unknown'),{status:503});
      if(scenario.startsWith('unknown-retry'))throw Object.assign(Error('synthetic retry'),f.posts.length===1?{}:{status:Number(scenario.slice(-3))});
      if(scenario==='bad-digest')v.receipt_digest='0'.repeat(64);
      if(scenario==='wrong-source'){v.source_ref.snapshot_id='ss-eeeeeeeeeeeeeeee';await seal(v);}
      if(scenario==='wrong-mode'){v.files[0].native_mode=(v.files[0].native_mode+1)%512;await seal(v);}
      return clone(v);
     }
     if(url.includes('/workspace-inputs/')){
      let v=clone(f.receipt||fixture.input);
      if(scenario==='readback-different'){v.files[0].content_digest='f'.repeat(64);await seal(v);}return v;
     }
     if(url.includes('/workspace-inputs?')){
      const row=clone(fixture.input);delete row.files;delete row.deletions;await seal(row);
      return {schema_version:1,inputs:[row],total:1,page_count:1,has_more:false,next_cursor:null,source_watermark:row.source_seq,view_digest:'a'.repeat(64)};
     }
     throw Error('unexpected synthetic API route');
    };
    window.ctrl=window.PyharnessOwnedInputs.create({main:document.querySelector('main'),api,contextKind:'selected'});ctrl.open(fixture.binding,fixture.manifest);
   },{fixture,scenario});
   assert.equal(await page.locator('[data-oi-confirm]').isChecked(),false);assert.equal(await page.evaluate(()=>f.calls.length),0);
   if(scenario==='default'){
    await page.locator('[data-oi-action=prepare]').click();assert.equal(await page.evaluate(()=>f.posts.length),0);
   }else if(scenario==='list'){
    await page.locator('[data-oi-action=list]').click();await page.waitForFunction(()=>document.querySelector('[data-oi-list] button'));
    await page.locator('[data-oi-list] button').click();await page.waitForFunction(()=>document.querySelector('[data-oi-status]').textContent.includes('持久确认'));
    assert.equal(await page.locator('[data-oi-confirm]').isChecked(),false);assert.equal(await page.evaluate(()=>f.posts.length),0);
   }else{
    await page.locator('[data-oi-action=read]').click();
    if(scenario==='late-close'||scenario==='late-refresh'){
     await page.waitForFunction(()=>f.entered);
     if(scenario==='late-close'){await page.keyboard.press('Escape');await page.evaluate(()=>ctrl.open(f.fixture.binding,f.fixture.manifest));}
     else await page.locator('[data-oi-action=refresh]').click();
     await page.evaluate(()=>f.release());await page.waitForFunction(()=>f.done);await page.waitForTimeout(30);
     assert.equal(await page.locator('[data-oi-result]').textContent(),'');assert.equal(await page.locator('[data-oi-confirm]').isChecked(),false);
     assert.equal(await page.locator('[data-oi-action=read]').isDisabled(),false);assert.equal(await page.evaluate(()=>f.posts.length),0);
    }else if(scenario==='stale'){
     await page.waitForFunction(()=>document.querySelector('[data-oi-status]').textContent.includes('被拒绝'));
     await page.locator('[data-oi-confirm]').check();await page.locator('[data-oi-action=prepare]').click();assert.equal(await page.evaluate(()=>f.posts.length),0);
    }else{
     await page.waitForFunction(()=>document.querySelector('[data-oi-status]').textContent.includes('来源已精确核对'));
     await page.locator('[data-oi-confirm]').check();await page.locator('[data-oi-action=prepare]').click();
     if(scenario==='late-command-edit'){
      await page.waitForFunction(()=>f.entered);await page.locator('[data-oi-action=refresh]').click();await page.evaluate(()=>f.release());await page.waitForFunction(()=>f.done);
      await page.waitForFunction(()=>document.querySelector('[data-oi-status]').textContent.includes('当前观察已失效'));
      assert.equal(await page.locator('[data-oi-confirm]').isChecked(),false);assert.equal(await page.locator('[data-oi-result]').textContent(),'');
     }else if(scenario==='prepare'){
      await page.waitForFunction(()=>document.querySelector('[data-oi-status]').textContent.includes('持久确认'));assert.equal(await page.evaluate(()=>JSON.parse(f.posts[0].body).schema_version),2);assert.equal(await page.evaluate(()=>f.posts.length),1);
     }else if(scenario==='expiry'){
      await page.waitForFunction(()=>document.querySelector('[data-oi-status]').textContent.includes('准备被拒绝'));assert.equal(await page.locator('[data-oi-action=retry]').isDisabled(),true);
     }else{
      await page.waitForFunction(()=>document.querySelector('[data-oi-status]').textContent.includes('结果未知'));assert.equal(await page.evaluate(()=>f.posts.length),1);
      if(scenario==='unknown'){
       await page.keyboard.press('Escape');await page.evaluate(()=>ctrl.open(f.fixture.binding,f.fixture.manifest));await page.locator('[data-oi-action=reconcile]').click();
       await page.waitForFunction(()=>document.querySelector('[data-oi-status]').textContent.includes('持久确认'));assert.equal(await page.evaluate(()=>f.posts.length),1);
      }else if(scenario==='same-body-retry'){
       await page.locator('[data-oi-action=retry]').click();await page.waitForFunction(()=>document.querySelector('[data-oi-status]').textContent.includes('持久确认'));
       const posts=await page.evaluate(()=>f.posts);assert.equal(posts.length,2);assert.deepEqual(posts[0],posts[1]);
      }else if(scenario.startsWith('unknown-retry')){
       await page.locator('[data-oi-action=retry]').click();await page.waitForFunction(status=>document.querySelector('[data-oi-status]').textContent.includes(status),scenario.slice(-3));
       assert.equal(await page.locator('[data-oi-action=reconcile]').isDisabled(),false,'unknown command was discarded on later rejection');
       const posts=await page.evaluate(()=>f.posts);assert.equal(posts.length,2);assert.deepEqual(posts[0],posts[1]);
       await page.keyboard.press('Escape');await page.evaluate(()=>ctrl.open(f.fixture.binding,f.fixture.manifest));
       await page.locator('[data-oi-action=reconcile]').click();await page.waitForFunction(()=>document.querySelector('[data-oi-status]').textContent.includes('持久确认'));
       assert.equal(await page.evaluate(()=>f.posts.length),2);
      }
     }
    }
   }
   assert.deepEqual(errors,[]);
  }finally{await browser.close();}
 });
}
