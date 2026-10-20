/* Real Chrome, explicitly synthetic API responses/timing. No repository or model calls. */
const {test}=require('node:test'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
for(const scenario of ['success','no-confirm','bad-digest','wrong-grant','edit','metadata-change','refresh','esc-reopen','401','403','409','503']){
 test('Git source preview UI '+scenario,async()=>{
  const browser=await chromium.launch({headless:true,channel:'chrome'});
  try{
   const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(3500);
   await page.route('http://127.0.0.1/**',r=>r.fulfill({contentType:'text/html',body:'<main></main><button id="open">open</button>'}));await page.goto('http://127.0.0.1/');
   const file=path.resolve('pyharness/ui/host-adapters/git-source.js');if(fs.existsSync(file))await page.addScriptTag({path:file});
   assert.equal(await page.evaluate(()=>typeof PyharnessGitSource),'object','Git preview product adapter must exist');
   await page.evaluate(async scenario=>{
    const project='p-0123456789abcdef',grant='sg-0123456789abcdef';
    window.f={calls:[],entered:false,finished:false};f.gate=new Promise(r=>f.release=r);
    window.a=PyharnessGitSource.create({main:document.querySelector('main'),api:async(url,opts)=>{
     if(!opts)return {grants:[grant,'sg-aaaaaaaaaaaaaaaa'].map(grant_id=>({grant_id,revision:1,source_seq:3,digest:'a'.repeat(64),root_identity:'b'.repeat(64),actions:['read'],expires_at:'2099-01-01T00:00:00Z',revoked:false})),total:2};
     f.calls.push(JSON.parse(opts.body));f.entered=true;
     if(['edit','metadata-change','refresh','esc-reopen'].includes(scenario))await f.gate;
     f.finished=true;if(/^\d+$/.test(scenario))throw Object.assign(Error('synthetic denial'),{status:Number(scenario)});
     const layer=(letter,size,native_mode=null)=>({schema_version:1,git_oid:letter.repeat(40),digest:letter.repeat(64),size_bytes:size,mode:33188,native_mode});
     const data={schema_version:1,status:'git_layers_observed',repository_kind:'ordinary',project_id:project,grant_id:scenario==='wrong-grant'?'sg-cccccccccccccccc':grant,
      grant_revision:1,source_seq:3,metadata_grant_id:null,metadata_grant_revision:null,head_oid:'a'.repeat(40),index_digest:'a'.repeat(64),
      items:[{schema_version:1,input_index:0,path:'selected.txt',head:layer('a',6),index:layer('b',7),working:layer('c',11,438),deletion_marker:false,index_deletion_marker:false,selected_untracked:false}],
      file_count:1,unique_content_size_bytes:24,policy:'reject-secrets-utf8-v1',capture_stability:'stable_observed',scope:'selected_git_layers',persisted:false,executable:false};
     const canonical=v=>JSON.stringify(v,(_,x)=>x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.entries(x).sort(([a],[b])=>a<b?-1:a>b?1:0)):x);
     data.observation_digest=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical(data)))),n=>n.toString(16).padStart(2,'0')).join('');
     if(scenario==='bad-digest')data.observation_digest='f'.repeat(64);
     return data;
    }});
    window.openGit=()=>a.open({project,grantId:grant,grantRevision:1,sourceSeq:3},'selected.txt',document.querySelector('#open'));await openGit();
   },scenario);
   if(scenario==='no-confirm'){
    await page.locator('[data-git-action=preview]').click();assert.equal(await page.evaluate(()=>f.calls.length),0);
   }else{
    await page.locator('[data-git-confirm]').check();await page.locator('[data-git-action=preview]').click();await page.waitForFunction(()=>f.entered);
    if(scenario==='edit')await page.locator('[data-git-paths]').fill('changed.txt');
    if(scenario==='metadata-change')await page.locator('[data-git-metadata]').selectOption('sg-aaaaaaaaaaaaaaaa');
    if(scenario==='refresh')await page.locator('[data-git-action=refresh]').click();
    if(scenario==='esc-reopen'){await page.locator('#gitSourceDialog').press('Escape');await page.evaluate(()=>openGit());}
    if(['edit','metadata-change','refresh','esc-reopen'].includes(scenario))await page.evaluate(()=>f.release());
    await page.waitForFunction(()=>f.finished);await page.waitForFunction(()=>!document.querySelector('[data-git-status]').textContent.includes('正在读取所选'));
    const text=await page.locator('[data-git-status]').textContent();
    if(scenario==='success'){
     assert.match(text,/git_layers_observed/);assert.match(text,/未持久/);assert.match(await page.locator('[data-git-result]').textContent(),/HEAD|暂存|工作区/);
     assert.deepEqual(await page.evaluate(()=>f.calls[0]),{schema_version:1,expected_revision:1,paths:['selected.txt'],metadata_grant_id:null,metadata_grant_revision:null});
    }else assert.doesNotMatch(text,/git_layers_observed/);
    assert.equal(await page.locator('[data-git-confirm]').isChecked(),false);
   }
   assert.deepEqual(errors,[]);
  }finally{await browser.close();}
 });
}
