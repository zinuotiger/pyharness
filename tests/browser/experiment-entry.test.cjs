// Real Chrome entry and minimal stop rendering; synthetic API replies only.
const test=require('node:test'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const pid='p-'+'1'.repeat(16),rid='er-'+'2'.repeat(16),base='/api/v1/projects/'+pid;
for(const archived of [false,true])test('project experiment entry '+(archived?'archived':'active'),async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const page=await browser.newPage(),project={project_id:pid,revision:1,name:'实验入口',description:'',archived};let posts=0;
  await page.route('http://127.0.0.1:8785/**',r=>r.fulfill({contentType:'text/html',body:'<main></main>'}));await page.goto('http://127.0.0.1:8785/');
  await page.exposeFunction('mockApi',async(path,options={})=>{if(options.method==='POST')posts++;if(path==='/api/v1/projects')return {projects:[{project,source_seq:2,digest:'a'.repeat(64),created_at:'2026-10-20T00:00:00Z'}],total:1};throw Error('unexpected entry request');});
  await page.addScriptTag({path:'pyharness/ui/host-adapters/projects.js'});
  await page.evaluate(async()=>{window.PyharnessSourceGrants={create:()=>({html:()=>'',invalidate(){}})};window.PyharnessExperiments={create:()=>({invalidate(){},async open(p){window.openedExperiment=p;}})};
   const owner=PyharnessProjects.create({main:document.querySelector('main'),api:mockApi,esc:String,date:String,render:async()=>{}});document.querySelector('main').innerHTML=await owner.render();});
  await page.locator('[data-project-action=experiments]').click();await page.waitForFunction(()=>window.openedExperiment);
  assert.equal(await page.evaluate(()=>window.openedExperiment.project_id),pid);assert.equal(posts,0);
 }finally{await browser.close();}
});

test('archived experiment workbench reads only control directory and stops exact owned run',async()=>{
 const browser=await chromium.launch({channel:'chrome',headless:true});
 try{
  const page=await browser.newPage(),requests=[];let stopped=false;
  const reference={experiment_run_id:rid,revision:1,digest:'b'.repeat(64),source_seq:12};
  await page.route('http://127.0.0.1:8786/**',r=>r.fulfill({contentType:'text/html',body:'<main></main>'}));await page.goto('http://127.0.0.1:8786/');
  await page.exposeFunction('mockApi',async(path,options={})=>{
   if(path==='/api/account/me')return {account:{tenant:'test',user_id:'A'}};requests.push({path,options});
   if(path===base+'/experiment-runs')return {schema_version:1,project_id:pid,items:[{...reference,status:'available',cancel_requested:false}],total:1,control_only:true,automatic_dispatch:false,execution_authorized:false};
   if(path===base+'/experiment-runs/'+rid)return {__deny:503};
   if(path===base+'/experiment-runs/'+rid+'/control')return {schema_version:1,project_id:pid,experiment_run_ref:reference,control_only:true,cancel_requested:stopped,automatic_dispatch:false};
   if(path===base+'/experiment-cancellations'){
    const body=JSON.parse(options.body);assert.deepEqual(body.experiment_run_ref,reference);assert.deepEqual(Object.keys(body).sort(),['cancellation_id','experiment_run_ref','schema_version']);stopped=true;
    return {schema_version:1,project_id:pid,cancellation_id:body.cancellation_id,experiment_run_ref:reference,persisted:true,cancel_requested:true,automatic_dispatch:false};
   }
   throw Error('archived workbench touched source or new authority '+path);
  });
  for(const file of ['workspace-input-contracts','experiments'])await page.addScriptTag({path:'pyharness/ui/host-adapters/'+file+'.js'});
  await page.evaluate(async pid=>{const api=async(...args)=>{const v=await mockApi(...args);if(v.__deny)throw Object.assign(Error('source unavailable'),{status:v.__deny});return v;};
   window.owner=PyharnessExperiments.create({main:document.querySelector('main'),api,esc:String});await owner.open({project_id:pid,revision:2,name:'已归档项目',archived:true});},pid);
  await page.waitForFunction(()=>document.querySelector('#experimentsDialog')?.getAttribute('aria-busy')==='false');
  assert.equal(await page.locator('[data-ep-action=prepare-preview]').isDisabled(),true);
  await page.selectOption('[data-ep-runs]',rid);await page.locator('[data-ep-action=load-run]').click();
  await page.waitForFunction(()=>document.querySelector('#experimentsDialog')?.getAttribute('aria-busy')==='false');
  await page.locator('[data-ep-action=stop]').click();await page.waitForFunction(()=>document.querySelector('#experimentsDialog')?.getAttribute('aria-busy')==='false');
  assert.ok(stopped);assert.equal(requests.filter(x=>x.options.method==='POST').length,1);
  assert.ok(requests.every(x=>!x.path.includes('regression-')&&!x.path.includes('/agents')&&!x.path.includes('previews')));
 }finally{await browser.close();}
});
