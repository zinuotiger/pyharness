const {test}=require('node:test'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
for(const entry of ['project','before-execution'])test('observations entry '+entry,async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const page=await browser.newPage();page.setDefaultTimeout(3000);
  const project={project_id:'p-'+'1'.repeat(16),revision:1,name:'Integrated observation entry',description:'',archived:false};
  const attempt={attempt_id:'ra-'+'2'.repeat(16),revision:1,digest:'a'.repeat(64),source_seq:7};
  const agent={agent_id:'a-'+'3'.repeat(32),version:1,sha256:'b'.repeat(64)};const posts=[];
  await page.route('http://127.0.0.1:8770/**',r=>r.fulfill({contentType:'text/html',body:'<main></main>'}));await page.goto('http://127.0.0.1:8770/');
  await page.exposeFunction('mockApi',async(path,options={})=>{
   if(options.method==='POST')posts.push(path);
   if(path==='/api/account/me')return {account:{tenant:'entry-test',user_id:'A'}};
   if(path==='/api/v1/projects')return {projects:[{project,digest:'a'.repeat(64),source_seq:2,created_at:'2026-10-19T00:00:00Z'}],total:1};
   if(path.endsWith('/execution-control'))return {schema_version:1,control_only:true,project_id:project.project_id,attempt_ref:attempt,status:'not_authorized',execution_ref:null,cancellation:null};
   if(path==='/api/v1/agents')return {agents:[{agent_id:agent.agent_id,status:'published'}]};
   if(path.endsWith('/versions'))return {versions:[{...agent,snapshot:{status:'published',name:'Published test'}}]};
   throw Error('unexpected route '+path);
  });
  for(const name of ['workspace-input-contracts','observations','executions','projects'])await page.addScriptTag({path:'pyharness/ui/host-adapters/'+name+'.js'});
  await page.evaluate(async({entry,project,attempt})=>{
   const main=document.querySelector('main'),esc=v=>String(v).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
   if(entry==='project'){
    window.PyharnessSourceGrants={create:()=>({html:()=>'',invalidate(){},open(){}})};
    window.owner=PyharnessProjects.create({main,api:mockApi,esc,date:String,render:async()=>{}});main.innerHTML=await owner.render();
   }else{window.owner=PyharnessExecutions.create({main,api:mockApi,esc});await owner.open(project,attempt.attempt_id);}
  },{entry,project,attempt});
  if(entry==='project')await page.locator('[data-project-action=observations]').click();
  else{await page.selectOption('[data-ex-agent]','0');await page.locator('[data-ex-action=observations]').click();}
  await page.locator('#observationsDialog').waitFor({state:'visible'});assert.equal(posts.length,0);
  if(entry==='before-execution')assert.match(await page.locator('[data-ob-basis]').textContent(),new RegExp(attempt.attempt_id));
 }finally{await browser.close();}
});
