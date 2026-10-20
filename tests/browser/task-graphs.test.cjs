const {test}=require('node:test'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
test('graph workbench loads without mutation and renders a visible graph',async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const page=await browser.newPage();const posts=[];
  await page.route('http://127.0.0.1:8766/**',r=>r.fulfill({contentType:'text/html',body:'<main></main>'}));await page.goto('http://127.0.0.1:8766/');
  await page.exposeFunction('mockApi',async(path,options={})=>{
   if(options.method==='POST')posts.push(path);
   if(path==='/api/account/me')return {account:{tenant:'synthetic',user_id:'A'}};
   if(path.endsWith('/product-tasks'))return {tasks:[]};if(path.endsWith('/agents'))return {agents:[]};
   const field=path.includes('/task-graph-runs')?'graph_runs':path.includes('/task-graphs')?'graphs':path.includes('/delivery-definitions')?'deliveries':path.includes('/citations')?'citations':'documents';
   return {[field]:[],next_cursor:null};
  });
  for(const name of ['workspace-input-contracts','task-graphs'])await page.addScriptTag({path:'pyharness/ui/host-adapters/'+name+'.js'});
  await page.evaluate(async()=>{
   window.ctrl=PyharnessTaskGraphs.create({main:document.querySelector('main'),api:mockApi,esc:s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))});
   await ctrl.open({project_id:'p-'+'1'.repeat(16),revision:1,name:'Graph test'});
  });
  assert.equal(posts.length,0);assert.equal(await page.locator('#taskGraphDialog').count(),1);
  assert.equal(await page.locator('[data-graph-svg]').count(),1);assert.equal(await page.locator('[data-graph-action=start]').isDisabled(),true);
 }finally{await browser.close();}
});

for(const scenario of ['save-start','double-start','dirty-definition','lost-reply-query','reload-read-only','tampered-hint','cancel-original-key','source-withdrawn-stop','identity-change','late-preview','safe-text','cycle-refused','host-confirm','repeat-stop','cancel-already-requested']){
 test('graph workbench '+scenario,async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});let release;const gate=new Promise(r=>release=r);
  try{
   const crypto=require('node:crypto'),canonical=v=>JSON.stringify(v,(_,x)=>x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.entries(x).sort(([a],[b])=>a<b?-1:a>b?1:0)):x);
   const project={project_id:'p-'+'1'.repeat(16),revision:1,name:'Synthetic graph'},task={product_task_id:'pt-'+'2'.repeat(16),revision:1,digest:'a'.repeat(64),source_seq:3,status:'definition_ready',title:'<img src=x onerror=window.graphXss=1>'};
   const agent={agent_id:'a-'+'3'.repeat(32),version:1,sha256:'b'.repeat(64)},state={posts:[],graph:null,run:null,cancel:null,realm:'A',deny:false,entered:false};
   const reference=(v,key)=>Object.fromEntries([key,'revision','digest','source_seq'].map(k=>[k,v[k]]));
   const hash=(operation,body)=>crypto.createHash('sha256').update(canonical({operation,project_id:project.project_id,command:body})).digest('hex');
   const page=await browser.newPage();page.setDefaultTimeout(8000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.route('http://127.0.0.1:8766/**',r=>r.fulfill({contentType:'text/html',body:'<main></main>'}));await page.goto('http://127.0.0.1:8766/');
   await page.exposeFunction('mockApi',async(path,options={})=>{
    const pathOnly=path.split('?')[0],fail=(status,message)=>({__error:{status,message}});
    if(path==='/api/account/me')return {account:{tenant:'synthetic',user_id:state.realm}};
    if(pathOnly.endsWith('/control'))return {control_only:true,graph_run_ref:reference(state.run,'graph_run_id'),request_digest:state.run.request_digest,submission_key:state.run.submission_key,cancel_requested:!!state.run.cancel_requested};
    if(state.deny&&!pathOnly.endsWith('/task-graph-cancellations')&&!pathOnly.endsWith('/task-graph-cancellation-submissions/by-key'))return fail(403,'source denied');
    if(options.method==='POST'){
     const body=JSON.parse(options.body),key=options.headers?.['Idempotency-Key'];state.posts.push({path,body,key});
     if(pathOnly.endsWith('/task-graphs')){
      if(body.nodes.some(n=>n.depends_on.includes(n.node_id)))return fail(422,'cycle refused');
      state.graph={...body,revision:body.expected_revision+1,digest:'c'.repeat(64),source_seq:5,persisted:true};return state.graph;
     }
     if(pathOnly.endsWith('/task-graph-previews')){
      if(scenario==='late-preview'){state.entered=true;await gate;}
      return {schema_version:1,project_id:project.project_id,graph_ref:body.graph_ref,concurrency:2,allocated_budget:{input_tokens:20000,output_tokens:4000,tool_calls:20,cost_microyuan:1000000},confirmation_digest:'d'.repeat(64),nodes:state.graph.nodes.map(n=>({...n,boundary:scenario==='host-confirm'?'host_unrestricted':'disabled'}))};
     }
     if(pathOnly.endsWith('/task-graph-runs')){
      state.run={...body,project_id:project.project_id,revision:1,digest:'e'.repeat(64),source_seq:7,submission_key:key,request_digest:hash('task_graph.start',body),persisted:true,status:'completed',business_acceptance:'not_evaluated',nodes:state.graph.nodes.map((n,i)=>({...n,status:'completed',execution_id:'ex-'+String(i+4).repeat(16),unknown_effects:false}))};
      if(['lost-reply-query','reload-read-only','tampered-hint'].includes(scenario))return fail(503,'lost reply');return state.run;
     }
     if(pathOnly.endsWith('/task-graph-cancellations')){state.run.cancel_requested=true;if(scenario==='cancel-already-requested')return fail(409,'already requested in another window');state.cancel={...body,persisted:true,request_digest:hash('task_graph_cancellation',body)};return scenario==='cancel-original-key'?fail(503,'cancel reply lost'):state.cancel;}
    }
    if(pathOnly.endsWith('/task-graph-run-submissions/by-key'))return {status:state.run?'committed':'not_found',graph_run:state.run};
    if(pathOnly.endsWith('/task-graph-cancellation-submissions/by-key'))return {status:state.cancel?'committed':'not_found',cancellation:state.cancel};
    if(pathOnly.includes('/task-graph-runs/gr-'))return state.run;
    if(pathOnly.endsWith('/product-tasks'))return {tasks:[task]};if(pathOnly.endsWith('/agents'))return {agents:[{agent_id:agent.agent_id,status:'published'}]};
    if(pathOnly.endsWith('/versions'))return {versions:[{...agent,snapshot:{status:'published',name:task.title}}]};
    const field=pathOnly.endsWith('/task-graph-runs')?'graph_runs':pathOnly.endsWith('/task-graphs')?'graphs':pathOnly.endsWith('/delivery-definitions')?'deliveries':pathOnly.endsWith('/citations')?'citations':'documents';
    return {[field]:field==='graphs'&&state.graph?[state.graph]:field==='graph_runs'&&state.run?[state.run]:[],next_cursor:null};
   });
   async function install(reload=false){if(reload)await page.reload();for(const name of ['workspace-input-contracts','task-graphs'])await page.addScriptTag({path:'pyharness/ui/host-adapters/'+name+'.js'});
    await page.evaluate(async project=>{const api=async(path,options={})=>{const v=await mockApi(path,options);if(v?.__error)throw Object.assign(Error(v.__error.message),v.__error);return v;};
     window.ctrl=PyharnessTaskGraphs.create({main:document.querySelector('main'),api,esc:s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))});await ctrl.open(project);},project);}
   const action=name=>page.locator('[data-graph-action='+name+']'),status=()=>page.locator('[data-graph-status]').textContent();
   await install();assert.equal(state.posts.length,0);await action('add').click();await page.locator('[data-node-field=task]').selectOption(task.product_task_id);await page.locator('[data-node-field=agent]').selectOption('0');
   if(scenario==='cycle-refused'){await page.locator('[data-node-field=depends_on]').fill('N1');await action('save').click();await page.waitForFunction(()=>document.querySelector('[data-graph-status]').textContent.includes('cycle refused'));assert.equal(state.run,null);assert(await action('start').isDisabled());await page.locator('[data-node-field=depends_on]').fill('');await action('save').click();await page.waitForFunction(()=>document.querySelector('[data-graph-status]').textContent.includes('图定义已保存'));}
   else{
    await action('save').click();await page.waitForFunction(()=>document.querySelector('[data-graph-status]').textContent.includes('图定义已保存'));
    if(scenario==='safe-text'){assert.equal(await page.locator('#taskGraphDialog img').count(),0);assert.equal(await page.evaluate(()=>window.graphXss),undefined);}
    await action('preview').click();
    if(scenario==='late-preview'){
     while(!state.entered)await new Promise(r=>setTimeout(r,10));await page.keyboard.press('Escape');await page.evaluate(project=>ctrl.open(project),project);release();
     assert.equal(await page.locator('[data-graph-preview]').textContent(),'');assert.equal(state.run,null);
    }else{
     await page.waitForFunction(()=>document.querySelector('[data-graph-status]').textContent.includes('预检完成'));
     if(scenario==='dirty-definition'){await page.locator('[data-graph-title]').fill('Changed after preview');assert(await action('start').isDisabled());assert(await action('preview').isDisabled());assert.equal(state.run,null);}
     else{
      if(scenario==='host-confirm'){await action('start').click();assert((await status()).includes('明确勾选'));assert.equal(state.run,null);await page.locator('[data-graph-host]').check();}
      if(scenario==='double-start')await page.evaluate(()=>{const b=document.querySelector('[data-graph-action=start]');b.click();b.click();});else await action('start').click();
      if(['lost-reply-query','reload-read-only','tampered-hint'].includes(scenario)){
       await page.waitForFunction(()=>document.querySelector('[data-graph-status]').textContent.includes('lost reply'));
       if(scenario==='tampered-hint')await page.evaluate(()=>{const k=Object.keys(localStorage).find(k=>k.startsWith('pyharness.graph.pending.'));const v=JSON.parse(localStorage.getItem(k));v.digest='0'.repeat(64);localStorage.setItem(k,JSON.stringify(v));});
       if(scenario!=='lost-reply-query'){const count=state.posts.length;await install(true);assert.equal(state.posts.length,count);}
       await action('query').click();
       if(scenario==='tampered-hint')assert((await status()).includes('摘要不符'));
       else await page.waitForFunction(()=>document.querySelector('[data-graph-result]').textContent.includes('completed'));
      }else{
       await page.waitForFunction(()=>document.querySelector('[data-graph-result]').textContent.includes('completed'));
       if(scenario==='repeat-stop'){await action('stop').click();await page.waitForFunction(()=>document.querySelector('[data-graph-status]').textContent.includes('停止请求已持久保存'));assert(await action('stop').isDisabled());}
       if(scenario==='cancel-already-requested'){await action('stop').click();await page.waitForFunction(()=>document.querySelector('[data-graph-status]').textContent.includes('already requested'));await action('query').click();await page.waitForFunction(()=>document.querySelector('[data-graph-status]').textContent.includes('本键未提交'));assert(await action('stop').isDisabled());}
       if(scenario==='identity-change'){state.realm='B';await action('refresh').click();await page.waitForFunction(()=>!document.querySelector('#taskGraphDialog'));}
       if(scenario==='source-withdrawn-stop'){state.deny=true;await action('refresh').click();await page.waitForFunction(()=>document.querySelector('[data-graph-status]').textContent.includes('停止引用'));assert.equal(await page.locator('[data-graph-result]').textContent(),'');await action('stop').click();await page.waitForFunction(()=>document.querySelector('[data-graph-status]').textContent.includes('停止请求已持久保存'));}
       if(scenario==='cancel-original-key'){await action('stop').click();await page.waitForFunction(()=>document.querySelector('[data-graph-status]').textContent.includes('cancel reply lost'));await action('query').click();await page.waitForFunction(()=>document.querySelector('[data-graph-status]').textContent.includes('原键确认'));}
      }
      assert.equal(state.posts.filter(p=>p.path.endsWith('/task-graph-runs')).length,1);
     }
    }
   }
   assert.equal(errors.length,0,errors.join('\n'));
  }finally{release();await browser.close();}
 });
}
