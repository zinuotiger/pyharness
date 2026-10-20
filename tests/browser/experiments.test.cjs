// Real Chrome rendering with synthetic private replies. Engine/HTTP proof is separate.
const test=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const canonical=v=>JSON.stringify(v,(_,x)=>x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.entries(x).sort(([a],[b])=>a.localeCompare(b,'en'))):x);
const hash=v=>crypto.createHash('sha256').update(v).digest('hex'),digest=v=>hash(canonical(v));
const project={project_id:'p-'+'1'.repeat(16),revision:1,name:'版本实验项目'},base='/api/v1/projects/'+project.project_id;
const cid='case-'+'2'.repeat(16),did='ds-'+'3'.repeat(16),agents=['a-'+'4'.repeat(16),'a-'+'5'.repeat(16)];
const agentRef=i=>({agent_id:agents[i],version:1,sha256:String(i+6).repeat(64)});
const reference=(v,key)=>Object.fromEntries([key,'revision','digest','source_seq'].map(k=>[k,v[k]]));
function record(value,seq){const raw=canonical(value);return {...value,digest:hash(raw),source_seq:seq,record_canonical:raw,persisted:true};}
function metadata(v,key){return {...reference(v,key),title:v.selection?.title,status:'available'};}
async function scenario(name){
 const browser=await chromium.launch({channel:'chrome',headless:true});let release;
 try{
  const hold=new Promise(r=>release=r),state={realm:'A',entered:false,posts:[],definition:null,run:null,report:null,cancel:null,revoked:false,keys:{}};
  const caseRow=record({schema_version:1,case_id:cid,project_id:project.project_id,project_revision:1,revision:1,created_by:'A',submission_key:'case-key',
   selection:{schema_version:1,title:name==='safe-text'?'<img src=x onerror="window.epXss=1">':'人工用例',input_text:'Case input',assertions:[{kind:'output_equals',expected:'done'}]}},10);
  const dataset=record({schema_version:1,dataset_id:did,project_id:project.project_id,project_revision:1,revision:1,created_by:'A',submission_key:'dataset-key',
   selection:{schema_version:1,title:'固定集合',case_refs:[reference(caseRow,'case_id')]}},11);
  const page=await browser.newPage({viewport:{width:1440,height:1100}});page.setDefaultTimeout(7000);
  await page.route('http://127.0.0.1:8784/**',r=>r.fulfill({contentType:'text/html',body:'<main></main>'}));await page.goto('http://127.0.0.1:8784/');
  const directory=(items,key)=>({schema_version:1,project_id:project.project_id,items:items.map(v=>metadata(v,key)),total:items.length,execution_authorized:false,automatic_dispatch:false});
  function reportData(){const runref=reference(state.run,'experiment_run_id'),defref=reference(state.definition,'experiment_id');return {
   schema_version:1,experiment_run_ref:runref,experiment_ref:defref,graph_run_ref:null,dataset_ref:reference(dataset,'dataset_id'),dataset_total:1,selected_count:1,excluded_count:0,
   basis:{control:{observed_seq:30,prefix_digest:'a'.repeat(64)},nodes:{c0_left:null,c0_right:null}},checker_version:'regression-assertions-v1',checker_implementation:'b'.repeat(64),
   comparison_contract:'fixed-experiment-input-and-declared-validator-v1',status_basis:'fixed-recorded-prefix-not-current-process-liveness',
   cases:[{case_ref:reference(caseRow,'case_id'),left:{quality:{verdict:'passed'}},right:{quality:{verdict:'failed'}},comparison_status:'aligned',incomparable_reasons:[]}],
   regressions:[{case_ref:reference(caseRow,'case_id'),kind:'quality_regression'}],execution_counts:{total:2,completed:2,failed:0,cancelled:0,unknown:0,interrupted:0,not_dispatched:0,nonterminal:0},
   quality_counts:{total:2,evaluable:2,passed:1,failed:1,unknown:0},business_counts:{total:2,evaluated:0,accepted:0,rejected:0,invalidated:0,not_evaluated:2},release_authorized:false};}
  await page.exposeFunction('mockApi',async(path,options={})=>{
   const url=path.split('?')[0],body=options.body?JSON.parse(options.body):null,key=options.headers?.['Idempotency-Key'];
   if(path==='/api/account/me')return {account:{tenant:'test',user_id:state.realm}};
   if(options.method==='POST')state.posts.push({path,body,key});
   if(url===base+'/regression-datasets')return {schema_version:1,project_id:project.project_id,items:[metadata(dataset,'dataset_id')],total:1,execution_authorized:false};
   if(url===base+'/regression-datasets/'+did)return dataset;
   if(url===base+'/regression-cases/'+cid)return name==='forged-case'?{...caseRow,selection:{...caseRow.selection,input_text:'foreign unverified private input'}}:caseRow;
   if(url==='/api/v1/agents')return {agents:agents.map((agent_id,i)=>({agent_id,name:'Agent '+i,status:'published'}))};
   for(let i=0;i<2;i++)if(url==='/api/v1/agents/'+agents[i]+'/versions')return {versions:[{...agentRef(i),snapshot:{status:'published'}}]};
   if(url===base+'/experiments'&&!body)return directory(state.definition?[state.definition]:[],'experiment_id');
   if(url===base+'/experiment-runs'&&!body)return directory(state.run?[state.run]:[],'experiment_run_id');
   if(url===base+'/experiment-reports'&&!body)return directory(state.report?[state.report]:[],'report_id');
   if(url===base+'/experiment-preparation-previews'){
    if(['identity','close'].includes(name)){state.entered=true;await hold;}
    return {...body,schema_version:1,project_id:project.project_id,case_refs:body.case_refs,selected_count:1,dataset_total:1,excluded_count:0,
     status:'confirmation_required',persisted:false,execution_authorized:false,confirmation_digest:'c'.repeat(64)};
   }
   if(url===base+'/experiments'&&body){
    const value={schema_version:1,experiment_id:body.experiment_id,project_id:project.project_id,project_revision:1,revision:1,created_by:'A',submission_key:'derived-finish-key',selection:body};
    state.definition={...record(value,20),execution_authorized:false,status:'prepared'};state.keys[key]=['experiment_preparation',state.definition];
    if(name==='forged-definition'){const forged=record({...value,selection:{...body,title:'different confirmed title'}},20);return {...state.definition,...forged};}
    return state.definition;
   }
   if(url===base+'/experiment-run-previews')return {schema_version:1,project_id:project.project_id,experiment_run_id:body.experiment_run_id,experiment_ref:body.experiment_ref,
    budget_grant:body.budget_grant,confirmation_digest:'d'.repeat(64),persisted:false,automatic_dispatch:false,
    graph_plan:{allocated_budget:{input_tokens:40000,output_tokens:8000,tool_calls:40,cost_microyuan:2000000},nodes:[{boundary:'disabled'},{boundary:'disabled'}]}};
   if(url===base+'/experiment-runs'&&body){
    const value={schema_version:1,experiment_run_id:body.experiment_run_id,project_id:project.project_id,project_revision:1,revision:1,created_by:'A',submission_key:key,selection:body};
    state.run={...record(value,25),automatic_dispatch:false,status:'completed',cancel_requested:false,nodes:[{node_id:'c0_left',status:'completed'},{node_id:'c0_right',status:'completed'}]};
    state.keys[key]=['experiment_run',state.run];if(name==='lost-start')throw Error('controlled lost start acknowledgement');return state.run;
   }
   if(url.startsWith(base+'/experiment-runs/')&&url.endsWith('/control'))return {schema_version:1,project_id:project.project_id,
    experiment_run_ref:reference(state.run,'experiment_run_id'),control_only:true,cancel_requested:!!state.cancel,submission_key:state.run.submission_key,
    request_digest:digest({operation:'experiment.start',project_id:project.project_id,command:state.run.selection}),automatic_dispatch:false};
   if(url.startsWith(base+'/experiment-runs/'))return state.revoked?{__deny:403}:state.run;
   if(url===base+'/experiment-report-previews'){
    const data=reportData(),raw=canonical(data);return {schema_version:1,report_id:body.report_id,experiment_run_ref:body.experiment_run_ref,basis:data.basis,
     report_data:data,report_canonical:raw,report_digest:hash(raw),confirmation_digest:'e'.repeat(64),export_authorized:false,release_authorized:false,persisted:false};
   }
   if(url===base+'/experiment-reports'&&body){
    const data=reportData(),value={schema_version:1,report_id:body.report_id,project_id:project.project_id,project_revision:1,revision:1,created_by:'A',submission_key:key,selection:body,report_data:data};
    state.report={...record(value,35),report_data:data,report_canonical:canonical(data),report_digest:digest(data),recomputed:true,export_authorized:false,release_authorized:false};
    state.keys[key]=['experiment_report',state.report];if(name==='pending-report-stop')throw Error('controlled lost report acknowledgement');
    if(name==='forged-report')return {...state.report,report_data:{...data,selected_count:9}};
    return state.report;
   }
   if(url.endsWith('/export-previews'))return {schema_version:1,report_id:state.report.report_id,report_digest:state.report.report_digest,confirmation_digest:'f'.repeat(64),export_authorized:false,release_authorized:false};
   if(url.endsWith('/exports'))return {schema_version:1,report_id:state.report.report_id,report_digest:name==='bad-export'?'0'.repeat(64):state.report.report_digest,
    report_canonical:state.report.report_canonical,export_authorized:true,release_authorized:false};
   if(url===base+'/experiment-cancellations'){
    state.cancel={schema_version:1,project_id:project.project_id,cancellation_id:body.cancellation_id,experiment_run_ref:body.experiment_run_ref,persisted:true,cancel_requested:true,automatic_dispatch:false};
    state.keys[key]=['experiment_cancellation',state.cancel];return state.cancel;
   }
   if(url.startsWith(base+'/experiment-command-submissions/')){
    const wanted=url.split('/').at(-1),found=state.keys[wanted];return {schema_version:1,project_id:project.project_id,submission_key:wanted,status:found?'committed':'not_found',resource_kind:found?.[0]||null,receipt:found?.[1]||null,control_only:false,writer_requires_reopen:false,automatic_dispatch:false};
   }
   throw Error('unexpected experiment renderer route '+path);
  });
  await page.addScriptTag({path:'pyharness/ui/host-adapters/workspace-input-contracts.js'});
  await page.addScriptTag({path:'pyharness/ui/host-adapters/experiments.js'});
  await page.evaluate(async p=>{const api=async(...args)=>{const v=await mockApi(...args);if(v.__deny)throw Object.assign(Error('denied'),{status:v.__deny});return v;};
   window.controller=PyharnessExperiments.create({main:document.querySelector('main'),api,esc:v=>String(v).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;')});await controller.open(p);},project);
  const click=a=>page.locator('[data-ep-action='+a+']').click(),settled=()=>page.waitForFunction(()=>document.querySelector('#experimentsDialog')?.getAttribute('aria-busy')==='false');
  await settled();await page.fill('[data-ep-title]','固定版本对照');await page.selectOption('[data-ep-dataset]',did);await click('load-dataset');await settled();
  if(name==='forged-case'){assert.equal(await page.locator('[data-ep-case]').count(),0);assert.ok(!(await page.locator('#experimentsDialog').textContent()).includes('foreign unverified private input'));return;}
  await page.check('[data-ep-case="0"]');for(const [side,index] of [['left',0],['right',1]]){await page.selectOption('[data-ep-'+side+'-agent]',agents[index]);await settled();await page.selectOption('[data-ep-'+side+'-version]','1');}
  await click('prepare-preview');
  if(['identity','close'].includes(name)){
   while(!state.entered)await new Promise(r=>setTimeout(r,10));if(name==='identity')state.realm='B';else await click('close');release();
   await page.waitForFunction(()=>!document.querySelector('#experimentsDialog'));assert.equal(state.posts.filter(x=>x.path===base+'/experiments').length,0);return;
  }
  await settled();assert.equal(await page.locator('[data-ep-action=prepare]').isDisabled(),true);
  if(name==='dirty-selection'){await page.check('[data-ep-confirm-prepare]');await page.uncheck('[data-ep-case="0"]');assert.equal(await page.locator('[data-ep-action=prepare]').isDisabled(),true);assert.equal(state.posts.filter(x=>x.path===base+'/experiments').length,0);return;}
  if(name==='safe-text'){assert.equal(await page.locator('[data-ep-cases] img').count(),0);assert.equal(await page.evaluate(()=>window.epXss||null),null);}
  if(['prepare-preview','safe-text'].includes(name))return;
  await page.check('[data-ep-confirm-prepare]');await click('prepare');await settled();assert.ok(state.definition);
  if(name==='forged-definition'){assert.equal(await page.locator('[data-ep-action=run-preview]').isDisabled(),true);assert.match(await page.locator('[data-ep-status]').textContent(),/不符/);return;}
  if(name==='prepare-confirm')return;
  if(name==='grant-edit'){await page.fill('[data-ep-grant-input_tokens]','50000');await page.locator('[data-ep-grant-input_tokens]').blur();assert.equal(await page.locator('[data-ep-action=run-preview]').isDisabled(),false);}
  await click('run-preview');await settled();assert.equal(await page.locator('[data-ep-action=run]').isDisabled(),true);
  if(name==='grant-edit'){await page.check('[data-ep-confirm-run]');await page.fill('[data-ep-grant-output_tokens]','9000');assert.equal(await page.locator('[data-ep-action=run]').isDisabled(),true);assert.equal(await page.locator('[data-ep-confirm-run]').isChecked(),false);assert.equal(state.posts.filter(x=>x.path===base+'/experiment-runs').length,0);return;}
  if(name==='run-unconfirmed'){assert.equal(state.posts.filter(x=>x.path===base+'/experiment-runs').length,0);return;}
  await page.check('[data-ep-confirm-run]');await click('run');await settled();
  if(name==='lost-start'){await click('query');await settled();assert.equal(state.posts.filter(x=>x.path===base+'/experiment-runs').length,1);assert.match(await page.locator('[data-ep-run-result]').textContent(),/completed/);return;}
  assert.ok(state.run);if(name==='run-confirm')return;
  if(name==='revoked'){state.revoked=true;await click('observe');await settled();assert.equal(await page.locator('[data-ep-cases]').textContent(),'');await click('stop');await settled();assert.ok(state.cancel);return;}
  await click('report-preview');await settled();assert.equal(await page.locator('[data-ep-action=save-report]').isDisabled(),true);
  await page.check('[data-ep-confirm-report]');await click('save-report');await settled();
  if(name==='forged-report'){assert.equal(await page.locator('[data-ep-action=export-preview]').isDisabled(),true);assert.match(await page.locator('[data-ep-status]').textContent(),/不符/);return;}
  if(name==='pending-report-stop'){await click('stop');await settled();assert.equal(state.posts.filter(x=>x.path===base+'/experiment-reports').length,1);assert.equal(state.posts.filter(x=>x.path===base+'/experiment-cancellations').length,1);return;}
  await click('export-preview');await settled();assert.equal(await page.locator('[data-ep-action=export]').isDisabled(),true);await page.check('[data-ep-confirm-export]');
  if(name==='bad-export'){let downloaded=false;page.on('download',()=>downloaded=true);await click('export');await settled();assert.equal(downloaded,false);assert.match(await page.locator('[data-ep-status]').textContent(),/摘要|不符/);}
  else {const download=page.waitForEvent('download');await click('export');assert.match((await download).suggestedFilename(),/^pyharness-epr-.*\.json$/);await settled();}
  assert.equal(await page.evaluate(()=>localStorage.length),0);assert.ok(state.posts.every(x=>!x.path.includes('/publish')&&!x.path.includes('/approvals')));
 }finally{release?.();await browser.close();}
}
for(const name of ['prepare-preview','prepare-confirm','run-unconfirmed','run-confirm','lost-start','report-export','bad-export','identity','close','safe-text','pending-report-stop','revoked','grant-edit','forged-case','forged-definition','forged-report','dirty-selection'])
 test('experiment workbench '+name,()=>scenario(name));
