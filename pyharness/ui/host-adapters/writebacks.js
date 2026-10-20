/* Explicit targets, frozen three-way previews, and observed file outcomes. */
(function(global){
 'use strict';
 global.PyharnessWritebacks={create({main,api,esc}){
  const c=global.PyharnessWorkspaceContracts,memory=new Map();let active=null;
  const spec={grant:['writeback_grant','grant_id','wg','writeback-grants'],revoke:['writeback_grant','grant_id','wg','writeback-grants'],plan:['merge_plan','plan_id','mp','merge-plans'],apply:['writeback_application','application_id','wa','writeback-applications'],export:['merge_export','export_id','we','merge-plan-exports']};
  const hex=()=>crypto.randomUUID().replaceAll('-','').slice(0,16),ref=(v,k)=>Object.fromEntries([k,'revision','digest','source_seq'].map(n=>[n,v[n]]));
  const q=(d,s)=>d.el.querySelector(s),base=d=>'/api/v1/projects/'+d.project.project_id,live=(d,n=d.epoch)=>active===d&&d.el.isConnected&&d.el.open&&n===d.epoch;
  const say=(d,t)=>{if(live(d))q(d,'[data-wb-status]').textContent=t;};
  async function who(){const v=await api('/api/account/me',{cache:'no-store'}),a=v.account;return {realm:JSON.stringify(a?[a.tenant,a.user_id||a.username]:['local',v.username||'local']),subject:a?.user_id||'local-operator',username:a?.username||v.username||'local-operator'};}
  function invalidate(){if(!active)return;const d=active;active=null;d.epoch++;d.el.replaceChildren();d.el.close();d.el.remove();}
  async function check(d,n){if(!live(d,n))throw Error('写回窗口已变化');if((await who()).realm!==d.realm){invalidate();throw Error('账户或团队已变化');}if(!live(d,n))throw Error('写回窗口已变化');}
  function clear(d){d.grant=null;d.package=null;d.acceptance=null;d.sources=null;d.plan=null;d.preview=null;
   for(const s of ['[data-wb-mapping]','[data-wb-preview]','[data-wb-plan]','[data-wb-result]','[data-wb-links]'])q(d,s)?.replaceChildren();
   d.el.querySelectorAll('textarea,input[type=text]').forEach(e=>e.value='');
  }
  async function read(d,n,path,options={}){await check(d,n);try{const value=await api(path,{cache:'no-store',...options});await check(d,n);return value;}
   catch(error){if(live(d,n)){try{await check(d,n);}catch(_){invalidate();throw error;}if(error.status===401||error.status===403){clear(d);say(d,'权限或来源已变化，已清除窗口中的私有内容。');}}throw error;}}
  const slot=d=>'pyharness.writeback.pending.v1:'+JSON.stringify([d.realm,d.project.project_id]);
  function pending(d){let p=memory.get(slot(d));try{p=p||JSON.parse(localStorage.getItem(slot(d)));}catch(_){}
   if(!p||!c.keys(p,'realm project operation id key digest')||p.realm!==d.realm||p.project!==d.project.project_id||!spec[p.operation]||!c.id(p.id,spec[p.operation][2])||!c.hash(p.digest)||!/^[A-Za-z0-9._-]{1,128}$/.test(p.key))return null;
   memory.set(slot(d),p);return p;
  }
  function remember(d,p){memory.set(slot(d),p);try{localStorage.setItem(slot(d),JSON.stringify(p));}catch(_){say(d,'无法保存原提交键，请保持窗口打开以便核对。');}}
  function forget(d,p){if(memory.get(slot(d))?.key===p.key)memory.delete(slot(d));try{if(JSON.parse(localStorage.getItem(slot(d)))?.key===p.key)localStorage.removeItem(slot(d));}catch(_){}}
  const identifier=(d,op)=>pending(d)?.operation===op?pending(d).id:spec[op][2]+'-'+hex();
  function controls(d){if(!live(d))return;const p=pending(d);d.el.querySelectorAll('button,input,select,textarea').forEach(e=>{if(e.dataset.wbAction!=='close')e.disabled=d.busy;});
   q(d,'[data-wb-pending]').hidden=!p;q(d,'[data-wb-pending-key]').textContent=p?'原提交键：'+p.key+'；未知结果先按该键只读核对，不自动重发。':'';
   q(d,'[data-wb-action=save-plan]').disabled=d.busy||d.preview?.status!=='mergeable'||!!p&&p.operation!=='plan';
   q(d,'[data-wb-action=apply]').disabled=d.busy||!d.plan||!q(d,'[name=inspected]').checked||!!p&&p.operation!=='apply';
   q(d,'[data-wb-action=authorize-export]').disabled=d.busy||!d.plan||!!p&&p.operation!=='export';
  }
  async function run(d,fn){if(d.busy||!live(d))return;d.busy=true;const n=d.epoch;controls(d);try{await fn(n);}catch(e){if(live(d,n))say(d,e.message);}finally{if(live(d,n)){d.busy=false;controls(d);}}}
  function choose(el,rows,label,key='resource_id'){const old=el.value;el.innerHTML='<option value="">请选择</option>'+rows.map(v=>`<option value="${esc(v[key])}">${esc(label(v))}</option>`).join('');if(rows.some(v=>v[key]===old))el.value=old;}
  async function inventory(d,n){const [own,delivery]=await Promise.all([read(d,n,base(d)+'/writeback-center'),read(d,n,base(d)+'/delivery-center')]);
   for(const [name,kind] of [['grant','writeback_grant'],['plan','merge_plan'],['application','writeback_application'],['export','merge_export']])choose(q(d,'[name='+name+']'),own.items?.[kind]||[],v=>v.resource_id+' · 第'+v.revision+'版');
   choose(q(d,'[name=package]'),(delivery.items?.delivery_package||[]).filter(v=>v.owned),v=>v.resource_id+' · 第'+v.revision+'版');
   choose(q(d,'[name=acceptance]'),delivery.items?.delivery_acceptance||[],v=>v.resource_id+' · 第'+v.revision+'版');
  }
  function validReceipt(d,r,op,id,digest){const [,key,prefix]=spec[op];return r&&r.project_id===d.project.project_id&&r[key]===id&&c.id(id,prefix)&&c.integer(r.revision,1,2)&&c.hash(r.digest)&&c.integer(r.source_seq)&&r.persisted===true&&r.request_digest===digest;}
  async function validateSaved(d,op,v){
   if(!['plan','apply'].includes(op))return;
   const record={schema_version:1,project_id:d.project.project_id,project_revision:v.project_revision,created_by:d.subject,submission_key:v.submission_key,revision:v.revision};
   if(op==='plan'){
    record.selection={schema_version:1,project_revision:v.project_revision,grant_ref:v.grant_ref,package_ref:v.package_ref,acceptance_ref:v.acceptance_ref,files:v.mappings,plan_id:v.plan_id,confirmation_digest:v.confirmation_digest};
    Object.assign(record,{merge_version:v.merge_version,files:v.files?.map(f=>Object.fromEntries(['target_path','target','base_digest','delivery_digest','merged_digest','merged_size','action','validation'].map(k=>[k,f[k]]))),basis_digest:v.basis_digest});
    if(!Array.isArray(v.files)||!v.files.length||v.basis_digest!==v.confirmation_digest||await c.digest(record)!==v.digest)throw Error('固定方案内容与持久摘要不符，不能批准应用。');
    const expected=await c.digest({operation:'writeback.apply',actor:d.subject,plan_ref:ref(v,'plan_id'),basis_digest:v.basis_digest});
    if(v.approval_digest!==expected)throw Error('批准摘要与固定方案不符。');
   }else{
    record.selection={schema_version:1,application_id:v.application_id,plan_ref:v.plan_ref,approval_digest:v.approval_digest};
    Object.assign(record,{origin_key:v.origin_key,status:v.record_status,effects:v.effects});
    if(await c.digest(record)!==v.digest||v.complete!==(v.status==='completed')||v.status==='completed'&&(!v.effects.length||v.effects.some(e=>e.status!=='verified'||e.observed_digest!==e.expected_digest)))throw Error('实际结果与持久摘要不符，未认定成功。');
   }
  }
  async function reconcile(d,n){const p=pending(d);if(!p){say(d,'没有待核对的提交。');return;}
   const value=await read(d,n,base(d)+'/writeback-submissions/by-key?key='+encodeURIComponent(p.key)),r=value.receipt;
   if(value.status!=='committed'){say(d,'原键暂未查到提交。线索已保留；仅相同内容可用原键手动重试。');return;}
   if(!r||r.resource_kind!==spec[p.operation][0]||r.resource_id!==p.id||r.request_digest!==p.digest||r.persisted!==true||!c.hash(r.digest)||!c.integer(r.source_seq))throw Error('原键回执与当前命令不符；保留线索，不自动重发。');
   const path=spec[p.operation][3],saved=await read(d,n,base(d)+'/'+path+'/'+p.id);
   if(!validReceipt(d,saved,p.operation,p.id,p.digest))throw Error('完整回执与原命令不符；保留线索。');
   await validateSaved(d,p.operation,saved);showSaved(d,p.operation,saved);if(p.operation!=='apply'||saved.status!=='unknown')forget(d,p);
   await inventory(d,n);say(d,saved.status==='unknown'?'已核对到持久应用意图，实际结果仍未知。该方案不能重新应用。':'已按原提交键核对持久结果，未重新执行。');
  }
  async function mutate(d,n,op,body,id){const command=op==='revoke'?{...body,grant_id:id}:body,digest=await c.digest({operation:'writeback.'+op,project_id:d.project.project_id,command});
   let p=pending(d);if(p&&(p.operation!==op||p.id!==id||p.digest!==digest))throw Error('上一提交结果未确认；请先核对原键，不能改变内容或生成新键。');
   p=p||{realm:d.realm,project:d.project.project_id,operation:op,id,key:'writeback-'+hex(),digest};remember(d,p);
   let value;try{value=await read(d,n,base(d)+'/'+spec[op][3]+(op==='revoke'?'/'+id+'/revoke':''),{method:'POST',body:JSON.stringify(body),headers:{'Idempotency-Key':p.key}});}
   catch(e){if(e.status&&e.status<500&&![401,403].includes(e.status))forget(d,p);throw e;}
   if(!validReceipt(d,value,op,id,digest))throw Error('服务回执与固定命令不符；保留原键核对，未认定成功。');
   await validateSaved(d,op,value);showSaved(d,op,value);if(op!=='apply'||value.status!=='unknown')forget(d,p);await inventory(d,n);return value;
  }
  function showSaved(d,op,v){
   if(op==='plan'){d.plan=v;q(d,'[data-wb-plan]').innerHTML=`<h3>固定方案 ${esc(v.plan_id)}</h3><p>每次应用前仍复查授权和目标；新预览不替换本方案。</p>${fileDetails(v.files)}<details><summary>批准所绑定的完整摘要</summary><code style="overflow-wrap:anywhere">${esc(v.approval_digest)}</code></details>`;q(d,'[name=inspected]').checked=false;}
   if(op==='apply'){const names={completed:'全部目标已核验完成',partial:'仅部分目标应用，未认定全部完成',failed:'应用未完成',unknown:'实际结果未知，禁止重放'};
    if(v.complete!==(v.status==='completed'))throw Error('应用完成标志不一致，保留原键核对。');
    q(d,'[data-wb-result]').innerHTML=`<h3>${esc(names[v.status]||'结果待核对')}</h3><p>${esc(v.application_id)}</p>${(v.effects||[]).map(e=>`<p>${esc(e.target_path)}：${esc(e.status)} · ${esc(e.observed_digest||'尚未获得结果摘要')}</p>`).join('')}`;
   }
   if(op==='grant'||op==='revoke')say(d,op==='grant'?'目标范围已明确授权；尚未写入任何目标文件。':'写回授权已撤回；历史事实保留。');
   if(op==='export')say(d,'已单独授权本方案的合并结果导出；请读取导出授权后下载。');
  }
  function fileDetails(files){return (files||[]).map(f=>`<article style="border-top:1px solid var(--line);padding:12px 0"><strong>${esc(f.target_path)}</strong><p>${esc(f.action)} · 固定验证 ${esc(f.validation?.status||'待检查')}</p><small>基线 ${esc(f.base_digest||'不存在')}<br>目标 ${esc(f.target?.sha256||'不存在')}<br>合并 ${esc(f.merged_digest||'删除')}</small>${f.conflicts?.length?'<p>存在冲突，不能应用。</p>':''}${f.diff?'<pre style="white-space:pre-wrap;overflow-wrap:anywhere">'+esc(f.diff)+'</pre>':''}</article>`).join('');}
  async function loadSources(d,n){const grantId=q(d,'[name=grant]').value,packageId=q(d,'[name=package]').value,acceptanceId=q(d,'[name=acceptance]').value;
   if(!grantId||!packageId||!acceptanceId)throw Error('请选择目标授权、固定交付包和独立验收记录。');
   const [grant,pkg,accepted,sources]=await Promise.all([read(d,n,base(d)+'/writeback-grants/'+grantId),read(d,n,base(d)+'/delivery-packages/'+packageId),read(d,n,base(d)+'/delivery-acceptances/'+acceptanceId),read(d,n,base(d)+'/delivery-packages/'+packageId+'/merge-source-options')]);
   if(accepted.business_acceptance!=='passed'||c.canonical(accepted.package_ref)!==c.canonical(ref(pkg,'package_id'))||c.canonical(sources.package_ref)!==c.canonical(ref(pkg,'package_id'))||grant.status!=='granted')throw Error('所选交付、验收或写回授权已不匹配。');
   d.grant=grant;d.package=pkg;d.acceptance=accepted;d.sources=sources;d.preview=null;d.plan=null;q(d,'[data-wb-plan]').replaceChildren();q(d,'[data-wb-preview]').replaceChildren();
   q(d,'[data-wb-mapping]').innerHTML=`<p>目标目录：${esc(grant.root_path)}；仅可写明确列出的相对路径。</p>`+grant.paths.map((path,i)=>`<fieldset data-wb-file="${i}"><legend><label><input type="checkbox" data-wb-include> ${esc(path)}</label></legend><label>输入基线 <select data-wb-baseline><option value="">新增文件（没有输入基线）</option>${sources.baseline_files.map(f=>`<option value="${esc(f.path)}" ${f.path===path?'selected':''}>${esc(f.path)}</option>`).join('')}</select></label><label>交付结果 <select data-wb-output><option value="">请选择固定产物或实际删除收据</option>${sources.outputs.map((o,j)=>`<option value="o-${j}">${esc(o.kind==='message'?'公开最终文本':o.artifact_id)} · ${o.size_bytes} 字节</option>`).join('')}${sources.deletions.map((o,j)=>`<option value="d-${j}">删除 ${esc(o.path)} · 实际收据 ${o.source_seq}</option>`).join('')}</select></label></fieldset>`).join('');
   say(d,'已读取固定版本。请选择具体文件映射，再生成只读预览。');
  }
  function mergeBody(d){if(!d.grant||!d.package||!d.acceptance||!d.sources)throw Error('请先读取并核对三个固定版本。');const files=[];
   for(const row of d.el.querySelectorAll('[data-wb-file]')){if(!row.querySelector('[data-wb-include]').checked)continue;
    const i=Number(row.dataset.wbFile),selected=row.querySelector('[data-wb-output]').value,source=row.querySelector('[data-wb-baseline]').value;
    if(!selected)throw Error('每个选中的目标都必须指定实际交付结果。');
    const index=Number(selected.slice(2)),output=selected.startsWith('o-')?d.sources.outputs[index]:null,deletion=selected.startsWith('d-')?d.sources.deletions[index]:null;
    if(!output&&!deletion)throw Error('固定产物选择已变化。');if(deletion&&source!==deletion.path)throw Error('删除操作须绑定同一路径的输入基线。');
    files.push({target_path:d.grant.paths[i],input_path:source||null,output_ref:output,deletion_ref:deletion});
   }
   if(!files.length)throw Error('请至少选中一个目标文件。');
   return {schema_version:1,project_revision:d.project.revision,grant_ref:ref(d.grant,'grant_id'),package_ref:ref(d.package,'package_id'),acceptance_ref:ref(d.acceptance,'acceptance_id'),files};
  }
  async function action(d,n,name){
   if(name==='refresh'){await inventory(d,n);say(d,'已刷新版本列表；未写入目标或启动任务。');}
   if(name==='reconcile')await reconcile(d,n);
   if(name==='sources')await loadSources(d,n);
   if(name==='preview'){const body=mergeBody(d),value=await read(d,n,base(d)+'/merge-previews',{method:'POST',body:JSON.stringify(body)});if(!c.hash(value.confirmation_digest))throw Error('预览摘要无效');d.preview=value;d.previewBody=c.canonical(body);q(d,'[data-wb-preview]').innerHTML=`<h3>${value.status==='mergeable'?'可合并，尚未批准应用':'有冲突或验证失败，写回已阻塞'}</h3>${fileDetails(value.files)}<details><summary>完整预览摘要</summary><code>${esc(value.confirmation_digest)}</code></details>`;say(d,'三方预览已生成，未写入目标文件。');}
   if(name==='save-plan'){const body=mergeBody(d);if(d.preview?.status!=='mergeable'||c.canonical(body)!==d.previewBody)throw Error('选择已变化，请重新预览。');const id=identifier(d,'plan');await mutate(d,n,'plan',{...body,plan_id:id,confirmation_digest:d.preview.confirmation_digest},id);say(d,'不可变方案已保存，尚未应用。');}
   if(name==='read-plan'){const id=q(d,'[name=plan]').value;if(!id)throw Error('请选择方案。');const value=await read(d,n,base(d)+'/merge-plans/'+id);await validateSaved(d,'plan',value);showSaved(d,'plan',value);say(d,'已读取固定方案，应用前仍会复查目标与权限。');}
   if(name==='apply'){if(!d.plan||!q(d,'[name=inspected]').checked)throw Error('请先核对固定方案。');const id=identifier(d,'apply');await mutate(d,n,'apply',{schema_version:1,application_id:id,plan_ref:ref(d.plan,'plan_id'),approval_digest:d.plan.approval_digest},id);}
   if(name==='read-application'){const id=q(d,'[name=application]').value;if(!id)throw Error('请选择应用记录。');const value=await read(d,n,base(d)+'/writeback-applications/'+id);await validateSaved(d,'apply',value);showSaved(d,'apply',value);}
   if(name==='authorize-export'){if(!d.plan)throw Error('请先读取固定方案。');const id=identifier(d,'export');await mutate(d,n,'export',{schema_version:1,export_id:id,plan_ref:ref(d.plan,'plan_id')},id);}
   if(name==='read-export'){const id=q(d,'[name=export]').value;if(!id)throw Error('请选择导出授权。');const e=await read(d,n,base(d)+'/merge-plan-exports/'+id);if(e.export_authorized!==true)throw Error('导出授权无效');const p=await read(d,n,base(d)+'/merge-plans/'+e.plan_ref.plan_id);await validateSaved(d,'plan',p);if(c.canonical(ref(p,'plan_id'))!==c.canonical(e.plan_ref))throw Error('导出方案版本不符');q(d,'[data-wb-links]').innerHTML=p.files.map((f,i)=>f.merged_digest?`<p><a download href="${esc(base(d)+'/merge-plan-exports/'+id+'/files/'+i)}">下载 ${esc(f.target_path)}</a></p>`:`<p>${esc(f.target_path)}：删除操作，没有文件可下载。</p>`).join('');}
   if(name==='revoke'){const id=q(d,'[name=grant]').value;if(!id)throw Error('请选择写回授权。');const value=await read(d,n,base(d)+'/writeback-grants/'+id);await mutate(d,n,'revoke',{schema_version:1,expected_revision:value.revision},id);}
  }
  return {invalidate,async open(project,trigger){invalidate();const identity=await who();if(project.archived)throw Error('归档项目不能写回');const el=document.createElement('dialog');el.id='writebackDialog';el.style.cssText='width:calc(100vw - 40px);max-width:1100px;max-height:92vh;overflow:auto;border:1px solid var(--line,#40506b);border-radius:14px;padding:24px;font:inherit';
   el.innerHTML=`<style>#writebackDialog .wb-columns{display:grid;grid-template-columns:1fr 1fr;gap:24px}#writebackDialog .wb-columns>section{min-width:0}#writebackDialog h2{font-size:20px;line-height:1.5;margin:0 0 8px}#writebackDialog h3{font-size:16px;line-height:1.5;margin:20px 0 12px}#writebackDialog p{line-height:1.65;margin:10px 0;overflow-wrap:anywhere}#writebackDialog small,#writebackDialog strong{overflow-wrap:anywhere}#writebackDialog button{margin:4px 4px 4px 0}#writebackDialog textarea{min-height:100px;height:110px;font:inherit}#writebackDialog label{display:block;margin:10px 0}#writebackDialog input,#writebackDialog textarea,#writebackDialog select{max-width:100%;font:inherit}#writebackDialog input[type=text],#writebackDialog textarea{width:95%}#writebackDialog fieldset{margin:12px 0;border:1px solid var(--line,#40506b)}@media(max-width:850px){#writebackDialog .wb-columns{grid-template-columns:1fr}}</style><button type="button" data-wb-action="close" style="float:right">关闭</button><h2>三方合并与安全写回 · ${esc(project.name)}</h2><p>固定输入基线 → 当前目标 → 已验收交付。先预览和核对，再明确批准应用；读取和导出不会自动写回。</p><p role="status" data-wb-status></p><aside data-wb-pending hidden><p data-wb-pending-key></p><button type="button" data-wb-action="reconcile">只读核对原提交键</button></aside><button type="button" data-wb-action="refresh">刷新版本列表</button><div class="wb-columns"><section><h3>1. 明确授权目标范围</h3><form data-wb-grant-form><label>目标目录的绝对路径 <input type="text" name="rootPath" required maxlength="1024"></label><label>接收写回权限的用户名 <input type="text" name="recipient" required maxlength="128" value="${esc(identity.username)}"></label><label>允许的相对文件路径，每行一个 <textarea name="paths" required rows="4"></textarea></label><label>授权到期时间 <input type="datetime-local" name="expiry" required></label><label><input type="checkbox" name="replace" checked> 替换已有文件</label><label><input type="checkbox" name="create"> 创建新文件（父目录须已存在）</label><label><input type="checkbox" name="delete"> 删除有实际删除收据的文件</label><button type="submit">仅保存目标授权</button></form><h3>2. 选择固定版本并预览</h3><label>写回授权 <select name="grant"></select></label><button type="button" data-wb-action="revoke">撤回所选写回授权</button><label>我创建的固定交付包 <select name="package"></select></label><label>独立验收记录 <select name="acceptance"></select></label><button type="button" data-wb-action="sources">读取版本与实际可选来源</button><div data-wb-mapping></div><button type="button" data-wb-action="preview">只读三方预览</button><div data-wb-preview></div><button type="button" data-wb-action="save-plan">保存固定合并方案</button></section><section><h3>3. 核对方案与实际应用</h3><label>已有固定方案 <select name="plan"></select></label><button type="button" data-wb-action="read-plan">读取固定方案</button><div data-wb-plan></div><label><input type="checkbox" name="inspected"> 我已核对本方案的目标范围、差异和完整摘要</label><button type="button" data-wb-action="apply">批准并申请应用所选方案</button><p>目标变化、授权或验收失效会阻塞。多文件出错将保留部分或未知收据，不自动重放或回滚。</p><label>应用记录 <select name="application"></select></label><button type="button" data-wb-action="read-application">只读核对实际结果</button><div data-wb-result></div><h3>4. 单独授权导出</h3><button type="button" data-wb-action="authorize-export">授权导出当前固定方案</button><label>导出授权 <select name="export"></select></label><button type="button" data-wb-action="read-export">读取授权并显示下载链接</button><div data-wb-links></div></section></div>`;
   main.append(el);const d={el,project,trigger,realm:identity.realm,subject:identity.subject,epoch:1,busy:false,preview:null,plan:null};active=d;el.showModal();q(d,'[name=expiry]').value=new Date(Date.now()+3600000-new Date().getTimezoneOffset()*60000).toISOString().slice(0,16);
   el.addEventListener('cancel',e=>{e.preventDefault();invalidate();trigger?.focus();});el.addEventListener('close',()=>{if(active===d)invalidate();});
   el.addEventListener('input',e=>{if(e.target.closest('[data-wb-mapping]')){d.preview=null;q(d,'[data-wb-preview]').replaceChildren();}controls(d);});
   el.addEventListener('change',e=>{if(['grant','package','acceptance'].includes(e.target.name)){d.preview=null;d.plan=null;d.sources=null;q(d,'[data-wb-mapping]').replaceChildren();q(d,'[data-wb-preview]').replaceChildren();q(d,'[data-wb-plan]').replaceChildren();}controls(d);});
   el.addEventListener('click',e=>{const button=e.target.closest('[data-wb-action]');if(!button)return;if(button.dataset.wbAction==='close'){invalidate();trigger?.focus();return;}run(d,n=>action(d,n,button.dataset.wbAction));});
   q(d,'[data-wb-grant-form]').addEventListener('submit',e=>{e.preventDefault();run(d,async n=>{const f=e.target,id=identifier(d,'grant'),actions=['replace','create','delete'].filter(k=>f.elements[k].checked),paths=f.elements.paths.value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean);if(!actions.length||!paths.length)throw Error('请明确至少一个路径和一种写回操作。');const body={schema_version:1,grant_id:id,project_revision:project.revision,root_path:f.elements.rootPath.value,recipient_username:f.elements.recipient.value,paths,actions,expires_at:new Date(f.elements.expiry.value).toISOString()};await mutate(d,n,'grant',body,id);});});
   await run(d,n=>inventory(d,n));controls(d);
  }};
 }};
})(window);
