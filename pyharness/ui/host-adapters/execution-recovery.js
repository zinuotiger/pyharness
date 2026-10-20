/* Read-only diagnostics, scoped to one exact execution and current identity. */
(function(global){'use strict';
 global.PyharnessRecoveryPlans={create({main,api,esc,onOpenAttempt=null}){
  const c=global.PyharnessWorkspaceContracts;let current=null,repairPanel=null,continuationPanel=null;
  const live=(d,n=d.version)=>current===d&&d.el.isConnected&&d.el.open&&d.version===n;
  const q=(d,s)=>d.el.querySelector(s);
  const same=(a,b)=>c.canonical(a)===c.canonical(b);
  async function identity(){const who=await api('/api/account/me',{cache:'no-store'}),a=who.account;return JSON.stringify(a?[a.tenant,a.user_id||a.username]:['local',who.username||'local']);}
  function invalidate(){repairPanel?.invalidate();continuationPanel?.invalidate();if(!current)return;const d=current;current=null;d.version++;d.el.replaceChildren();d.el.close();d.el.remove();d.trigger?.focus();}
  async function check(d,n){if(!live(d,n))throw Error('窗口已变化');if(await identity()!==d.realm){if(current===d)invalidate();throw Error('主体已变化');}if(!live(d,n))throw Error('窗口已变化');}
  const labels={execution_live:'原执行仍由当前服务持有，不能另开恢复执行',execution_completed:'原执行已完成',journal_unconfirmed:'原执行日志无法确认',child_journal_unconfirmed:'子任务日志无法确认',journal_scope_truncated:'子任务日志范围超限',artifact_scope_truncated:'产物范围超限',unknown_effects:'存在未确认的副作用或产物字节',security_refusal:'存在安全拒绝，不允许自动修复',checkpoint_missing:'尚无已确认检查点',restore_state_unavailable:'尚无可恢复的完整状态映像',continuation_not_implemented:'该版本尚未接入受控续做',continuation_gate_required:'续做须另行核对条件并显式创建新尝试'};
  async function refresh(d){
   if(!live(d)||d.busy||!d.realm)return;const n=++d.version;d.busy=true;q(d,'[data-rp-refresh]').disabled=true;q(d,'[data-rp-body]').replaceChildren();
   try{
    await check(d,n);const v=await api('/api/v1/projects/'+d.project.project_id+'/executions/'+d.reference.execution_id+'/recovery-plan',{cache:'no-store'});await check(d,n);
    if(v?.schema_version!==1||v.project_id!==d.project.project_id||!same(v.execution_ref,d.reference)||!c.hash(v.plan_digest)
      ||v.continuation_authorized!==false||v.repair_authorized!==false||v.automatic_dispatch!==false||v.atomic_snapshot!==false
      ||typeof v.restore_state_available!=='boolean'||!['pending','available'].includes(v.repair_implementation)||!Array.isArray(v.continuation_blockers)||v.continuation_blockers.length>20
      ||!v.continuation_blockers.every(b=>Object.hasOwn(labels,b))||!Array.isArray(v.journals)||v.journals.length>401||!Array.isArray(v.artifacts)||v.artifacts.length>1000)throw Error('预检回执范围或能力不符');
    const {plan_digest,...basis}=v;if(await c.digest(basis)!==plan_digest)throw Error('预检摘要不符');await check(d,n);
    const status=document.createElement('p');status.textContent='原执行状态：'+v.execution_status+'。本次仅观察，没有恢复、修复或重发。';
    const state=document.createElement('p');state.textContent=v.restore_state_available?'最近检查点的私有状态映像已核对；这不等于当前执行可以续做。':'最近检查点尚无可恢复的完整状态映像。';
    const list=document.createElement('ul');for(const b of v.continuation_blockers){const li=document.createElement('li');li.textContent=labels[b];list.append(li);}
    const index=document.createElement('p');index.textContent='派生索引：'+({current:'与当前内容水位一致',stale:'水位不一致',not_built:'尚未建立',unavailable:'无法只读核对'}[v.index_observation?.state]||'未确认')+'。索引状态不会解除副作用或安全阻塞。';
    const details=document.createElement('details'),summary=document.createElement('summary'),raw=document.createElement('pre');summary.textContent='精确日志、检查点和产物观察依据';raw.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere';raw.textContent=JSON.stringify(v,null,2);details.append(summary,raw);
    q(d,'[data-rp-body]').append(status,state,list,index,details);
    if(global.PyharnessContinuations){const button=document.createElement('button');button.textContent='核对断点续做条件';button.setAttribute('data-rp-continuation','');button.onclick=()=>{continuationPanel=continuationPanel||global.PyharnessContinuations.create({main,api,onOpenAttempt});continuationPanel.open(d.project,d.reference,button);};q(d,'[data-rp-body]').append(button);}
    if(v.repair_implementation==='available'&&global.PyharnessIndexRepairs){const button=document.createElement('button');button.textContent='管理有限索引修复';button.setAttribute('data-rp-repairs','');button.onclick=()=>{repairPanel=repairPanel||global.PyharnessIndexRepairs.create({main,api,esc});repairPanel.open(d.project,d.reference,button);};q(d,'[data-rp-body]').append(button);}
    q(d,'[data-rp-status]').textContent='预检已核对。它是多个精确日志前缀的观察，不是原子快照，也不是执行许可。';
   }catch(e){if(live(d,n)){repairPanel?.invalidate();continuationPanel?.invalidate();q(d,'[data-rp-body]').replaceChildren();q(d,'[data-rp-status]').textContent='预检未确认：'+e.message+'。没有修改日志、索引或执行状态。';}}
   finally{if(live(d,n)){d.busy=false;q(d,'[data-rp-refresh]').disabled=false;}}
  }
  async function open(project,reference,trigger=null){
   invalidate();const el=document.createElement('dialog'),d={el,project,reference,trigger,realm:'',version:0,busy:false};current=d;
   el.setAttribute('aria-label','恢复预检');el.style.cssText='width:min(900px,94vw);max-height:90vh;overflow:auto;padding:24px;font:inherit;box-sizing:border-box';
   el.innerHTML=`<h2>恢复预检 · ${esc(project.name)}</h2><button data-rp-close>关闭</button> <button data-rp-refresh disabled>重新只读核对</button><p data-rp-status role="status"></p><section data-rp-body></section>`;
   main.append(el);el.showModal();el.oncancel=e=>{e.preventDefault();invalidate();};q(d,'[data-rp-close]').onclick=invalidate;q(d,'[data-rp-refresh]').onclick=()=>refresh(d);
   try{const n=d.version,realm=await identity();if(!live(d,n))return;d.realm=realm;await refresh(d);}catch(e){if(live(d))q(d,'[data-rp-status]').textContent='身份核对未完成：'+e.message;}
  }
  return {open,invalidate};
 }};
})(window);
