const {test}=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const canonical=v=>JSON.stringify(v,(_,x)=>x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.entries(x).sort(([a],[b])=>a<b?-1:a>b?1:0)):x);
const hash=v=>crypto.createHash('sha256').update(canonical(v)).digest('hex'),rawHash=v=>crypto.createHash('sha256').update(v).digest('hex');
const ref=(v,k)=>Object.fromEntries([k,'revision','digest','source_seq'].map(n=>[n,v[n]]));
for(const scenario of ['read-only','grant','freeze','conflict','double-apply','lost-reply','forged-plan','identity-change','export-links','denied-after-commit']){
 test('safe writeback '+scenario,async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});let release;const hold=new Promise(r=>release=r);
  try{
   const project={project_id:'p-'+'1'.repeat(16),revision:1,name:'Synthetic writeback UI'},base='/api/v1/projects/'+project.project_id;
   const meta={revision:1,digest:'a'.repeat(64),source_seq:10,created_at:'2026-10-19T00:00:00Z'};
   const grant={...meta,schema_version:1,grant_id:'wg-'+'2'.repeat(16),project_revision:1,root_path:'C:/synthetic-explicit-target',recipient_username:'owner',paths:['result.txt'],actions:['replace'],expires_at:'2099-01-01T00:00:00Z',status:'granted'};
   const pkg={...meta,package_id:'dp-'+'3'.repeat(16)},accepted={...meta,acceptance_id:'av-'+'4'.repeat(16),package_ref:ref(pkg,'package_id'),business_acceptance:'passed'};
   const output={kind:'message',execution_id:'ex-'+'5'.repeat(16),session_id:'s-pa-'+'6'.repeat(32),task_id:'p-'+'7'.repeat(32),source_seq:12,record_digest:'b'.repeat(64),sha256:rawHash('done'),size_bytes:4,artifact_id:null};
   const file={target_path:'result.txt',target:{schema_version:1,path:'result.txt',size_bytes:8,sha256:rawHash('original'),mtime_ns:'100',mode:438,device:1,inode:2},base_digest:rawHash('original'),delivery_digest:output.sha256,merged_digest:output.sha256,merged_size:4,action:'replace',validation:{version:'text-policy-syntax-v1',status:'passed',checks:['bounded-text-policy'],reason:null}};
   const state={realm:'A',sequence:20,posts:[],receipts:new Map(),plans:new Map(),applications:new Map(),entered:false};
   const selection={schema_version:1,project_revision:1,grant_ref:ref(grant,'grant_id'),package_ref:ref(pkg,'package_id'),acceptance_ref:ref(accepted,'acceptance_id'),files:[{target_path:'result.txt',input_path:'unchanged.txt',output_ref:output,deletion_ref:null}]};
   function savedPlan(body,key){const record={schema_version:1,project_id:project.project_id,project_revision:1,created_by:'A',submission_key:key,revision:1,selection:body,merge_version:'exact-lines-three-way-v1',files:[file],basis_digest:body.confirmation_digest};
    const value={...body,...meta,project_id:project.project_id,project_revision:1,submission_key:key,digest:hash(record),source_seq:++state.sequence,files:[file],mappings:body.files,merge_version:record.merge_version,basis_digest:record.basis_digest,persisted:true,request_digest:hash({operation:'writeback.plan',project_id:project.project_id,command:body}),status:'frozen',applicable:false};
    value.approval_digest=hash({operation:'writeback.apply',actor:'A',plan_ref:ref(value,'plan_id'),basis_digest:value.basis_digest});return value;
   }
   const initial=savedPlan({...selection,plan_id:'mp-'+'8'.repeat(16),confirmation_digest:'c'.repeat(64)},'initial-plan');state.plans.set(initial.plan_id,initial);
   const exportValue={...meta,export_id:'we-'+'9'.repeat(16),plan_ref:ref(initial,'plan_id'),export_authorized:true};
   const page=await browser.newPage();page.setDefaultTimeout(7000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.route('http://127.0.0.1:8768/**',r=>r.fulfill({contentType:'text/html',body:'<main></main>'}));await page.goto('http://127.0.0.1:8768/');
   await page.exposeFunction('mockApi',async(path,options={})=>{
    const route=path.split('?')[0],fail=(status,message)=>({__error:{status,message}});
    if(route==='/api/account/me'&&state.denyMe)return fail(403,'membership withdrawn');
    if(route==='/api/account/me')return {account:{user_id:state.realm,username:'owner',tenant:'synthetic'}};
    if(route.endsWith('/writeback-center')){const item=(v,k)=>({resource_id:v[k],revision:v.revision,digest:v.digest,source_seq:v.source_seq});return {items:{writeback_grant:[item(grant,'grant_id')],merge_plan:[...state.plans.values()].map(v=>item(v,'plan_id')),writeback_application:[...state.applications.values()].map(v=>item(v,'application_id')),merge_export:[item(exportValue,'export_id')]}};}
    if(route.endsWith('/delivery-center'))return {items:{delivery_package:[{resource_id:pkg.package_id,...meta,owned:true}],delivery_acceptance:[{resource_id:accepted.acceptance_id,...meta,owned:false}]}};
    if(route.endsWith('/writeback-submissions/by-key')){const r=state.receipts.get(new URL('http://localhost'+path).searchParams.get('key'));return {status:r?'committed':'not_found',receipt:r||null};}
    if(route.endsWith('/merge-source-options')){if(scenario==='identity-change'){state.entered=true;await hold;}return {package_ref:ref(pkg,'package_id'),baseline_files:[{path:'unchanged.txt',sha256:file.base_digest,size_bytes:8}],outputs:[output],deletions:[]};}
    if(route.endsWith('/merge-previews')){state.posts.push({path,body:JSON.parse(options.body),preview:true});return {status:scenario==='conflict'?'conflict':'mergeable',confirmation_digest:'c'.repeat(64),files:[{...file,status:scenario==='conflict'?'conflict':'mergeable',conflicts:scenario==='conflict'?[{reason:'overlapping_changes'}]:[],diff:'<img src=x onerror=window.writebackXss=1>'}],applicable:false};}
    if(options.method==='POST'){
     const body=JSON.parse(options.body),key=options.headers['Idempotency-Key'];state.posts.push({path,body,key});
     let value,kind,idName,operation;
     if(route.endsWith('/writeback-grants')){operation='grant';kind='writeback_grant';idName='grant_id';value={...body,...meta,project_id:project.project_id,persisted:true,request_digest:hash({operation:'writeback.grant',project_id:project.project_id,command:body}),status:'granted'};}
     else if(route.endsWith('/merge-plans')){operation='plan';kind='merge_plan';idName='plan_id';value=savedPlan(body,key);state.plans.set(body.plan_id,value);}
     else if(route.endsWith('/writeback-applications')){
      operation='apply';kind='writeback_application';idName='application_id';
      const effects=[{target_path:file.target_path,action:file.action,status:'verified',before_digest:file.target.sha256,expected_digest:file.merged_digest,observed_digest:file.merged_digest}];
      const record={schema_version:1,project_id:project.project_id,project_revision:1,created_by:'A',submission_key:'synthetic-result-key',revision:2,selection:body,origin_key:key,status:'completed',effects};
      value={...body,...meta,project_id:project.project_id,project_revision:1,revision:2,submission_key:record.submission_key,origin_key:key,record_status:'completed',status:'completed',complete:true,effects,persisted:true,request_digest:hash({operation:'writeback.apply',project_id:project.project_id,command:body}),digest:hash(record),source_seq:++state.sequence,replay_allowed:false};state.applications.set(body.application_id,value);
     }else return fail(422,'unexpected mutation');
     state.receipts.set(key,{resource_kind:kind,resource_id:body[idName],revision:value.revision,digest:value.digest,source_seq:value.source_seq,request_digest:value.request_digest,persisted:true});
     if(operation==='apply'&&scenario==='denied-after-commit')state.denyMe=true;
     if(operation==='apply'&&scenario==='double-apply'){state.entered=true;await hold;}
     if(operation==='apply'&&scenario==='lost-reply')return fail(503,'lost reply after actual result persisted');
     if(operation==='plan'&&scenario==='forged-plan')return {...value,files:[{...file,target_path:'wrong-target.txt'}]};
     return value;
    }
    if(route===base+'/writeback-grants/'+grant.grant_id)return grant;
    if(route===base+'/delivery-packages/'+pkg.package_id)return pkg;
    if(route===base+'/delivery-acceptances/'+accepted.acceptance_id)return accepted;
    if(route.includes('/merge-plans/'))return state.plans.get(route.split('/').pop())||fail(404,'unknown plan');
    if(route.includes('/writeback-applications/'))return state.applications.get(route.split('/').pop())||fail(404,'unknown application');
    if(route.includes('/merge-plan-exports/'))return exportValue;
    return fail(404,'unknown '+route);
   });
   for(const file of ['workspace-input-contracts','writebacks'])await page.addScriptTag({path:'pyharness/ui/host-adapters/'+file+'.js'});
   await page.evaluate(async project=>{
    const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    window.controller=PyharnessWritebacks.create({main:document.querySelector('main'),esc,api:async(path,options)=>{const value=await mockApi(path,options);if(value?.__error){const e=Error(value.__error.message);e.status=value.__error.status;throw e;}return value;}});await controller.open(project);
   },project);
   const click=name=>page.locator('[data-wb-action='+name+']').click(),settled=()=>page.waitForFunction(()=>!document.querySelector('[data-wb-action=refresh]')?.disabled);
   assert.equal(state.posts.length,0);
   if(scenario==='grant'){
    await page.fill('[name=rootPath]','C:/synthetic-only-target');await page.fill('[name=paths]','result.txt');await page.locator('[data-wb-grant-form] button[type=submit]').click();await settled();assert.equal(state.posts.length,1);assert.deepEqual(state.posts[0].body.paths,['result.txt']);
   }else if(['freeze','conflict','forged-plan','identity-change'].includes(scenario)){
    await page.selectOption('[name=grant]',grant.grant_id);await page.selectOption('[name=package]',pkg.package_id);await page.selectOption('[name=acceptance]',accepted.acceptance_id);await click('sources');
    if(scenario==='identity-change'){
     await page.waitForFunction(()=>document.querySelector('[data-wb-action=sources]').disabled);state.realm='B';release();await page.waitForFunction(()=>!document.querySelector('#writebackDialog'));assert.equal(state.posts.length,0);
    }else{
     await settled();await page.check('[data-wb-include]');await page.selectOption('[data-wb-baseline]','unchanged.txt');await page.selectOption('[data-wb-output]','o-0');await click('preview');await settled();assert.equal(state.posts.length,1);assert.equal(await page.evaluate(()=>window.writebackXss||null),null);
     if(scenario==='conflict')assert.equal(await page.locator('[data-wb-action=save-plan]').isDisabled(),true);
     else{await click('save-plan');await settled();assert.equal(state.posts.length,2);
      if(scenario==='forged-plan'){assert.match(await page.locator('[data-wb-status]').textContent(),/摘要不符/);assert.equal(await page.locator('[data-wb-pending]').isVisible(),true);assert.equal(await page.locator('[data-wb-action=apply]').isDisabled(),true);}
      else assert.match(await page.locator('[data-wb-plan]').textContent(),/固定方案/);
     }
    }
   }else if(['double-apply','lost-reply','denied-after-commit'].includes(scenario)){
    await page.selectOption('[name=plan]',initial.plan_id);await click('read-plan');await settled();await page.check('[name=inspected]');await click('apply');
    if(scenario==='double-apply'){await page.waitForTimeout(30);await page.locator('[data-wb-action=apply]').dispatchEvent('click');release();}
    if(scenario==='denied-after-commit'){await page.waitForFunction(()=>!document.querySelector('#writebackDialog'));const kept=await page.evaluate(()=>JSON.stringify({...localStorage}));assert.ok(kept.includes('apply'),'committed original key must survive a later account denial');assert.equal(state.posts.length,1);return;}
    await settled();assert.equal(state.posts.length,1);
    if(scenario==='lost-reply'){assert.equal(await page.locator('[data-wb-pending]').isVisible(),true);await click('reconcile');await settled();assert.equal(state.posts.length,1);}
    assert.match(await page.locator('[data-wb-result]').textContent(),/全部目标已核验完成/);assert.equal(await page.locator('[data-wb-pending]').isVisible(),false);
   }else if(scenario==='export-links'){
    assert.equal(await page.locator('[data-wb-links] a').count(),0);await page.selectOption('[name=export]',exportValue.export_id);await click('read-export');await settled();assert.equal(state.posts.length,0);assert.equal(await page.locator('[data-wb-links] a').count(),1);assert.equal(await page.locator('[data-wb-links] a').getAttribute('href'),base+'/merge-plan-exports/'+exportValue.export_id+'/files/0');
   }
   if(await page.locator('#writebackDialog').count()){
    const stored=await page.evaluate(()=>JSON.stringify({...localStorage}));assert.ok(!stored.includes('root_path'));assert.ok(!stored.includes('synthetic-only-target'));assert.ok(!stored.includes('onerror'));
   }
   assert.deepEqual(errors,[]);
  }finally{release?.();await browser.close();}
 });
}
