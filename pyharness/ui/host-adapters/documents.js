/* Versioned private reading, exact citations and delivery definitions. */
(function (global) {
  'use strict';
  global.PyharnessDocuments = {create({main, api, esc}) {
    const pending = new Map(), c = global.PyharnessWorkspaceContracts;
    let el = null, epoch = 0, realm = '', scope = '', project = null, trigger = null, sending = false, loaded = false;
    let docs = [], cites = [], deliveries = [], inputs = [], grants = [], tasks = [], cursors = {};
    let editing = null, deliveryEditing = null, inputFiles = [], reader = null, preview = null;
    const node = s => el?.querySelector(s), id = () => crypto.randomUUID().replaceAll('-', '').slice(0, 16);
    const base = () => '/api/v1/projects/' + project.project_id;
    const same = (a, b) => c.canonical(a) === c.canonical(b);
    const ref = (v, key) => Object.fromEntries([key, 'revision', 'digest', 'source_seq'].map(k => [k, v[k]]));
    const live = (dialog, v) => el === dialog && dialog.isConnected && dialog.open && epoch === v;
    const say = t => { if (node('[data-doc-status]')) node('[data-doc-status]').textContent = t; };
    const label = s => ({current:'当前版本', stale:'引用已过期', missing:'资料缺失或已退役', unlocatable:'不可定位', defined:'交付要求已定义', retired:'已退役'})[s] || s;
    async function identity() {
      const who = await api('/api/account/me'), a = who.account;
      return JSON.stringify(a ? [a.tenant, a.user_id || a.username] : ['local', who.username || 'local']);
    }
    async function read(url) {
      const expected = realm;
      if (!expected || await identity() !== expected) throw Error('主体已变化，请重新打开。');
      const value = await api(url);
      if (await identity() !== expected) throw Error('主体已变化，旧资料不会显示。');
      return value;
    }
    function setView(view) {
      el?.querySelectorAll('[data-doc-view]').forEach(n => n.hidden = n.dataset.docView !== view);
      el?.querySelectorAll('[data-view]').forEach(n => n.setAttribute('aria-pressed', String(n.dataset.view === view)));
    }
    function controls() {
      el?.querySelectorAll('form input,form textarea,form select,form button,[data-doc-write]').forEach(n => n.disabled = !realm || !loaded || sending || pending.has(scope));
      if (node('[data-doc-recovery]')) node('[data-doc-recovery]').hidden = !pending.has(scope);
      for (const action of ['retry','reconcile']) if (node(`[data-doc-action=${action}]`)) node(`[data-doc-action=${action}]`).disabled = sending || !pending.has(scope);
      if (node('[data-doc-action=save-citation]')) node('[data-doc-action=save-citation]').disabled = !reader || reader.status !== 'current' || sending || pending.has(scope);
    }
    function clearReader() { reader = null; node('[data-doc-reader]')?.replaceChildren(); }
    function invalidatePreview() { preview = null; const n = node('[name=pdfConfirmed]'); if (n) n.checked = false; node('[data-pdf-preview]')?.replaceChildren(); }
    function invalidate() { epoch++; el?.close(); el?.remove(); el = null; reader = preview = null; }
    function close() { invalidate(); trigger?.focus(); }
    function options(rows, key, title) { return rows.map(v => `<option value="${esc(v[key])}">${esc(title(v))}</option>`).join(''); }
    function docForm(value = null) {
      editing = value; inputFiles = []; invalidatePreview();
      if (node('[data-doc-editor]')) node('[data-doc-editor]').open = Boolean(value) || !docs.length;
      const f = node('#documentForm'); if (!f) return;
      f.reset(); f.elements.title.value = value?.title || '';
      f.elements.input.value = value?.source.kind === 'workspace_input' ? value.source.input_ref.input_id : inputs[0]?.input_id || '';
      f.elements.sourceKind.value = value?.source.kind || 'workspace_input';
      f.elements.retired.checked = value?.retired || false;
      if (value?.source.kind === 'pdf_grant') { f.elements.grant.value = value.source.grant_id; f.elements.pdfPath.value = value.source.file.path; }
      node('[data-doc-editing]').textContent = value ? `修订资料：${value.title}（当前第${value.revision}版）` : '添加资料';
      sourceVisibility();
    }
    function sourceVisibility() {
      const pdf = node('#documentForm')?.elements.sourceKind.value === 'pdf_grant';
      node('[data-doc-input]').hidden = pdf; node('[data-doc-pdf]').hidden = !pdf;
    }
    function drawLists() {
      node('[data-doc-list]').innerHTML = docs.length ? docs.map(d => `<article data-doc-row="${esc(d.document_id)}"><h4>${esc(d.title)} · 第${d.revision}版</h4><p>${esc(label(d.status))} · ${d.format === 'pdf' ? d.page_count + '个物理页' : '纯文本'} · ${d.block_count}段</p><label>阅读版本 <input data-doc-revision type="number" min="1" max="${d.revision}" value="${d.revision}" style="width:80px"></label> <button data-doc-action="read-document" data-id="${esc(d.document_id)}">阅读资料</button> <button data-doc-action="edit-document" data-id="${esc(d.document_id)}" ${d.retired||d.schema_version===2?'disabled':''}>修订资料</button></article>`).join('') : '<p>暂无当前主体可读取的资料。</p>';
      node('[data-cite-list]').innerHTML = cites.length ? cites.map((v,i) => `<article><strong>引用${i+1} · ${esc(label(v.status))}</strong><p>${v.page ? '物理页 '+v.page : '文本'} · 第${v.paragraph}段</p><button data-doc-action="locate" data-id="${esc(v.citation_id)}">定位原文</button></article>`).join('') : '<p>阅读资料后，可保存精确区间引用。</p>';
      node('[data-delivery-list]').innerHTML = deliveries.length ? deliveries.map(v => `<article data-delivery-row="${esc(v.delivery_id)}"><h4>交付清单 · 第${v.revision}版</h4><p>${esc(label(v.status))} · ${v.items.length}项要求 · 尚未验收</p><ul>${v.items.map(i=>`<li>${esc(i.title)}${i.required?'（必交）':''}</li>`).join('')}</ul><label>查看版本 <input data-delivery-revision type="number" min="1" max="${v.revision}" value="${v.revision}" style="width:80px"></label> <button data-doc-action="read-delivery" data-id="${esc(v.delivery_id)}">查看清单</button> <button data-doc-action="edit-delivery" data-id="${esc(v.delivery_id)}" ${v.retired?'disabled':''}>修订清单</button></article>`).join('') : '<p>先定义正式产品任务，再添加交付要求。</p>';
      for (const key of ['documents','citations','deliveries','inputs']) if (node(`[data-more=${key}]`)) node(`[data-more=${key}]`).disabled = !cursors[key];
    }
    function addItem(value = null) {
      const holder = node('[data-delivery-items]'), item = document.createElement('fieldset');
      item.dataset.itemId = value?.item_id || 'item-' + id();
      item.innerHTML = `<legend>交付项目</legend><label>名称 <input data-item-title maxlength="200" required value="${esc(value?.title || '')}"></label><label><input data-item-required type="checkbox" ${!value || value.required?'checked':''}> 必须交付</label><label>验收标准（每行一项）<textarea data-item-criteria required>${esc(value?.acceptance_criteria.join('\n') || '')}</textarea></label><label>依据引用（可多选）<select data-item-citations multiple>${cites.map((v,i)=>`<option value="${esc(v.citation_id)}" ${value?.citation_refs.some(r=>r.citation_id===v.citation_id)?'selected':''}>引用${i+1} · ${v.page?'物理页'+v.page:'文本'} · 第${v.paragraph}段 · ${esc(label(v.status))}</option>`).join('')}</select></label><button type="button" data-doc-action="remove-item">移除此项</button>`;
      holder.append(item); controls();
    }
    function deliveryForm(value = null) {
      deliveryEditing = value; const f = node('#deliveryForm'); if (!f) return;
      f.elements.task.innerHTML = '<option value="">请选择正式产品任务</option>' + options(tasks,'product_task_id',t=>t.title+' · 第'+t.revision+'版');
      if (value) {
        const found = tasks.find(t=>same(ref(t,'product_task_id'),value.task_ref));
        if (!found) f.elements.task.insertAdjacentHTML('beforeend','<option value="__original__">原任务版本（保存时重新检查）</option>');
        f.elements.task.value = found ? found.product_task_id : '__original__';
      }
      f.elements.retired.checked = value?.retired || false;
      node('[data-delivery-items]').replaceChildren(); (value?.items || [null]).forEach(addItem);
      node('[data-delivery-editing]').textContent = value ? `修订交付清单（当前第${value.revision}版）` : '定义交付清单';
    }
    async function load() {
      const dialog = el, v = ++epoch; loaded = false; clearReader(); invalidatePreview(); controls();
      docs=[];cites=[];deliveries=[];inputs=[];grants=[];tasks=[];cursors={}; drawLists();
      node('[name=input]').replaceChildren(); node('[name=grant]').replaceChildren(); node('[data-delivery-detail]').replaceChildren();
      docForm(); deliveryForm(); say('正在核对主体与资料版本…');
      try {
        const [ds,cs,ls,ws,gs,ts] = await Promise.all([read(base()+'/documents?limit=50'),read(base()+'/citations?limit=50'),read(base()+'/delivery-definitions?limit=50'),read(base()+'/workspace-inputs?limit=50'),read(base()+'/source-grants'),read(base()+'/product-tasks')]);
        if (!live(dialog,v)) return;
        loaded=true;docs=ds.documents;cites=cs.citations;deliveries=ls.deliveries;inputs=ws.inputs;grants=gs.grants.filter(g=>!g.revoked);tasks=ts.tasks.filter(t=>!t.retired);
        cursors={documents:ds.next_cursor,citations:cs.next_cursor,deliveries:ls.next_cursor,inputs:ws.next_cursor};
        node('[name=input]').innerHTML = options(inputs,'input_id',i=>'私有输入 · '+i.file_count+'个文件 · '+label(i.status));
        node('[name=grant]').innerHTML = options(grants,'grant_id',g=>'来源授权 · '+g.grant_id);
        drawLists(); docForm(); deliveryForm(); controls();
        say(pending.has(scope)?'原命令结果未确认，请只读核对或使用原键原正文重试。':'已加载。资料、引用和清单定义均不会启动任务或完成验收。');
      } catch(error) { if(live(dialog,v)) { say('读取失败：'+error.message); controls(); } }
    }
    async function loadFiles() {
      const dialog=el,v=++epoch; inputFiles=[];invalidatePreview();
      const f=node('#documentForm'), ident=f.elements.input.value;f.elements.file.innerHTML='';
      if (!ident) return;
      const wi=await read(base()+'/workspace-inputs/'+ident); if(!live(dialog,v))return;
      const original=inputs.find(i=>i.input_id===ident);
      if(!original || !same(ref(wi,'input_id'),ref(original,'input_id')) || wi.status!=='input_prepared')throw Error('输入版本已变化，请刷新。');
      inputFiles=wi.files;f.elements.file.innerHTML=inputFiles.map((x,i)=>`<option value="${i}">${esc(x.path)}</option>`).join('');
      if(editing?.source.kind==='workspace_input' && editing.source.input_ref.input_id===ident)f.elements.file.value=editing.source.file_index;
      say('已读取确切私有输入，请选择其中的文本文件。');
    }
    function matches(value, cmd) {
      const b=cmd.body, key=cmd.kind==='document'?'document_id':cmd.kind==='citation'?'citation_id':'delivery_id';
      if(!value || value[key]!==b[key] || value.project_id!==cmd.project || value.revision!==b.expected_revision+1 || !c.hash(value.digest) || !c.integer(value.source_seq))return false;
      if(!Object.entries(b).filter(([k])=>!['expected_revision','source'].includes(k)).every(([k,val])=>same(value[k],val)))return false;
      if(cmd.kind!=='document')return true;
      if(b.source.kind==='workspace_input')return same(value.source,b.source);
      return value.source.kind==='pdf_grant' && value.source.grant_id===b.source.grant_id && value.source.grant_revision===b.source.grant_revision && value.source.file.path===b.source.path && value.source.confirmation_digest===b.source.confirmation_digest;
    }
    async function send(cmd, readOnly=false) {
      if(sending)return; const dialog=el,v=++epoch,commandScope=scope;sending=true;controls();clearReader();invalidatePreview();
      say(readOnly?'正在只读核对原命令…':'正在保存并核对本次修订…');
      try {
        if(await identity()!==cmd.realm)throw Error('主体已变化，原命令不会重发。');
        let receipt=null;
        if(!readOnly){receipt=await api(cmd.url,{method:'POST',body:cmd.serialized,headers:{'Idempotency-Key':cmd.key}});cmd.uncertain=true;}
        if(await identity()!==cmd.realm)throw Error('主体已变化，不能确认旧命令。');
        const exact=await api(cmd.exact);
        if(await identity()!==cmd.realm || !matches(exact,cmd) || (receipt && (!matches(receipt,cmd)||receipt.digest!==exact.digest||receipt.source_seq!==exact.source_seq)))throw Error('持久回读与原命令不一致。');
        pending.delete(commandScope);
        if(live(dialog,v)){await load();if(el===dialog){setView(cmd.kind==='citation'?'citations':cmd.kind==='document'?'documents':'deliveries');say('已保存并精确回读原修订；未执行任务、未完成交付验收。');}}
      }catch(error){
        const rejected=!readOnly&&!cmd.uncertain&&error.status>=400&&error.status<500;
        if(rejected)pending.delete(commandScope);else cmd.uncertain=true;
        if(live(dialog,v))say(rejected?'命令被明确拒绝：'+error.message:'结果未确认：'+error.message+' 保留原键与原正文，可只读核对。');
      }finally{sending=false;if(el===dialog)controls();}
    }
    function submit(kind, body) {
      if(sending||pending.has(scope)||!realm)return;
      const route=kind==='document'?'documents':kind==='citation'?'citations':'delivery-definitions';
      const key=kind==='document'?'document_id':kind==='citation'?'citation_id':'delivery_id';
      const url=base()+'/'+route, exact=kind==='citation'?url+'/'+body[key]:url+'/'+body[key]+'/revisions/'+(body.expected_revision+1);
      const cmd={kind,body,serialized:JSON.stringify(body),key:kind+'-'+id(),url,exact,project:project.project_id,realm};
      pending.set(scope,cmd);return send(cmd);
    }
    function drawReader(value, locator=null) {
      reader=value;const panel=node('[data-doc-reader]');
      panel.innerHTML=`<h3>${esc(value.title)} · 第${value.revision}版</h3><p>${esc(label(value.status))}${value.format==='pdf'?' · '+value.page_count+'个物理页':''}</p>${value.status==='missing'?'<p>来源已缺失或资料已退役，不显示旧正文。</p>':''}${value.capability==='unlocatable'?'<p>此资料不可定位：'+esc(({no_text_layer:'没有可提取的文本层；未执行OCR',encrypted_pdf:'加密PDF',invalid_pdf:'无法解析的PDF',empty_text:'空文本'})[value.capability_reason]||value.capability_reason)+'</p>':''}${value.unlocatable_pages.length?'<p>无可定位文本的物理页：'+value.unlocatable_pages.join('、')+'</p>':''}${value.blocks.map(b=>`<article data-block="${esc(b.block_id)}"><strong>${b.page?'物理页 '+b.page:'文本'} · 第${b.paragraph}段</strong><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${locator?.block_id===b.block_id?esc(Array.from(b.text).slice(0,locator.start-b.start).join(''))+'<mark>'+esc(Array.from(b.text).slice(locator.start-b.start,locator.end-b.start).join(''))+'</mark>'+esc(Array.from(b.text).slice(locator.end-b.start).join('')):esc(b.text)}</pre><button data-doc-action="select-block" data-id="${esc(b.block_id)}" ${value.status!=='current'?'disabled':''}>引用此段</button></article>`).join('')}<fieldset data-citation-form hidden><legend>保存区间引用</legend><p>位置按当前段落的Unicode字符计算，开始包含、结束不包含。</p><label>开始 <input name="quoteStart" type="number" min="0"></label><label>结束 <input name="quoteEnd" type="number" min="1"></label><button data-doc-action="save-citation" data-doc-write>保存精确引用</button></fieldset>`;
      controls(); if(locator)panel.querySelector(`[data-block="${locator.block_id}"]`)?.scrollIntoView({block:'nearest'});
    }
    async function act(action,button) {
      if(action==='close')return close();if(sending)return;
      if(action==='tab'){epoch++;setView(button.dataset.view);return;}
      if(action==='refresh')return load();
      if(action==='retry'||action==='reconcile'){const cmd=pending.get(scope);if(cmd)return send(cmd,action==='reconcile');return;}
      if(action==='new-document'){epoch++;docForm();node('[data-doc-editor]').open=true;setView('documents');clearReader();controls();return;}
      if(action==='new-delivery'){epoch++;deliveryForm();setView('deliveries');controls();return;}
      if(action==='add-item'){if(node('[data-delivery-items]').children.length>=30)throw Error('最多30项交付要求。');addItem();return;}
      if(action==='remove-item'){button.closest('fieldset').remove();return;}
      if(action==='load-files')return loadFiles();
      if(action==='edit-document'){epoch++;docForm(docs.find(d=>d.document_id===button.dataset.id));clearReader();controls();return;}
      if(action==='edit-delivery'){epoch++;deliveryForm(deliveries.find(d=>d.delivery_id===button.dataset.id));controls();return;}
      const dialog=el,v=++epoch;
      if(action==='preview-pdf'){
        invalidatePreview();const f=node('#documentForm'),g=grants.find(g=>g.grant_id===f.elements.grant.value);if(!g)throw Error('请先建立只读来源授权。');
        const body={schema_version:1,project_revision:project.revision,grant_id:g.grant_id,grant_revision:g.revision,path:f.elements.pdfPath.value};
        if(await identity()!==realm)throw Error('主体已变化。');
        const value=await api(base()+'/document-pdf-previews',{method:'POST',body:JSON.stringify(body)});
        if(await identity()!==realm)throw Error('主体已变化。');if(!live(dialog,v))return;
        if(value.status!=='confirmation_required'||value.persisted!==false||!c.hash(value.confirmation_digest)||!Object.entries(body).filter(([k])=>k!=='schema_version').every(([k,x])=>same(value[k],x)))throw Error('PDF预览绑定不一致。');
        preview=value;node('[data-pdf-preview]').textContent=`待导入：${value.path}（${value.size_bytes}字节）。请确认后保存。`;return;
      }
      if(action==='read-document'){
        clearReader();const revision=Number(button.closest('article').querySelector('[data-doc-revision]').value);
        const value=await read(base()+'/documents/'+button.dataset.id+'/revisions/'+revision);
        if(live(dialog,v)){if(value.document_id!==button.dataset.id||value.revision!==revision||value.project_id!==project.project_id)throw Error('资料版本回读不一致。');drawReader(value);}return;
      }
      if(action==='locate'){
        clearReader();const citation=await read(base()+'/citations/'+button.dataset.id),r=citation.locator.document_ref;
        const value=await read(base()+'/documents/'+r.document_id+'/revisions/'+r.revision);if(!live(dialog,v))return;
        if(citation.citation_id!==button.dataset.id||!same(ref(value,'document_id'),r)||value.project_id!==project.project_id)throw Error('引用原版本不一致。');
        setView('documents');drawReader(value,citation.locator);say('已定位引用绑定的原版本：'+label(value.status));return;
      }
      if(action==='select-block'){
        const b=reader?.blocks.find(b=>b.block_id===button.dataset.id);if(!b)return;
        const form=node('[data-citation-form]');form.hidden=false;form.dataset.block=b.block_id;
        node('[name=quoteStart]').value=b.start;node('[name=quoteEnd]').value=b.end;controls();return;
      }
      if(action==='save-citation'){
        const block=reader?.blocks.find(b=>b.block_id===node('[data-citation-form]').dataset.block);if(!block||reader.status!=='current')throw Error('请读取当前资料版本。');
        const start=Number(node('[name=quoteStart]').value),end=Number(node('[name=quoteEnd]').value);
        if(!Number.isSafeInteger(start)||!Number.isSafeInteger(end)||!(block.start<=start&&start<end&&end<=block.end))throw Error('区间必须位于所选段落内。');
        // JS strings use UTF-16; the API intervals use Unicode codepoints.
        const text=Array.from(block.text).slice(start-block.start,end-block.start).join('');
        const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text));if(!live(dialog,v))return;
        const quote_digest=Array.from(new Uint8Array(bytes),x=>x.toString(16).padStart(2,'0')).join('');
        return submit('citation',{schema_version:1,citation_id:'cit-'+id(),expected_revision:0,project_revision:project.revision,locator:{document_ref:ref(reader,'document_id'),block_id:block.block_id,start,end,quote_digest}});
      }
      if(action==='read-delivery'){
        node('[data-delivery-detail]').replaceChildren();const revision=Number(button.closest('article').querySelector('[data-delivery-revision]').value);
        const value=await read(base()+'/delivery-definitions/'+button.dataset.id+'/revisions/'+revision);if(!live(dialog,v))return;
        if(value.delivery_id!==button.dataset.id||value.revision!==revision||value.project_id!==project.project_id)throw Error('清单版本回读不一致。');
        node('[data-delivery-detail]').innerHTML=`<h3>第${value.revision}版交付要求 · ${esc(label(value.status))}</h3><p>尚未验收；未授权导出或执行。</p>${value.items.map(i=>`<article><h4>${esc(i.title)}${i.required?'（必交）':''}</h4><ul>${i.acceptance_criteria.map(x=>'<li>'+esc(x)+'</li>').join('')}</ul></article>`).join('')}`;return;
      }
      if(action==='more'){
        const kind=button.dataset.more,cursor=cursors[kind];if(!cursor)return;
        const route=({documents:'documents',citations:'citations',deliveries:'delivery-definitions',inputs:'workspace-inputs'})[kind];
        const page=await read(base()+'/'+route+'?limit=50&cursor='+encodeURIComponent(cursor));if(!live(dialog,v))return;
        ({documents:docs,citations:cites,deliveries,inputs})[kind].push(...page[kind]);cursors[kind]=page.next_cursor;
        if(kind==='inputs')node('[name=input]').innerHTML=options(inputs,'input_id',i=>'私有输入 · '+i.file_count+'个文件');drawLists();return;
      }
    }
    async function open(value,button) {
      invalidate();project=value;trigger=button;realm='';scope='';loaded=false;editing=deliveryEditing=null;
      const dialog=document.createElement('dialog');el=dialog;dialog.className='document-dialog';dialog.setAttribute('aria-label','项目资料与交付要求');
      dialog.style.cssText='width:min(1100px,95vw);max-height:92vh;overflow:auto;padding:24px;box-sizing:border-box;font:inherit';
      dialog.innerHTML=`<style>.document-dialog article{border-top:1px solid var(--line,#ddd);padding:12px 0}.document-dialog form,.document-dialog fieldset{display:grid;gap:10px}.document-dialog fieldset{min-width:0;margin:10px 0}.document-dialog input,.document-dialog select,.document-dialog textarea{max-width:100%;box-sizing:border-box}.document-dialog textarea{display:block;width:100%;height:80px;min-height:80px;max-height:180px;resize:vertical}.document-dialog h2{font-size:20px;margin:0 0 10px}.document-dialog h3{font-size:16px;margin:16px 0 8px}.document-dialog p{margin:8px 0;line-height:1.6}.document-dialog nav{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0}.document-dialog label{line-height:1.6}.document-dialog fieldset{border:1px solid var(--line,#ccc);border-radius:8px;padding:12px}.document-dialog summary{cursor:pointer;margin:10px 0;font-weight:600}.document-dialog select[multiple]{width:100%;min-height:75px}.document-dialog [hidden]{display:none!important}.document-dialog pre{font:inherit}.document-dialog [data-doc-status]{line-height:1.6}</style><h2>项目资料与交付要求 · ${esc(project.name)}</h2><p>阅读资料、保存原版本引用，定义正式任务的交付清单。</p><button data-doc-action="close">关闭</button> <button data-doc-action="refresh">刷新核对</button><p data-doc-status role="status"></p><div data-doc-recovery hidden><button data-doc-action="reconcile">只读核对原命令</button> <button data-doc-action="retry">原键原正文重试</button></div><nav aria-label="资料工作台"><button data-doc-action="tab" data-view="documents" aria-pressed="true">资料阅读</button> <button data-doc-action="tab" data-view="citations" aria-pressed="false">保存的引用</button> <button data-doc-action="tab" data-view="deliveries" aria-pressed="false">交付要求</button></nav><section data-doc-view="documents"><button data-doc-action="new-document" data-doc-write>添加资料</button><details data-doc-editor open><summary>导入或修订资料</summary><h3 data-doc-editing></h3><button data-doc-action="new-document" data-doc-write>添加新资料</button><form id="documentForm"><label>资料标题 <input name="title" maxlength="100" required></label><label>来源 <select name="sourceKind"><option value="workspace_input">已准备的私有文本输入</option><option value="pdf_grant">授权目录中的PDF</option></select></label><fieldset data-doc-input><legend>选择确切私有输入</legend><label>输入版本 <select name="input"></select></label><button type="button" data-doc-action="load-files">读取输入文件清单</button><button type="button" data-doc-action="more" data-more="inputs">更多输入</button><label>文件 <select name="file"></select></label></fieldset><fieldset data-doc-pdf hidden><legend>明确选择PDF</legend><label>只读来源授权 <select name="grant"></select></label><label>授权目录内PDF相对路径 <input name="pdfPath" maxlength="1024" placeholder="资料/说明.pdf"></label><button type="button" data-doc-action="preview-pdf">预览PDF导入</button><p data-pdf-preview></p><label><input name="pdfConfirmed" type="checkbox"> 确认导入本次预览的文件</label><p>仅阅读文本层；扫描页不执行OCR，不运行文档脚本。</p></fieldset><label><input name="retired" type="checkbox"> 退役资料（不可恢复，历史引用不再显示正文）</label><p><button type="submit">保存资料版本</button></p></form></details><h3>资料列表</h3><section data-doc-list></section><button data-doc-action="more" data-more="documents">更多资料</button><section data-doc-reader></section></section><section data-doc-view="citations" hidden><h3>保存的引用</h3><section data-cite-list></section><button data-doc-action="more" data-more="citations">更多引用</button></section><section data-doc-view="deliveries" hidden><h3 data-delivery-editing></h3><button data-doc-action="new-delivery" data-doc-write>新建交付清单</button><form id="deliveryForm"><label>正式产品任务版本 <select name="task" required></select></label><div data-delivery-items></div><button type="button" data-doc-action="add-item">添加交付项目</button><label><input name="retired" type="checkbox"> 退役清单（不可恢复）</label><p><button type="submit">保存交付要求</button></p></form><section data-delivery-list></section><button data-doc-action="more" data-more="deliveries">更多清单</button><section data-delivery-detail></section></section>`;
      main.append(dialog);dialog.showModal();docForm();deliveryForm();controls();
      dialog.oncancel=e=>{e.preventDefault();close();};
      dialog.oninput=e=>{epoch++;if(e.target.name!=='pdfConfirmed')invalidatePreview();if(e.target.closest('#documentForm')&&!['quoteStart','quoteEnd'].includes(e.target.name))clearReader();};
      dialog.onchange=e=>{if(e.target.name==='sourceKind')sourceVisibility();if(e.target.name==='input'){inputFiles=[];node('[name=file]').replaceChildren();}};
      dialog.onclick=e=>{const b=e.target.closest('[data-doc-action]');if(!b||(!realm&&b.dataset.docAction!=='close'))return;
        const current=dialog;act(b.dataset.docAction,b).catch(error=>{if(el===current){say('操作未完成：'+error.message);controls();}});};
      dialog.onsubmit=e=>{
        e.preventDefault();if(sending||pending.has(scope)||!realm)return;
        try{
          if(e.target.id==='documentForm'){
            const f=e.target;let source;
            if(f.elements.sourceKind.value==='pdf_grant'){
              if(!preview||!f.elements.pdfConfirmed.checked||preview.path!==f.elements.pdfPath.value||preview.grant_id!==f.elements.grant.value)throw Error('请预览并确认当前PDF。');
              source={kind:'pdf_grant',grant_id:preview.grant_id,grant_revision:preview.grant_revision,path:preview.path,confirmation_digest:preview.confirmation_digest};
            }else{
              const wi=inputs.find(i=>i.input_id===f.elements.input.value),index=Number(f.elements.file.value);
              if(!wi||!inputFiles[index]||f.elements.file.value==='')throw Error('请读取输入文件清单并选择文件。');
              source={kind:'workspace_input',input_ref:ref(wi,'input_id'),file_index:index};
            }
            submit('document',{schema_version:1,document_id:editing?.document_id||'doc-'+id(),expected_revision:editing?.revision||0,project_revision:project.revision,title:f.elements.title.value,source,retired:f.elements.retired.checked});
          }else if(e.target.id==='deliveryForm'){
            const f=e.target,task=tasks.find(t=>t.product_task_id===f.elements.task.value),task_ref=f.elements.task.value==='__original__'?deliveryEditing.task_ref:task?ref(task,'product_task_id'):null;
            if(!task_ref)throw Error('请选择正式产品任务。');
            const items=Array.from(node('[data-delivery-items]').children,item=>({item_id:item.dataset.itemId,title:item.querySelector('[data-item-title]').value,required:item.querySelector('[data-item-required]').checked,acceptance_criteria:item.querySelector('[data-item-criteria]').value.split('\n'),citation_refs:Array.from(item.querySelector('[data-item-citations]').selectedOptions,o=>ref(cites.find(c=>c.citation_id===o.value),'citation_id'))}));
            if(!items.length||items.some(i=>i.acceptance_criteria.some(t=>!t.trim()||t.length>1000)||i.acceptance_criteria.length>20))throw Error('至少一项交付；每项需1～20条非空验收标准，每条最多1000字。');
            submit('delivery_definition',{schema_version:1,delivery_id:deliveryEditing?.delivery_id||'del-'+id(),expected_revision:deliveryEditing?.revision||0,project_revision:project.revision,task_ref,items,retired:f.elements.retired.checked});
          }
        }catch(error){say(error.message);}
      };
      try{const v=epoch,who=await identity();if(!live(dialog,v))return;realm=who;scope=JSON.stringify([realm,project.project_id]);await load();}catch(error){if(el===dialog)say('身份核对失败：'+error.message);}
    }
    return {open,invalidate};
  }};
})(window);
