/* Synthetic API: unknown old command must remain recoverable after parent advances. */
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fixture=JSON.parse(fs.readFileSync('tests/browser/fixtures/owned-inputs.json','utf8'));
for(const kind of ['workspace','selected','selected-parent'])test(`unknown input r1 recovery after parent r2 ${kind}`,async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const page=await browser.newPage();page.setDefaultTimeout(4000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://127.0.0.1:8765/**',r=>r.fulfill({body:'<main></main>',contentType:'text/html'}));await page.goto('http://127.0.0.1:8765/');
  for(const name of ['workspace-input-contracts','selected-input-contracts','owned-inputs','context-manifests'])await page.addScriptTag({path:`pyharness/ui/host-adapters/${name}.js`});
  await page.evaluate(async({fixture,kind})=>{
   const clone=v=>JSON.parse(JSON.stringify(v)),c=PyharnessWorkspaceContracts,selected=kind!=='workspace';
   const f=window.f={posts:[],calls:[],fixture};let cm=clone(fixture.manifest),input=clone(fixture.input),binding=clone(fixture.binding);
   const seal=async(v,key='receipt_digest')=>{delete v[key];v[key]=await c.digest(v);return v;};
   let descriptor;
   if(selected){
    const ref=cm.source_ref;
    cm={schema_version:1,manifest_id:cm.manifest_id,revision:1,project_id:cm.project_id,project_revision:1,grant_id:ref.grant_id,grant_revision:1,
     snapshot_id:'ss-0123456789abcdef',snapshot_revision:1,snapshot_digest:ref.digest,snapshot_source_seq:ref.source_seq,task_ref:null,policy:cm.policy,
     items:[{input_index:0,path:'unchanged.txt',size_bytes:11,content_digest:'a'.repeat(64)}],total_size_bytes:11,file_count:1,digest:cm.digest,source_seq:cm.source_seq,created_at:cm.created_at,status:'current',stale_reasons:[],binding_mode:'project_only',executable:false};
    binding={project:cm.project_id,grant:cm.grant_id,grantRevision:1};
    const source={source_kind:'source_snapshot',snapshot_id:cm.snapshot_id,revision:1,digest:cm.snapshot_digest,source_seq:cm.snapshot_source_seq,
     grant_id:cm.grant_id,grant_revision:1,grant_source_seq:ref.grant_source_seq,root_identity:ref.root_identity,confirmation_digest:ref.confirmation_digest};
    descriptor={schema_version:1,status:'selected_inputs_observed',project_id:cm.project_id,project_revision:1,manifest_ref:input.manifest_ref,
     source_ref:source,task_ref:null,files:cm.items.map(i=>({...i,native_mode:438,applied_mode:384})),deletions:[],total_size_bytes:11,policy:'private-selected-copy-v1',file_count:1,deletion_count:0,persisted:false,executable:false};
    await seal(descriptor,'descriptor_digest');input={...input,schema_version:2,source_ref:source,files:descriptor.files,deletions:[],total_size_bytes:11,file_count:1,deletion_count:0,policy:'private-selected-copy-v1'};
   }
   f.cm=clone(cm);f.latest=clone(cm);f.binding=clone(binding);
   const api=async(url,options={})=>{
    f.calls.push(url);
    if(url==='/api/v1/projects')return {projects:[{project:{project_id:cm.project_id,revision:1,archived:false}}],total:1};
    if(url.endsWith('/context-task-references'))return {task_references:[],total:0};
    if(url.endsWith('/context-manifests')){const row=clone(f.latest);delete row.items;return {manifests:[row],total:1};}
    if(options.method==='POST'){
     f.posts.push({body:options.body,key:options.headers['Idempotency-Key']});input.input_id=JSON.parse(options.body).input_id;
     await seal(input);throw Object.assign(Error('synthetic response lost after commit'),{status:503});
    }
    if(url.endsWith('/input-descriptor'))return clone(descriptor);
    if(url.includes('/context-manifests/')||url.includes('/workspace-context-manifests/'))return clone(url.endsWith('/2')?f.latest:cm);
    if(url.includes('/workspace-snapshots/'))return clone(fixture.snapshot);
    if(url.includes('/workspace-inputs/')){const v={...clone(input),status:'stale',stale_reasons:['manifest_revision_superseded'],files:[],deletions:[]};return seal(v);}
    throw Error('unexpected synthetic route');
   };
   if(kind==='selected-parent'){
    const trigger=document.createElement('button');document.body.append(trigger);window.parentCtrl=PyharnessContextManifests.create({main:document.querySelector('main'),api});
    await parentCtrl.open({...binding,snapshotId:cm.snapshot_id,snapshotDigest:cm.snapshot_digest,fileCount:cm.file_count,totalSize:cm.total_size_bytes,sourceSeq:cm.snapshot_source_seq},trigger);
   }else{window.ctrl=PyharnessOwnedInputs.create({main:document.querySelector('main'),api,contextKind:kind});ctrl.open(binding,cm);}
  },{fixture,kind});
  if(kind==='selected-parent'){await page.locator('[data-cm-rows] button').click();await page.waitForFunction(()=>!document.querySelector('[data-cm-action=inputs]').disabled);await page.locator('[data-cm-action=inputs]').click();}
  await page.locator('[data-oi-action=read]').click();await page.waitForFunction(()=>document.querySelector('[data-oi-status]').textContent.includes('来源已精确核对'));
  await page.locator('[data-oi-confirm]').check();await page.locator('[data-oi-action=prepare]').click();await page.waitForFunction(()=>document.querySelector('[data-oi-status]').textContent.includes('结果未知'));
  const original=await page.evaluate(()=>f.posts[0]);await page.keyboard.press('Escape');
  if(kind==='selected-parent'){
   await page.evaluate(()=>{f.latest={...f.cm,revision:2,digest:'f'.repeat(64),source_seq:f.cm.source_seq+1};});
   await page.locator('[data-cm-action=refresh]').click();await page.waitForFunction(()=>document.querySelector('[data-cm-rows]')?.textContent.includes('r2'));
   await page.locator('[data-cm-rows] button').click();await page.waitForFunction(()=>!document.querySelector('[data-cm-action=inputs]').disabled);await page.locator('[data-cm-action=inputs]').click();
  }else await page.evaluate(()=>ctrl.open(f.binding,{...f.cm,revision:2,digest:'f'.repeat(64),source_seq:f.cm.source_seq+1}));
  assert.equal(await page.locator('[data-oi-action=reconcile]').isDisabled(),true);const calls=await page.evaluate(()=>f.calls.length);
  await page.locator('[data-oi-action=original]').click();
  assert.equal(await page.evaluate(()=>f.calls.length),calls,'returning to original metadata must not query or replay');
  assert.equal(await page.locator('[data-oi-confirm]').isChecked(),false);assert.equal(await page.locator('[data-oi-action=reconcile]').isDisabled(),false);
  await page.locator('[data-oi-action=reconcile]').click();await page.waitForFunction(()=>document.querySelector('[data-oi-status]').textContent.includes('过期输入定义'));
  const result=JSON.parse(await page.locator('[data-oi-result]').textContent());assert.equal(result.input_id,JSON.parse(original.body).input_id);assert.equal(result.manifest_ref.revision,1);
  assert.deepEqual(await page.evaluate(()=>f.posts),[original]);assert.equal(await page.locator('[data-oi-confirm]').isChecked(),false);assert.equal(await page.locator('[data-oi-action=retry]').isDisabled(),true);assert.deepEqual(errors,[]);
 }finally{await browser.close();}
});
