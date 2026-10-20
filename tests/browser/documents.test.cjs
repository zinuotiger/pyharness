const {test}=require('node:test'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fixture=require('./fixtures/owned-inputs.json');
for(const scenario of ['text-citation-delivery','unicode-range','unknown-retry','unknown-reconcile','late-close','refresh-denied','safe-html','identity-change','pdf-confirmation','pdf-edit-invalidates','late-citation']){
 test(`versioned documents ${scenario}`,async()=>{
  const browser=await chromium.launch({channel:'chrome',headless:true});
  try{
   const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(7000);
   await page.route('http://127.0.0.1:8765/**',r=>r.fulfill({body:'<style>textarea{min-height:330px}</style><main></main>',contentType:'text/html'}));await page.goto('http://127.0.0.1:8765/');
   await page.addScriptTag({path:'pyharness/ui/host-adapters/workspace-input-contracts.js'});await page.addScriptTag({path:'pyharness/ui/host-adapters/documents.js'});
   await page.evaluate(({fixture,scenario})=>{
    const clone=v=>JSON.parse(JSON.stringify(v)),f=window.f={realm:'A',posts:[],doc:null,cite:null,delivery:null,scenario};
    f.gate=new Promise(resolve=>f.release=resolve);
    f.project={project_id:fixture.input.project_id,revision:fixture.input.project_revision,name:'Synthetic project'};
    const task={schema_version:2,product_task_id:'pt-0123456789abcdef',revision:1,digest:'a'.repeat(64),source_seq:7,title:'Synthetic formal task',retired:false};
    const page=(key,rows)=>({[key]:rows,total:rows.length,next_cursor:null});
    const text=scenario==='safe-html'?'<img src=x onerror=window.syntheticXss=1>':scenario==='unicode-range'?'A😀BC':'Synthetic source paragraph';
    const api=async(url,options={})=>{
     if(url==='/api/account/me')return {account:{tenant:'synthetic',user_id:f.realm}};
     if(url.includes('/workspace-inputs?'))return page('inputs',[clone(fixture.input)]);
     if(url.includes('/workspace-inputs/'))return clone(fixture.input);
     if(url.endsWith('/source-grants'))return {grants:[{grant_id:'sg-0123456789abcdef',revision:1,revoked:false}]};
     if(url.endsWith('/product-tasks'))return {tasks:[task],total:1};
     if(url.endsWith('/document-pdf-previews'))return {...JSON.parse(options.body),size_bytes:150,confirmation_digest:'c'.repeat(64),status:'confirmation_required',persisted:false};
     if(options.method==='POST'){
      const b=JSON.parse(options.body),{expected_revision,...fields}=b;f.posts.push({url,body:options.body,key:options.headers['Idempotency-Key']});
      if(url.endsWith('/documents')){
       const pdf=b.source.kind==='pdf_grant';
       f.doc={...fields,project_id:f.project.project_id,revision:expected_revision+1,digest:'d'.repeat(64),source_seq:8,created_at:'2026-10-18',status:'current',content_digest:'e'.repeat(64),size_bytes:30,format:pdf?'pdf':'text',page_count:pdf?1:0,unlocatable_pages:[],capability:'locatable',capability_reason:'text',block_count:1,stale_reasons:[],blocks:[{block_id:'b-0001',page:pdf?1:null,paragraph:1,start:0,end:Array.from(text).length,text,quote_digest:'f'.repeat(64)}]};
       if(pdf)f.doc.source={...fields.source,file:{path:fields.source.path}};
       if(['unknown-retry','unknown-reconcile','identity-change'].includes(scenario)&&f.posts.length===1)throw Object.assign(Error('synthetic lost acknowledgement'),{status:503});
       if(scenario==='late-close'){f.entered=true;await f.gate;}
       return clone(f.doc);
      }
      if(url.endsWith('/citations')){
       f.cite={...fields,project_id:f.project.project_id,revision:1,digest:'b'.repeat(64),source_seq:9,quote:Array.from(text).slice(b.locator.start,b.locator.end).join(''),page:null,paragraph:1,status:'current'};
       if(scenario==='late-citation'){f.entered=true;await f.gate;}
       return clone(f.cite);
      }
      if(url.endsWith('/delivery-definitions')){
       f.delivery={...fields,project_id:f.project.project_id,revision:expected_revision+1,digest:'c'.repeat(64),source_seq:10,status:'defined',acceptance_status:'not_evaluated',execution_authorized:false,export_authorized:false};return clone(f.delivery);
      }
     }
     if(url.includes('/documents?')){if(f.denied)throw Object.assign(Error('synthetic revoked'),{status:403});return page('documents',f.doc?[clone(f.doc)]:[]);}
     if(url.includes('/citations?'))return page('citations',f.cite?[clone(f.cite)]:[]);
     if(url.includes('/delivery-definitions?'))return page('deliveries',f.delivery?[clone(f.delivery)]:[]);
     if(url.includes('/documents/')&&f.doc)return clone(f.doc);
     if(url.includes('/citations/')&&f.cite)return clone(f.cite);
     if(url.includes('/delivery-definitions/')&&f.delivery)return clone(f.delivery);
     throw Error('unexpected '+url);
    };
    window.ctrl=window.PyharnessDocuments.create({main:document.querySelector('main'),api,esc:s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))});ctrl.open(f.project,null);
   },{fixture,scenario});
   const dialog=page.getByRole('dialog');await page.waitForFunction(()=>document.querySelector('[data-doc-status]').textContent.includes('已加载'));
   await dialog.locator('[data-view=deliveries]').click();
   const height=await dialog.locator('[data-item-criteria]').evaluate(n=>n.getBoundingClientRect().height);assert.ok(height>=80&&height<=180);
   await dialog.locator('[data-view=documents]').click();
   await dialog.locator('#documentForm [name=title]').fill('Synthetic reading document');
   if(scenario.startsWith('pdf-')){
    await dialog.locator('[name=sourceKind]').selectOption('pdf_grant');await dialog.locator('[name=pdfPath]').fill('synthetic.pdf');
    await dialog.locator('[data-doc-action=preview-pdf]').click();await page.waitForFunction(()=>document.querySelector('[data-pdf-preview]').textContent.includes('待导入'));
    await dialog.locator('[name=pdfConfirmed]').check();
    if(scenario==='pdf-edit-invalidates'){
     await dialog.locator('#documentForm [name=title]').fill('Changed after preview');assert.equal(await dialog.locator('[name=pdfConfirmed]').isChecked(),false);
     await dialog.getByRole('button',{name:'保存资料版本',exact:true}).click();await page.waitForFunction(()=>document.querySelector('[data-doc-status]').textContent.includes('请预览并确认'));
     assert.equal(await page.evaluate(()=>f.posts.length),0);assert.deepEqual(errors,[]);return;
    }
   }else{
    await dialog.locator('[data-doc-action=load-files]').click();await page.waitForFunction(()=>document.querySelector('[name=file]').options.length>0);
   }
   await dialog.getByRole('button',{name:'保存资料版本',exact:true}).click();
   if(scenario==='late-close'){
    await page.waitForFunction(()=>f.entered);await page.keyboard.press('Escape');await page.evaluate(()=>ctrl.open(f.project,null));
    await page.waitForFunction(()=>document.querySelector('[data-doc-status]').textContent.includes('原命令结果未确认'));await page.evaluate(()=>f.release());await page.waitForTimeout(100);
    assert.equal(await dialog.locator('[data-doc-reader]').textContent(),'');assert.equal(await page.evaluate(()=>f.posts.length),1);
   }else if(['unknown-retry','unknown-reconcile','identity-change'].includes(scenario)){
    await page.waitForFunction(()=>document.querySelector('[data-doc-status]').textContent.includes('结果未确认'));
    if(scenario==='identity-change')await page.evaluate(()=>f.realm='B');
    await dialog.locator(`[data-doc-action=${scenario==='unknown-reconcile'?'reconcile':'retry'}]`).click();
    if(scenario==='identity-change'){await page.waitForFunction(()=>document.querySelector('[data-doc-status]').textContent.includes('主体已变化'));assert.equal(await page.evaluate(()=>f.posts.length),1);}
    else{await page.waitForFunction(()=>document.querySelector('[data-doc-status]').textContent.includes('精确回读'));const posts=await page.evaluate(()=>f.posts);assert.equal(posts.length,scenario==='unknown-reconcile'?1:2);if(posts.length===2)assert.deepEqual(posts[0],posts[1]);}
   }else{
    await page.waitForFunction(()=>document.querySelector('[data-doc-status]').textContent.includes('精确回读'));
    await dialog.locator('[data-doc-action=read-document]').click();await page.waitForFunction(()=>document.querySelector('[data-doc-reader] [data-block]'));
    if(scenario==='refresh-denied'){
     await page.evaluate(()=>f.denied=true);await dialog.locator('[data-doc-action=refresh]').click();await page.waitForFunction(()=>document.querySelector('[data-doc-status]').textContent.includes('读取失败'));
     assert.equal(await dialog.locator('[data-doc-reader]').textContent(),'');assert.equal(await dialog.locator('[data-doc-list] article').count(),0);
    }else if(scenario==='safe-html'){
     assert.equal(await dialog.locator('[data-doc-reader] img').count(),0);assert.equal(await page.evaluate(()=>window.syntheticXss),undefined);
     assert.ok((await dialog.locator('[data-doc-reader]').textContent()).includes('<img'));
    }else if(['text-citation-delivery','unicode-range','late-citation'].includes(scenario)){
     await dialog.locator('[data-doc-action=select-block]').click();
     if(scenario==='unicode-range'){await dialog.locator('[name=quoteStart]').fill('1');await dialog.locator('[name=quoteEnd]').fill('3');}
     await dialog.locator('[data-doc-action=save-citation]').click();
     if(scenario==='late-citation'){
      await page.waitForFunction(()=>f.entered);const message=await dialog.locator('[data-doc-status]').textContent();
      assert.ok(message.includes('正在保存'));assert.equal(message.includes('精确回读'),false);await page.evaluate(()=>f.release());
     }
     await page.waitForFunction(()=>f.cite&&document.querySelector('[data-cite-list] article'));
     if(scenario==='unicode-range'){
      assert.equal(await page.evaluate(()=>f.cite.quote),'😀B');await dialog.locator('[data-doc-action=locate]').click();await dialog.locator('[data-doc-reader] mark').waitFor();assert.equal(await dialog.locator('[data-doc-reader] mark').textContent(),'😀B');
     }else{
      await dialog.locator('[data-view=deliveries]').click();await dialog.locator('#deliveryForm [name=task]').selectOption('pt-0123456789abcdef');await dialog.locator('[data-item-title]').fill('Review report');await dialog.locator('[data-item-criteria]').fill('Explain source evidence');
      const option=await page.evaluate(()=>f.cite.citation_id);await dialog.locator('[data-item-citations]').selectOption(option);
      await dialog.getByRole('button',{name:'保存交付要求',exact:true}).click();await page.waitForFunction(()=>f.delivery&&document.querySelector('[data-delivery-list] article'));
      assert.equal(await page.evaluate(()=>f.delivery.items[0].citation_refs[0].citation_id),option);assert.equal(await page.evaluate(()=>f.delivery.acceptance_status),'not_evaluated');
     }
    }else if(scenario==='pdf-confirmation')assert.ok((await dialog.locator('[data-doc-reader]').textContent()).includes('物理页 1'));
   }
   assert.deepEqual(errors,[]);
  }finally{await browser.close();}
 });
}
