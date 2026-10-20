// Entry wiring only; domain privacy and real execution facts have separate gates.
const {test}=require('node:test'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
for(const entry of ['project','execution'])test('regression data entry '+entry,async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const page=await browser.newPage();page.setDefaultTimeout(3000);
  const project={project_id:'p-'+'1'.repeat(16),revision:1,name:'回归入口',description:'',archived:false};
  const attempt={attempt_id:'ra-'+'2'.repeat(16),revision:1,digest:'a'.repeat(64),source_seq:7};
  const execution={execution_id:'ex-'+'3'.repeat(16),revision:1,digest:'b'.repeat(64),source_seq:10};
  const posts=[],opened=[];
  await page.route('http://127.0.0.1:8772/**',r=>r.fulfill({contentType:'text/html',body:'<main></main>'}));await page.goto('http://127.0.0.1:8772/');
  await page.exposeFunction('recordOpen',(p,options)=>opened.push({project:p,options}));
  await page.exposeFunction('mockApi',async(path,options={})=>{
   if(options.method==='POST')posts.push(path);
   if(path==='/api/account/me')return {account:{tenant:'entry-test',user_id:'A'}};
   if(path==='/api/v1/projects')return {projects:[{project,digest:'c'.repeat(64),source_seq:2,created_at:'2026-10-20T00:00:00Z'}],total:1};
   if(path.endsWith('/execution-control'))return {schema_version:1,control_only:true,project_id:project.project_id,attempt_ref:attempt,status:'authorized',execution_ref:execution,cancellation:null};
   if(path.endsWith('/executions/'+execution.execution_id))return {schema_version:1,project_id:project.project_id,...execution,attempt_ref:attempt,status:'failed',
    session_id:'s-pa-'+'4'.repeat(32),task_id:'product-task',execution_started:true,unknown_effects:false,effects:[],artifacts:[],observed_seq:18,result_summary:'',result_truncated:false};
   throw Error('unexpected entry route '+path);
  });
  for(const name of ['workspace-input-contracts','executions','projects'])await page.addScriptTag({path:'pyharness/ui/host-adapters/'+name+'.js'});
  await page.evaluate(async({entry,project,attempt})=>{
   const main=document.querySelector('main'),esc=v=>String(v).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
   window.PyharnessRegressionData={create:()=>({invalidate(){},async open(p,trigger,options){await recordOpen(p,options||null);window.entryOpened=true;}})};
   if(entry==='project'){window.PyharnessSourceGrants={create:()=>({html:()=>'',invalidate(){},open(){}})};window.owner=PyharnessProjects.create({main,api:mockApi,esc,date:String,render:async()=>{}});main.innerHTML=await owner.render();}
   else{window.owner=PyharnessExecutions.create({main,api:mockApi,esc});await owner.open(project,attempt.attempt_id);}
  },{entry,project,attempt});
  await page.locator(entry==='project'?'[data-project-action=regression-data]':'[data-ex-action=regression-data]').click();
  await page.waitForFunction(()=>window.entryOpened===true);assert.equal(opened.length,1);assert.equal(opened[0].project.project_id,project.project_id);
  if(entry==='execution')assert.equal(opened[0].options.execution_id,execution.execution_id);
  assert.equal(posts.length,0);
 }finally{await browser.close();}
});
