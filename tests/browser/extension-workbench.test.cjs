// Real Chrome UI race tests with explicitly synthetic replies; backend is tested separately.
const test=require('node:test'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const snapshot={snapshot_id:'ss-demo',snapshot:{name:'demo',body:'PRIVATE-FIXED-BODY',description:'demo',
 source_sha256:'a'.repeat(64),loaded_sha256:'b'.repeat(64),truncated:false,frozen:true}};
async function harness(api){
 const browser=await chromium.launch({channel:'chrome',headless:true});
 const page=await browser.newPage();
 await page.route('http://127.0.0.1:8791/**',r=>r.fulfill({contentType:'text/html',body:'<main></main>'}));
 await page.goto('http://127.0.0.1:8791/');await page.exposeFunction('syntheticApi',api);
 await page.addScriptTag({path:'pyharness/ui/host-adapters/extension-workbench.js'});
 await page.evaluate(()=>{window.owner=PyharnessExtensionWorkbench.create({api:syntheticApi,
  modal:(name,html)=>document.querySelector('main').innerHTML=html,
  esc:value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))});});
 return {browser,page};
}
const ready=page=>page.locator('[data-ew-status]').filter({hasText:'尚未启动演练'}).waitFor();

test('closed snapshot request cannot restore its private body',async()=>{
 let resolve,captured=false;
 const h=await harness(async path=>{if(path==='/api/account/me')return {mode:'local_operator'};
  if(path.endsWith('/skill-snapshots')){captured=true;return new Promise(r=>resolve=r);}throw Error(path);});
 try{
  await h.page.evaluate(()=>void owner.skill('demo'));
  await h.page.waitForFunction(()=>!!document.querySelector('[data-extension-workbench]'));
  while(!captured)await new Promise(r=>setTimeout(r,5));
  await h.page.evaluate(()=>window.dispatchEvent(new Event('pyharness-panel-close')));
  resolve(snapshot);await h.page.waitForTimeout(30);
  assert.equal((await h.page.content()).includes('PRIVATE-FIXED-BODY'),false);
  assert.equal(await h.page.locator('[data-ew-rehearse]').count(),0);
 }finally{await h.browser.close();}
});

test('team identity before a response clears all private snapshot content',async()=>{
 let team=false,resolve,posted=false;
 const h=await harness(async path=>{if(path==='/api/account/me')return team?{account:{role:'owner',tenant:'team',user_id:'A'}}:{mode:'local_operator'};
  if(path.endsWith('/skill-snapshots')){posted=true;return new Promise(r=>resolve=r);}throw Error(path);});
 try{
  await h.page.evaluate(()=>void owner.skill('demo'));
  while(!posted)await new Promise(r=>setTimeout(r,5));
  team=true;resolve(snapshot);
  await h.page.locator('[data-extension-workbench]').filter({hasText:'已清除调试内容'}).waitFor();
  assert.equal((await h.page.content()).includes('PRIVATE-FIXED-BODY'),false);
 }finally{await h.browser.close();}
});

test('lost discovery reply queries the original key and never resubmits',async()=>{
 let posts=0,key;
 const result=()=>({command_key:key,source:'managed',name:'echo',status:'completed',carrier_closed:true,
  protocol_status:'verified',server:{version:'1.0'},tools:[{name:'mcp.echo.echo',schema:{type:'object',properties:{}}}],stale:false});
 const h=await harness(async(path,options={})=>{if(path==='/api/account/me')return {mode:'local_operator'};
  if(path.endsWith('/mcp/managed/echo'))return {config_sha256:'a'.repeat(64),status:'configuration_only'};
  if(path.endsWith('/discoveries')){posts++;key=JSON.parse(options.body).command_key;throw Object.assign(Error('request_timeout'),{code:'request_timeout'});}
  if(path.endsWith('/discoveries/'+key))return result();throw Error(path);});
 try{
  await h.page.evaluate(()=>owner.mcp('managed~echo'));
  await h.page.locator('[data-ew-confirm]').check();await h.page.locator('[data-ew-discover]').click();
  await h.page.locator('[data-ew-status]').filter({hasText:'request_timeout'}).waitFor();
  assert.equal(posts,1);assert.ok(key);
  await h.page.locator('[data-ew-discover]').click();
  await h.page.locator('[data-ew-status]').filter({hasText:'请先查询原命令结果'}).waitFor();assert.equal(posts,1);
  await h.page.locator('[data-ew-query]').click();await h.page.locator('[data-ew-status]').filter({hasText:'拥有的进程已关闭'}).waitFor();
  assert.equal(posts,1);assert.equal(JSON.parse(await h.page.locator('[data-ew-result]').textContent()).command_key,key);
 }finally{await h.browser.close();}
});

test('snapshot capture is not rehearsal and closed content leaves no browser storage',async()=>{
 let rehearsals=0;
 const h=await harness(async(path,options={})=>{if(path==='/api/account/me')return {mode:'local_operator'};
  if(path.endsWith('/skill-snapshots'))return snapshot;
  if(path.includes('/rehearsals')){rehearsals++;throw Error('must not rehearse without confirmation');}throw Error(path);});
 try{
  await h.page.evaluate(()=>owner.skill('demo'));await ready(h.page);assert.equal(rehearsals,0);
  await h.page.locator('[data-ew-rehearse]').click();await h.page.locator('[data-ew-status]').filter({hasText:'勾选'}).waitFor();assert.equal(rehearsals,0);
  await h.page.evaluate(()=>owner.destroy());
  assert.equal((await h.page.content()).includes('PRIVATE-FIXED-BODY'),false);
  assert.deepEqual(await h.page.evaluate(()=>[Object.keys(localStorage),Object.keys(sessionStorage)]),[[],[]]);
 }finally{await h.browser.close();}
});
