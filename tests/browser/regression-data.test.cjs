// Real Chrome renderer with synthetic private API replies; actual HTTP is a separate gate.
const test=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const canonical=v=>JSON.stringify(v,(_,x)=>x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.entries(x).sort(([a],[b])=>a.localeCompare(b,'en'))):x);
const hash=s=>crypto.createHash('sha256').update(s).digest('hex');
const project={project_id:'p-'+'1'.repeat(16),revision:1,name:'回归项目'};
const execution='ex-'+'2'.repeat(16),cid='case-'+'3'.repeat(16),did='ds-'+'4'.repeat(16);
const origin={tenant_id:'test',subject_ref:'A',execution_ref:{execution_id:execution,revision:1,digest:'b'.repeat(64),source_seq:10},
 session_id:'s-pa-'+'5'.repeat(32),observed_seq:18,prefix_digest:'a'.repeat(64),failure_kind:'execution_failed',acceptance_ref:null};
const base='/api/v1/projects/'+project.project_id;

function evidence(scenario){return {schema_version:1,project_id:project.project_id,origin,
 observed_input_text:scenario==='safe-text'?'<img src=x onerror="window.rgXss=1"> public input':'public input',observed_input_state:'available',
 observed_output_text:null,observed_output_state:'unavailable',root_tool_call_count:1,
 policy:'bounded-public-regression-v1',assertion_version:'regression-assertions-v1'};}
function receipt(kind,selection,proof,key='controlled-key'){
 const value={schema_version:1,project_id:project.project_id,project_revision:1,revision:selection.expected_revision+1,
 created_by:'A',submission_key:key,selection};
 value[kind==='regression_case'?'case_id':'dataset_id']=selection[kind==='regression_case'?'case_id':'dataset_id'];
 if(kind==='regression_case')value.evidence=proof;
 const raw=canonical(value);return {...value,record_canonical:raw,digest:hash(raw),source_seq:20,created_at:'2026-10-20T00:00:00Z',status:'saved',persisted:true,execution_authorized:false};
}
function ref(value,key){return {[key]:value[key],revision:value.revision,digest:value.digest,source_seq:value.source_seq};}

async function scenario(name){
 const browser=await chromium.launch({channel:'chrome',headless:true});let release;
 try{
  const hold=new Promise(r=>release=r),proof=evidence(name),candidate={...proof,candidate_digest:hash(canonical(proof)),status:'confirmation_required',persisted:false,execution_authorized:false,historical_tools_replayed:false};
  const command={schema_version:1,case_id:cid,expected_revision:0,project_revision:1,title:'既有用例',origin,candidate_digest:candidate.candidate_digest,
   input_text:'public input',assertions:[{kind:'output_contains',expected:'done',pointer:null}],confirmed:true};
  let oldCase=receipt('regression_case',command,proof);
  if(name==='history'){
   const chosen={...command,assertions:[{kind:'json_pointer_equals',pointer:'/n',expected:9007199254740992}]};
   oldCase=receipt('regression_case',chosen,proof);
   oldCase.record_canonical=oldCase.record_canonical.replace('9007199254740992','9007199254740993');oldCase.digest=hash(oldCase.record_canonical);
  }
  const selection={schema_version:1,dataset_id:did,expected_revision:0,project_revision:1,title:'固定数据集',case_refs:[ref(oldCase,'case_id')],confirmed:true};
  const oldDataset=receipt('regression_dataset',selection),rawBundle=canonical({format:'synthetic-renderer-fixture',case_refs:selection.case_refs}),bundleDigest=hash(rawBundle);
  const state={realm:'A',posts:[],gets:[],entered:false,saved:null,pendingKey:null};
  const page=await browser.newPage({viewport:{width:1400,height:1200}});page.setDefaultTimeout(7000);
  await page.route('http://127.0.0.1:8771/**',r=>r.fulfill({contentType:'text/html',body:'<style>dialog{max-width:1100px;max-height:90vh;overflow:auto}textarea{width:95%;min-height:60px}</style><main></main>'}));
  await page.goto('http://127.0.0.1:8771/');
  await page.exposeFunction('mockApi',async(path,options={})=>{
   const url=path.split('?')[0],body=options.body?JSON.parse(options.body):null;
   if(path==='/api/account/me')return {account:{tenant:'test',user_id:state.realm,username:state.realm}};
   if(options.method==='POST')state.posts.push({path,body,headers:options.headers});else state.gets.push(path);
   if(url===base+'/regression-cases'&&!body)return {schema_version:1,project_id:project.project_id,items:[{...ref(oldCase,'case_id'),title:'既有用例',status:'available'}],total:1,execution_authorized:false};
   if(url===base+'/regression-datasets'&&!body)return {schema_version:1,project_id:project.project_id,items:[{...ref(oldDataset,'dataset_id'),title:'固定数据集',status:'available'}],total:1,execution_authorized:false};
   if(url===base+'/failure-candidates'){
    if(['identity','close'].includes(name)){state.entered=true;await hold;}
    if(name==='revoked')return {__deny:403};
    return name==='forged'?{...candidate,project_id:'p-'+'9'.repeat(16)}:candidate;
   }
   if(url===base+'/regression-cases/'+cid)return name==='forged-view'?{...oldCase,evidence:{...proof,observed_input_text:'foreign private content'}}:oldCase;
   if(url===base+'/regression-datasets/'+did)return oldDataset;
   if(url===base+'/regression-cases'&&body){
    if(name==='invalid-input')return {__deny:422};
    state.pendingKey=options.headers['Idempotency-Key'];state.saved=receipt('regression_case',body,proof,state.pendingKey);
    if(name==='lost-ack')throw Error('controlled lost acknowledgement');
    if(name==='forged-receipt')return receipt('regression_case',{...body,case_id:'case-'+'9'.repeat(16)},proof,state.pendingKey);
    return state.saved;
   }
   if(url===base+'/regression-submissions/'+state.pendingKey)return {schema_version:1,project_id:project.project_id,
    submission_key:state.pendingKey,status:'committed',resource_kind:'regression_case',receipt:state.saved,writer_requires_reopen:false,execution_authorized:false};
   if(url.endsWith('/export-previews'))return {schema_version:1,project_id:project.project_id,dataset_ref:ref(oldDataset,'dataset_id'),bundle_digest:bundleDigest,
    confirmation_digest:'c'.repeat(64),case_count:1,case_refs:selection.case_refs,title:'固定数据集',status:'confirmation_required',persisted:false,execution_authorized:false,export_authorized:false};
   if(url.endsWith('/exports'))return {schema_version:1,project_id:project.project_id,dataset_ref:ref(oldDataset,'dataset_id'),bundle_digest:name==='bad-export'?'0'.repeat(64):bundleDigest,
    bundle_canonical:rawBundle,suggested_filename:'pyharness-'+did+'-r1.json',content_type:'application/json',persisted:false,execution_authorized:false,export_authorized:true};
   if(url.endsWith('/regression-dataset-import-previews'))return {schema_version:1,project_id:project.project_id,dataset_id:body.dataset_id,
    source_dataset_ref:ref(oldDataset,'dataset_id'),bundle_digest:bundleDigest,case_count:1,case_refs:selection.case_refs,
    confirmation_digest:'d'.repeat(64),status:'confirmation_required',persisted:false,execution_authorized:false};
   if(url.endsWith('/regression-dataset-imports'))return receipt('regression_dataset',{
    schema_version:1,dataset_id:body.dataset_id,expected_revision:body.expected_revision,project_revision:1,title:body.title,
    case_refs:selection.case_refs,confirmed:true,import_source:{dataset_ref:ref(oldDataset,'dataset_id'),bundle_digest:bundleDigest}},null,options.headers['Idempotency-Key']);
   throw Error('unexpected renderer route '+path);
  });
  await page.addScriptTag({path:'pyharness/ui/host-adapters/workspace-input-contracts.js'});
  await page.addScriptTag({path:'pyharness/ui/host-adapters/regression-data.js'});
  await page.evaluate(async project=>{
   const api=async(...args)=>{const value=await mockApi(...args);if(value.__deny)throw Object.assign(Error('denied'),{status:value.__deny});return value;};
   window.controller=PyharnessRegressionData.create({main:document.querySelector('main'),api,esc:v=>String(v).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;')});
   await controller.open(project);
  },project);
  const click=a=>page.locator('[data-rg-action='+a+']').click(),settled=()=>page.waitForFunction(()=>document.querySelector('#regressionDataDialog')?.getAttribute('aria-busy')==='false');
  if(['export','bad-export'].includes(name)){
   await click('load-dataset');await settled();await click('preview-export');await settled();
   assert.equal(await page.locator('[data-rg-action=export]').isDisabled(),true);
   await page.check('[data-rg-confirm-export]');
   if(name==='export'){
    const download=page.waitForEvent('download');await click('export');assert.match((await download).suggestedFilename(),/\.json$/);await settled();
   }else{let downloaded=false;page.on('download',()=>downloaded=true);await click('export');await settled();assert.equal(downloaded,false);assert.match(await page.locator('[data-rg-status]').textContent(),/摘要|核对|不符/);}
  }else if(name==='import'){
   await click('new-dataset');await page.fill('[data-rg-dataset-title]','明确导入新集合');
   await page.setInputFiles('[data-rg-import-file]',{name:'dataset.json',mimeType:'application/json',buffer:Buffer.from(rawBundle)});
   await click('preview-import');await settled();assert.equal(await page.locator('[data-rg-action=import]').isDisabled(),true);
   await page.check('[data-rg-confirm-import]');await click('import');await settled();
   assert.equal(state.posts.filter(p=>p.path.endsWith('/regression-dataset-imports')).length,1);
  }else if(name==='history'||name==='forged-view'){
   await click('load-case');await settled();
   if(name==='history')assert.match(await page.locator('[data-rg-case-detail]').textContent(),/9007199254740993/);
   else{assert.equal(await page.locator('[data-rg-case-detail]').textContent(),'');assert.match(await page.locator('[data-rg-status]').textContent(),/不符/);}
   assert.equal(await page.locator('[data-rg-action=edit-case]').isDisabled(),true);
  }else{
   await page.fill('[data-rg-execution]',execution);await click('preview');
   if(['identity','close'].includes(name)){
    while(!state.entered)await new Promise(r=>setTimeout(r,10));
    if(name==='identity')state.realm='B';else await click('close');release();
    await page.waitForFunction(()=>!document.querySelector('#regressionDataDialog'));
   }else if(name==='revoked')await page.waitForFunction(()=>!document.querySelector('#regressionDataDialog'));
   else{
    await settled();
    if(name==='forged')assert.equal(await page.locator('[data-rg-action=save-case]').isDisabled(),true);
    else{
     assert.equal(await page.locator('[data-rg-action=save-case]').isDisabled(),true);
     if(name==='safe-text'){assert.equal(await page.locator('[data-rg-candidate] img').count(),0);assert.equal(await page.evaluate(()=>window.rgXss||null),null);}
     if(['confirm','lost-ack','forged-receipt','invalid-input'].includes(name)){
      await page.fill('[data-rg-case-title]','明确确认的新用例');await page.fill('[data-rg-input]','human reproduction');
      await page.fill('[data-rg-expected]','done');await page.check('[data-rg-confirm-case]');await click('save-case');await settled();
      assert.equal(state.posts.filter(p=>p.path.endsWith('/regression-cases')).length,1);
      if(name==='lost-ack'){await click('query');await settled();assert.equal(state.posts.filter(p=>p.path.endsWith('/regression-cases')).length,1);}
      if(name==='invalid-input'){assert.equal(await page.locator('[data-rg-input]').isDisabled(),false);assert.equal(await page.locator('[data-rg-confirm-case]').isChecked(),false);assert.match(await page.locator('[data-rg-status]').textContent(),/字段|修正/);}
      else if(name==='forged-receipt')assert.match(await page.locator('[data-rg-status]').textContent(),/未知|不符|查询/);
      else{assert.match(await page.locator('[data-rg-status]').textContent(),/保存|已确认/);assert.equal(await page.locator('[data-rg-case-select]').inputValue(),state.saved.case_id);}
     }
    }
   }
  }
  assert.equal(await page.evaluate(()=>localStorage.length),0);
  assert.ok(state.posts.every(p=>!p.path.includes('/executions')&&!p.path.includes('/approvals')));
 }finally{release?.();await browser.close();}
}
for(const name of ['preview','confirm','unconfirmed','lost-ack','identity','close','forged','safe-text','history','export','bad-export','import','revoked','forged-receipt','forged-view','invalid-input'])
 test('regression data workbench '+name,()=>scenario(name));
