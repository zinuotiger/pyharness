/* Real Chrome / production adapter; API responses and timing are synthetic. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const adapter=require('node:path').resolve(__dirname,'../../pyharness/ui/host-adapters/source-grants.js');
for(const operation of ['preflight','selection','snapshot-list'])for(const code of [401,403,409,503])
test(`${operation} ${code} invalidates another row's confirmed snapshot`,async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try{
  const page=await browser.newPage(),errors=[],consoleErrors=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('console',m=>{if(m.type()==='error')consoleErrors.push(m.text());});
  await page.setContent('<main></main><button id="trigger">open</button>');await page.addScriptTag({path:adapter});
  await page.evaluate(async({operation,code})=>{
   crypto.randomUUID ||= ()=>'01234567890123456789012345678901';
   const project='p-0123456789abcdef',ids=['sg-0123456789abcdef','sg-1111111111111111'];window.writes=0;
   window.a=PyharnessSourceGrants.create({main:document.querySelector('main'),esc:String,date:String,api:async(url,opts)=>{
    if(url.endsWith('/snapshot-previews'))return {schema_version:1,project_id:project,grant_id:ids[0],revision:1,status:'confirmation_required',policy:'reject-secrets-utf8-v1',confirmation_digest:'a'.repeat(64),file_count:1,total_size_bytes:1};
    if(opts&&url.endsWith('/snapshots')){window.writes++;throw Error('unexpected write');}
    if(url.endsWith('/preflight')||url.endsWith('/selection-previews')||url.endsWith('/snapshots'))throw Object.assign(Error('synthetic denial'),{status:code});
    return {total:2,grants:ids.map(grant_id=>({grant_id,revision:1,revoked:false,expires_at:'2099-01-01',root_identity:'synthetic',source_seq:3,digest:'synthetic'}))};
   }});
   document.querySelector('main').innerHTML=a.html();await a.open(project,document.querySelector('#trigger'));
  },{operation,code});
  const rows=page.locator('article');await rows.nth(0).locator('[data-selection-paths]').fill('unchanged.txt');
  await rows.nth(0).locator('[data-grant-action=snapshot-preview]').click();
  await page.waitForFunction(()=>document.querySelector('[data-snapshot-result]').textContent.includes('confirmation_required'));
  await rows.nth(0).locator('[data-snapshot-confirm]').check();
  // No input event here: the other row's observation itself must invalidate row zero.
  await rows.nth(1).locator('[data-selection-paths]').evaluate(el=>el.value='other.txt');
  await rows.nth(1).locator(`[data-grant-action=${operation}]`).click();
  await page.waitForFunction(()=>[...document.querySelectorAll('article')][1].querySelector('button:disabled')===null);
  assert.equal(await rows.nth(0).locator('[data-snapshot-confirm]').isChecked(),false);
  assert.equal(await rows.nth(0).locator('[data-snapshot-result]').textContent(),'');
  await rows.nth(0).locator('[data-grant-action=snapshot-capture]').click();
  assert.equal(await page.evaluate(()=>writes),0);assert.deepEqual(errors,[]);assert.deepEqual(consoleErrors,[]);
 }finally{await browser.close();}
});
