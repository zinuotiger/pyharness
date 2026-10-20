/* Private regression data. Saving and transfer never start an execution. */
(function(global){
 'use strict';
 global.PyharnessRegressionData={create({main,api,esc}){
  const c=global.PyharnessWorkspaceContracts;let active=null,serial=0;
  const hash=async text=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text)))).map(n=>n.toString(16).padStart(2,'0')).join('');
  const same=(a,b)=>c.canonical(a)===c.canonical(b),integer=(v,min=1,max=Number.MAX_SAFE_INTEGER)=>Number.isSafeInteger(v)&&v>=min&&v<=max;
  const id=(v,p)=>new RegExp('^'+p+'-[0-9a-f]{16}$').test(v),fresh=p=>p+'-'+crypto.randomUUID().replaceAll('-','').slice(0,16);
  const q=(d,s)=>d.el.querySelector(s),all=(d,s)=>Array.from(d.el.querySelectorAll(s)),base=d=>'/api/v1/projects/'+d.project.project_id;
  const live=(d,n=d.epoch)=>active===d&&d.el.isConnected&&d.el.open&&d.epoch===n;
  const say=(d,text)=>{if(active===d&&d.el.isConnected)q(d,'[data-rg-status]').textContent=text;};
  const field=(d,key)=>q(d,'[data-rg-'+key+']'),checked=(d,key)=>field(d,'confirm-'+key).checked;
  async function who(){const v=await api('/api/account/me',{cache:'no-store'}),a=v.account;return {tenant:a?.tenant||null,actor:a?.user_id||a?.username||v.username||'local-operator'};}
  function invalidate(){serial++;if(!active)return;const d=active;active=null;d.epoch++;for(const url of d.urls)URL.revokeObjectURL(url);d.el.replaceChildren();d.el.close();d.el.remove();if(d.trigger?.isConnected)d.trigger.focus();}
  async function check(d,n){if(!live(d,n))throw Error('窗口或选择已变化');let current;try{current=await who();}catch(e){invalidate();throw Error('账户不可用，已清除内容');}if(!same(current,d.who)){invalidate();throw Error('账户或团队已变化，已清除内容');}if(!live(d,n))throw Error('窗口或选择已变化');}
  async function read(d,n,path,options={}){await check(d,n);try{const v=await api(path,{cache:'no-store',...options});await check(d,n);return v;}catch(e){if(e.status===401||e.status===403)invalidate();throw e;}}
  const post=(d,n,path,body,key)=>read(d,n,path,{method:'POST',body:JSON.stringify(body),...(key?{headers:{'Idempotency-Key':key}}:{})});
  function scope(d,v){return v?.schema_version===1&&v.project_id===d.project.project_id&&v.execution_authorized===false;}
  function reference(v,key){return {[key]:v[key],revision:v.revision,digest:v.digest,source_seq:v.source_seq};}
  function goodRef(v,key,prefix){return v&&id(v[key],prefix)&&integer(v.revision,1,200)&&c.hash(v.digest)&&integer(v.source_seq);}
  function goodOrigin(d,o){return o&&c.keys(o,'tenant_id subject_ref execution_ref session_id observed_seq prefix_digest failure_kind acceptance_ref')&&
   typeof o.tenant_id==='string'&&o.subject_ref===d.who.actor&&(!d.who.tenant||o.tenant_id===d.who.tenant)&&goodRef(o.execution_ref,'execution_id','ex')&&o.execution_ref.revision===1&&
   /^s-pa-[0-9a-f]{32}$/.test(o.session_id)&&integer(o.observed_seq,2,10000)&&c.hash(o.prefix_digest)&&['execution_failed','business_failed'].includes(o.failure_kind)&&
   (o.failure_kind==='execution_failed'?o.acceptance_ref===null:goodRef(o.acceptance_ref,'acceptance_id','av')&&o.acceptance_ref.revision===1);}
  async function candidate(d,v){
   if(!scope(d,v)||!goodOrigin(d,v.origin)||v.persisted!==false||v.status!=='confirmation_required'||v.historical_tools_replayed!==false||!c.hash(v.candidate_digest))throw Error('候选来源或范围不符');
   const keys=['schema_version','project_id','origin','observed_input_text','observed_input_state','observed_output_text','observed_output_state','root_tool_call_count','policy','assertion_version'];
   const proof=Object.fromEntries(keys.map(k=>[k,v[k]]));
   for(const key of ['input','output'])if(!['available','withheld','truncated','unavailable'].includes(proof['observed_'+key+'_state'])||!(proof['observed_'+key+'_text']===null||typeof proof['observed_'+key+'_text']==='string'&&proof['observed_'+key+'_text'].length<=2048))throw Error('候选文本不合法');
   if(!integer(proof.root_tool_call_count,0,10000)||proof.policy!=='bounded-public-regression-v1'||proof.assertion_version!=='regression-assertions-v1'||await hash(c.canonical(proof))!==v.candidate_digest)throw Error('候选摘要不符');
   return v;
  }
  function safeNumbers(v,depth=0){if(depth>20)return false;if(typeof v==='number')return Number.isFinite(v)&&(!Number.isInteger(v)||Number.isSafeInteger(v));if(v&&typeof v==='object')return Object.values(v).every(x=>safeNumbers(x,depth+1));return true;}
  async function receipt(d,v,kind,expected=null,key=null){
   const keyName=kind==='regression_case'?'case_id':'dataset_id',prefix=kind==='regression_case'?'case':'ds';
   if(!scope(d,v)||v.persisted!==true||v.status!=='saved'||!goodRef(v,keyName,prefix)||typeof v.record_canonical!=='string'||new TextEncoder().encode(v.record_canonical).length>32768||await hash(v.record_canonical)!==v.digest)throw Error('保存记录摘要或范围不符');
   const model=JSON.parse(v.record_canonical),keys=kind==='regression_case'?'schema_version project_id project_revision revision created_by submission_key case_id selection evidence':'schema_version project_id project_revision revision created_by submission_key dataset_id selection';
   if(!same(model,Object.fromEntries(Object.keys(model).map(k=>[k,v[k]]))))throw Error('版本原始字节与公开字段不符');
   if(!c.keys(model,keys)||model.schema_version!==1||model.selection?.schema_version!==1||model[keyName]!==v[keyName]||model.selection[keyName]!==model[keyName]||model.project_id!==d.project.project_id||model.created_by!==d.who.actor||!integer(model.project_revision)||model.revision!==v.revision||!same(model.selection,v.selection)||model.selection?.confirmed!==true||model.selection.expected_revision+1!==model.revision||model.selection.project_revision!==model.project_revision||key&&model.submission_key!==key)throw Error('保存记录身份不符');
   if(expected&&(!same(model.selection,expected)||model[keyName]!==expected[keyName]))throw Error('保存回执与本次内容不符');
   if(kind==='regression_case'){
    await candidate(d,{...model.evidence,candidate_digest:model.selection.candidate_digest,status:'confirmation_required',persisted:false,execution_authorized:false,historical_tools_replayed:false});
    if(!same(model.evidence.origin,model.selection.origin)||typeof model.selection.input_text!=='string'||!Array.isArray(model.selection.assertions)||model.selection.assertions.length<1||model.selection.assertions.length>16)throw Error('用例资料不符');
   }else if(!Array.isArray(model.selection.case_refs)||model.selection.case_refs.length<1||model.selection.case_refs.length>32||model.selection.case_refs.some(r=>!goodRef(r,'case_id','case')))throw Error('集合引用不符');
   return v;
  }
  function controls(d){if(!live(d))return;d.el.setAttribute('aria-busy',String(d.busy));const locked=d.busy||!!d.pending;all(d,'button,input,select,textarea').forEach(e=>{e.disabled=locked;});
   q(d,'[data-rg-action=close]').disabled=false;q(d,'[data-rg-action=query]').disabled=d.busy||(!d.pending&&!field(d,'query-key').value.trim());
   if(!locked){
    q(d,'[data-rg-action=save-case]').disabled=!d.proof||!checked(d,'case');
    q(d,'[data-rg-action=edit-case]').disabled=!d.caseView||!d.caseEditable;
    q(d,'[data-rg-action=add-case]').disabled=!d.caseView;
    q(d,'[data-rg-action=save-dataset]').disabled=!d.refs.length||!checked(d,'dataset');
    q(d,'[data-rg-action=preview-export]').disabled=!d.datasetView;
    q(d,'[data-rg-action=export]').disabled=!d.exportPreview||!checked(d,'export');
    q(d,'[data-rg-action=preview-import]').disabled=!d.bundle;
    q(d,'[data-rg-action=import]').disabled=!d.importPreview||!checked(d,'import');
   }
   q(d,'[data-rg-action=retry]').disabled=d.busy||!d.pending||d.pending.state!=='not_found'||d.pending.reopen;
  }
  const labels={output_contains:'输出包含文本',output_excludes:'输出不包含文本',output_equals:'输出完全相同',json_pointer_equals:'JSON 字段等于指定值',execution_status:'执行终态'};
  function addAssertion(d,value={kind:'output_contains',expected:'',pointer:null}){
   if(all(d,'[data-rg-assertion]').length>=16)throw Error('最多16条断言');const row=document.createElement('div');row.dataset.rgAssertion='';row.style.cssText='display:flex;gap:8px;flex-wrap:wrap;margin:8px 0';
   row.innerHTML='<select data-rg-kind>'+Object.entries(labels).map(([k,v])=>'<option value="'+k+'">'+v+'</option>').join('')+'</select><input data-rg-expected aria-label="期望内容" maxlength="2048"><input data-rg-pointer placeholder="字面 JSON 指针，例如 /answer" aria-label="JSON 字段"><select data-rg-state><option value="completed">执行完成</option><option value="failed">明确失败</option></select><button type="button" data-rg-remove>移除</button>';
   row.querySelector('[data-rg-kind]').value=value.kind;row.querySelector('[data-rg-expected]').value=value.kind==='json_pointer_equals'?JSON.stringify(value.expected):String(value.expected??'');row.querySelector('[data-rg-pointer]').value=value.pointer||'';row.querySelector('[data-rg-state]').value=value.kind==='execution_status'?value.expected:'completed';
   const update=()=>{const kind=row.querySelector('[data-rg-kind]').value;row.querySelector('[data-rg-pointer]').hidden=kind!=='json_pointer_equals';row.querySelector('[data-rg-state]').hidden=kind!=='execution_status';row.querySelector('[data-rg-expected]').hidden=kind==='execution_status';};update();row.querySelector('[data-rg-kind]').addEventListener('change',update);
   row.querySelector('[data-rg-remove]').addEventListener('click',()=>{if(all(d,'[data-rg-assertion]').length>1){row.remove();touch(d,'case');}});field(d,'assertions').append(row);
  }
  function assertions(d){return all(d,'[data-rg-assertion]').map(row=>{
   const kind=row.querySelector('[data-rg-kind]').value;let expected=row.querySelector('[data-rg-expected]').value,pointer=null;
   if(kind==='json_pointer_equals'){try{expected=JSON.parse(expected);}catch(e){throw Error('JSON期望需要填写一个合法的标量值');}if(expected&&typeof expected==='object'||!safeNumbers(expected))throw Error('期望超出界面可精确编辑范围');pointer=row.querySelector('[data-rg-pointer]').value;}
   else if(kind==='execution_status')expected=row.querySelector('[data-rg-state]').value;
   else if(!expected)throw Error('请填写非空期望内容');return {kind,expected,pointer};
  });}
  function touch(d,part){if(active!==d)return;d.epoch++;if(part==='case')field(d,'confirm-case').checked=false;else{for(const key of ['dataset','export','import'])field(d,'confirm-'+key).checked=false;d.exportPreview=null;d.importPreview=null;d.importBody=null;}controls(d);}
  function newCase(d){d.caseId=fresh('case');d.caseRevision=0;d.proof=null;field(d,'case-title').value='';field(d,'input').value='';field(d,'case-key').textContent=d.caseId+' · 新用例';field(d,'assertions').replaceChildren();addAssertion(d);field(d,'confirm-case').checked=false;field(d,'candidate').replaceChildren();}
  function drawRefs(d){field(d,'refs').replaceChildren();for(const ref of d.refs){const row=document.createElement('p');row.textContent=ref.case_id+' · v'+ref.revision+' ';const button=document.createElement('button');button.type='button';button.textContent='移除';button.onclick=()=>{if(d.busy||d.pending)return;d.refs=d.refs.filter(r=>r.case_id!==ref.case_id);drawRefs(d);touch(d,'dataset');};row.append(button);field(d,'refs').append(row);}}
  function newDataset(d){d.datasetId=fresh('ds');d.datasetRevision=0;d.datasetView=null;d.refs=[];d.exportPreview=null;d.importPreview=null;d.importBody=null;d.bundle=null;field(d,'dataset-title').value='';field(d,'dataset-key').textContent=d.datasetId+' · 新数据集';field(d,'import-file').value='';field(d,'transfer').replaceChildren();drawRefs(d);for(const key of ['dataset','export','import'])field(d,'confirm-'+key).checked=false;}
  function showCandidate(d,v){const p=document.createElement('pre'),states={available:'可用公开文本',withheld:'含屏蔽项',truncated:'公开文本已裁剪',unavailable:'未记录可用公开文本'};p.style.cssText='white-space:pre-wrap;max-height:240px;overflow:auto';p.textContent='来源：'+(v.origin.failure_kind==='execution_failed'?'原执行失败':'独立验收不通过')+' · 原日志 seq '+v.origin.observed_seq+'\n原输入：'+states[v.observed_input_state]+'\n'+(v.observed_input_text||'无可用公开输入')+'\n\n原输出：'+states[v.observed_output_state]+'\n'+(v.observed_output_text||'无可用公开输出')+'\n\n历史工具事件 '+v.root_tool_call_count+' 条；仅作资料，不会重放。';field(d,'candidate').replaceChildren(p);}
  function showCase(d,v){d.caseView=v;d.caseEditable=safeNumbers(v.selection);field(d,'case-detail').textContent=v.record_canonical;
   field(d,'case-summary').textContent=v.selection.title+' · v'+v.revision+' · '+v.selection.assertions.length+'条断言'+(d.caseEditable?'':'；期望数值超出界面精度，仅保留原始资料与导出，编辑入口不可用。');}
  function showDataset(d,v){d.datasetView=v;d.datasetId=v.dataset_id;d.datasetRevision=v.revision;d.refs=structuredClone(v.selection.case_refs);field(d,'dataset-title').value=v.selection.title;field(d,'dataset-key').textContent=v.dataset_id+' · v'+v.revision;field(d,'dataset-detail').textContent=v.record_canonical;d.exportPreview=null;d.importPreview=null;drawRefs(d);}
  async function directory(d,n,kind){const path=kind==='case'?'/regression-cases':'/regression-datasets',v=await read(d,n,base(d)+path),key=kind==='case'?'case_id':'dataset_id',prefix=kind==='case'?'case':'ds';
   if(!scope(d,v)||!Array.isArray(v.items)||v.items.length>200||v.items.some(x=>!goodRef(x,key,prefix)||!['available','source_unavailable'].includes(x.status)))throw Error('目录范围不符');
   if(kind==='case')d.cases=v.items;else d.datasets=v.items;drawDirectory(d,kind);
  }
  function drawDirectory(d,kind,selected=null){const key=kind==='case'?'case_id':'dataset_id',select=field(d,kind+'-select');select.replaceChildren();for(const item of kind==='case'?d.cases:d.datasets){const option=document.createElement('option');option.value=item[key];option.textContent=(item.title||'来源不可用')+' · v'+item.revision;option.disabled=item.status!=='available';option.dataset.revision=String(item.revision);select.append(option);}if(selected)select.value=selected;}
  function savedDirectory(d,v,kind){const type=kind==='regression_case'?'case':'dataset',key=type==='case'?'case_id':'dataset_id',items=type==='case'?d.cases:d.datasets;const row={...reference(v,key),title:v.selection.title,status:'available'};const index=items.findIndex(i=>i[key]===v[key]);if(index<0)items.push(row);else items[index]=row;drawDirectory(d,type,v[key]);field(d,type+'-revision').value=String(v.revision);}
  function requested(d,kind){const select=field(d,kind+'-select'),ident=select.value;if(!ident)throw Error('请先选择一个可读版本');const raw=field(d,kind+'-revision').value.trim(),revision=raw?Number(raw):Number(select.selectedOptions[0].dataset.revision);if(!integer(revision,1,200))throw Error('版本号需为1到200');return {ident,revision};}
  async function commit(d,n,pending){d.pending=pending;pending.state='unknown';field(d,'query-key').value=pending.key;controls(d);
   let v;try{v=await post(d,n,pending.path,pending.body,pending.key);}catch(e){if(e.status===422){d.pending=null;for(const key of ['case','dataset','import'])field(d,'confirm-'+key).checked=false;throw Error('字段或大小被拒绝，未保存；请修正内容后重新确认。');}throw e;}
   await receipt(d,v,pending.kind,pending.expected,pending.key);await check(d,n);applySaved(d,v,pending.kind);}
  function applySaved(d,v,kind){d.pending=null;savedDirectory(d,v,kind);if(kind==='regression_case'){showCase(d,v);d.caseId=v.case_id;d.caseRevision=v.revision;field(d,'case-key').textContent=v.case_id+' · v'+v.revision;field(d,'confirm-case').checked=false;}else{showDataset(d,v);for(const key of ['dataset','import'])field(d,'confirm-'+key).checked=false;}say(d,'已确认保存 '+(kind==='regression_case'?'用例':'数据集')+' v'+v.revision+'；没有开始执行。');}
  async function operate(d,action){if(action==='close'){invalidate();return;}if(!live(d)||d.busy)return;if(d.pending&&!['query','retry'].includes(action))return;d.busy=true;const n=++d.epoch;controls(d);
   try{
    if(action==='refresh'){await directory(d,n,'case');await directory(d,n,'dataset');say(d,'已读取当前主体的版本目录；未开始执行。');}
    else if(action==='new-case'){newCase(d);say(d,'新用例需先预览真实失败来源，再填写复现输入与期望。');}
    else if(action==='new-dataset'){newDataset(d);say(d,'新数据集只绑定确切用例版本；保存不会运行用例。');}
    else if(action==='add-assertion'){addAssertion(d);field(d,'confirm-case').checked=false;}
    else if(action==='preview'){
     d.proof=null;field(d,'candidate').replaceChildren();field(d,'confirm-case').checked=false;const execution=field(d,'execution').value.trim();if(!id(execution,'ex'))throw Error('请填写有效的失败执行ID');
     const v=await post(d,n,base(d)+'/failure-candidates',{schema_version:1,execution_id:execution});await candidate(d,v);await check(d,n);if(v.origin.execution_ref.execution_id!==execution)throw Error('候选执行范围不符');d.proof=v;showCandidate(d,v);if(v.observed_input_state==='available')field(d,'input').value=v.observed_input_text;say(d,'候选已核对；请独立确认人工复现输入和期望，尚未入库。');
    }else if(action==='save-case'){
     if(!d.proof||!checked(d,'case'))throw Error('请先核对来源并明确确认');const title=field(d,'case-title').value.trim(),input=field(d,'input').value;if(!title||!input)throw Error('请填写用例标题和可用复现输入');
     const body={schema_version:1,case_id:d.caseId,expected_revision:d.caseRevision,project_revision:d.project.revision,title,origin:d.proof.origin,candidate_digest:d.proof.candidate_digest,input_text:input,assertions:assertions(d),confirmed:true};
     await commit(d,n,{key:'regression-case-'+crypto.randomUUID(),kind:'regression_case',path:base(d)+'/regression-cases',body,expected:structuredClone(body)});
    }else if(action==='load-case'||action==='load-dataset'){
     const kind=action==='load-case'?'case':'dataset',r=requested(d,kind),resource=kind==='case'?'regression_case':'regression_dataset',v=await read(d,n,base(d)+'/regression-'+(kind==='case'?'cases':'datasets')+'/'+r.ident+'?revision='+r.revision);
     await receipt(d,v,resource);await check(d,n);if(v[kind==='case'?'case_id':'dataset_id']!==r.ident||v.revision!==r.revision)throw Error('读取版本不符');if(kind==='case')showCase(d,v);else showDataset(d,v);say(d,'已核对固定历史版本；没有运行或改写它。');
    }else if(action==='edit-case'){
     const v=d.caseView;if(!v||!d.caseEditable)throw Error('此版本无法在界面精确编辑');d.caseId=v.case_id;d.caseRevision=v.revision;d.proof={...v.evidence,candidate_digest:v.selection.candidate_digest,status:'confirmation_required',persisted:false,execution_authorized:false,historical_tools_replayed:false};field(d,'case-title').value=v.selection.title;field(d,'input').value=v.selection.input_text;field(d,'case-key').textContent=v.case_id+' · 基于 v'+v.revision+' 编辑';field(d,'execution').value=v.selection.origin.execution_ref.execution_id;field(d,'assertions').replaceChildren();v.selection.assertions.forEach(a=>addAssertion(d,a));field(d,'confirm-case').checked=false;showCandidate(d,d.proof);say(d,'修改将保存为新修订；旧版本不变，请重新确认。');
    }else if(action==='add-case'){
     if(!d.caseView)throw Error('先读取并核对用例版本');const fixed=reference(d.caseView,'case_id');d.refs=d.refs.filter(r=>r.case_id!==fixed.case_id);if(d.refs.length>=32)throw Error('最多32个用例');d.refs.push(fixed);drawRefs(d);field(d,'confirm-dataset').checked=false;say(d,'已加入确切用例版本，尚未保存集合。');
    }else if(action==='save-dataset'){
     const title=field(d,'dataset-title').value.trim();if(!title||!d.refs.length||!checked(d,'dataset'))throw Error('请填写标题、选择版本并确认');const body={schema_version:1,dataset_id:d.datasetId,expected_revision:d.datasetRevision,project_revision:d.project.revision,title,case_refs:structuredClone(d.refs),confirmed:true};await commit(d,n,{key:'regression-dataset-'+crypto.randomUUID(),kind:'regression_dataset',path:base(d)+'/regression-datasets',body,expected:structuredClone(body)});
    }else if(action==='query'){
     const key=d.pending?.key||field(d,'query-key').value.trim();if(!/^[A-Za-z0-9._-]{1,128}$/.test(key))throw Error('请填写原查询键');const v=await read(d,n,base(d)+'/regression-submissions/'+encodeURIComponent(key));if(!scope(d,v)||v.submission_key!==key||!['committed','not_found'].includes(v.status))throw Error('提交查询范围不符');
     if(v.status==='committed'){if(!['regression_case','regression_dataset'].includes(v.resource_kind))throw Error('提交资源类型不符');await receipt(d,v.receipt,v.resource_kind,d.pending?.expected||null,key);await check(d,n);applySaved(d,v.receipt,v.resource_kind);}else{if(d.pending){d.pending.state='not_found';d.pending.reopen=v.writer_requires_reopen;}say(d,'原键未找到提交；'+(v.writer_requires_reopen?'写入器需重新打开，请重启后按原键查询。':'可在原内容不变时重试原键；不要换键盲目重发。'));}
    }else if(action==='retry'){if(!d.pending||d.pending.state!=='not_found'||d.pending.reopen)throw Error('请先查询原键并核对写入器状态');await commit(d,n,d.pending);}
    else if(action==='preview-export'){
     const row=d.datasetView;if(!row)throw Error('先读取已保存的集合');d.exportPreview=null;field(d,'confirm-export').checked=false;const v=await post(d,n,base(d)+'/regression-datasets/'+row.dataset_id+'/export-previews',{schema_version:1,revision:row.revision});if(!scope(d,v)||v.status!=='confirmation_required'||v.persisted!==false||v.export_authorized!==false||!same(v.dataset_ref,reference(row,'dataset_id'))||!same(v.case_refs,row.selection.case_refs)||!c.hash(v.confirmation_digest)||!c.hash(v.bundle_digest))throw Error('导出预览范围不符');d.exportPreview=v;field(d,'transfer').textContent='导出已保存集合 v'+row.revision+'，固定用例 '+v.case_count+' 个；不含未保存编辑。';say(d,'已预览导出版本，请明确确认本次下载。');
    }else if(action==='export'){
     const p=d.exportPreview;if(!p||!checked(d,'export'))throw Error('先核对导出预览并确认');const v=await post(d,n,base(d)+'/regression-datasets/'+p.dataset_ref.dataset_id+'/exports',{schema_version:1,revision:p.dataset_ref.revision,confirmation_digest:p.confirmation_digest,confirmed:true});
     if(!scope(d,v)||v.export_authorized!==true||!same(v.dataset_ref,p.dataset_ref)||v.bundle_digest!==p.bundle_digest||typeof v.bundle_canonical!=='string'||new TextEncoder().encode(v.bundle_canonical).length>65536||await hash(v.bundle_canonical)!==v.bundle_digest||!/^pyharness-ds-[0-9a-f]{16}-r[0-9]{1,3}\.json$/.test(v.suggested_filename))throw Error('导出内容摘要或范围不符');await check(d,n);const url=URL.createObjectURL(new Blob([v.bundle_canonical],{type:'application/json;charset=utf-8'}));d.urls.push(url);const a=document.createElement('a');a.href=url;a.download=v.suggested_filename;d.el.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);field(d,'confirm-export').checked=false;say(d,'已核对原始JSON字节并导出，未执行用例。');
    }else if(action==='preview-import'){
     if(!d.bundle)throw Error('先选择最多64KiB的JSON数据包');const title=field(d,'dataset-title').value.trim();if(!title)throw Error('请填写目标集合标题');const body={schema_version:1,dataset_id:d.datasetId,expected_revision:d.datasetRevision,project_revision:d.project.revision,title,bundle_canonical:d.bundle};d.importPreview=null;field(d,'confirm-import').checked=false;
     const v=await post(d,n,base(d)+'/regression-dataset-import-previews',body);if(!scope(d,v)||v.dataset_id!==body.dataset_id||v.status!=='confirmation_required'||v.persisted!==false||!goodRef(v.source_dataset_ref,'dataset_id','ds')||!c.hash(v.bundle_digest)||await hash(d.bundle)!==v.bundle_digest||!c.hash(v.confirmation_digest)||!Array.isArray(v.case_refs)||v.case_refs.length<1||v.case_refs.length>32||v.case_refs.some(r=>!goodRef(r,'case_id','case')))throw Error('导入预览范围不符');await check(d,n);d.importPreview=v;d.importBody=body;field(d,'transfer').textContent='来源集合 '+v.source_dataset_ref.dataset_id+' · v'+v.source_dataset_ref.revision+'；固定 '+v.case_refs.length+' 个本地可证明用例版本。仅另存集合，不创建用例或执行。';say(d,'导入来源已核对，请明确确认。');
    }else if(action==='import'){
     const p=d.importPreview;if(!p||!d.importBody||!checked(d,'import'))throw Error('先预览并明确确认导入');const body={...d.importBody,confirmation_digest:p.confirmation_digest,confirmed:true},expected={schema_version:1,dataset_id:body.dataset_id,expected_revision:body.expected_revision,project_revision:body.project_revision,title:body.title,case_refs:p.case_refs,confirmed:true,import_source:{dataset_ref:p.source_dataset_ref,bundle_digest:p.bundle_digest}};await commit(d,n,{key:'regression-import-'+crypto.randomUUID(),kind:'regression_dataset',path:base(d)+'/regression-dataset-imports',body,expected});
    }
   }catch(e){if(active===d)say(d,(d.pending?'保存结果未知，请先查询原键；':'')+String(e.message||'操作未完成'));}
   finally{if(active===d){d.busy=false;controls(d);}}
  }
  async function fileChanged(d){touch(d,'dataset');d.bundle=null;const file=field(d,'import-file').files[0];if(!file){controls(d);return;}if(file.size>65536){say(d,'数据包超过64KiB，未读取或导入。');controls(d);return;}d.busy=true;const n=++d.epoch;controls(d);try{const raw=new TextDecoder('utf-8',{fatal:true}).decode(await file.arrayBuffer());await check(d,n);d.bundle=raw;say(d,'文件已读取；尚未验证、保存或执行，请预览导入。');}catch(e){if(active===d)say(d,'文件不可用：'+e.message);}finally{if(active===d){d.busy=false;controls(d);}}}
  return {invalidate,async open(project,trigger=null,options={}){
   invalidate();const ticket=serial,owner=await who();if(ticket!==serial)return;const el=document.createElement('dialog');el.id='regressionDataDialog';el.style.cssText='width:min(1100px,94vw);max-width:1100px;max-height:88vh;overflow:auto';
   el.innerHTML='<h2>失败用例与数据集 · '+esc(project.name)+'</h2><button data-rg-action="close">关闭</button><p>预览、保存和导入导出均不会运行用例。人工复现输入与原始请求分别保留。</p><p data-rg-status role="status"></p><p><button data-rg-action="refresh">刷新版本目录</button></p>'+
    '<section><h3>1. 失败候选与人工期望</h3><label>失败执行ID <input data-rg-execution aria-label="失败执行ID"></label><button data-rg-action="preview">预览失败记录</button><button data-rg-action="new-case">新建独立用例</button><div data-rg-candidate></div><p data-rg-case-key></p><label>用例标题 <input data-rg-case-title maxlength="100"></label><p><label>人工复现输入（最多2048字符）<textarea data-rg-input maxlength="2048" rows="4"></textarea></label></p><div data-rg-assertions></div><button data-rg-action="add-assertion">添加断言</button><p><label><input type="checkbox" data-rg-confirm-case>我已核对来源、复现输入和期望，确认保存此用例修订</label></p><button data-rg-action="save-case">确认保存用例新修订</button></section>'+
    '<section><h3>2. 已保存的用例版本</h3><select data-rg-case-select aria-label="用例目录"></select><input data-rg-case-revision type="number" min="1" max="200" placeholder="历史版本，可留空" aria-label="用例版本"><button data-rg-action="load-case">读取选中版本</button><button data-rg-action="edit-case">基于此版本编辑</button><button data-rg-action="add-case">加入数据集</button><p data-rg-case-summary></p><details><summary>查看原始版本资料与精确数值</summary><pre data-rg-case-detail style="white-space:pre-wrap;max-height:260px;overflow:auto"></pre></details></section>'+
    '<section><h3>3. 固定版本数据集</h3><select data-rg-dataset-select aria-label="数据集目录"></select><input data-rg-dataset-revision type="number" min="1" max="200" placeholder="历史版本，可留空" aria-label="数据集版本"><button data-rg-action="load-dataset">读取数据集</button><button data-rg-action="new-dataset">新建数据集</button><p data-rg-dataset-key></p><label>集合标题 <input data-rg-dataset-title maxlength="100"></label><div data-rg-refs></div><p><label><input type="checkbox" data-rg-confirm-dataset>我已核对全部固定用例版本，确认保存集合修订</label></p><button data-rg-action="save-dataset">确认保存数据集新修订</button><details><summary>查看原始集合版本</summary><pre data-rg-dataset-detail style="white-space:pre-wrap;max-height:220px;overflow:auto"></pre></details></section>'+
    '<section><h3>4. 显式导入与导出</h3><button data-rg-action="preview-export">预览已保存版本的导出</button><label><input type="checkbox" data-rg-confirm-export>确认本次下载</label><button data-rg-action="export">导出JSON</button><p>导入仅复用本租户、本项目、本主体在原日志中可证明的用例版本；不从外来文件创建用例。</p><input data-rg-import-file type="file" accept="application/json,.json" aria-label="数据包"><button data-rg-action="preview-import">预览导入</button><label><input type="checkbox" data-rg-confirm-import>确认另存数据集修订</label><button data-rg-action="import">确认导入集合</button><pre data-rg-transfer style="white-space:pre-wrap"></pre></section>'+
    '<section><h3>保存结果查询</h3><p>网络中断后保留原查询键，先查询；不换键盲目重发。</p><input data-rg-query-key maxlength="128" aria-label="原查询键"><button data-rg-action="query">查询原保存结果</button><button data-rg-action="retry">重试原提交</button></section>';
   const style=document.createElement('style');style.textContent='#regressionDataDialog section{border-top:1px solid var(--line,#456);margin-top:16px;padding-top:12px}#regressionDataDialog h3{margin:0 0 12px;font-size:16px}#regressionDataDialog textarea{width:100%;height:120px;min-height:96px;max-height:240px;box-sizing:border-box;resize:vertical}#regressionDataDialog select{min-width:180px;max-width:100%}#regressionDataDialog input[type=checkbox]{width:auto}';el.prepend(style);
   const d={el,project:structuredClone(project),trigger,who:owner,epoch:0,busy:false,pending:null,proof:null,caseView:null,caseEditable:false,datasetView:null,refs:[],cases:[],datasets:[],urls:[],bundle:null};active=d;main.append(el);el.showModal();newCase(d);newDataset(d);if(options.execution_id)field(d,'execution').value=options.execution_id;
   el.addEventListener('click',e=>{const b=e.target.closest('[data-rg-action]');if(b)operate(d,b.dataset.rgAction);});
   el.addEventListener('input',e=>{if(e.target.matches('[data-rg-query-key]')){controls(d);return;}if(e.target.matches('[data-rg-case-title],[data-rg-input],[data-rg-expected],[data-rg-pointer],[data-rg-kind],[data-rg-state]'))touch(d,'case');else if(e.target.matches('[data-rg-dataset-title]'))touch(d,'dataset');else if(e.target.matches('[data-rg-execution]')){d.proof=null;field(d,'candidate').replaceChildren();touch(d,'case');}});
   el.addEventListener('change',e=>{if(e.target.matches('[data-rg-import-file]'))fileChanged(d);else if(e.target.matches('[data-rg-confirm-case],[data-rg-confirm-dataset],[data-rg-confirm-export],[data-rg-confirm-import]'))controls(d);else if(e.target.matches('[data-rg-kind],[data-rg-state]'))touch(d,'case');});
   el.addEventListener('cancel',e=>{e.preventDefault();invalidate();});el.addEventListener('close',()=>{if(active===d)invalidate();});controls(d);await operate(d,'refresh');
  }};
 }};
})(window);
