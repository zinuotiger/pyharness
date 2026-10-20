// Actual Chrome renderer; synthetic metadata API, not backend proof.
const test=require('node:test'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const response={status:'configuration_only',process_started:false,connection:{type:'mcp',config:{enabled:false,name:'PRIVATE-BROWSER-CONFIG'}}};
async function harness(api){
 const browser=await chromium.launch({channel:'chrome',headless:true}),page=await browser.newPage(),errors=[],consoleErrors=[];
 page.on('pageerror',e=>errors.push(String(e)));page.on('console',m=>{if(m.type()==='error')consoleErrors.push(m.text());});
 await page.route('http://127.0.0.1:8793/**',r=>r.fulfill({contentType:'text/html',body:'<main></main>'}));
 await page.goto('http://127.0.0.1:8793/');await page.exposeFunction('metadata',api);
 await page.addScriptTag({path:'pyharness/ui/host-adapters/browser-workbench.js'});
 await page.evaluate(()=>{window.saved=[];window.workbench=PyharnessBrowserWorkbench.create({
  api:async(...args)=>{const v=await metadata(...args);if(v?.denied)throw Object.assign(Error('denied'),{status:403});return v;},
  modal:(_title,html)=>document.querySelector('main').innerHTML=html,saveForm:value=>saved.push(value)});
 });
 return {page,async close(){try{assert.deepEqual(errors,[]);assert.deepEqual(consoleErrors,[]);}finally{await browser.close();}}};
}
async function fill(page){
 for(const key of ['node','playwright','executable'])await page.locator('[name="'+key+'"]').fill('C:/synthetic/'+key);
 await page.locator('[name=origins]').fill('http://127.0.0.1:1234');
}
test('metadata preview preserves original save boundary and never starts a browser itself',async()=>{
 let calls=0;const h=await harness(async p=>p==='/api/account/me'?{}:(calls++,response));
 try{
  await h.page.evaluate(()=>workbench.open());await fill(h.page);
  assert.equal(await h.page.locator('[data-bw-save]').isDisabled(),true);
  await h.page.locator('[type=submit]').click();await h.page.locator('[data-bw-status]').filter({hasText:'零进程、未保存'}).waitFor();
  assert.equal(calls,1);assert.deepEqual(await h.page.evaluate(()=>saved),[]);
  await h.page.locator('[data-bw-save]').click();
  assert.deepEqual(await h.page.evaluate(()=>saved),[response.connection]);
 }finally{await h.close();}
});
test('late metadata response cannot repopulate closed private panel',async()=>{
 let release,entered=false;const h=await harness(async p=>p==='/api/account/me'?{}:(entered=true,new Promise(r=>release=r)));
 try{
  await h.page.evaluate(()=>workbench.open());await fill(h.page);await h.page.locator('[type=submit]').click();
  while(!entered)await new Promise(r=>setTimeout(r,5));
  await h.page.evaluate(()=>window.dispatchEvent(new Event('pyharness-panel-close')));release(response);
  await h.page.waitForTimeout(30);assert.equal((await h.page.content()).includes('PRIVATE-BROWSER-CONFIG'),false);
  assert.equal(await h.page.locator('[data-bw-save]').count(),0);
 }finally{await h.close();}
});
test('changed inputs discard in-flight configuration without leaving form stuck',async()=>{
 let release,entered=false;const h=await harness(async p=>p==='/api/account/me'?{}:(entered=true,new Promise(r=>release=r)));
 try{
  await h.page.evaluate(()=>workbench.open());await fill(h.page);await h.page.locator('[type=submit]').click();
  while(!entered)await new Promise(r=>setTimeout(r,5));
  await h.page.locator('[name=origins]').fill('http://127.0.0.1:4321');release(response);
  await h.page.waitForTimeout(30);assert.equal((await h.page.content()).includes('PRIVATE-BROWSER-CONFIG'),false);
  assert.equal(await h.page.locator('[data-bw-save]').isDisabled(),true);
  assert.equal(await h.page.locator('[type=submit]').isDisabled(),false);
 }finally{await h.close();}
});
test('server revocation clears all private configuration fields',async()=>{
 const h=await harness(async p=>p==='/api/account/me'?{}:{denied:true});
 try{
  await h.page.evaluate(()=>workbench.open());await fill(h.page);await h.page.locator('[type=submit]').click();
  await h.page.waitForTimeout(40);assert.equal(await h.page.locator('[name=node]').count(),0);
  assert.equal((await h.page.content()).includes('C:/synthetic'),false);
 }finally{await h.close();}
});
