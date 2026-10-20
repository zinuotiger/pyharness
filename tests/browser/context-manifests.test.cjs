/* Chrome renderer, explicitly synthetic API; real loopback is a separate acceptance. */
const {test}=require('node:test'),assert=require('node:assert/strict'),path=require('node:path'),fs=require('node:fs');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
for(const scenario of ['create','stale','bad-readback','unknown','read-failure-clears-target','cross-snapshot','correct-after-409','accepted-get403','unknown-retry409','task-create','task-edit','task-refresh','task-unknown-history','late-close','late-edit','refresh','401','403','409','503']) {
  test('persistent context UI '+scenario,async()=>{
    const browser=await chromium.launch({headless:true,channel:'chrome'});
    try {
      const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(4000);
      await page.setContent('<main></main><button id="open">open</button>');
      const script=path.resolve('pyharness/ui/host-adapters/context-manifests.js');
      if(fs.existsSync(script))await page.addScriptTag({path:script});
      assert.equal(await page.evaluate(()=>typeof window.PyharnessContextManifests),'object','persistent context product adapter must exist');
      await page.evaluate(async scenario=>{
        crypto.randomUUID ||= (()=>{let n=0;return()=>(++n).toString(16).padStart(16,'0')+'f'.repeat(16)})();
        const project='p-0123456789abcdef',grant='sg-0123456789abcdef',snapshot='ss-0123456789abcdef';
        window.f={posts:[],entered:false,done:false,row:null};f.gate=new Promise(r=>f.release=r);
        window.openContext=()=>window.a.open({project,grant,grantRevision:1,snapshotId:snapshot,snapshotDigest:'b'.repeat(64),fileCount:1,totalSize:11,sourceSeq:4},document.querySelector('#open'));
        window.a=PyharnessContextManifests.create({main:document.querySelector('main'),api:async(url,opts)=>{
          if(url==='/api/v1/projects')return {projects:[{project:{project_id:project,revision:1,archived:false}}],total:1};
          if(url.endsWith('/context-task-references')&&!opts)return {task_references:f.target?[f.target]:[],total:f.target?1:0};
          if(url.includes('/context-task-references/'))return structuredClone(f.taskReceipt);
          if(opts){
            f.posts.push({body:opts.body,key:opts.headers['Idempotency-Key']});const body=JSON.parse(opts.body);f.entered=true;
            if(scenario.startsWith('late-')||['task-edit','task-refresh'].includes(scenario))await f.gate;
            if(url.endsWith('/context-task-references')){
              f.taskReceipt={schema_version:1,mode:'context_reference_only',product_task_id:body.product_task_id,project_id:project,project_revision:1,
                revision:body.expected_revision+1,title:body.title,retired:false,digest:'c'.repeat(64),source_seq:5,created_at:'2026-10-17T00:00:00Z',status:'current',executable:false};
              if(scenario==='task-unknown-history'){
                f.taskReceipt.status='stale';f.target={...f.taskReceipt,revision:2,status:'current'};
                f.done=true;if(f.posts.length===1)throw Error('synthetic task unknown, another context has r2');
              }else f.target=f.taskReceipt;
              f.done=true;return structuredClone(f.taskReceipt);
            }
            f.row={schema_version:1,manifest_id:body.manifest_id,revision:body.expected_revision+1,project_id:project,project_revision:1,
              grant_id:grant,grant_revision:1,snapshot_id:snapshot,snapshot_revision:1,snapshot_digest:'b'.repeat(64),snapshot_source_seq:4,
              task_ref:null,policy:'reject-secrets-utf8-v1',items:[{input_index:0,path:'<img src=x onerror=alert(1)>.txt',size_bytes:11,content_digest:'e'.repeat(64)}],
              total_size_bytes:11,file_count:1,digest:'c'.repeat(64),source_seq:5,created_at:'2026-10-17T00:00:00Z',status:'current',stale_reasons:[],binding_mode:'project_only',executable:false};
            f.done=true;
            if(['unknown','cross-snapshot','unknown-retry409'].includes(scenario)&&f.posts.length===1)throw Error('synthetic transport unknown after actual fixture commit');
            if(scenario==='correct-after-409'&&f.posts.length===1){f.row=null;throw Object.assign(Error('synthetic precommit CAS denial'),{status:409});}
            if(scenario==='unknown-retry409'&&f.posts.length>1)throw Object.assign(Error('synthetic retry denied; original outcome unknown'),{status:409});
            if(/^\d+$/.test(scenario))throw Object.assign(Error('synthetic denial'),{status:Number(scenario)});
            return structuredClone(f.row);
          }
          if(url.includes('/revisions/')){
            if(scenario==='read-failure-clears-target'&&url.includes('cm-aaaaaaaaaaaaaaaa'))throw Object.assign(Error('synthetic denial for second resource'),{status:403});
            if(scenario==='accepted-get403')throw Object.assign(Error('synthetic read denial after accepted POST'),{status:403});
            if(!f.row)throw Object.assign(Error('not visible'),{status:403});
            const row=structuredClone(f.row);
            if(scenario==='bad-readback')row.digest='d'.repeat(64);
            if(scenario==='stale'){row.status='stale';row.stale_reasons=['project_revision_changed'];row.items=[];}
            return row;
          }
          if(url.endsWith('/context-manifests')){
            const rows=f.row?[Object.fromEntries(Object.entries(f.row).filter(([k])=>k!=='items'))]:[];
            if(rows.length&&scenario==='read-failure-clears-target')rows.push({...rows[0],manifest_id:'cm-aaaaaaaaaaaaaaaa',digest:'d'.repeat(64)});
            return {manifests:rows,total:rows.length};
          }
          throw Error('unexpected fixture route '+url);
        }});await openContext();
      },scenario);
      if(scenario.startsWith('task-')){
        await page.locator('[data-cm-title]').fill('synthetic target');await page.locator('[data-cm-action=task]').click();
      }else await page.locator('[data-cm-action=save]').click();
      await page.waitForFunction(()=>f.entered);
      if(scenario==='late-close'){
        await page.locator('#contextManifestDialog').press('Escape');await page.evaluate(()=>openContext());await page.evaluate(()=>f.release());
      }else if(scenario==='late-edit'){
        await page.locator('[data-cm-title]').fill('edited target');await page.evaluate(()=>f.release());
      }else if(scenario==='task-edit'){
        await page.locator('[data-cm-title]').fill('edited target');await page.evaluate(()=>f.release());
      }else if(scenario==='task-refresh'){
        await page.locator('[data-cm-action=refresh]').click();await page.evaluate(()=>f.release());
      }
      await page.waitForFunction(()=>f.done);
      await page.waitForFunction(()=>!document.querySelector('[data-cm-status]').textContent.includes('正在提交'));
      let text=await page.locator('[data-cm-status]').textContent();
      if(scenario==='task-create')assert.match(text,/持久确认/);
      else if(['task-edit','task-refresh'].includes(scenario))assert.match(text,/原命令已精确读回/);
      else if(scenario==='task-unknown-history'){
        await page.locator('[data-cm-action=retry]').click();await page.waitForFunction(()=>document.querySelector('[data-cm-status]').textContent.includes('过期'));
        const posts=await page.evaluate(()=>f.posts);assert.deepEqual(posts[0],posts[1]);
        assert.equal(JSON.parse(await page.locator('[data-cm-result]').textContent()).revision,1);
      }else if(scenario==='create'){
        assert.match(text,/持久确认/);assert.match(await page.locator('[data-cm-result]').textContent(),/<img src=x/);assert.equal(await page.locator('img').count(),0);
      }else if(scenario==='read-failure-clears-target'){
        const original=await page.evaluate(()=>f.row.manifest_id);
        await page.locator('[data-cm-action=refresh]').click();
        await page.locator('[data-cm-rows] button').filter({hasText:original}).click();await page.waitForFunction(()=>document.querySelector('[data-cm-status]').textContent.includes('持久确认'));
        await page.locator('[data-cm-rows] button').filter({hasText:'cm-aaaaaaaaaaaaaaaa'}).click();await page.waitForFunction(()=>document.querySelector('[data-cm-status]').textContent.includes('被拒绝'));
        await page.locator('[data-cm-action=save]').click();await page.waitForFunction(()=>f.posts.length===2);
        assert.notEqual(JSON.parse((await page.evaluate(()=>f.posts[1])).body).manifest_id,original);
      }else if(scenario==='correct-after-409'){
        await page.locator('[data-cm-action=refresh]').click();await page.locator('[data-cm-action=save]').click();
        await page.waitForFunction(()=>document.querySelector('[data-cm-status]').textContent.includes('持久确认'));
        const posts=await page.evaluate(()=>f.posts);assert.equal(posts.length,2);assert.notEqual(posts[0].key,posts[1].key);
      }else if(['accepted-get403','unknown-retry409'].includes(scenario)){
        await page.locator('[data-cm-action=retry]').click();await page.waitForFunction(()=>f.posts.length===2);
        await page.locator('[data-cm-action=save]').click();assert.deepEqual(await page.evaluate(()=>f.posts[0]),await page.evaluate(()=>f.posts[1]));
        assert.equal(await page.evaluate(()=>f.posts.length),2);
      }else if(scenario==='cross-snapshot'){
        await page.evaluate(()=>a.open({project:'p-0123456789abcdef',grant:'sg-0123456789abcdef',grantRevision:1,snapshotId:'ss-aaaaaaaaaaaaaaaa',snapshotDigest:'f'.repeat(64),fileCount:1,totalSize:11,sourceSeq:9},document.querySelector('#open')));
        assert.doesNotMatch(await page.locator('[data-cm-version]').textContent(),/ss-aaaaaaaaaaaaaaaa/);
        assert.equal(await page.evaluate(()=>f.posts.length),1);
      }else if(scenario==='unknown'){
        assert.doesNotMatch(text,/持久确认/);await page.locator('[data-cm-title]').fill('edited pending');await page.locator('[data-cm-action=refresh]').click();
        await page.locator('[data-cm-action=retry]').click();await page.waitForFunction(()=>document.querySelector('[data-cm-status]').textContent.includes('持久确认'));
        const posts=await page.evaluate(()=>f.posts);assert.equal(posts.length,2);assert.deepEqual(posts[0],posts[1]);
      }else if(scenario==='refresh'){
        assert.match(text,/持久确认/);await page.locator('[data-cm-action=refresh]').click();assert.doesNotMatch(await page.locator('[data-cm-result]').textContent(),/img/);
      }else if(scenario==='late-edit'){
        assert.match(text,/原命令已精确读回/);assert.equal(await page.locator('[data-cm-result]').textContent(),'');
      }else if(scenario==='stale')assert.match(text,/过期|未确认/);
      else assert.doesNotMatch(text,/持久确认/);
      assert.deepEqual(errors,[]);
    }finally{await browser.close();}
  });
}
