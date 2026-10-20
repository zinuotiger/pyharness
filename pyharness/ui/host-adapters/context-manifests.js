/* Explicit immutable context references. No task execution or automatic command replay. */
(function (global) {
  'use strict';
  const hash = v => typeof v === 'string' && /^[0-9a-f]{64}$/.test(v);
  const integer = (v, min=1, max=Number.MAX_SAFE_INTEGER) => Number.isSafeInteger(v) && v >= min && v <= max;
  const canonical = v => JSON.stringify(v, (_, x) => x && typeof x === 'object' && !Array.isArray(x)
    ? Object.fromEntries(Object.entries(x).sort(([a],[b]) => a.localeCompare(b))) : x);
  const id = prefix => prefix + crypto.randomUUID().replaceAll('-', '').slice(0,16);
  const ref = task => task ? Object.fromEntries(['product_task_id','revision','digest','source_seq'].map(k => [k,task[k]])) : null;
  global.PyharnessContextManifests = {
    create({main, api}) {
      let binding=null, projectRevision=null, tasks=[], selected=null, pending=null, commandBusy=false;
      let epoch=0, lifecycle=0, trigger=null;
      const uncertainCommands=new WeakSet();
      const owned=global.PyharnessOwnedInputs?.create({main,api,contextKind:'selected'});
      let inputSelection=null;
      const dialog=()=>main.querySelector('#contextManifestDialog');
      const node=s=>dialog()?.querySelector(s);
      const text=(s,t)=>{const n=node(s);if(n)n.textContent=t;};
      const live=(el,e,l)=>el?.isConnected && el.open && epoch===e && lifecycle===l;
      const commandLive=(el,l)=>el?.isConnected && el.open && lifecycle===l;
      function invalidate() {
        owned?.invalidate();inputSelection=null;
        epoch++;lifecycle++;projectRevision=null;tasks=[];selected=null;
        dialog()?.close();dialog()?.remove();
      }
      function clearView() {
        owned?.invalidate();inputSelection=null;if(node('[data-cm-action=inputs]'))node('[data-cm-action=inputs]').disabled=true;
        epoch++;text('[data-cm-result]','');text('[data-cm-status]',pending?'原命令尚未确认；只可核对或显式同键重试。':'观察已失效；保存时以当前版本为准。');
      }
      function validTask(t) {
        return t && t.schema_version===1 && t.mode==='context_reference_only' && t.project_id===binding.project
          && /^pt-[0-9a-f]{16}$/.test(t.product_task_id) && integer(t.revision,1,200) && integer(t.project_revision)
          && typeof t.title==='string' && typeof t.retired==='boolean' && hash(t.digest) && integer(t.source_seq)
          && t.executable===false && ['current','stale','retired'].includes(t.status) && Number.isFinite(Date.parse(t.created_at));
      }
      function validManifest(m, items=true) {
        if (!(m && m.schema_version===1 && /^cm-[0-9a-f]{16}$/.test(m.manifest_id) && integer(m.revision,1,200)
          && m.project_id===binding.project && integer(m.project_revision) && /^sg-[0-9a-f]{16}$/.test(m.grant_id)
          && integer(m.grant_revision) && /^ss-[0-9a-f]{16}$/.test(m.snapshot_id) && m.snapshot_revision===1
          && hash(m.snapshot_digest) && integer(m.snapshot_source_seq) && hash(m.digest) && integer(m.source_seq,m.snapshot_source_seq+1)
          && integer(m.file_count,1,200) && integer(m.total_size_bytes,0,10485760) && m.policy==='reject-secrets-utf8-v1'
          && m.executable===false && ['current','stale'].includes(m.status) && Number.isFinite(Date.parse(m.created_at))
          && Array.isArray(m.stale_reasons) && m.stale_reasons.every(r=>['project_revision_changed','task_revision_changed','manifest_revision_superseded'].includes(r))
          && (m.status==='current')===(m.stale_reasons.length===0))) return false;
        if(m.task_ref!==null && !(m.task_ref && /^pt-[0-9a-f]{16}$/.test(m.task_ref.product_task_id)
          && integer(m.task_ref.revision) && hash(m.task_ref.digest) && integer(m.task_ref.source_seq)))return false;
        if(m.binding_mode!==(m.task_ref?'context_task_reference':'project_only'))return false;
        if(!items)return !Object.hasOwn(m,'items');
        if(!Array.isArray(m.items))return false;
        if(m.status==='stale')return m.items.length===0;
        return m.items.length===m.file_count && m.items.every((i,index)=>i.input_index===index && typeof i.path==='string'
          && i.path.length>0 && i.path.length<=1024 && integer(i.size_bytes,0,10485760) && hash(i.content_digest))
          && new Set(m.items.map(i=>i.path.toLowerCase())).size===m.file_count
          && m.items.reduce((sum,i)=>sum+i.size_bytes,0)===m.total_size_bytes;
      }
      function matches(m, command) {
        const b=JSON.parse(command.body),s=command.source;
        if(command.kind==='task')return validTask(m) && m.product_task_id===b.product_task_id
          && m.revision===b.expected_revision+1 && m.project_revision===b.project_revision && m.title===b.title && m.retired===b.retired;
        return validManifest(m) && m.manifest_id===b.manifest_id && m.revision===b.expected_revision+1
          && m.project_revision===b.project_revision && m.grant_id===b.grant_id && m.grant_revision===b.grant_revision
          && m.snapshot_id===b.snapshot_id && m.snapshot_digest===b.snapshot_digest && canonical(m.task_ref)===canonical(b.task_ref)
          && m.file_count===s.fileCount && m.total_size_bytes===s.totalSize && m.snapshot_source_seq===s.sourceSeq;
      }
      function show(m, kind) {
        text('[data-cm-status]', m.status==='current' ? '持久确认 · 已精确读回；仅引用，不执行任务。' : '已读回持久记录，但已过期或退役，不能用作当前上下文。');
        text('[data-cm-result]',JSON.stringify(m,null,2));
        if(kind==='manifest'){selected=m;inputSelection=m;if(node('[data-cm-action=inputs]'))node('[data-cm-action=inputs]').disabled=!owned;}
      }
      async function observe() {
        owned?.invalidate();inputSelection=null;if(node('[data-cm-action=inputs]'))node('[data-cm-action=inputs]').disabled=true;
        const el=dialog(),e=++epoch,l=lifecycle;
        projectRevision=null;tasks=[];selected=null;text('[data-cm-result]','');text('[data-cm-status]','正在读取当前版本和持久引用…');
        node('[data-cm-rows]')?.replaceChildren();node('[data-cm-target]')?.replaceChildren(new Option('仅绑定项目版本',''));
        try {
          const base=`/api/v1/projects/${binding.project}`;
          const [p,t,m]=await Promise.all([api('/api/v1/projects'),api(base+'/context-task-references'),api(base+'/context-manifests')]);
          if(!live(el,e,l))return;
          const parent=p.projects?.find(r=>r.project?.project_id===binding.project)?.project;
          if(!parent || parent.archived || !integer(parent.revision) || !Array.isArray(t.task_references)
            || t.total!==t.task_references.length || t.total>200 || !t.task_references.every(validTask)
            || !Array.isArray(m.manifests) || m.total!==m.manifests.length || m.total>200 || !m.manifests.every(row=>validManifest(row,false))
            || new Set(t.task_references.map(r=>r.product_task_id)).size!==t.total || new Set(m.manifests.map(r=>r.manifest_id)).size!==m.total)throw Error('invalid context version listing');
          projectRevision=parent.revision;tasks=t.task_references;
          tasks.filter(t=>!t.retired).forEach(t=>node('[data-cm-target]').append(new Option(`${t.title} · r${t.revision} · ${t.status}`,t.product_task_id)));
          m.manifests.filter(r=>r.snapshot_id===binding.snapshotId).forEach(row=>{
            const b=document.createElement('button');b.textContent=`读取 ${row.manifest_id} · r${row.revision} · ${row.status}`;
            b.addEventListener('click',()=>read(row));node('[data-cm-rows]').append(b);
          });
          text('[data-cm-version]',`项目 r${projectRevision} · 快照 ${binding.snapshotId} · ${binding.fileCount} 文件 / ${binding.totalSize} 字节。`);
          text('[data-cm-status]',pending?'原命令结果未确认；保留原键和原正文，不会自动重发。':'当前版本已读取；请显式保存或读取已有清单。');
        }catch(error){if(live(el,e,l)){projectRevision=null;text('[data-cm-status]',`读取被拒绝或未确认（${error.status||'未知'}）；未启动执行。`);}}
      }
      async function read(row) {
        owned?.invalidate();inputSelection=null;if(node('[data-cm-action=inputs]'))node('[data-cm-action=inputs]').disabled=true;
        selected=null;
        const el=dialog(),e=++epoch,l=lifecycle;text('[data-cm-result]','');text('[data-cm-status]','正在精确读回持久版本…');
        try {
          const m=await api(`/api/v1/projects/${binding.project}/context-manifests/${row.manifest_id}/revisions/${row.revision}`);
          if(!live(el,e,l))return;
          const summary={...m};delete summary.items;
          if(!validManifest(m) || canonical(summary)!==canonical(row))throw Error('context readback mismatch');
          node('[data-cm-target]').value=m.task_ref?.product_task_id||'';show(m,'manifest');
        }catch(error){if(live(el,e,l))text('[data-cm-status]',`读回被拒绝或未确认（${error.status||'未知'}），没有可用清单。`);}
      }
      async function reconcile(command, receipt=null) {
        const b=JSON.parse(command.body);
        const value=command.kind==='manifest' ? await api(`${command.url}/${b.manifest_id}/revisions/${b.expected_revision+1}`)
          : await api(`${command.url}/${b.product_task_id}/revisions/${b.expected_revision+1}`);
        if(!matches(value,command) || (receipt && canonical(value)!==canonical(receipt)))throw Error('exact context readback mismatch');
        return value;
      }
      async function send(command, readOnly=false) {
        if(commandBusy)return;
        commandBusy=true;pending=command;
        let postAccepted=readOnly || uncertainCommands.has(command);
        const el=dialog(),e=epoch,l=lifecycle;text('[data-cm-result]','');text('[data-cm-status]',readOnly?'正在核对原命令…':'正在提交并精确读回…');
        try {
          const receipt=readOnly?null:await api(command.url,{method:'POST',body:command.body,headers:{'Idempotency-Key':command.key}});
          postAccepted=true;
          if(receipt && !matches(receipt,command))throw Error('invalid context receipt');
          const value=await reconcile(command,receipt);
          pending=null;
          if(commandLive(el,l)) {
            if(epoch===e)show(value,command.kind);
            else text('[data-cm-status]',`原命令已精确读回 · ${value.manifest_id||value.product_task_id} r${value.revision} · ${value.status}；当前观察已失效，请刷新。`);
          }
        }catch(error){
          if(!postAccepted && [401,403,409,422].includes(error.status))pending=null;
          else uncertainCommands.add(command);
          if(commandLive(el,l))text('[data-cm-status]',pending
            ? `提交被拒绝或结果未确认（${error.status||'未知'}）；保留原key/body，可核对或显式同键重试，不会自动重发。`
            : `提交明确拒绝（${error.status}）；未创建，请刷新当前版本后纠正输入。`);
        }
        finally{commandBusy=false;}
      }
      function save(kind, retired=false) {
        if(pending){text('[data-cm-status]','原命令尚未确认；先核对或同键重试。');return;}
        if(commandBusy || !projectRevision){text('[data-cm-status]','请先刷新并成功读取当前版本。');return;}
        const task=tasks.find(t=>t.product_task_id===node('[data-cm-target]').value);
        let body;
        if(kind==='task'){
          const title=node('[data-cm-title]').value;
          if(!title.trim() || title.length>100 || (retired && !task)){text('[data-cm-status]','请填写有界引用名称；退役须先选已有引用。');return;}
          body={schema_version:1,product_task_id:task?.product_task_id||id('pt-'),expected_revision:task?.revision||0,project_revision:projectRevision,title,retired};
        }else{
          body={schema_version:1,manifest_id:selected?.manifest_id||id('cm-'),expected_revision:selected?.revision||0,project_revision:projectRevision,
            grant_id:binding.grant,grant_revision:binding.grantRevision,snapshot_id:binding.snapshotId,snapshot_digest:binding.snapshotDigest,task_ref:ref(task)};
        }
        send(Object.freeze({kind,url:`/api/v1/projects/${binding.project}/`+(kind==='task'?'context-task-references':'context-manifests'),
          key:crypto.randomUUID().replaceAll('-',''),body:JSON.stringify(body),source:Object.freeze({...binding})}));
      }
      return {
        invalidate,
        async open(source, button) {
          if(pending && canonical(pending.source)!==canonical(source)){button.textContent='请先核对原快照上下文命令';return;}
          invalidate();binding={...source};trigger=button;
          const el=document.createElement('dialog');el.id='contextManifestDialog';el.setAttribute('aria-label','持久上下文清单');
          el.style.cssText='width:min(800px,calc(100vw - 32px));max-height:85vh;box-sizing:border-box;overflow:auto;overflow-wrap:anywhere';
          // Static controls only. All API/user content below uses textContent/Option.
          el.innerHTML='<h2>持久上下文清单</h2><button data-cm-action="close">关闭清单</button><p>绑定确切项目版本和可选任务引用；不执行任务、不注入模型。刷新旧清单会创建新版本。任务引用不是任务收件箱。</p><p data-cm-version></p><p><label>上下文目标 <select data-cm-target><option value="">仅绑定项目版本</option></select></label></p><p><label>任务引用名称 <input data-cm-title maxlength="100" autocomplete="off"></label><button data-cm-action="task">保存引用新版本</button><button data-cm-action="retire">退役所选引用（不可恢复）</button></p><p><button data-cm-action="save">保存清单新版本</button><button data-cm-action="refresh">刷新当前版本</button><button data-cm-action="reconcile">仅核对原命令</button><button data-cm-action="retry">同键重试原命令</button></p><p data-cm-status role="status" aria-live="polite"></p><div data-cm-rows></div><pre data-cm-result style="white-space:pre-wrap;overflow-wrap:anywhere"></pre>';
          main.append(el);
          const inputButton=document.createElement('button');inputButton.dataset.cmAction='inputs';inputButton.textContent='准备所选文件的私有输入';inputButton.disabled=true;
          el.querySelector('[data-cm-rows]').before(inputButton);
          el.addEventListener('input',clearView);
          el.querySelector('[data-cm-target]').addEventListener('change',()=>{selected=null;const t=tasks.find(t=>t.product_task_id===node('[data-cm-target]').value);node('[data-cm-title]').value=t?.title||'';clearView();});
          el.addEventListener('click',event=>{
            const action=event.target.closest('[data-cm-action]')?.dataset.cmAction;
            if(action==='close'){invalidate();trigger?.focus();}
            if(action==='refresh')observe();
            if(action==='save')save('manifest');
            if(action==='task')save('task');
            if(action==='retire')save('task',true);
            if(action==='retry' && pending)send(pending);
            if(action==='reconcile' && pending)send(pending,true);
            if(action==='inputs'&&owned&&inputSelection){const cm=inputSelection;clearView();owned.open(binding,cm);}
          });
          el.oncancel=event=>{event.preventDefault();invalidate();trigger?.focus();};el.showModal();await observe();
        }
      };
    }
  };
})(window);
