/* Explicit immutable input preparation. No commands, host paths or backend activation. */
(function(global){
  'use strict';
  global.PyharnessOwnedInputs={create({main,api,contextKind='workspace'}){
    const c=global.PyharnessWorkspaceContracts;
    if(!['workspace','selected'].includes(contextKind))throw Error('unknown input context kind');
    const selected=contextKind==='selected',sc=global.PyharnessSelectedInputContracts;
    const manifestURL=()=>`/api/v1/projects/${binding.project}/${selected?'context-manifests':'workspace-context-manifests'}/${manifest.manifest_id}/revisions/${manifest.revision}`;
    const uncertain=new WeakSet();
    let el=null,epoch=0,lifecycle=0,binding=null,manifest=null,observed=null,pending=null,sending=false,cursor=null,view=null;
    const node=s=>el?.querySelector(s),say=v=>{const n=node('[data-oi-status]');if(n)n.textContent=v;};
    const live=(dialog,e,l)=>el===dialog&&dialog?.isConnected&&dialog.open&&epoch===e&&lifecycle===l;
    const commandLive=(dialog,l)=>el===dialog&&dialog?.isConnected&&dialog.open&&lifecycle===l;
    const ref=v=>Object.fromEntries(['manifest_id','revision','digest','source_seq'].map(k=>[k,v[k]]));
    const scope=()=>c.canonical({binding,manifest_ref:ref(manifest)}),same=cmd=>cmd?.scope===scope();
    function controls(){for(const action of ['prepare','retry','reconcile']){const n=node(`[data-oi-action=${action}]`);if(n)n.disabled=sending||(action==='prepare'?!!pending:!same(pending));}
      const original=node('[data-oi-action=original]');if(original)original.disabled=sending||!pending||same(pending);}
    function reset(){epoch++;observed=null;cursor=null;view=null;if(node('[data-oi-confirm]'))node('[data-oi-confirm]').checked=false;
      node('[data-oi-result]')?.replaceChildren();node('[data-oi-list]')?.replaceChildren();
      for(const action of ['read','list'])if(node(`[data-oi-action=${action}]`))node(`[data-oi-action=${action}]`).disabled=false;
      if(node('[data-oi-action=more]'))node('[data-oi-action=more]').disabled=true;controls();}
    function invalidate(){epoch++;lifecycle++;observed=null;el?.close();el?.remove();el=null;cursor=null;view=null;}
    function files(cm,ws){return selected?ws.files:cm.items.map(i=>({...i,native_mode:ws.items.find(r=>r.input_index===i.input_index).working.native_mode,applied_mode:384}));}
    function deletions(ws){return selected?[]:ws.items.filter(i=>i.working===null).map(i=>({input_index:i.input_index,path:i.path}));}
    async function valid(v,command=null,detail=true){
      const fields='schema_version input_id project_revision manifest_ref revision project_id source_ref task_ref total_size_bytes policy status stale_reasons workspace_state materialization_status materialization_lifetime persisted executable file_count deletion_count digest source_seq created_at receipt_digest'+(detail?' files deletions':'');
      if(!c.keys(v,fields)||v.schema_version!==(selected?2:1)||!c.id(v.input_id,'wi')||v.revision!==1||v.project_id!==binding.project||v.project_revision!==manifest.project_revision
        ||c.canonical(v.manifest_ref)!==c.canonical(ref(manifest))||(selected?!sc.source(v.source_ref,manifest):c.canonical(v.source_ref)!==c.canonical(manifest.source_ref))||c.canonical(v.task_ref)!==c.canonical(manifest.task_ref)
        ||v.total_size_bytes!==manifest.total_size_bytes||v.policy!==(selected?'private-selected-copy-v1':'private-working-copy-v1')||!['input_prepared','stale'].includes(v.status)
        ||!Array.isArray(v.stale_reasons)||!c.unique(v.stale_reasons)||v.stale_reasons.some(x=>!['project_revision_changed','manifest_revision_superseded','task_revision_changed',...(selected?[]:['workspace_snapshot_superseded'])].includes(x))
        ||(v.status==='stale')!==(v.stale_reasons.length>0)||v.workspace_state!=='not_started'||v.materialization_status!=='checked_at_commit'||v.materialization_lifetime!=='per_explicit_use'
        ||v.persisted!==true||v.executable!==false||!c.integer(v.file_count,0,200)||v.file_count!==manifest.file_count||!c.integer(v.deletion_count,0,200)
        ||v.file_count+v.deletion_count<1||v.file_count+v.deletion_count>200||(selected&&v.deletion_count!==0)||!c.hash(v.digest)||!c.integer(v.source_seq)||typeof v.created_at!=='string'||!Number.isFinite(Date.parse(v.created_at))||!c.hash(v.receipt_digest))return false;
      if(command){const body=JSON.parse(command.body);if(v.input_id!==body.input_id||c.canonical(v.manifest_ref)!==c.canonical(body.manifest_ref)||v.project_revision!==body.project_revision)return false;}
      if(command?.expectedSourceRef&&c.canonical(v.source_ref)!==c.canonical(command.expectedSourceRef))return false;
      if(detail){
        if(!Array.isArray(v.files)||!Array.isArray(v.deletions))return false;
        if(v.status==='stale'){if(v.files.length||v.deletions.length)return false;}
        else{
          if(v.files.length!==v.file_count||v.deletions.length!==v.deletion_count)return false;
          for(const i of v.files)if(!c.keys(i,'input_index path size_bytes content_digest native_mode applied_mode')||!c.integer(i.input_index,0,199)||!c.path(i.path)||!c.integer(i.size_bytes,0,10485760)||!c.hash(i.content_digest)||!c.integer(i.native_mode,0,511)||i.applied_mode!==384)return false;
          for(const i of v.deletions)if(!c.keys(i,'input_index path')||!c.integer(i.input_index,0,199)||!c.path(i.path))return false;
          if(c.canonical(v.files.map(({native_mode,applied_mode,...i})=>i))!==c.canonical(manifest.items))return false;
          const all=[...v.files,...v.deletions];if(!c.unique(all.map(i=>i.path))||new Set(all.map(i=>i.input_index)).size!==all.length)return false;
          let expectedFiles=command?.expectedFiles,expectedDeletions=command?.expectedDeletions;
          if(!expectedFiles){
            const ws=observed?.ws||await api(selected?manifestURL()+'/input-descriptor':`/api/v1/projects/${binding.project}/workspace-snapshots/${manifest.source_ref.snapshot_id}`);
            if(selected){if(!await sc.descriptor(ws,manifest)||c.canonical(v.source_ref)!==c.canonical(ws.source_ref))return false;}
            else if(!await c.snapshot(ws,binding)||ws.status!=='captured'||c.canonical(c.sourceReference(ws))!==c.canonical(manifest.source_ref))return false;
            expectedFiles=files(manifest,ws);expectedDeletions=deletions(ws);
          }
          if(c.canonical(v.files)!==c.canonical(expectedFiles)||c.canonical(v.deletions)!==c.canonical(expectedDeletions))return false;
        }
      }
      const raw={...v};delete raw.receipt_digest;return new TextEncoder().encode(c.canonical(v)).length<=524288&&await c.digest(raw)===v.receipt_digest;
    }
    function show(value){node('[data-oi-result]').textContent=JSON.stringify(value,null,2);say(value.status==='stale'?'已读回过期输入定义；禁止作为当前工作区输入。':'输入定义已持久确认；未启动工作区后端或执行。临时核验副本已结束，每次使用须重新核权物化。');}
    async function read(){
      reset();const dialog=el,e=epoch,l=lifecycle;node('[data-oi-action=read]').disabled=true;say('正在核对确切清单与已提交快照…');
      try{
        const cm=await api(manifestURL());
        if(!live(dialog,e,l))return;
        if(!(selected?sc.manifest(cm,binding):await c.manifest(cm,binding))||cm.status!=='current'||c.canonical(cm)!==c.canonical(manifest))throw Error('manifest changed');
        const ws=await api(selected?manifestURL()+'/input-descriptor':`/api/v1/projects/${binding.project}/workspace-snapshots/${cm.source_ref.snapshot_id}`);
        if(!live(dialog,e,l))return;
        if(selected){if(!await sc.descriptor(ws,cm))throw Error('selected descriptor changed');}
        else if(!await c.snapshot(ws,binding)||ws.status!=='captured'||c.canonical(c.sourceReference(ws))!==c.canonical(cm.source_ref))throw Error('snapshot changed');
        if(!live(dialog,e,l))return;observed={cm,ws};
        node('[data-oi-result]').textContent=files(cm,ws).map(i=>`${i.path} · ${i.size_bytes} bytes`).join('\n')+(selected?'\n仅此显式文件子集。':`\n删除标记 ${deletions(ws).length} 项；不会从HEAD/暂存补回。`);
        say('来源已精确核对。请明确确认本次准备；只使用已捕获私有字节。');
      }catch(error){if(live(dialog,e,l)){observed=null;say(`来源核对被拒绝或发生变化（${error.status||'未知'}），请关闭并刷新清单。`);}}
      finally{if(live(dialog,e,l))node('[data-oi-action=read]').disabled=false;}
    }
    async function send(readOnly=false){
      const cmd=pending;if(sending||!same(cmd)){say('请回到原项目及清单版本核对原命令。');return;}
      sending=true;controls();const dialog=el,e=epoch,l=lifecycle;
      say(readOnly?'正在只读核对原编号…':'正在准备输入定义并精确读回…');
      try{
        let receipt=null;
        if(!readOnly){receipt=await api(cmd.url,{method:'POST',body:cmd.body,headers:{'Idempotency-Key':cmd.key}});uncertain.add(cmd);if(!await valid(receipt,cmd))throw Error('invalid prepared input receipt');}
        const value=await api(cmd.url+'/'+JSON.parse(cmd.body).input_id);
        if(!await valid(value,cmd)||(receipt&&c.canonical(receipt)!==c.canonical(value)))throw Error('prepared input readback mismatch');
        pending=null;if(live(dialog,e,l))show(value);else if(commandLive(dialog,l))say('原命令已精确读回；当前观察已失效，请显式重新读取，不恢复旧确认。');
      }catch(error){
        if(!readOnly&&!uncertain.has(cmd)&&[401,403,409,422].includes(error.status))pending=null;
        else uncertain.add(cmd);
        if(commandLive(dialog,l))say(pending?`结果未知（${error.status||'未知'}）；保留原key/body，只读核对或显式原样重试。`:`准备被拒绝（${error.status}），请重新核对来源。`);
      }finally{sending=false;controls();}
    }
    function prepare(){
      if(pending||sending||!observed||!node('[data-oi-confirm]').checked){say('须重新核对来源并明确确认，且先解决原未知命令。');return;}
      node('[data-oi-confirm]').checked=false;
      const body={schema_version:selected?2:1,input_id:'wi-'+crypto.randomUUID().replaceAll('-','').slice(0,16),project_revision:observed.cm.project_revision,manifest_ref:ref(observed.cm)};
      pending={scope:scope(),binding:JSON.parse(JSON.stringify(binding)),manifest:JSON.parse(JSON.stringify(manifest)),url:`/api/v1/projects/${binding.project}/workspace-inputs`,body:JSON.stringify(body),key:crypto.randomUUID().replaceAll('-',''),expectedSourceRef:selected?observed.ws.source_ref:observed.cm.source_ref,expectedFiles:files(observed.cm,observed.ws),expectedDeletions:deletions(observed.ws)};send();
    }
    async function list(more=false){
      epoch++;observed=null;node('[data-oi-confirm]').checked=false;node('[data-oi-action=read]').disabled=false;
      const dialog=el,e=epoch,l=lifecycle;
      try{
        const page=await api(`/api/v1/projects/${binding.project}/workspace-inputs?limit=25`+(more?`&cursor=${encodeURIComponent(cursor)}`:''));
        if(!live(dialog,e,l))return;if(!c.page(page,'inputs')||(more&&view!==page.view_digest))throw Error('input page changed');
        const rows=page.inputs.filter(v=>c.canonical(v.manifest_ref)===c.canonical(ref(manifest)));
        for(const row of rows)if(!await valid(row,null,false))throw Error('invalid prepared summary');
        if(!live(dialog,e,l))return;if(!more)node('[data-oi-list]').replaceChildren();
        for(const row of rows){const button=document.createElement('button');button.textContent=`读取 ${row.input_id} · ${row.status}`;button.onclick=async()=>{
          epoch++;observed=null;node('[data-oi-confirm]').checked=false;node('[data-oi-action=read]').disabled=false;
          const dialog=el,e=epoch,l=lifecycle;
          try{const value=await api(`/api/v1/projects/${binding.project}/workspace-inputs/${row.input_id}`);if(!live(dialog,e,l))return;
            const header={...value};delete header.files;delete header.deletions;delete header.receipt_digest;header.receipt_digest=await c.digest(header);
            if(!await valid(value)||c.canonical(header)!==c.canonical(row))throw Error('input summary mismatch');if(live(dialog,e,l))show(value);
          }catch(error){if(live(dialog,e,l))say('输入定义读取被拒绝或发生变化，请刷新。');}};node('[data-oi-list]').append(button);}
        cursor=page.next_cursor;view=page.view_digest;node('[data-oi-action=more]').disabled=!page.has_more;
        say(`此视图 ${page.total} 项；本页仅显示该确切清单版本 ${rows.length} 项。`);
      }catch(error){if(live(dialog,e,l)){cursor=null;node('[data-oi-action=more]').disabled=true;say(`列表未确认（${error.status||'未知'}），请刷新。`);}}
    }
    const controller={invalidate,open(definition,cm){
      invalidate();binding=Object.freeze({...definition});manifest=JSON.parse(JSON.stringify(cm));
      el=document.createElement('dialog');el.id='ownedInputsDialog';el.setAttribute('aria-label','自有输入准备');
      el.style.cssText='width:min(850px,calc(100vw - 32px));max-height:85vh;overflow:auto;box-sizing:border-box;overflow-wrap:anywhere';
      el.innerHTML='<h2>自有输入准备</h2><button data-oi-action="close">关闭</button><p>仅使用已提交快照的工作层字节和删除标记；不读取当前宿主正文，不启动模型、后端或执行。临时副本不是OS沙箱。</p><p data-oi-version></p><button data-oi-action="read">核对该清单与快照版本</button><button data-oi-action="refresh">清除观察与确认</button><p><label><input type="checkbox" data-oi-confirm>确认准备这个确切版本的私有输入定义；不授权执行或回写</label></p><button data-oi-action="prepare">准备不可变输入定义</button><button data-oi-action="list">读取持久输入列表</button><button data-oi-action="more" disabled>下一页输入</button><section data-oi-list></section><button data-oi-action="reconcile">只读核对原命令</button><button data-oi-action="retry">显式原样重试</button><p data-oi-status role="status" aria-live="polite"></p><pre data-oi-result style="white-space:pre-wrap;overflow-wrap:anywhere"></pre>';
      if(selected)el.querySelector('h2').nextElementSibling.nextElementSibling.textContent='准备显式选中文件的已提交私有字节；不读取当前宿主正文，不启动模型、后端或执行。临时副本不是OS沙箱。';
      const original=document.createElement('button');original.dataset.oiAction='original';original.textContent='返回原绑定核对未知命令';el.querySelector('[data-oi-action=reconcile]').before(original);
      main.append(el);node('[data-oi-version]').textContent=`项目 r${cm.project_revision} · 清单 ${cm.manifest_id} r${cm.revision} · 来源 ${selected?cm.snapshot_id:cm.source_ref.snapshot_id}`;
      el.onclick=event=>{const action=event.target.closest('[data-oi-action]')?.dataset.oiAction;
        if(action==='close')invalidate();else if(action==='read')read();else if(action==='refresh'){reset();say('观察与确认已清除；请重新核对来源。');}
        else if(action==='prepare')prepare();else if(action==='list')list();else if(action==='more'&&cursor)list(true);else if(action==='retry')send();else if(action==='reconcile')send(true);};
      original.onclick=event=>{event.stopPropagation();if(pending&&!sending&&!same(pending)){const cmd=pending;controller.open(cmd.binding,cmd.manifest);}};
      el.oncancel=event=>{event.preventDefault();invalidate();};el.showModal();controls();say(pending?(same(pending)?'存在原未确认命令；请在原清单版本核对，不能自动重发。':'存在原未知命令；当前绑定不同，请返回原绑定后只读核对。'):'请先核对来源；所有确认默认关闭。');
    }};return controller;
  }};
})(window);
