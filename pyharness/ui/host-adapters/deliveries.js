/* Fixed evidence, real named subjects, independent review and explicit export. */
(function(global){
  'use strict';
  global.PyharnessDeliveries={create({main,api,esc}){
    const c=global.PyharnessWorkspaceContracts, memory=new Map();let current=null;
    const paths={delivery_package:'delivery-packages',role_contract:'role-contracts',role_handoff:'role-handoffs',handoff_ack:'handoff-acks',delivery_acceptance:'delivery-acceptances',delivery_export:'delivery-exports',handoff_document:'handoff-documents'};
    const ids={delivery_package:['package_id','dp'],role_contract:['role_id','rc'],role_handoff:['handoff_id','ho'],handoff_ack:['ack_id','ha'],delivery_acceptance:['acceptance_id','av'],delivery_export:['export_id','xp'],handoff_document:['document_id','doc']};
    const hex=()=>crypto.randomUUID().replaceAll('-','').slice(0,16),ref=(v,key)=>Object.fromEntries([key,'revision','digest','source_seq'].map(k=>[k,v[k]]));
    const q=(d,s)=>d.el.querySelector(s),base=d=>'/api/v1/projects/'+d.project.project_id;
    const live=(d,n=d.epoch)=>current===d&&d.el.isConnected&&d.el.open&&n===d.epoch;
    const say=(d,text)=>{if(live(d))q(d,'[data-delivery-status]').textContent=text;};
    async function who(){const v=await api('/api/account/me',{cache:'no-store'}),a=v.account;return {realm:JSON.stringify(a?[a.tenant,a.user_id||a.username]:['local',v.username||'local']),id:a?.user_id||'local-operator'};}
    function invalidate(){if(!current)return;const d=current;current=null;d.epoch++;d.el.replaceChildren();d.el.close();d.el.remove();}
    async function check(d,n){if(!live(d,n))throw Error('交付窗口已变化');if((await who()).realm!==d.realm){invalidate();throw Error('账户或团队已变化');}if(!live(d,n))throw Error('交付窗口已变化');}
    function clearPrivate(d){d.output=null;d.package=null;d.role=null;d.handoff=null;d.acceptance=null;d.export=null;d.agents=[];
      for(const selector of ['[data-delivery-output-list]','[data-delivery-bindings]','[data-delivery-package-detail]','[data-delivery-handoff-detail]','[data-delivery-criteria]','[data-delivery-export-detail]'])q(d,selector)?.replaceChildren();
      d.el.querySelectorAll('textarea,input[type=text]').forEach(e=>e.value='');
    }
    async function read(d,n,path,options={}){await check(d,n);try{const v=await api(path,{cache:'no-store',...options});await check(d,n);return v;}
      catch(error){if(live(d,n)){await check(d,n);if(error.status===401||error.status===403){clearPrivate(d);say(d,'权限或来源已变化，已清除窗口内的私有内容。');}}throw error;}}
    const slot=d=>'pyharness.delivery.pending.v1:'+JSON.stringify([d.realm,d.project.project_id]);
    function validPending(d,v){return c.keys(v,'realm project kind id key digest')&&v.realm===d.realm&&v.project===d.project.project_id&&ids[v.kind]&&
      new RegExp('^'+ids[v.kind][1]+'-[0-9a-f]{16}$').test(v.id)&&/^[A-Za-z0-9._-]{1,128}$/.test(v.key)&&c.hash(v.digest);}
    function pending(d){let value=memory.get(slot(d));try{value=value||JSON.parse(localStorage.getItem(slot(d)));}catch(_){}if(!validPending(d,value))return null;memory.set(slot(d),value);return value;}
    function remember(d,value){memory.set(slot(d),value);try{localStorage.setItem(slot(d),JSON.stringify(value));}catch(_){say(d,'无法保存原提交键核对线索，请保持窗口打开。');}}
    function forget(d,value){if(memory.get(slot(d))?.key===value.key)memory.delete(slot(d));try{if(JSON.parse(localStorage.getItem(slot(d)))?.key===value.key)localStorage.removeItem(slot(d));}catch(_){}}
    const identifier=(d,kind)=>pending(d)?.kind===kind?pending(d).id:ids[kind][1]+'-'+hex();
    function controls(d){if(!live(d))return;d.el.querySelectorAll('button,input,select,textarea').forEach(e=>{if(e.dataset.deliveryAction!=='close')e.disabled=d.busy;});
      const p=pending(d);q(d,'[data-delivery-pending]').hidden=!p;q(d,'[data-delivery-pending-text]').textContent=p?'原提交键：'+p.key+'；只核对该命令，不自动重发。':'';
      for(const form of d.el.querySelectorAll('form[data-delivery-form]'))form.querySelector('button[type=submit]').disabled=d.busy||!!p&&form.dataset.deliveryForm!==p.kind;
    }
    function evidence(value){return `<details><summary>固定版本与持久化证据</summary><p>第 ${value.revision} 版 · 控制日志 ${value.source_seq}</p><code style="overflow-wrap:anywhere">${esc(value.digest)}</code></details>`;}
    function choose(select,rows,label,key='resource_id'){const old=select.value;select.innerHTML='<option value="">请选择</option>'+rows.map(v=>`<option value="${esc(v[key])}">${esc(label(v))}</option>`).join('');if(rows.some(v=>v[key]===old))select.value=old;}
    function group(d,kind){return d.inventory?.items?.[kind]||[];}
    async function loadInventory(d,n){d.inventory=await read(d,n,base(d)+'/delivery-center');
      choose(q(d,'[name=execution]'),d.inventory.executions||[],v=>v.execution_id+' · 已绑定交付要求','execution_id');
      for(const [selector,kind] of [['[name=package]','delivery_package'],['[name=role]','role_contract'],['[name=handoff]','role_handoff'],['[name=acceptance]','delivery_acceptance'],['[name=export]','delivery_export']])
        choose(q(d,selector),group(d,kind),v=>(v.owned?'我创建的':'交给我的')+' · '+v.resource_id+' · 第'+v.revision+'版');
    }
    async function reconcile(d,n,expected=null){const p=pending(d);if(!p){say(d,'没有待核对的提交。');return null;}
      const observed=await read(d,n,base(d)+'/delivery-submissions/by-key?key='+encodeURIComponent(p.key));
      if(observed.status!=='committed'){say(d,'原键暂未查到提交。线索已保留；可恢复相同选择与文字后，以原键重试。');return null;}
      const r=observed.receipt,kind=p.kind==='handoff_document'?'document':p.kind;
      if(!r||r.resource_kind!==kind||r.resource_id!==p.id||r.request_digest!==p.digest||r.persisted!==true||!c.hash(r.digest)||!c.integer(r.source_seq)||
          expected&&(expected.digest!==r.digest||expected.source_seq!==r.source_seq))throw Error('提交收据与原命令不符，保留核对线索。');
      await check(d,n);forget(d,p);await loadInventory(d,n);say(d,'原键已确认保存。'+(observed.writer_requires_reopen?'服务写入需恢复后再操作。':''));return r;
    }
    async function write(d,n,kind,body){const digest=await c.digest({operation:kind+'.save',project_id:d.project.project_id,command:body});await check(d,n);
      let p=pending(d);if(p&&(p.kind!==kind||p.id!==body[ids[kind][0]]||p.digest!==digest))throw Error('还有待核对命令；重试必须保留完全相同的选择、版本和文字。');
      p=p||{realm:d.realm,project:d.project.project_id,kind,id:body[ids[kind][0]],key:'delivery-'+hex(),digest};remember(d,p);controls(d);
      const saved=await read(d,n,base(d)+'/'+paths[kind],{method:'POST',headers:{'Idempotency-Key':p.key},body:JSON.stringify(body)});
      if(saved.persisted!==true||saved[ids[kind][0]]!==p.id||!c.hash(saved.digest))throw Error('提交结果未确认，保留原键。');
      await reconcile(d,n,saved);return saved;
    }
    async function run(d,fn){if(d.busy)return;const n=d.epoch;d.busy=true;controls(d);try{await fn(n);}catch(error){if(live(d,n))say(d,error.message||'操作未确认，请核对原提交键。');}finally{if(live(d,n)){d.busy=false;controls(d);}}}
    function selected(d,selector,outputs){return Array.from(d.el.querySelectorAll(selector+':checked'),e=>outputs[Number(e.value)]);}
    function outputLabel(o){return (o.filename||'公开最终文本')+' · '+o.reference.size_bytes+' B · '+o.reference.sha256.slice(0,14)+'…';}
    function showOutputs(root,outputs,prefix){root.innerHTML=outputs.map((o,i)=>`<article style="border-top:1px solid var(--line);padding:12px 0"><label><input type="checkbox" ${prefix} value="${i}" checked> ${esc(outputLabel(o))}</label><p>校验：${esc(o.integrity)}</p>${o.text_preview!==null?`<blockquote style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(o.text_preview)}</blockquote>${o.preview_truncated?'<p>这里只显示节选；全文需单独导出授权。</p>':''}`:'<p>文件内容需生产者单独授予审阅导出。</p>'}</article>`).join('');}
    async function loadOutputs(d,n){const id=q(d,'[name=executionId]').value.trim()||q(d,'[name=execution]').value;if(!/^ex-[0-9a-f]{16}$/.test(id))throw Error('请选择一次生产执行。');
      const execution=await read(d,n,base(d)+'/executions/'+id),attempt=await read(d,n,base(d)+'/run-attempts/'+execution.attempt_ref.attempt_id);
      const delivery=attempt.context.delivery_ref;if(!delivery)throw Error('该执行提交时未绑定交付清单，不能事后降低标准补作交付。');
      const definition=await read(d,n,base(d)+'/delivery-definitions/'+delivery.delivery_id+'/revisions/'+delivery.revision);
      const inventory=await read(d,n,base(d)+'/executions/'+id+'/delivery-outputs');
      d.output={execution,delivery,definition,outputs:inventory.outputs};showOutputs(q(d,'[data-delivery-output-list]'),inventory.outputs,'data-delivery-selected-output');
      q(d,'[data-delivery-bindings]').innerHTML=definition.items.map(item=>`<label style="display:block;margin:12px 0">${esc(item.title)}${item.required?'（必交）':'（可选）'}<select multiple data-delivery-item="${esc(item.item_id)}" style="width:100%;min-height:70px">${inventory.outputs.map((o,i)=>`<option value="${i}">${esc(outputLabel(o))}</option>`).join('')}</select><small>为该交付项目选择实际产物，可按 Ctrl 多选。</small></label>`).join('');say(d,'已读取实际执行产物。逐项选择后冻结交付包。');
    }
    async function loadPackage(d,n,id=q(d,'[name=package]').value){if(!id)throw Error('请选择交付包。');d.package=await read(d,n,base(d)+'/delivery-packages/'+id);d.role=null;
      q(d,'[data-delivery-package-detail]').innerHTML=`<h3>已冻结交付包</h3><p>包含 ${d.package.bindings.length} 项映射；独立验收状态需在验收记录中核对。</p>${evidence(d.package)}`;
      const refs=new Map();for(const b of d.package.bindings)for(const r of b.outputs)refs.set(c.canonical(r),r);
      d.packageOutputs=[...refs.values()];q(d,'[data-delivery-grant-outputs]').innerHTML=d.packageOutputs.map((o,i)=>`<label style="display:block"><input type="checkbox" data-delivery-grant-output value="${i}" checked> ${esc(o.kind)} · ${o.sha256.slice(0,14)}…</label>`).join('');
    }
    async function loadHandoff(d,n,id=q(d,'[name=handoff]').value){if(!id)throw Error('请选择交接。');d.handoff=await read(d,n,base(d)+'/role-handoffs/'+id);const h=d.handoff;
      q(d,'[data-delivery-handoff-detail]').innerHTML=`<h3>具名交接 · ${h.acknowledged?'已签收':'待签收'}</h3><p>生产主体 ${esc(h.producer_id)}<br>承接主体 ${esc(h.recipient_id)} · ${h.role==='consumer'?'承接执行角色':'独立审阅角色'}</p><blockquote style="white-space:pre-wrap">${esc(h.minimum_context)}</blockquote>${evidence(h)}<section data-delivery-received-outputs></section>`;
      showOutputs(q(d,'[data-delivery-received-outputs]'),h.outputs,'data-delivery-received-output');
      q(d,'[data-delivery-criteria]').innerHTML=h.delivery_items.map(item=>`<fieldset data-delivery-review-item="${esc(item.item_id)}"><legend>${esc(item.title)}${item.required?'（必交）':''}</legend>${item.acceptance_criteria.map((text,i)=>`<label style="display:block;margin:10px 0">${esc(text)}<select data-delivery-criterion="${i}"><option value="unknown">尚无法确认</option><option value="pass">通过</option><option value="fail">不通过</option></select><input type="text" data-delivery-review-note="${i}" maxlength="1000" placeholder="核对说明"></label>`).join('')}</fieldset>`).join('');
      choose(q(d,'[name=importOutput]'),h.outputs.map((o,i)=>({resource_id:String(i),label:outputLabel(o)})),v=>v.label);
      q(d,'[data-delivery-download-links]').replaceChildren();d.pdfPreview=null;say(d,'已核对交接版本。签收、独立验收、导出授权分别保存。');
    }
    function importBody(d){if(!d.handoff)throw Error('先读取具名交接。');const i=Number(q(d,'[name=importOutput]').value),o=d.handoff.outputs[i];if(!o||q(d,'[name=importOutput]').value==='')throw Error('请选择导入的产物。');
      const format=q(d,'[name=importFormat]').value;
      const id=d.importId||(d.importId=identifier(d,'handoff_document'));
      return {schema_version:2,document_id:id,project_revision:d.project.revision,title:q(d,'[name=importTitle]').value,
        handoff_ref:ref(d.handoff,'handoff_id'),output_ref:o.reference,format,text_selection:null,
        confirmation_digest:format==='pdf'?d.pdfPreview?.confirmation_digest||null:null};
    }
    async function submitForm(d,n,kind,form){
      let body;
      if(kind==='delivery_package'){
        if(!d.output)throw Error('先读取生产执行与固定交付要求。');const bindings=[];
        for(const item of d.output.definition.items){const options=q(d,`[data-delivery-item="${CSS.escape(item.item_id)}"]`).selectedOptions;
          const values=Array.from(options,o=>d.output.outputs[Number(o.value)].reference);if(item.required&&!values.length)throw Error('必交项目尚未选择实际产物：'+item.title);if(values.length)bindings.push({item_id:item.item_id,outputs:values});}
        body={schema_version:1,package_id:identifier(d,kind),project_revision:d.project.revision,execution_ref:ref(d.output.execution,'execution_id'),delivery_ref:d.output.delivery,bindings};
      }else if(kind==='role_contract'){
        if(!d.package)throw Error('先读取固定交付包。');const selected=form.elements.roleKind.value,agent=d.agents.find(a=>a.key===form.elements.consumerAgent.value);
        if(selected==='consumer'&&!agent)throw Error('请选择固定的已发布承接 Agent。');
        body={schema_version:1,role_id:identifier(d,kind),expected_revision:0,project_revision:d.project.revision,title:form.elements.roleTitle.value,
          parent_execution_ref:d.package.execution_ref,recipient_username:form.elements.recipient.value,role:selected,
          consumer_agent_ref:selected==='consumer'?agent.reference:null,max_context_bytes:Number(form.elements.contextLimit.value),retired:false};
      }else if(kind==='role_handoff'){
        if(!d.package)throw Error('先读取固定交付包。');const id=q(d,'[name=role]').value;if(!id)throw Error('请选择具名角色。');
        const role=await read(d,n,base(d)+'/role-contracts/'+id);const values=selected(d,'[data-delivery-grant-output]',d.packageOutputs);
        if(!values.length)throw Error('至少选择一个冻结产物。');body={schema_version:1,handoff_id:identifier(d,kind),role_ref:ref(role,'role_id'),
          package_ref:ref(d.package,'package_id'),outputs:values,minimum_context:form.elements.minimumContext.value};
      }else if(kind==='delivery_acceptance'){
        if(!d.handoff||!form.elements.inspected.checked)throw Error('先读取并逐项核对具名交接的实际产物。');
        const decisions=[];for(const item of d.el.querySelectorAll('[data-delivery-review-item]'))for(const criterion of item.querySelectorAll('[data-delivery-criterion]'))
          decisions.push({item_id:item.dataset.deliveryReviewItem,criterion_index:Number(criterion.dataset.deliveryCriterion),verdict:criterion.value,note:item.querySelector(`[data-delivery-review-note="${criterion.dataset.deliveryCriterion}"]`).value});
        body={schema_version:1,acceptance_id:identifier(d,kind),handoff_ref:ref(d.handoff,'handoff_id'),package_ref:d.handoff.package_ref,decisions};
      }else if(kind==='delivery_export'){
        if(!d.handoff)throw Error('先读取具名交接。');const values=selected(d,'[data-delivery-received-output]',d.handoff.outputs).map(v=>v.reference);
        if(!values.length)throw Error('请选择明确导出的产物。');let acceptance_ref=null;
        if(form.elements.purpose.value==='delivery'){const id=q(d,'[name=acceptance]').value;if(!id)throw Error('请选择独立验收记录。');const a=await read(d,n,base(d)+'/delivery-acceptances/'+id);if(a.business_acceptance!=='passed')throw Error('独立验收尚未通过。');acceptance_ref=ref(a,'acceptance_id');}
        body={schema_version:1,export_id:identifier(d,kind),purpose:form.elements.purpose.value,handoff_ref:ref(d.handoff,'handoff_id'),package_ref:d.handoff.package_ref,outputs:values,acceptance_ref};
      }else if(kind==='handoff_document')body=importBody(d);
      const saved=await write(d,n,kind,body);
      if(kind==='delivery_package'){await loadPackage(d,n,saved.package_id);say(d,'实际产物已冻结，可以定义具名承接角色。');}
      if(kind==='role_contract')say(d,'具名角色已保存；尚未授予任何执行或导出权限。');
      if(kind==='role_handoff'){await loadHandoff(d,n,saved.handoff_id);say(d,'已交接给指定账户，等待签收。');}
      if(kind==='delivery_acceptance'){d.acceptance=saved;say(d,'独立验收结果：'+({passed:'通过',rejected:'不通过',pending:'尚无法确认'}[saved.business_acceptance]||saved.business_acceptance));}
      if(kind==='delivery_export')say(d,'已保存对指定承接账户、指定产物的单独导出授权。');
      if(kind==='handoff_document'){q(d,'[data-delivery-import-result]').innerHTML=`<p>已保存承接资料：${esc(saved.title)} · ${saved.blocks.length} 段。可在原任务收件箱中明确选择该资料和固定承接 Agent，再预览并启动。</p>${evidence(saved)}`;d.importId=null;d.pdfPreview=null;say(d,'承接资料已固定；保存资料不会自动执行。');}
    }
    async function action(d,n,name){
      if(name==='refresh')return loadInventory(d,n);
      if(name==='reconcile')return reconcile(d,n);
      if(name==='outputs')return loadOutputs(d,n);
      if(name==='package')return loadPackage(d,n);
      if(name==='handoff')return loadHandoff(d,n);
      if(name==='ack'){
        if(!d.handoff)throw Error('先读取交接。');const saved=await write(d,n,'handoff_ack',{schema_version:1,ack_id:identifier(d,'handoff_ack'),handoff_ref:ref(d.handoff,'handoff_id')});
        await loadHandoff(d,n,d.handoff.handoff_id);say(d,'签收已确认；签收不等于独立验收通过。');return saved;
      }
      if(name==='pdf-preview'){
        const body=importBody(d);if(body.format!=='pdf')throw Error('请选择 PDF 格式。');body.confirmation_digest=null;
        const preview=await read(d,n,base(d)+'/handoff-document-previews',{method:'POST',body:JSON.stringify(body)});d.pdfPreview=preview;
        q(d,'[data-delivery-import-result]').textContent=`预览：${preview.page_count} 个物理页；不可定位页 ${preview.unlocatable_pages.join('、')||'无'}。核对后点击固定承接资料。`;return;
      }
      if(name==='export'){
        const id=q(d,'[name=export]').value;if(!id)throw Error('请选择导出授权。');const value=await read(d,n,base(d)+'/delivery-exports/'+id);d.export=value;
        q(d,'[data-delivery-export-detail]').innerHTML=`<p>${value.purpose==='delivery'?'交付':'审阅'}导出 · 固定 ${value.outputs.length} 个对象</p>${evidence(value)}`;
        const links=q(d,'[data-delivery-download-links]');links.replaceChildren();
        for(let i=0;i<value.outputs.length;i++){const a=document.createElement('a');a.textContent='下载授权对象 '+(i+1);a.href=base(d)+'/delivery-exports/'+encodeURIComponent(id)+'/outputs/'+i;a.style.marginRight='16px';a.download='';links.append(a);}say(d,'下载时服务端会再次核对账户、授权、验收和原始字节。');
      }
    }
    async function open(project,trigger){invalidate();const el=document.createElement('dialog');el.id='deliveryCenterDialog';el.className='delivery-center-dialog';el.style.cssText='width:min(1100px,95vw);max-width:min(1100px,95vw);max-height:90vh;overflow:auto;padding:24px;font:inherit;box-sizing:border-box;border-radius:14px';main.append(el);
      const d=current={el,project,trigger,epoch:1,busy:false,realm:null,agents:[],inventory:null};
      el.innerHTML=`<style>.delivery-center-dialog h2{font-size:20px;margin:0 0 8px;line-height:1.5}.delivery-center-dialog h3{font-size:16px;margin:20px 0 12px}.delivery-center-dialog p{line-height:1.65;margin:10px 0}.delivery-center-dialog button{margin:4px 4px 4px 0}.delivery-center-dialog textarea{display:block;box-sizing:border-box;width:100%}.delivery-center-dialog select{max-width:100%}.delivery-center-dialog small{display:block;color:var(--muted)}</style><header style="display:flex;justify-content:space-between;align-items:flex-start;gap:16px"><div><h2>${esc(project.name)} · 交付中心</h2><p>冻结产物 → 具名交接 → 独立验收 → 受控导出</p></div><button data-delivery-action="close">关闭</button></header>
        <p data-delivery-status role="status" aria-live="polite">正在核对当前账户…</p><section data-delivery-pending hidden><p data-delivery-pending-text></p><button data-delivery-action="reconcile">按原键核对</button></section>
        <p><button data-delivery-action="refresh">刷新交付记录</button></p><div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,440px),1fr));gap:20px">
        <section><h3>1. 冻结实际产物</h3><label>生产执行 <select name="execution"></select></label><details><summary>读取较早的执行</summary><input type="text" name="executionId" placeholder="执行标识 ex-…" maxlength="19"></details><button data-delivery-action="outputs">读取实际产物</button><div data-delivery-output-list></div>
        <form data-delivery-form="delivery_package"><div data-delivery-bindings></div><button type="submit">按固定清单冻结交付包</button></form><hr><label>已有交付包 <select name="package"></select></label><button data-delivery-action="package">读取交付包</button><div data-delivery-package-detail></div>
        <h3>2. 定义具名承接角色</h3><form data-delivery-form="role_contract"><p><label>角色名称 <input type="text" name="roleTitle" required maxlength="100"></label></p><p><label>承接账户用户名 <input type="text" name="recipient" required maxlength="128"></label></p><p><label>职责 <select name="roleKind"><option value="reviewer">独立审阅</option><option value="consumer">承接执行</option></select></label></p><p><label>固定承接 Agent <select name="consumerAgent"><option value="">仅承接执行时选择</option></select></label></p><p><label>上下文字节上限 <input name="contextLimit" type="number" min="1" max="98304" value="98304"></label></p><small>权限、预算和运行范围受父任务及所选发布版本限制。</small><p><button type="submit">保存具名角色</button></p></form>
        <form data-delivery-form="role_handoff"><label>角色版本 <select name="role"></select></label><div data-delivery-grant-outputs></div><p><label>最小交接说明 <textarea name="minimumContext" maxlength="98304" rows="4"></textarea></label></p><button type="submit">交接所选产物给该账户</button></form></section>
        <section><h3>3. 签收与独立验收</h3><label>具名交接 <select name="handoff"></select></label><button data-delivery-action="handoff">读取交接</button><div data-delivery-handoff-detail></div><p><button data-delivery-action="ack">签收当前交接</button></p>
        <form data-delivery-form="delivery_acceptance"><div data-delivery-criteria></div><p><label><input name="inspected" type="checkbox" required> 已逐项核对所选实际产物</label></p><button type="submit">提交独立验收结论</button><p>生产者不能自批；工具审批、签收和技术执行完成均不代替独立验收。</p></form>
        <h3>4. 单独授权导出</h3><form data-delivery-form="delivery_export"><label>用途 <select name="purpose"><option value="review">审阅导出</option><option value="delivery">验收通过后的交付导出</option></select></label><p><label>独立验收记录 <select name="acceptance"></select></label></p><button type="submit">授予承接账户所选对象的导出权限</button></form><p><label>已有导出授权 <select name="export"></select></label><button data-delivery-action="export">核对导出授权</button></p><div data-delivery-export-detail></div><p data-delivery-download-links></p>
        <h3>承接为后续任务的引用资料</h3><form data-delivery-form="handoff_document"><p><label>选定产物 <select name="importOutput"></select></label></p><p><label>资料标题 <input type="text" name="importTitle" required maxlength="100"></label></p><p><label>格式 <select name="importFormat"><option value="text">UTF-8 文本</option><option value="pdf">PDF 物理页文本</option></select></label></p><small>整段 UTF-8 文本或 PDF 物理页文本，受角色上下文上限限制。</small><button type="button" data-delivery-action="pdf-preview">预览 PDF</button> <button type="submit">固定承接资料</button></form><div data-delivery-import-result></div></section></div>`;
      el.showModal();el.addEventListener('cancel',e=>{e.preventDefault();invalidate();trigger?.focus();});
      el.addEventListener('click',e=>{const button=e.target.closest('[data-delivery-action]');if(!button)return;const name=button.dataset.deliveryAction;if(name==='close'){invalidate();trigger?.focus();return;}void run(d,n=>action(d,n,name));});
      el.addEventListener('submit',e=>{const form=e.target.closest('[data-delivery-form]');if(!form)return;e.preventDefault();void run(d,n=>submitForm(d,n,form.dataset.deliveryForm,form));});
      el.addEventListener('change',e=>{if(e.target.closest('[data-delivery-form=handoff_document]'))d.pdfPreview=null;});
      await run(d,async n=>{const actor=await who();if(!live(d,n))return;d.realm=actor.realm;d.subject=actor.id;
        await loadInventory(d,n);
        const agents=await read(d,n,'/api/v1/agents');
        for(const a of agents.agents||[]){if(!a.current_version||a.status==='deprecated')continue;const versions=await read(d,n,'/api/v1/agents/'+encodeURIComponent(a.agent_id)+'/versions');
          for(const v of versions.versions||[]){const reference={agent_id:a.agent_id,version:v.version,sha256:v.sha256};d.agents.push({key:reference.agent_id+':'+reference.version,reference,label:a.name+' · v'+v.version});}}
        choose(q(d,'[name=consumerAgent]'),d.agents,v=>v.label,'key');say(d,pending(d)?'发现待核对提交，请先按原键核对。':'记录已读取，尚未执行任何提交。');
      });
    }
    const recheck=()=>{const d=current;if(d?.realm&&live(d))void check(d,d.epoch).catch(()=>{});};global.addEventListener('focus',recheck);global.addEventListener('storage',recheck);
    return {open,invalidate};
  }};
})(window);
