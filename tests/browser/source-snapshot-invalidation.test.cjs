// Independent regression: real Chrome + production adapter, explicitly synthetic API responses.
const {test}=require('node:test');const assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const adapter=require('node:path').resolve(__dirname,'../../pyharness/ui/host-adapters/source-grants.js');
const out=process.env.SNAPSHOT_EVIDENCE_DIR;
for(const scenario of ['failed-preflight','superseded-preview','input','reopen'])test(scenario+' invalidates snapshot confirmation',async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const page=await browser.newPage();const errors=[],consoleErrors=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')consoleErrors.push(m.text())});
  await page.setContent('<main></main><button id="trigger">open</button>');await page.addScriptTag({path:adapter});
  await page.evaluate(async scenario=>{
   crypto.randomUUID ||= ()=>'01234567890123456789012345678901';
   const project='p-0123456789abcdef',grant='sg-0123456789abcdef';
   window.writes=0;window.released=false;
   const gate=new Promise(r=>window.release=()=>{window.released=true;r()});
   const summary={snapshot_id:'ss-0123456789abcdef',capture_stability:'stable_observed',confirmation_digest:'a'.repeat(64),digest:'b'.repeat(64),source_seq:4,file_count:1,total_size_bytes:1};
   window.a=PyharnessSourceGrants.create({main:document.querySelector('main'),esc:String,date:String,api:async(url,opts)=>{
    if(url.endsWith('/snapshot-previews')){
     if(scenario!=='failed-preflight'){window.waiting=true;await gate;}
     return {schema_version:1,project_id:project,grant_id:grant,revision:1,status:'confirmation_required',policy:'reject-secrets-utf8-v1',confirmation_digest:'a'.repeat(64),file_count:1,total_size_bytes:1};
    }
    if(url.endsWith('/preflight'))throw Object.assign(Error('synthetic authorization/root denial'),{status:403});
    if(url.endsWith('/snapshots')){if(opts){window.writes++;summary.snapshot_id=JSON.parse(opts.body).snapshot_id;return summary;}return {snapshots:[summary],total:1};}
    return {total:1,grants:[{grant_id:grant,revision:1,revoked:false,expires_at:'2099-01-01',root_identity:'synthetic',source_seq:3,digest:'synthetic'}]};
   }});
   // Trusted production-adapter static shell, not user or server markup.
   document.querySelector('main').innerHTML=a.html();await a.open(project,document.querySelector('#trigger'));
  },scenario);
  await page.locator('[data-selection-paths]').fill('unchanged.txt');await page.locator('[data-grant-action=snapshot-preview]').click();
  if(scenario!=='failed-preflight')await page.waitForFunction(()=>window.waiting);
  else {await page.waitForFunction(()=>document.querySelector('[data-snapshot-result]').textContent.includes('confirmation_required'));await page.locator('[data-snapshot-confirm]').check();}
  if(scenario==='input')await page.locator('[data-selection-paths]').fill('changed.txt');
  else if(scenario==='reopen'){
   await page.keyboard.press('Escape');
   assert.equal(await page.evaluate(()=>document.activeElement.id),'trigger');
   await page.evaluate(()=>a.open('p-0123456789abcdef',document.querySelector('#trigger')));
  }else{
   await page.locator('[data-grant-action=preflight]').click();
   await page.waitForFunction(()=>document.querySelector('[data-preflight-result]').textContent.includes('检查被拒绝'));
  }
  if(scenario!=='failed-preflight'){await page.evaluate(()=>release());await page.waitForTimeout(80);}
  const state=await page.evaluate(()=>({snapshot:document.querySelector('[data-snapshot-result]').textContent,confirmed:document.querySelector('[data-snapshot-confirm]').checked,preflight:document.querySelector('[data-preflight-result]').textContent}));
  if(out)await page.screenshot({path:out+'/'+scenario+'.png'});
  if(scenario==='failed-preflight'){await page.locator('[data-grant-action=snapshot-capture]').click();await page.waitForTimeout(80);}
  console.log(JSON.stringify({scenario,state,writes:await page.evaluate(()=>writes),errors,consoleErrors}));
  assert.deepEqual(errors,[]);assert.deepEqual(consoleErrors,[]);
  if(scenario==='failed-preflight'){assert.equal(await page.evaluate(()=>writes),0,'failed preflight retained old preview/checked confirmation and sent capture');}
  else {assert.equal(state.confirmed,false);assert.equal(state.snapshot,'','superseded response must leave neither a confirmation nor permanent loading');}
 }finally{await browser.close()}
});
