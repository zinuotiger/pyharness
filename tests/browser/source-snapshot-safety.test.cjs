/* Production adapter + real Chrome. ALL API data/delays below are synthetic, not HTTP acceptance. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const adapter=path.resolve(__dirname,'../../pyharness/ui/host-adapters/source-grants.js');
async function fixture(scenario,run){
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const page=await browser.newPage({viewport:{width:390,height:844}}),errors=[],consoleErrors=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')consoleErrors.push(m.text());});
  page.setDefaultTimeout(3000);
  await page.setContent('<main></main><button id="trigger">open</button>');await page.addScriptTag({path:adapter});
  await page.evaluate(async scenario=>{
   crypto.randomUUID ||= ()=>String(window.serial=(window.serial||0)+1).padStart(32,'0');
   const project='p-0123456789abcdef',grant='sg-0123456789abcdef';
   window.f={posts:[],gets:0,previewCalls:0,finished:0};
   f.gate=new Promise(r=>f.release=r);
   const summary={snapshot_id:'ss-0123456789abcdef',project_id:project,grant_id:grant,revision:1,kind:'explicit_selection',policy:'reject-secrets-utf8-v1',capture_stability:'stable_observed',confirmation_digest:'a'.repeat(64),digest:'b'.repeat(64),source_seq:4,file_count:1,total_size_bytes:11};
   window.a=PyharnessSourceGrants.create({main:document.querySelector('main'),esc:String,date:String,api:async(url,opts)=>{
    if(url.endsWith('/snapshot-previews')){
     f.previewCalls++;
     if(scenario==='button-owner'){await f.gate;f.finished++;}
     if(scenario==='preview-422')throw Object.assign(Error('synthetic'),{status:422});
     return {schema_version:1,project_id:project,grant_id:grant,revision:1,status:'confirmation_required',policy:'reject-secrets-utf8-v1',confirmation_digest:'a'.repeat(64),file_count:JSON.parse(opts.body).paths.length,total_size_bytes:11};
    }
    if(url.endsWith('/preflight')){await f.gate;return {};}
    if(url.endsWith('/snapshots')){
     if(opts){
      f.posts.push({body:opts.body,key:opts.headers['Idempotency-Key']});summary.snapshot_id=JSON.parse(opts.body).snapshot_id;
      if(scenario==='readback-403'&&f.posts.length===2)throw Object.assign(Error('synthetic retry denial'),{status:403});
      if(scenario==='capture-input'){f.waiting=true;await f.gate;}
      if(scenario==='capture-422')throw Object.assign(Error('synthetic'),{status:422});
      if(scenario==='missing-proof'){delete summary.digest;delete summary.source_seq;}
      return {...summary};
     }
     f.gets++;
     if(scenario==='readback-403'&&f.gets===1)throw Object.assign(Error('synthetic'),{status:403});
     const exact={...summary};
     if(scenario==='wrong-count')exact.file_count=2;
     if(scenario==='wrong-project')exact.project_id='p-1111111111111111';
     if(scenario==='wrong-confirmation')exact.confirmation_digest='c'.repeat(64);
     if(scenario==='list-malformed')delete exact.digest;
     return {snapshots:[exact],total:1};
    }
    return {total:1,grants:[{grant_id:grant,revision:1,revoked:false,expires_at:'2099-01-01',root_identity:'synthetic',source_seq:3,digest:'synthetic'}]};
   }});
   // Trusted static adapter shell only; no user/API content is assigned here.
   document.querySelector('main').innerHTML=a.html();await a.open(project,document.querySelector('#trigger'));
  },scenario);
  await page.locator('[data-selection-paths]').fill('unchanged.txt');
  await run(page);
  assert.deepEqual(errors,[]);assert.deepEqual(consoleErrors,[]);
 }finally{await browser.close();}
}
async function preview(page){
 await page.locator('[data-grant-action=snapshot-preview]').click();
 await page.waitForFunction(()=>document.querySelector('[data-snapshot-result]').textContent.includes('confirmation_required'));
 await page.locator('[data-snapshot-confirm]').check();
}
async function settled(page){
 await page.waitForFunction(()=>!document.querySelector('#sourceGrantStatus').textContent.includes('正在持久提交'));
}
for(const scenario of ['missing-proof','wrong-count','wrong-project','wrong-confirmation'])
test('unconfirmed malformed readback: '+scenario,()=>fixture(scenario,async page=>{
 await preview(page);await page.locator('[data-grant-action=snapshot-capture]').click();await settled(page);
 assert.doesNotMatch(await page.locator('#sourceGrantStatus').textContent(),/已持久确认/);
 assert.match(await page.locator('#sourceGrantStatus').textContent(),/未确认/);
 assert.equal(await page.evaluate(()=>f.posts.length),1);
}));
test('accepted POST followed by denied GET retains original command for explicit same-key retry',()=>fixture('readback-403',async page=>{
 await preview(page);await page.locator('[data-grant-action=snapshot-capture]').click();await settled(page);
 assert.match(await page.locator('#sourceGrantStatus').textContent(),/未确认/);
 await page.locator('[data-grant-action=retry]').click();
 await page.waitForFunction(()=>f.posts.length===2);
 await settled(page);assert.match(await page.locator('#sourceGrantStatus').textContent(),/未确认/);
 await page.locator('[data-grant-action=retry]').click();
 await page.waitForFunction(()=>document.querySelector('#sourceGrantStatus').textContent.includes('已持久确认'));
 const posts=await page.evaluate(()=>f.posts);assert.equal(posts.length,3);assert.deepEqual(posts[1],posts[0]);assert.deepEqual(posts[2],posts[0]);
}));
test('malformed list never renders an invented count or digest',()=>fixture('list-malformed',async page=>{
 await page.locator('[data-grant-action=snapshot-list]').click();
 await page.waitForFunction(()=>!document.querySelector('[data-snapshot-result]').textContent.includes('正在'));
 assert.doesNotMatch(await page.locator('[data-snapshot-result]').textContent(),/已有选择性快照|undefined/);
}));
for(const action of ['preview','capture'])test(action+' 422 provides bounded quota feedback',()=>fixture(action+'-422',async page=>{
 if(action==='capture'){await preview(page);await page.locator('[data-grant-action=snapshot-capture]').click();await settled(page);}
 else{await page.locator('[data-grant-action=snapshot-preview]').click();await page.waitForFunction(()=>!document.querySelector('[data-snapshot-result]').textContent.includes('正在'));}
 const text=await page.locator(action==='capture'?'#sourceGrantStatus':'[data-snapshot-result]').textContent();
 assert.match(text,/422|限额/);assert.match(text,/10 MiB/);assert.doesNotMatch(text,/已持久确认/);
}));
test('superseded observation releases its button without losing new observation ownership',()=>fixture('button-owner',async page=>{
 await page.locator('[data-grant-action=snapshot-preview]').click();
 assert.equal(await page.locator('[data-grant-action=snapshot-preview]').isDisabled(),true);
 await page.locator('[data-selection-paths]').fill('changed.txt');
 assert.equal(await page.locator('[data-grant-action=snapshot-preview]').isDisabled(),false);
 await page.evaluate(()=>f.release());await page.waitForFunction(()=>f.finished===1);
 assert.equal(await page.locator('[data-snapshot-result]').textContent(),'');
}));
test('capture readback survives editing; ESC restores focus and narrow layout is bounded',()=>fixture('capture-input',async page=>{
 await preview(page);await page.locator('[data-grant-action=snapshot-capture]').click();await page.waitForFunction(()=>f.waiting);
 await page.locator('[data-selection-paths]').fill('changed.txt');await page.evaluate(()=>f.release());
 await page.waitForFunction(()=>document.querySelector('#sourceGrantStatus').textContent.includes('已持久确认'));
 assert.equal(await page.evaluate(()=>f.posts.length),1);assert.equal(await page.evaluate(()=>f.gets),1);
 assert.equal(await page.locator('[data-snapshot-confirm]').isChecked(),false);
 assert.equal(await page.locator('dialog').evaluate(e=>e.getBoundingClientRect().width<=innerWidth),true);
 if(process.env.SNAPSHOT_EVIDENCE_DIR)await page.screenshot({path:process.env.SNAPSHOT_EVIDENCE_DIR+'/capture-mobile.png',fullPage:true});
 await page.keyboard.press('Escape');assert.equal(await page.evaluate(()=>document.activeElement.id),'trigger');
}));
test('empty and 201 paths never reach preview; exact 200 paths are not silently truncated',()=>fixture('bounds',async page=>{
 for(const value of ['',Array.from({length:201},(_,i)=>'f'+i+'.txt').join('\n')]){
  await page.locator('[data-selection-paths]').fill(value);await page.locator('[data-grant-action=snapshot-preview]').click();
  assert.equal(await page.evaluate(()=>f.previewCalls),0);
 }
 await page.locator('[data-selection-paths]').fill(Array.from({length:200},(_,i)=>'f'+i+'.txt').join('\n'));
 await preview(page);assert.equal(await page.evaluate(()=>f.previewCalls),1);
 assert.match(await page.locator('[data-snapshot-result]').textContent(),/200 文件/);
}));
test('two independently created adapters never share confirmation state',()=>fixture('two-instances',async page=>{
 await preview(page);
 await page.evaluate(async()=>{
  document.querySelector('dialog').close();
  const second=document.createElement('main');document.body.append(second);
  window.b=PyharnessSourceGrants.create({main:second,esc:String,date:String,api:async()=>({total:0,grants:[]})});
  // Trusted static shell; this second main owns a separate closure and DOM subtree.
  second.innerHTML=b.html();await b.open('p-1111111111111111',document.querySelector('#trigger'));b.invalidate();
  second.querySelector('dialog').close();second.remove();document.querySelector('dialog').showModal();
 });
 assert.equal(await page.locator('[data-snapshot-confirm]').isChecked(),true);
 await page.locator('[data-grant-action=snapshot-capture]').click();
 await page.waitForFunction(()=>document.querySelector('#sourceGrantStatus').textContent.includes('已持久确认'));
 assert.equal(await page.evaluate(()=>f.posts.length),1);
}));
