/* Durable Git inputs and source-bound context; no execution or host writeback. */
(function(global){
  'use strict';
  global.PyharnessWorkspaceInputs={create({main,api}){
    const c=global.PyharnessWorkspaceContracts;
    let candidates=null;
    const ownedInputs=global.PyharnessOwnedInputs?.create({main,api});
    let binding=null,trigger=null,epoch=0,lifecycle=0,ready=false,commandBusy=false,pending=null;
    let projectRevision=null,grants=[],tasks=[],preview=null,selectedSnapshot=null,selectedManifest=null;
    const cursors={snapshots:null,manifests:null},views={snapshots:null,manifests:null},uncertain=new WeakSet();
    const dialog=()=>main.querySelector('#workspaceInputsDialog'),node=s=>dialog()?.querySelector(s),text=(s,v)=>{const n=node(s);if(n)n.textContent=v;};
    const live=(el,e,l)=>el?.isConnected&&el.open&&epoch===e&&lifecycle===l;
    const commandLive=(el,l)=>el?.isConnected&&el.open&&lifecycle===l;
    const identity=b=>c.canonical(b),same=command=>command&&identity(command.binding)===identity(binding);
    const key=()=>crypto.randomUUID().replaceAll('-','');
    function controls(){for(const action of ['capture','manifest','task','retry','reconcile']){const n=node(`[data-ws-action=${action}]`);if(n)n.disabled=commandBusy||(['retry','reconcile'].includes(action)&&(!pending||!same(pending)));} }
    function clear({source=true,manifest=true}={}){
      candidates?.invalidate();
      ownedInputs?.invalidate();
      epoch++;preview=null;if(manifest)selectedManifest=null;if(source)selectedSnapshot=null;
      text('[data-ws-result]','');text('[data-ws-status]','当前观察已失效；请重新核对当前选择。');
      for(const sel of ['[data-ws-read-confirm]','[data-ws-save-confirm]'])if(node(sel))node(sel).checked=false;
      if(node('[data-ws-action=preview]'))node('[data-ws-action=preview]').disabled=false;
      controls();
    }
    function invalidate(){candidates?.invalidate();ownedInputs?.invalidate();epoch++;lifecycle++;ready=false;preview=null;selectedSnapshot=null;selectedManifest=null;dialog()?.close();dialog()?.remove();}
    function chooseCandidates(){
      if(!ready||!global.PyharnessGitCandidates){text('[data-ws-status]','当前版本或候选组件未准备完成。');return;}
      const choice=node('[data-ws-metadata]').value,meta=grants.find(g=>g.grant_id===choice);
      if(choice&&!meta){clear();return;}
      clear();const el=dialog(),e=epoch,l=lifecycle;
      candidates??=global.PyharnessGitCandidates.create({main,api});
      candidates.open(binding,{schema_version:1,project_revision:projectRevision,expected_revision:binding.grantRevision,
        metadata_grant_id:meta?.grant_id||null,metadata_grant_revision:meta?.revision||null},names=>{
          if(!live(el,e,l))return;node('[data-ws-paths]').value=names.join('\n');clear();
          text('[data-ws-status]','名称已明确选择，所有旧确认已清除；仍须正文安全预览、人工确认及服务端重新验证。');
        });
    }
    function paths(){const v=node('[data-ws-paths]').value.split(/\r?\n/);if(v.at(-1)==='')v.pop();return v.length>0&&v.length<=200&&v.every(c.path)&&c.unique(v)?v:null;}
    const base=()=>`/api/v1/projects/${binding.project}`;
    function show(value,kind){
      if(kind==='snapshots')selectedSnapshot=value;else selectedManifest=value;
      preview=null;node('[data-ws-save-confirm]').checked=false;
      text('[data-ws-result]',JSON.stringify(value,null,2));
      text('[data-ws-status]',value.status==='stale'?`已精确读回过期版本：${value.stale_reasons.join('、')}。不得当作当前上下文。`:'持久确认：已严格校验并精确读回；未启动任何执行。');
    }
    async function page(kind,more=false){
      const el=dialog(),e=epoch,l=lifecycle;
      const url=base()+(kind==='snapshots'?'/workspace-snapshots':'/workspace-context-manifests');
      const suffix=more?`?limit=25&cursor=${encodeURIComponent(cursors[kind])}`:'?limit=25';
      try{
        const data=await api(url+suffix);if(!live(el,e,l))return;
        if(!c.page(data,kind)||(more&&views[kind]!==data.view_digest))throw Error('workspace page mismatch');
        const validate=kind==='snapshots'?c.snapshot:c.manifest;
        // Manifests from other grants remain server-authorized, but this panel
        // intentionally displays only the selected source; the page count is explicit.
        for(const row of data[kind]){
          const ownerGrant=kind==='snapshots'?row.grant_id:row.source_ref?.grant_id;
          if(ownerGrant!==binding.grantId)continue;
          if(!await validate(row,binding,false))throw Error('invalid workspace summary');
        }
        if(!live(el,e,l))return;
        const container=node(`[data-ws-${kind}]`);if(!more)container.replaceChildren();
        data[kind].filter(row=>(kind==='snapshots'?row.grant_id:row.source_ref?.grant_id)===binding.grantId).forEach(row=>{
          const b=document.createElement('button');b.type='button';b.textContent=`读取 ${row.snapshot_id||row.manifest_id} · r${row.revision} · ${row.status}`;
          b.addEventListener('click',()=>read(row,kind));container.append(b);
        });
        cursors[kind]=data.next_cursor;views[kind]=data.view_digest;node(`[data-ws-action=more-${kind}]`).disabled=!data.has_more;
        text(`[data-ws-${kind}-count]`,`此页 ${data.page_count} 项 / 此视图 ${data.total} 项；仅显示当前源授权。水位 ${data.source_watermark}。`);
      }catch(error){if(live(el,e,l)){cursors[kind]=null;node(`[data-ws-action=more-${kind}]`).disabled=true;text('[data-ws-status]',`列表被拒绝或视图变化（${error.status||'未知'}）；请刷新。`);}}
    }
    async function load(){
      clear();ready=false;projectRevision=null;const el=dialog(),e=epoch,l=lifecycle;
      node('[data-ws-metadata]').replaceChildren(new Option('无外置元数据授权',''));node('[data-ws-target]').replaceChildren(new Option('仅绑定项目',''));
      node('[data-ws-snapshots]').replaceChildren();node('[data-ws-manifests]').replaceChildren();
      text('[data-ws-status]','正在读取当前项目、授权与任务引用…');
      try{
        const [p,g,t]=await Promise.all([api('/api/v1/projects'),api(base()+'/source-grants'),api(base()+'/context-task-references')]);
        if(!live(el,e,l))return;
        const parent=p.projects?.find(r=>r.project?.project_id===binding.project)?.project;
        if(!parent||parent.archived||!c.integer(parent.revision)||!Array.isArray(g.grants)||g.total!==g.grants.length||g.total>200||!g.grants.every(c.grant)
          ||new Set(g.grants.map(x=>x.grant_id)).size!==g.total||!Array.isArray(t.task_references)||t.total!==t.task_references.length||t.total>200
          ||!t.task_references.every(x=>c.task(x)&&x.project_id===binding.project)||new Set(t.task_references.map(x=>x.product_task_id)).size!==t.total)throw Error('invalid current workspace versions');
        const grant=g.grants.find(x=>x.grant_id===binding.grantId);
        if(!grant||grant.revision!==binding.grantRevision||grant.source_seq!==binding.sourceSeq||grant.revoked||Date.parse(grant.expires_at)<=Date.now())throw Error('source grant changed');
        projectRevision=parent.revision;grants=g.grants.filter(x=>x.grant_id!==binding.grantId&&!x.revoked&&Date.parse(x.expires_at)>Date.now());tasks=t.task_references;
        grants.forEach(g=>node('[data-ws-metadata]').append(new Option(`${g.grant_id} · r${g.revision}`,g.grant_id)));
        tasks.filter(t=>!t.retired).forEach(t=>node('[data-ws-target]').append(new Option(`${t.title} · r${t.revision} · ${t.status}`,t.product_task_id)));
        ready=true;text('[data-ws-version]',`项目 r${projectRevision} · 授权 ${binding.grantId} r${binding.grantRevision} · 任务引用只作来源关联，不能执行。`);
        text('[data-ws-status]',pending?'原命令结果尚未核对；保留原键和原正文。':'当前版本已读取；显式选择全部 tracked 及需要的 untracked 后确认预览。');
        await Promise.all([page('snapshots'),page('manifests')]);
      }catch(error){if(live(el,e,l))text('[data-ws-status]',`版本读取被拒绝或未确认（${error.status||'未知'}），请关闭并刷新授权。`);}
    }
    async function observe(){
      const selected=paths();if(!ready||!selected||!node('[data-ws-read-confirm]').checked){text('[data-ws-status]','须成功读取版本，显式选择1–200个文件并确认可读。');return;}
      const choice=node('[data-ws-metadata]').value,meta=grants.find(g=>g.grant_id===choice);if(choice&&!meta){clear();return;}
      const body={schema_version:1,expected_revision:binding.grantRevision,project_revision:projectRevision,paths:selected,metadata_grant_id:meta?.grant_id||null,metadata_grant_revision:meta?.revision||null};
      clear();const el=dialog(),e=epoch,l=lifecycle;node('[data-ws-action=preview]').disabled=true;text('[data-ws-status]','正在安全读取并检查版本一致性…');
      try{
        const value=await api(base()+`/source-grants/${binding.grantId}/workspace-snapshot-previews`,{method:'POST',body:JSON.stringify(body)});
        if(!live(el,e,l))return;
        if(!await c.preview(value,body,binding))throw Error('workspace preview digest or source mismatch');
        if(!live(el,e,l))return;
        preview={value,body};text('[data-ws-result]',JSON.stringify(value,null,2));text('[data-ws-status]','预览已核验，可确认创建私有副本；尚未持久化。');
      }catch(error){if(live(el,e,l))text('[data-ws-status]',`预览被拒绝或未确认（${error.status||'未知'}）；没有可用确认。`);}
      finally{if(live(el,e,l))node('[data-ws-action=preview]').disabled=false;}
    }
    async function read(row,kind){
      clear({manifest:kind!=='snapshots'});const el=dialog(),e=epoch,l=lifecycle;
      try{
        const url=base()+(kind==='snapshots'?`/workspace-snapshots/${row.snapshot_id}`:`/workspace-context-manifests/${row.manifest_id}/revisions/${row.revision}`);
        const value=await api(url);if(!live(el,e,l))return;
        if(!await (kind==='snapshots'?c.snapshot:c.manifest)(value,binding)||c.canonical(await c.summary(value,kind))!==c.canonical(row))throw Error('workspace summary readback mismatch');
        if(!live(el,e,l))return;
        if(kind==='manifests'){
          const source=await api(base()+`/workspace-snapshots/${value.source_ref.snapshot_id}`);
          if(!await c.snapshot(source,binding)||c.canonical(c.sourceReference(source))!==c.canonical(value.source_ref))throw Error('manifest source readback mismatch');
          if(!live(el,e,l))return;
          selectedSnapshot=source;node('[data-ws-target]').value=value.task_ref?.product_task_id||'';
        }
        if(live(el,e,l))show(value,kind);
      }catch(error){if(live(el,e,l))text('[data-ws-status]',`精确读回被拒绝或版本变化（${error.status||'未知'}）；请刷新。`);}
    }
    function matches(value,command){
      const b=JSON.parse(command.body);
      if(command.kind==='snapshots')return value.snapshot_id===b.snapshot_id&&value.project_revision===b.project_revision&&value.confirmation_digest===b.confirmation_digest
        &&c.canonical(value.items.map(i=>i.path))===c.canonical(b.paths)&&value.metadata_grant_id===b.metadata_grant_id&&value.metadata_grant_revision===b.metadata_grant_revision;
      if(command.kind==='manifests')return value.manifest_id===b.manifest_id&&value.revision===b.expected_revision+1&&value.project_revision===b.project_revision
        &&c.canonical(value.task_ref)===c.canonical(b.task_ref)&&c.canonical(value.source_ref)===c.canonical(command.expectedSource);
      return value.project_id===command.binding.project&&value.product_task_id===b.product_task_id&&value.revision===b.expected_revision+1&&value.project_revision===b.project_revision&&value.title===b.title&&value.retired===b.retired;
    }
    async function validate(value,command){return matches(value,command)&&await (command.kind==='snapshots'?c.snapshot(value,command.binding):command.kind==='manifests'?c.manifest(value,command.binding):c.task(value));}
    async function exact(command,receipt=null){
      const b=JSON.parse(command.body),url=command.kind==='snapshots'?`/api/v1/projects/${command.binding.project}/workspace-snapshots/${b.snapshot_id}`
        :command.url+`/${b.manifest_id||b.product_task_id}/revisions/${b.expected_revision+1}`;
      const value=await api(url);if(!await validate(value,command)||(receipt&&c.canonical(value)!==c.canonical(receipt)))throw Error('exact workspace receipt mismatch');return value;
    }
    async function send(command,readOnly=false){
      if(commandBusy||!same(command)){text('[data-ws-status]','请在原项目及源授权核对待定命令。');return;}
      commandBusy=true;pending=command;controls();const el=dialog(),e=epoch,l=lifecycle;
      text('[data-ws-result]','');text('[data-ws-status]',readOnly?'正在只读核对原编号…':'正在提交原编号，随后精确读回…');
      try{
        let receipt=null;
        if(!readOnly){receipt=await api(command.url,{method:'POST',body:command.body,headers:{'Idempotency-Key':command.key}});uncertain.add(command);if(!await validate(receipt,command))throw Error('invalid workspace commit receipt');}
        const value=await exact(command,receipt);pending=null;uncertain.delete(command);
        if(live(el,e,l)){
          if(command.kind==='tasks'){tasks=tasks.filter(t=>t.product_task_id!==value.product_task_id);tasks.push(value);node('[data-ws-target]').replaceChildren(new Option('仅绑定项目',''));tasks.filter(t=>!t.retired).forEach(t=>node('[data-ws-target]').append(new Option(`${t.title} · r${t.revision}`,t.product_task_id)));node('[data-ws-target]').value=value.retired?'':value.product_task_id;text('[data-ws-result]',JSON.stringify(value,null,2));text('[data-ws-status]','任务引用持久确认，仅作来源关联。');}
          else show(value,command.kind);
        }else if(commandLive(el,l))text('[data-ws-status]',`原命令已精确读回 ${value.snapshot_id||value.manifest_id||value.product_task_id} · r${value.revision} · ${value.status}；当前观察已失效，请显式刷新读取，不恢复旧选择。`);
      }catch(error){
        if(!readOnly&&!uncertain.has(command)&&[401,403,409,422].includes(error.status))pending=null;else uncertain.add(command);
        if(commandLive(el,l))text('[data-ws-status]',pending?`结果未确认（${error.status||'未知'}）；原key/body保留，只读核对或显式原样重试。`:`命令被拒绝（${error.status}）；请刷新、修正后重新确认。`);
      }finally{commandBusy=false;controls();}
    }
    function command(kind){
      if(pending){text('[data-ws-status]','存在未确认原命令；先只读核对或显式原样重试。');return;}
      if(!ready||!node('[data-ws-save-confirm]').checked){text('[data-ws-status]','请显式确认本次持久操作。');return;}
      let body,url;
      if(kind==='snapshots'){
        if(!preview){text('[data-ws-status]','当前选择缺少有效预览确认，请重新预览。');return;}
        body={...preview.body,snapshot_id:'ws-'+key().slice(0,16),confirmation_digest:preview.value.confirmation_digest};url=base()+`/source-grants/${binding.grantId}/workspace-snapshots`;
      }else if(kind==='manifests'){
        const snapshot=selectedSnapshot,task=tasks.find(t=>t.product_task_id===node('[data-ws-target]').value);
        if(!snapshot){text('[data-ws-status]','请先精确读取或创建来源快照。');return;}
        if(node('[data-ws-target]').value&&(!task||task.retired||task.project_revision!==projectRevision)){text('[data-ws-status]','任务引用已过期，请先显式刷新其版本。');return;}
        body={schema_version:2,manifest_id:selectedManifest?.manifest_id||'cm-'+key().slice(0,16),expected_revision:selectedManifest?.revision||0,project_revision:projectRevision,
          source_ref:Object.fromEntries(['snapshot_id','revision','digest','source_seq'].map(k=>[k,snapshot[k]])),task_ref:task?Object.fromEntries(['product_task_id','revision','digest','source_seq'].map(k=>[k,task[k]])):null};url=base()+'/workspace-context-manifests';
      }else{
        const task=tasks.find(t=>t.product_task_id===node('[data-ws-target]').value),title=node('[data-ws-title]').value;
        if(!title||title.length>100){text('[data-ws-status]','任务引用标题须1–100字符。');return;}
        body={schema_version:1,product_task_id:task?.product_task_id||'pt-'+key().slice(0,16),expected_revision:task?.revision||0,project_revision:projectRevision,title,retired:false};url=base()+'/context-task-references';
      }
      node('[data-ws-save-confirm]').checked=false;send({kind,url,body:JSON.stringify(body),key:key(),binding:{...binding},
        ...(kind==='manifests'?{expectedSource:c.sourceReference(selectedSnapshot)}:{})});
    }
    return{invalidate,async open(definition,initialPaths,button){
      invalidate();binding=Object.freeze({...definition});trigger=button;
      const el=document.createElement('dialog');el.id='workspaceInputsDialog';el.setAttribute('aria-label','持久Git输入与上下文清单');
      el.style.cssText='width:min(920px,calc(100vw - 32px));max-height:85vh;overflow:auto;overflow-wrap:anywhere;box-sizing:border-box';
      el.innerHTML='<h2>持久 Git 输入与上下文清单</h2><button data-ws-action="close">关闭</button><p>明确选择全部 tracked 与需要的 untracked；不会暗纳其他文件。区分 HEAD、暂存、工作区原字节及删除。每次最多200项，不同版本累计10MiB；25秒观察预算。stable_observed 不保证OS原子快照。</p><p data-ws-version></p><label>显式相对路径（每行一项）<textarea data-ws-paths rows="5" maxlength="205000" spellcheck="false" style="width:100%;box-sizing:border-box"></textarea></label><p><label>外置元数据授权 <select data-ws-metadata></select></label></p><label><input type="checkbox" data-ws-read-confirm>确认材料有权读取且不含秘密或客户材料</label><p><button data-ws-action="preview">安全预览当前版本</button><button data-ws-action="refresh">刷新持久状态</button></p><label><input type="checkbox" data-ws-save-confirm>确认本次创建私有副本或保存来源关联；不会执行或回写</label><p><button data-ws-action="capture">创建私有 Git 快照</button></p><label>任务引用 <select data-ws-target></select></label><label>引用标题 <input data-ws-title maxlength="100" autocomplete="off"></label><button data-ws-action="task">保存/刷新任务引用</button><button data-ws-action="manifest">保存/刷新上下文清单</button><p><button data-ws-action="reconcile">只读核对原命令</button><button data-ws-action="retry">显式原样重试原命令</button></p><p data-ws-status role="status" aria-live="polite"></p><h3>已有快照</h3><p data-ws-snapshots-count></p><section data-ws-snapshots></section><button data-ws-action="more-snapshots" disabled>下一页快照</button><h3>已有上下文清单</h3><p data-ws-manifests-count></p><section data-ws-manifests></section><button data-ws-action="more-manifests" disabled>下一页清单</button><pre data-ws-result style="white-space:pre-wrap;overflow-wrap:anywhere"></pre>';
      main.append(el);node('[data-ws-paths]').value=initialPaths;
      const discover=document.createElement('button');discover.type='button';discover.dataset.wsAction='candidates';discover.textContent='显式列出 Git 候选元数据';
      node('[data-ws-paths]').closest('label').after(discover);
      const prepareInput=document.createElement('button');prepareInput.type='button';prepareInput.dataset.wsAction='owned-inputs';prepareInput.textContent='准备所读清单的自有输入';
      node('[data-ws-action=manifest]').after(prepareInput);
      el.addEventListener('input',event=>{if(!event.target.matches('[data-ws-read-confirm],[data-ws-save-confirm]'))clear({source:event.target.matches('[data-ws-paths],[data-ws-metadata]'),manifest:!event.target.matches('[data-ws-title]')});});
      el.addEventListener('change',event=>{if(event.target.matches('[data-ws-metadata]'))clear();if(event.target.matches('[data-ws-target]'))clear({source:false});});
      el.addEventListener('click',event=>{
        const action=event.target.closest('[data-ws-action]')?.dataset.wsAction;
        if(action==='close'){invalidate();trigger?.focus();}else if(action==='refresh')load();else if(action==='preview')observe();else if(action==='candidates')chooseCandidates();
        else if(action==='owned-inputs'){
          if(!selectedManifest||selectedManifest.status!=='current'||!ownedInputs){text('[data-ws-status]','请先精确读取当前清单版本。');return;}
          node('[data-ws-read-confirm]').checked=false;node('[data-ws-save-confirm]').checked=false;preview=null;
          ownedInputs.open(binding,selectedManifest);
        }
        else if(action==='capture')command('snapshots');else if(action==='manifest')command('manifests');else if(action==='task')command('tasks');
        else if(action==='retry'&&pending)send(pending);else if(action==='reconcile'&&pending)send(pending,true);
        else if(action==='more-snapshots'&&cursors.snapshots)page('snapshots',true);else if(action==='more-manifests'&&cursors.manifests)page('manifests',true);
      });
      el.oncancel=event=>{event.preventDefault();invalidate();trigger?.focus();};el.showModal();controls();await load();
    }};
  }};
})(window);
