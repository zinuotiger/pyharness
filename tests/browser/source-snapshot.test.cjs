/* Actual Chrome, production adapter; synthetic API gates are explicitly labelled. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const adapterPath=path.resolve(__dirname,'../../pyharness/ui/host-adapters/source-grants.js');
test('snapshot requires content preview and explicit confirmation before durable capture',async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.setContent('<main></main><button id="trigger">open</button>');await page.addScriptTag({path:adapterPath});
  await page.evaluate(async()=>{
   const main=document.querySelector('main');window.writes=0;window.previews=0;
   crypto.randomUUID ||= ()=>'01234567890123456789012345678901';
   const project='p-0123456789abcdef',grant='sg-0123456789abcdef';
   const summary={snapshot_id:'ss-0123456789abcdef',project_id:project,grant_id:grant,revision:1,kind:'explicit_selection',policy:'reject-secrets-utf8-v1',capture_stability:'stable_observed',total_size_bytes:11,file_count:1,confirmation_digest:'a'.repeat(64),digest:'b'.repeat(64),source_seq:4};
   window.adapter=PyharnessSourceGrants.create({main,esc:String,date:String,api:async(url,opts)=>{
    if(url.endsWith('snapshot-previews')){previews++;return {schema_version:1,project_id:project,grant_id:grant,revision:1,status:'confirmation_required',policy:'reject-secrets-utf8-v1',confirmation_digest:'a'.repeat(64),file_count:1,total_size_bytes:11};}
    if(url.endsWith('/snapshots')){
     if(opts){writes++;const cmd=JSON.parse(opts.body);summary.snapshot_id=cmd.snapshot_id;return summary;}
     return {snapshots:writes?[summary]:[],total:writes};
    }
    return {total:1,grants:[{grant_id:grant,revision:1,revoked:false,expires_at:'2099-01-01',root_identity:'x',source_seq:3,digest:'x'}]};
   }});
   // Trusted adapter-owned shell, never user markup.
   main.innerHTML=adapter.html();await adapter.open(project,document.querySelector('#trigger'));
  });
  assert.equal(await page.locator('[data-grant-action=snapshot-preview]').count(),1);
  await page.locator('[data-selection-paths]').fill('unchanged.txt');
  await page.locator('[data-grant-action=snapshot-capture]').click();
  assert.equal(await page.evaluate(()=>writes),0);
  await page.locator('[data-grant-action=snapshot-preview]').click();
  await page.waitForFunction(()=>document.querySelector('[data-snapshot-result]').textContent.includes('confirmation_required'));
  await page.locator('[data-grant-action=snapshot-capture]').click();assert.equal(await page.evaluate(()=>writes),0);
  await page.locator('[data-snapshot-confirm]').check();await page.locator('[data-grant-action=snapshot-capture]').click();
  await page.waitForFunction(()=>document.querySelector('#sourceGrantStatus').textContent.includes('快照已持久确认'));
  assert.equal(await page.evaluate(()=>writes),1);assert.equal(await page.evaluate(()=>previews),1);
  assert.deepEqual(errors,[]);
 }finally{await browser.close()}
});

test('closing during post-commit grant refresh never overwrites reopened dialog',async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const page=await browser.newPage();await page.setContent('<main></main><button id="trigger">open</button>');await page.addScriptTag({path:adapterPath});
  await page.evaluate(async()=>{
   crypto.randomUUID ||= ()=>'01234567890123456789012345678901';
   const main=document.querySelector('main');let reads=0;
   const gate=new Promise(r=>window.releaseRefresh=r);
   const data={total:1,grants:[{grant_id:'sg-0123456789abcdef',revision:1,revoked:false,expires_at:'2099-01-01',root_identity:'x',source_seq:3,digest:'x'}]};
   window.adapter=PyharnessSourceGrants.create({main,esc:String,date:String,api:async(url,opts)=>{
    if(opts)return {revision:2};reads++;if(reads===2){window.refreshWaiting=true;await gate;}return data;
   }});
   // Trusted adapter-owned shell, never user markup.
   main.innerHTML=adapter.html();await adapter.open('p-0123456789abcdef',document.querySelector('#trigger'));
  });
  await page.locator('[data-grant-action=revoke]').click();await page.waitForFunction(()=>window.refreshWaiting);
  await page.locator('[data-grant-action=close]').click();
  await page.evaluate(()=>adapter.open('p-1111111111111111',document.querySelector('#trigger')));
  const before=await page.locator('#sourceGrantStatus').innerText();
  await page.evaluate(()=>releaseRefresh());await page.waitForTimeout(50);
  assert.equal(await page.locator('#sourceGrantStatus').innerText(),before);
 }finally{await browser.close()}
});
