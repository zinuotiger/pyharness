// Real Chrome; explicitly synthetic API replies exercise renderer races only.
const test=require('node:test'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base='/api/v1/configuration-history/model/fixture';
const revision={revision_id:'cr-demo',number:1,created_at:'2026-10-20T00:00:00Z',snapshot:{model:'PRIVATE-MODEL-CONTENT'}};
const history={revisions:[revision],commands:[]},current={source_sha256:'a'.repeat(64),snapshot:{model:'PRIVATE-MODEL-CONTENT'}};
const preview={revision_id:revision.revision_id,compatible:true,issues:[],changes:[],permission_increases:['max_tokens_increased'],preview_sha256:'b'.repeat(64)};
async function harness(api){
 const browser=await chromium.launch({channel:'chrome',headless:true}),page=await browser.newPage(),errors=[],consoleEvents=[];
 page.on('pageerror',e=>errors.push(String(e)));page.on('console',m=>consoleEvents.push({type:m.type(),text:m.text()}));
 await page.route('http://127.0.0.1:8792/**',r=>r.fulfill({contentType:'text/html',body:'<main></main>'}));
 await page.goto('http://127.0.0.1:8792/');await page.exposeFunction('syntheticApi',api);
 await page.addScriptTag({path:'pyharness/ui/host-adapters/configuration-history.js'});
 await page.evaluate(()=>window.owner=PyharnessConfigurationHistory.create({api:async(...args)=>{const v=await syntheticApi(...args);
  if(v?.__synthetic_error)throw Object.assign(Error(v.code),{status:v.status,code:v.code});return v;},
  modal:(name,html)=>document.querySelector('main').innerHTML=html,
  esc:value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}));
 return {browser,page,async close(){try{assert.deepEqual(errors,[]);assert.deepEqual(consoleEvents.filter(v=>v.type==='error'),[]);}finally{await browser.close();}}};
}
function defaults(path){if(path==='/api/account/me')return {mode:'local_operator'};
 if(path===base+'/revisions')return history;if(path===base+'/current')return current;
 if(path.endsWith('/preview'))return preview;throw Error('unexpected '+path);}

test('late private response cannot repopulate a closed configuration panel',async()=>{
 let resolve,entered=false;
 const h=await harness(async p=>{if(p===base+'/revisions'){entered=true;return new Promise(r=>resolve=r);}return defaults(p);});
 try{await h.page.evaluate(()=>void owner.open('model','fixture'));while(!entered)await new Promise(r=>setTimeout(r,5));
  await h.page.evaluate(()=>window.dispatchEvent(new Event('pyharness-panel-close')));resolve(history);
  await h.page.waitForTimeout(30);assert.equal((await h.page.content()).includes('PRIVATE-MODEL-CONTENT'),false);
  assert.equal(await h.page.locator('[data-ch-capture]').count(),0);
 }finally{await h.close();}
});

test('tenant switch before response clears identity-bound configuration metadata',async()=>{
 let changed=false,resolve,entered=false;
 const h=await harness(async p=>{if(p==='/api/account/me')return {account:{user_id:'A',tenant:changed?'B':'A',role:'owner'}};
  if(p===base+'/current'){entered=true;return new Promise(r=>resolve=r);}return defaults(p);});
 try{await h.page.evaluate(()=>void owner.open('model','fixture'));while(!entered)await new Promise(r=>setTimeout(r,5));
  changed=true;resolve(current);await h.page.locator('[data-configuration-history]').filter({hasText:'已清除配置历史'}).waitFor();
  assert.equal((await h.page.content()).includes('PRIVATE-MODEL-CONTENT'),false);
 }finally{await h.close();}
});

test('wider restore requires two explicit confirmations before any POST',async()=>{
 let posts=0;
 const h=await harness(async(p,o={})=>{if(o.method==='POST'){posts++;return {status:'applied'};}return defaults(p);});
 try{await h.page.evaluate(()=>owner.open('model','fixture'));await h.page.locator('[data-ch-preview]').click();
  await h.page.locator('[data-ch-compatibility]').filter({hasText:'兼容：'}).waitFor();
  assert.equal(await h.page.locator('[data-ch-apply]').isDisabled(),true);await h.page.locator('[data-ch-confirm]').check();
  assert.equal(await h.page.locator('[data-ch-apply]').isDisabled(),true);assert.equal(posts,0);
  await h.page.locator('[data-ch-widen]').check();await h.page.locator('[data-ch-apply]').click();
  await h.page.locator('[data-ch-status]').filter({hasText:'已应用'}).waitFor();assert.equal(posts,1);
 }finally{await h.close();}
});

test('lost apply reply preserves its key for query and never repeats a POST',async()=>{
 let posts=0,key;
 const h=await harness(async(p,o={})=>{if(o.method==='POST'){posts++;key=JSON.parse(o.body).command_key;throw Error('request_timeout');}
  if(p===base+'/commands/'+key)return {status:'applied',historical_result:true};return defaults(p);});
 try{await h.page.evaluate(()=>owner.open('model','fixture'));await h.page.locator('[data-ch-preview]').click();
  await h.page.locator('[data-ch-confirm]').check();await h.page.locator('[data-ch-widen]').check();
  await h.page.locator('[data-ch-apply]').click();await h.page.locator('[data-ch-status]').filter({hasText:'request_timeout'}).waitFor();
  assert.equal(posts,1);assert.ok(key);assert.equal(await h.page.locator('[data-ch-apply]').isDisabled(),true);
  assert.equal(await h.page.locator('[data-ch-refresh]').isDisabled(),true);
  await h.page.locator('[data-ch-query]').click();await h.page.locator('[data-ch-status]').filter({hasText:'已应用'}).waitFor();
  assert.equal(posts,1);await h.page.evaluate(()=>owner.destroy());
  assert.deepEqual(await h.page.evaluate(()=>[Object.keys(localStorage),Object.keys(sessionStorage)]),[[],[]]);
 }finally{await h.close();}
});

test('cold panel can query committed keys even when current dependencies are unavailable',async()=>{
 let posts=0,queries=0;
 const h=await harness(async(p,o={})=>{if(o.method==='POST')posts++;
  if(p===base+'/revisions')return {...history,commands:[{command_key:'old-key',status:'applied',created_at:null}]};
  if(p===base+'/current')throw Error('CRED-703');
  if(p===base+'/commands/old-key'){queries++;return {status:'applied',historical_result:true};}return defaults(p);});
 try{await h.page.evaluate(()=>owner.open('model','fixture'));assert.equal(await h.page.locator('[data-ch-capture]').isDisabled(),true);
  await h.page.locator('[data-ch-recent]').locator('..').locator('summary').click();
  await h.page.locator('[data-ch-recent] button').click();await h.page.locator('[data-ch-status]').filter({hasText:'已应用'}).waitFor();
  assert.equal(queries,1);assert.equal(posts,0);
 }finally{await h.close();}
});

test('incompatible preview cannot be enabled by checking confirmations',async()=>{
 const h=await harness(async p=>p.endsWith('/preview')?{...preview,compatible:false,issues:['credential_binding_changed']}:defaults(p));
 try{await h.page.evaluate(()=>owner.open('model','fixture'));await h.page.locator('[data-ch-preview]').click();
  await h.page.locator('[data-ch-compatibility]').filter({hasText:'凭据代次或服务地址已变化'}).waitFor();
  await h.page.locator('[data-ch-confirm]').check();await h.page.locator('[data-ch-widen]').check();
  assert.equal(await h.page.locator('[data-ch-apply]').isDisabled(),true);
 }finally{await h.close();}
});

test('an explicit stale-preview refusal permits refresh without automatically retrying the request',async()=>{
 let posts=0;
 const h=await harness(async(p,o={})=>{if(o.method==='POST'){posts++;return {__synthetic_error:true,status:422,code:'configuration_preview_stale'};}return defaults(p);});
 try{await h.page.evaluate(()=>owner.open('model','fixture'));await h.page.locator('[data-ch-preview]').click();
  await h.page.locator('[data-ch-confirm]').check();await h.page.locator('[data-ch-widen]').check();await h.page.locator('[data-ch-apply]').click();
  await h.page.locator('[data-ch-status]').filter({hasText:'configuration_preview_stale'}).waitFor();
  assert.equal(posts,1);assert.equal(await h.page.locator('[data-ch-refresh]').isEnabled(),true);
  assert.equal(await h.page.locator('[data-ch-apply]').isDisabled(),true);
  assert.equal((await h.page.locator('[data-ch-result]').textContent()).includes('已明确拒绝'),true);
  await h.page.locator('[data-ch-refresh]').click();assert.equal(posts,1);
 }finally{await h.close();}
});

test('a request forbidden by current authority clears the private panel',async()=>{
 let denied=false;
 const h=await harness(async p=>denied&&p===base+'/revisions'?{__synthetic_error:true,status:403,code:'configuration_manager_required'}:defaults(p));
 try{await h.page.evaluate(()=>owner.open('model','fixture'));denied=true;await h.page.locator('[data-ch-refresh]').click();
  await h.page.locator('[data-configuration-history]').filter({hasText:'已清除配置历史'}).waitFor();
  assert.equal((await h.page.content()).includes('PRIVATE-MODEL-CONTENT'),false);
 }finally{await h.close();}
});
