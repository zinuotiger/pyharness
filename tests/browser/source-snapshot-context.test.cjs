/* Real Chrome / production adapter; context API data and timing are explicitly synthetic. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
for (const scenario of ['success', 'delayed-input', 'malformed', 'wrong-seq', '401', '403', '409', '503']) {
  test('committed snapshot context preview: ' + scenario, async () => {
    const browser = await chromium.launch({headless:true,channel:'chrome'});
    try {
      const page = await browser.newPage(), errors = [];
      page.setDefaultTimeout(3000);
      page.on('pageerror', e => errors.push(e.message));
      await page.setContent('<main></main><button id="trigger">open</button>');
      await page.addScriptTag({path:path.resolve(process.cwd(),'pyharness/ui/host-adapters/source-grants.js')});
      await page.evaluate(async scenario => {
        crypto.randomUUID ||= () => '01234567890123456789012345678901';
        const project='p-0123456789abcdef', grant='sg-0123456789abcdef';
        const snapshot={snapshot_id:'ss-0123456789abcdef',project_id:project,grant_id:grant,revision:1,
          kind:'explicit_selection',policy:'reject-secrets-utf8-v1',capture_stability:'stable_observed',
          confirmation_digest:'a'.repeat(64),digest:'b'.repeat(64),source_seq:4,file_count:1,total_size_bytes:11};
        window.f={requests:[],writes:0,entered:false,finished:false};
        f.gate=new Promise(resolve=>f.release=resolve);
        window.a=PyharnessSourceGrants.create({main:document.querySelector('main'),esc:String,date:String,api:async(url,opts)=>{
          if(url.endsWith('/snapshots')){if(opts)f.writes++;return {snapshots:[snapshot],total:1};}
          if(url.endsWith('/snapshot-context-previews')){
            f.requests.push(JSON.parse(opts.body));f.entered=true;
            if(scenario==='delayed-input')await f.gate;
            f.finished=true;
            if(/^\d+$/.test(scenario))throw Object.assign(Error('synthetic denial'),{status:Number(scenario)});
            return {schema_version:1,project_id:project,grant_id:grant,grant_revision:1,snapshot_id:snapshot.snapshot_id,
              snapshot_revision:1,snapshot_digest:scenario==='malformed'?'c'.repeat(64):snapshot.digest,source_seq:scenario==='wrong-seq'?5:4,
              policy:snapshot.policy,status:'context_observed',file_count:1,total_size_bytes:11,context_digest:'d'.repeat(64),
              items:[{input_index:0,path:'<img src=x onerror=alert(1)>.txt',size_bytes:11,content_digest:'e'.repeat(64)}]};
          }
          return {total:1,grants:[{grant_id:grant,revision:1,revoked:false,expires_at:'2099-01-01',root_identity:'synthetic',source_seq:3,digest:'synthetic'}]};
        }});
        // Trusted static adapter shell only.
        document.querySelector('main').innerHTML=a.html();await a.open(project,document.querySelector('#trigger'));
      },scenario);
      await page.locator('[data-grant-action=snapshot-list]').click();
      await page.locator('[data-grant-action=context-preview]').click();
      await page.waitForFunction(()=>f.entered);
      if(scenario==='delayed-input'){
        await page.locator('[data-selection-paths]').fill('new.txt');await page.evaluate(()=>f.release());
      }
      await page.waitForFunction(()=>f.finished);
      await page.waitForFunction(()=>!document.querySelector('[data-snapshot-result]').textContent.includes('正在'));
      const result=await page.locator('[data-snapshot-result]').textContent();
      if(scenario==='success'){
        assert.match(result,/context_observed/);assert.match(result,/未持久绑定/);
        assert.match(result,/<img src=x onerror=alert\(1\)>.txt/);assert.equal(await page.locator('img').count(),0);
        assert.deepEqual(await page.evaluate(()=>f.requests[0]),{schema_version:1,expected_revision:1,snapshot_id:'ss-0123456789abcdef',snapshot_digest:'b'.repeat(64)});
      }else if(scenario==='delayed-input')assert.equal(result,'');
      else {assert.doesNotMatch(result,/context_observed/);assert.match(result,/拒绝|失效|冲突|不可用|未确认/);}
      assert.equal(await page.locator('[data-snapshot-confirm]').isChecked(),false);
      await page.locator('[data-grant-action=snapshot-capture]').click();
      assert.equal(await page.evaluate(()=>f.writes),0);assert.deepEqual(errors,[]);
    }finally{await browser.close();}
  });
}
