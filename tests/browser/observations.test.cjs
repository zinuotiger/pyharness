const {test}=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const canonical=v=>JSON.stringify(v,(_,x)=>x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.entries(x).sort(([a],[b])=>a<b?-1:a>b?1:0)):x);
const hash=v=>crypto.createHash('sha256').update(canonical(v)).digest('hex');
for(const scenario of ['context','tool-calls','numeric-json','child','digest-mismatch','unavailable','permission','comparison','forged','identity','close'])test('observation workbench '+scenario,async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});let release;const hold=new Promise(r=>release=r);
 try{
  const project={project_id:'p-'+'1'.repeat(16),name:'Observation test',revision:1},execution='ex-'+'2'.repeat(16),other='ex-'+'3'.repeat(16),session='s-pa-'+'4'.repeat(32),ident='co-'+'5'.repeat(16);
  const selection={schema_version:1,attempt_ref:{attempt_id:'ra-'+'6'.repeat(16),revision:1,digest:'a'.repeat(64),source_seq:5},agent_ref:{agent_id:'a-'+'7'.repeat(32),version:1,sha256:'b'.repeat(64)}};
  const view={schema_version:1,layer:'adapter_input',model:'fixed',method:'chat',messages:[{role:'user',content:'Public <img src=x onerror="window.observationXss=1">',name:null,tool_call_id:null,tool_calls:null}],tools:[],counts:{messages:1,tools:0,images:0,images_exact:true},truncated:false,withheld:false};
  if(scenario==='tool-calls')view.messages[0].tool_calls=[{id:'actual-call-id',type:'function',function:{name:'fs.read_file',arguments:{path:'actual-public-args.txt'}}}];
  if(scenario==='numeric-json')view.tools=[{type:'function',function:{name:'numeric_fixture',description:'Exact numbers',parameters:{minimum:1e-7,maximum:1}}}];
  const rawView=canonical(view).replace('"minimum":1e-7','"minimum":1e-07').replace('"maximum":1,','"maximum":1.0,');
  const item={observation_id:ident,source_seq:8,phase:'prepared',layer:'adapter_input',model:'fixed',method:'chat',role:'main_agent',attempt:1,counts:view.counts,truncated:false,withheld:false,public_view_digest:hash(view),dispatch_confirmation:'unavailable_for_adapter',source_content_state:'injected_fixed_prompt'};
  item.public_view_digest=crypto.createHash('sha256').update(rawView).digest('hex');
  const common={schema_version:1,project_id:project.project_id,execution_id:execution,session_id:session,attempt_ref:selection.attempt_ref,agent_ref:selection.agent_ref,execution_authorized:false};
  const state={realm:'A',posts:[],entered:false};const page=await browser.newPage();page.setDefaultTimeout(7000);
  await page.route('http://127.0.0.1:8769/**',r=>r.fulfill({contentType:'text/html',body:'<main></main>'}));await page.goto('http://127.0.0.1:8769/');
  await page.exposeFunction('mockApi',async(path,options={})=>{
   const route=path.split('?')[0];
   if(path==='/api/account/me')return {account:{tenant:'test',user_id:state.realm,username:state.realm}};
   if(options.method==='POST')state.posts.push({path,body:JSON.parse(options.body)});
   const child='s-pa-'+'8'.repeat(32),selected=new URL('http://local'+path).searchParams.get('session_id');
   const scoped={...common,session_id:selected||session};
   if(route.endsWith('/contexts')){
    if(['identity','close'].includes(scenario)){state.entered=true;await hold;}
    return {...scoped,context_status:scenario==='unavailable'?'unavailable':'available',reason:scenario==='unavailable'?'no_recorded_context':null,items:scenario==='unavailable'?[]:[item],total:scenario==='unavailable'?0:1,truncated:false,child_sessions:scenario==='child'?[{session_id:child,depth:1}]:[]};
   }
   if(route.endsWith('/contexts/'+ident)){state.details=(state.details||0)+1;state.detailSession=selected;return {...scoped,...item,execution_id:scenario==='forged'?other:execution,public_view:view,public_view_canonical:rawView,public_view_digest:scenario==='digest-mismatch'?'0'.repeat(64):item.public_view_digest,derivation:{history_window:{available:false},system_window:{available:false},final_messages:1}};}
   if(path.endsWith('/permission-previews'))return {...selection,...common,tool_name:'fs.read_file',prediction:'allow',reason:'builtin_policy_prediction',guard_ids:['g-schema'],policy_refs:[],policy_fingerprint:'c'.repeat(64),materials_digest:'d'.repeat(64),persisted:false,side_effects_executed:false,live_recheck_required:true,scope:'fixed_selection_policy_only'};
   if(path.endsWith('/run-comparisons'))return {schema_version:1,project_id:project.project_id,comparison_status:'aligned',incomparable_reasons:[],left:{execution_id:execution,status:'completed',business_acceptance:'not_evaluated',usage:{cost_est:null,input_tokens:null,output_tokens:null}},right:{execution_id:other,status:'failed',business_acceptance:'not_evaluated',usage:{cost_est:null,input_tokens:null,output_tokens:null}},regressions:[{kind:'execution_regression',from_status:'completed',to_status:'failed'}],deltas:{cost_est:null,input_tokens:null,output_tokens:null,duration_seconds:null},business_quality_verdict:'not_evaluated',execution_authorized:false,persisted:false};
   throw Error('unexpected route '+path);
  });
  await page.addScriptTag({path:'pyharness/ui/host-adapters/workspace-input-contracts.js'});
  await page.addScriptTag({path:'pyharness/ui/host-adapters/observations.js'});
  await page.evaluate(async({project,selection,execution})=>{
   const esc=v=>String(v).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
   window.controller=PyharnessObservations.create({main:document.querySelector('main'),esc,api:mockApi});
   await controller.open(project,null,{selection,execution_id:execution});
  },{project,selection,execution});
  assert.equal(state.posts.length,0);
  const click=a=>page.locator('[data-ob-action='+a+']').click(),settled=()=>page.waitForFunction(()=>!document.querySelector('[data-ob-action=contexts]')?.disabled);
  if(scenario==='permission'){
   await page.fill('[data-ob-tool]','fs.read_file');await page.fill('[data-ob-args]','{"path":"sample.txt"}');await click('permission');await settled();
   assert.equal(state.posts.length,1);assert.ok(state.posts[0].path.endsWith('/permission-previews'));assert.deepEqual(state.posts[0].body,{...selection,tool_name:'fs.read_file',arguments:{path:'sample.txt'}});
   assert.match(await page.locator('[data-ob-permission]').textContent(),/预测允许/);
  }else if(scenario==='comparison'){
   await page.fill('[data-ob-right]',other);await click('compare');await settled();
   assert.match(await page.locator('[data-ob-comparison]').textContent(),/执行回归/);assert.match(await page.locator('[data-ob-comparison]').textContent(),/未知/);
   assert.equal(state.posts.length,1);assert.ok(state.posts[0].path.endsWith('/run-comparisons'));
  }else{
   await click('contexts');
   if(['identity','close'].includes(scenario)){
    while(!state.entered)await new Promise(r=>setTimeout(r,10));
    if(scenario==='identity')state.realm='B';else await click('close');release();
    await page.waitForFunction(()=>!document.querySelector('#observationsDialog'));assert.equal(await page.locator('body').textContent(),'');
   }else{
    await settled();
    if(scenario==='unavailable')assert.match(await page.locator('[data-ob-status]').textContent(),/没有记录/);
    else{
     if(scenario==='child'){await page.selectOption('[data-ob-session]','s-pa-'+'8'.repeat(32));await click('contexts');await settled();}
     await click('detail');await settled();const text=await page.locator('[data-ob-context]').textContent();assert.equal(state.details,1);
     if(['forged','digest-mismatch'].includes(scenario))assert.equal(text,'');else{assert.match(text,/Public <img/);assert.equal(await page.locator('[data-ob-context] img').count(),0);assert.equal(await page.evaluate(()=>window.observationXss||null),null);assert.match(await page.locator('[data-ob-status]').textContent(),/准备/);}
     if(scenario==='tool-calls')assert.match(text,/actual-public-args.txt/);
     if(scenario==='numeric-json'){assert.match(text,/1e-07/);assert.match(text,/1\.0/);}
     if(scenario==='child')assert.equal(state.detailSession,'s-pa-'+'8'.repeat(32));
    }
   }
   assert.equal(state.posts.length,0);
  }
  assert.equal(await page.evaluate(()=>localStorage.length),0);
 }finally{release?.();await browser.close();}
});
