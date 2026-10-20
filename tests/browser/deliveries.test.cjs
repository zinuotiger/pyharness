const {test}=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const canonical=v=>JSON.stringify(v,(_,x)=>x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.entries(x).sort(([a],[b])=>a<b?-1:a>b?1:0)):x);
const hash=v=>crypto.createHash('sha256').update(canonical(v)).digest('hex');
const reference=(v,k)=>Object.fromEntries([k,'revision','digest','source_seq'].map(n=>[n,v[n]]));

for(const scenario of ['read-only','freeze','double-freeze','lost-reply','forged-receipt','identity-change','review-unknown','export-links','pdf-preview-change']){
 test('delivery center '+scenario,async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});let release;const gate=new Promise(r=>release=r);
  try{
   const project={project_id:'p-'+'1'.repeat(16),revision:1,name:'Delivery fixture'},base='/api/v1/projects/'+project.project_id;
   const meta={revision:1,digest:'a'.repeat(64),source_seq:10,created_at:'2026-10-19T00:00:00Z'};
   const execution={...meta,execution_id:'ex-'+'2'.repeat(16),status:'completed',attempt_ref:{...meta,attempt_id:'ra-'+'3'.repeat(16)}};
   const definition={...meta,delivery_id:'del-'+'4'.repeat(16),items:[{item_id:'report',title:'Final report',required:true,acceptance_criteria:['<img src=x onerror=window.deliveryXss=1>'],citation_refs:[]}]};
   const output={kind:'message',execution_id:execution.execution_id,session_id:'s-pa-'+'5'.repeat(32),task_id:'p-'+'6'.repeat(32),source_seq:14,record_digest:'b'.repeat(64),sha256:'c'.repeat(64),size_bytes:4,artifact_id:null};
   const displayed={reference:output,filename:null,integrity:'verified',preview_capability:'text',text_preview:'<img src=x onerror=window.deliveryXss=1>',preview_truncated:false};
   const packaged={...meta,package_id:'dp-'+'7'.repeat(16),execution_ref:reference(execution,'execution_id'),delivery_ref:reference(definition,'delivery_id'),bindings:[{item_id:'report',outputs:[output]}],persisted:true};
   const handoff={...meta,handoff_id:'ho-'+'8'.repeat(16),package_ref:reference(packaged,'package_id'),role_ref:{...meta,role_id:'rc-'+'9'.repeat(16)},minimum_context:'<img src=x onerror=window.deliveryXss=1>',role:'reviewer',recipient_id:'B',producer_id:'A',acknowledged:false,delivery_items:definition.items,outputs:[displayed]};
   const exporter={...meta,export_id:'xp-'+'a'.repeat(16),purpose:'review',outputs:[output],export_authorized:true};
   const state={realm:['review-unknown','export-links','pdf-preview-change'].includes(scenario)?'B':'A',posts:[],receipts:new Map(),package:packaged,handoff,export:exporter,entered:false,sequence:20};
   const page=await browser.newPage();page.setDefaultTimeout(7000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.route('http://127.0.0.1:8767/**',r=>r.fulfill({contentType:'text/html',body:'<main></main>'}));await page.goto('http://127.0.0.1:8767/');
   await page.exposeFunction('mockApi',async(path,options={})=>{
    const route=path.split('?')[0],fail=(status,message)=>({__error:{status,message}});
    if(path==='/api/account/me')return {account:{tenant:'synthetic',user_id:state.realm}};
    if(route.endsWith('/delivery-submissions/by-key')){
     const key=new URL('http://localhost'+path).searchParams.get('key'),receipt=state.receipts.get(key);
     return {status:receipt?'committed':'not_found',receipt:receipt||null,writer_requires_reopen:false};
    }
    if(route.endsWith('/delivery-center')){
     const item=(v,k,owned)=>({resource_id:v[k],revision:v.revision,digest:v.digest,source_seq:v.source_seq,owned});
     return {executions:[execution],items:{delivery_package:[item(state.package,'package_id',true)],role_contract:[],role_handoff:[item(state.handoff,'handoff_id',state.realm==='A')],handoff_ack:[],delivery_acceptance:[],delivery_export:[item(state.export,'export_id',false)]}};
    }
    if(route.endsWith('/handoff-document-previews')){state.posts.push({path,body:JSON.parse(options.body)});return {confirmation_digest:'d'.repeat(64),page_count:3,unlocatable_pages:[2]};}
    if(options.method==='POST'){
     const body=JSON.parse(options.body),key=options.headers['Idempotency-Key'];state.posts.push({path,body,key});
     const info=route.endsWith('/delivery-packages')?['delivery_package','package_id']:route.endsWith('/handoff-acks')?['handoff_ack','ack_id']:route.endsWith('/delivery-acceptances')?['delivery_acceptance','acceptance_id']:route.endsWith('/handoff-documents')?['handoff_document','document_id']:null;
     if(!info)return fail(422,'unexpected mutation');
     if(info[0]==='handoff_document')return fail(409,'PDF confirmation required');
     const digest=hash({operation:info[0]+'.save',project_id:project.project_id,command:body});
     const saved={...body,revision:1,digest:hash(body),source_seq:++state.sequence,created_at:meta.created_at,persisted:true,request_digest:digest};
     if(info[0]==='delivery_package')state.package=saved;
     if(info[0]==='handoff_ack')state.handoff.acknowledged=true;
     if(info[0]==='delivery_acceptance')saved.business_acceptance=body.decisions.some(d=>d.verdict==='unknown')?'pending':'passed';
     state.receipts.set(key,{resource_kind:info[0],resource_id:body[info[1]],revision:1,digest:saved.digest,source_seq:saved.source_seq,request_digest:scenario==='forged-receipt'?'f'.repeat(64):digest,persisted:true});
     if(scenario==='double-freeze'){state.entered=true;await gate;}
     if(scenario==='lost-reply')return fail(503,'response lost after durable commit');
     return saved;
    }
    if(route.endsWith('/delivery-outputs')){if(scenario==='identity-change'){state.entered=true;await gate;}return {outputs:[displayed]};}
    if(route===base+'/executions')return {executions:[execution]};
    if(route===base+'/executions/'+execution.execution_id)return execution;
    if(route.includes('/run-attempts/'))return {context:{delivery_ref:reference(definition,'delivery_id')}};
    if(route.includes('/delivery-definitions/'))return definition;
    if(route.includes('/delivery-packages/'))return state.package;
    if(route.includes('/role-handoffs/'))return state.handoff;
    if(route.includes('/delivery-exports/'))return state.export;
    if(route==='/api/v1/agents')return {agents:[]};
    return fail(404,'not found '+route);
   });
   for(const file of ['workspace-input-contracts','deliveries'])await page.addScriptTag({path:'pyharness/ui/host-adapters/'+file+'.js'});
   await page.evaluate(async project=>{
    const escape=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    window.controller=PyharnessDeliveries.create({main:document.querySelector('main'),esc:escape,api:async(path,options)=>{const v=await mockApi(path,options);if(v?.__error){const e=Error(v.__error.message);e.status=v.__error.status;throw e;}return v;}});
    await controller.open(project);
   },project);
   assert.equal(state.posts.length,0);
   const click=action=>page.locator('[data-delivery-action='+action+']').click();
   const settled=()=>page.waitForFunction(()=>!document.querySelector('[data-delivery-action=refresh]')?.disabled);
   if(['freeze','double-freeze','lost-reply','forged-receipt','identity-change'].includes(scenario)){
    await page.selectOption('[name=execution]',execution.execution_id);await click('outputs');
    if(scenario==='identity-change'){
     await page.waitForFunction(()=>document.querySelector('[data-delivery-action=outputs]').disabled);state.realm='B';release();
     await page.waitForFunction(()=>!document.querySelector('#deliveryCenterDialog'));assert.equal(state.posts.length,0);
    }else{
     await settled();await page.selectOption('[data-delivery-item=report]','0');
     const submit=page.locator('[data-delivery-form=delivery_package] button[type=submit]');await submit.click();
     if(scenario==='double-freeze'){await page.waitForTimeout(30);await submit.dispatchEvent('click');release();}
     await settled();assert.equal(state.posts.length,1);assert.equal(state.posts[0].body.bindings[0].outputs[0].source_seq,14);
     if(scenario==='lost-reply'){assert.equal(await page.locator('[data-delivery-pending]').isVisible(),true);await click('reconcile');await settled();assert.equal(state.posts.length,1);}
     if(scenario==='forged-receipt'){assert.equal(await page.locator('[data-delivery-pending]').isVisible(),true);assert.match(await page.locator('[data-delivery-status]').textContent(),/不符/);}
     else assert.equal(await page.locator('[data-delivery-pending]').isVisible(),false);
     const stored=await page.evaluate(()=>JSON.stringify({...localStorage}));assert.ok(!stored.includes('onerror'));assert.ok(!stored.includes('minimum_context'));
    }
   }
   if(['review-unknown','pdf-preview-change'].includes(scenario)){
    await page.selectOption('[name=handoff]',handoff.handoff_id);await click('handoff');await settled();assert.equal(await page.locator('[data-delivery-criterion="0"]').inputValue(),'unknown');
    assert.equal(await page.evaluate(()=>window.deliveryXss||null),null);
    if(scenario==='review-unknown'){
     await click('ack');await settled();await page.check('[name=inspected]');await page.locator('[data-delivery-form=delivery_acceptance] button[type=submit]').click();await settled();
     assert.equal(state.posts.length,2);assert.equal(state.posts[1].body.decisions[0].verdict,'unknown');assert.match(await page.locator('[data-delivery-status]').textContent(),/尚无法确认/);
    }else{
     await page.selectOption('[name=importOutput]','0');await page.fill('[name=importTitle]','Exact PDF');await page.selectOption('[name=importFormat]','pdf');await click('pdf-preview');await settled();
     assert.match(await page.locator('[data-delivery-import-result]').textContent(),/3 个物理页/);
     await page.fill('[name=importTitle]','Changed after preview');await page.locator('[data-delivery-form=handoff_document] button[type=submit]').click();await settled();
     assert.equal(state.posts[1].body.confirmation_digest,null);
    }
   }
   if(scenario==='export-links'){
    assert.equal(await page.locator('[data-delivery-download-links] a').count(),0);
    await page.selectOption('[name=export]',exporter.export_id);await click('export');await settled();
    assert.equal(await page.locator('[data-delivery-download-links] a').count(),1);
    assert.equal(await page.locator('[data-delivery-download-links] a').getAttribute('href'),base+'/delivery-exports/'+exporter.export_id+'/outputs/0');assert.equal(state.posts.length,0);
   }
   if(scenario==='read-only'){
    assert.match(await page.locator('#deliveryCenterDialog').textContent(),/独立验收/);assert.equal(state.posts.length,0);
    if(process.env.DELIVERY_SCREENSHOT)await page.screenshot({path:process.env.DELIVERY_SCREENSHOT,fullPage:true});
   }
   assert.deepEqual(errors,[]);
  }finally{release?.();await browser.close();}
 });
}
