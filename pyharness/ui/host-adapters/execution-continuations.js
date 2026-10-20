/* Explicit new attempts; references-only recovery hints never confer authority. */
(function(global){'use strict';
 global.PyharnessContinuations={create({main,api,onOpenAttempt=null}){
  const c=global.PyharnessWorkspaceContracts,memory=new Map();let current=null;
  const same=(a,b)=>c.canonical(a)===c.canonical(b),nonce=()=>crypto.randomUUID().replaceAll('-','').slice(0,16);
  const q=(d,s)=>d.el.querySelector(s),base=d=>'/api/v1/projects/'+d.project.project_id;
  const live=(d,n=d.version)=>current===d&&d.el.isConnected&&d.el.open&&d.version===n;
  const reference=r=>c.keys(r,'execution_id revision digest source_seq')&&c.id(r.execution_id,'ex')&&r.revision===1&&c.hash(r.digest)&&c.integer(r.source_seq);
  const slot=d=>'pyharness.continuation.pending.v1:'+JSON.stringify([d.realm,d.project.project_id,d.reference.execution_id]);
  const say=(d,text)=>{if(live(d))q(d,'[data-co-status]').textContent=text;};
  const labels={execution_live:'原执行仍在运行',execution_completed:'原执行已完成',journal_unconfirmed:'原日志未确认',child_journal_unconfirmed:'子任务日志未确认',journal_scope_truncated:'日志范围超限',artifact_scope_truncated:'产物范围超限',
   unknown_effects:'存在未确认的副作用或产物',security_refusal:'存在安全拒绝',checkpoint_missing:'没有已确认检查点',continuation_not_implemented:'该版本未提供续做',journal_writer_active:'原日志仍有写入者',state_unavailable:'没有可用状态映像',
   state_integrity_unconfirmed:'状态映像校验未确认',state_filesystem_incomplete:'旧映像缺少完整文件或目录状态',state_materials_changed:'原资料版本已变化',checkpoint_superseded:'检查点之后已有新结果或动作',configuration_changed:'执行配置已变化',
   configuration_unavailable:'当前执行配置不可用',delegated_state_not_supported:'该子任务状态暂不支持续做',pending_correction:'还有未纳入检查点的补充',unsupported_effect_state:'有尚不支持恢复的工具状态',continuation_contract_missing:'旧映像未固定续做契约',
   budget_observation_unconfirmed:'请求用量或费用估算未确认',budget_exhausted:'剩余额度不足',round_limit_exhausted:'剩余轮数不足',runtime_contract_changed:'程序版本已变化',runtime_contract_unconfirmed:'程序版本未确认',auxiliary_state_not_supported:'附加技能或知识状态暂不支持恢复',
   continuation_chain_not_supported:'当前版本不支持继续恢复这段来源链',lineage_unconfirmed:'较早执行的来源链未确认',checkpoint_reserved:'该检查点已预留了新尝试'};
  async function identity(){const w=await api('/api/account/me',{cache:'no-store'}),a=w.account;return JSON.stringify(a?[a.tenant,a.user_id||a.username]:['local',w.username||'local']);}
  function invalidate(){if(!current)return;const d=current;current=null;d.version++;d.el.replaceChildren();d.el.close();d.el.remove();d.trigger?.focus();}
  async function check(d,n){if(!live(d,n))throw Error('窗口已变化');if(await identity()!==d.realm){if(current===d)invalidate();throw Error('主体已变化');}if(!live(d,n))throw Error('窗口已变化');}
  async function request(d,n,url,options={}){await check(d,n);try{const v=await api(url,{cache:'no-store',...options});await check(d,n);return v;}catch(e){await check(d,n);throw e;}}
  function validHint(d,h){return c.keys(h,'realm project execution_ref key body request_digest')&&h.realm===d.realm&&h.project===d.project.project_id&&reference(h.execution_ref)&&same(h.execution_ref,d.reference)
   &&typeof h.key==='string'&&/^[A-Za-z0-9._-]{1,128}$/.test(h.key)&&c.hash(h.request_digest)&&c.keys(h.body,'schema_version execution_ref plan_digest attempt_id execution_id confirm_host_boundary')
   &&h.body.schema_version===1&&reference(h.body.execution_ref)&&same(h.body.execution_ref,d.reference)&&c.hash(h.body.plan_digest)&&c.id(h.body.attempt_id,'ra')&&c.id(h.body.execution_id,'ex')
   &&h.body.execution_id!==d.reference.execution_id&&typeof h.body.confirm_host_boundary==='boolean';}
  function recover(d){let h=memory.get(slot(d));try{const raw=localStorage.getItem(slot(d));if(!h&&raw&&raw.length<=8192)h=JSON.parse(raw);}catch(_){}return validHint(d,h)?h:null;}
  function save(d,h){memory.set(slot(d),h);try{localStorage.setItem(slot(d),JSON.stringify(h));return true;}catch(_){return false;}}
  function forget(d,h){if(memory.get(slot(d))?.key===h.key)memory.delete(slot(d));try{if(JSON.parse(localStorage.getItem(slot(d)))?.key===h.key)localStorage.removeItem(slot(d));}catch(_){}}
  const fingerprint=(d,body)=>c.digest({operation:'execution.continue',project_id:d.project.project_id,command:body});
  function publicNumbers(v){if(typeof v==='number'){if(!Number.isFinite(v))throw Error('无效数值');const b=new ArrayBuffer(8);new DataView(b).setFloat64(0,v,false);return {$number:Array.from(new Uint8Array(b),x=>x.toString(16).padStart(2,'0')).join('')};}
   if(Array.isArray(v))return v.map(publicNumbers);if(v&&typeof v==='object')return Object.fromEntries(Object.entries(v).map(([k,x])=>[k,publicNumbers(x)]));return v;}
  function safePlan(d,p){return p?.schema_version===1&&p.project_id===d.project.project_id&&reference(p.execution_ref)&&same(p.execution_ref,d.reference)&&c.hash(p.plan_digest)&&c.hash(p.recovery_plan_digest)
   &&p.continuation_authorized===false&&p.automatic_dispatch===false&&p.replay_authorized===false&&p.atomic_snapshot===false&&typeof p.can_continue==='boolean'&&typeof p.state_available==='boolean'
   &&Array.isArray(p.blockers)&&p.blockers.length<=32&&p.blockers.every(b=>Object.hasOwn(labels,b))&&Array.isArray(p.journals)&&p.journals.length<=401&&Array.isArray(p.lineage_journals)&&p.lineage_journals.length<=20
   &&(!p.can_continue||(p.blockers.length===0&&p.journal_writer_active===false&&p.state_available&&p.state&&c.hash(p.state.state_digest)&&p.state.integrity==='verified'&&p.budget&&c.integer(p.budget.remaining_rounds)
    &&c.integer(p.budget.remaining_limits?.max_in_tokens)&&c.integer(p.budget.remaining_limits?.max_out_tokens)&&Number.isFinite(p.budget.remaining_limits?.max_cost_yuan)&&p.budget.remaining_limits.max_cost_yuan>0));}
  function clear(d){d.plan=null;d.committed=null;q(d,'[data-co-summary]').textContent='';q(d,'[data-co-budget]').textContent='';q(d,'[data-co-result]').replaceChildren();q(d,'[data-co-details]').textContent='';}
  function controls(d){if(!live(d))return;const h=recover(d);d.el.querySelectorAll('button,input').forEach(e=>{if(!e.hasAttribute('data-co-close'))e.disabled=d.busy;});
   q(d,'[data-co-submit]').disabled=d.busy||!d.plan?.can_continue||!!h||!!d.committed||(d.boundary==='host_unrestricted'&&!q(d,'[data-co-host]').checked);
   q(d,'[data-co-retry]').disabled=d.busy||!h;q(d,'[data-co-open]').disabled=d.busy||!d.committed||!onOpenAttempt;
   q(d,'[data-co-hint]').textContent=h?'原提交键待核对：'+h.key+'。不会自动另建尝试。':'';}
  async function refresh(d,n){
   clear(d);const p=await request(d,n,base(d)+'/executions/'+d.reference.execution_id+'/continuation-plan');
   if(!safePlan(d,p))throw Error('续做预检范围或能力不符');const {plan_digest,...basis}=p;if(await c.digest(publicNumbers(basis))!==plan_digest)throw Error('续做预检摘要不符');await check(d,n);
   const root=await request(d,n,base(d)+'/executions/'+d.reference.execution_id);
   if(root?.project_id!==d.project.project_id||!same({execution_id:root.execution_id,revision:root.revision,digest:root.digest,source_seq:root.source_seq},d.reference)||!['disabled','host_unrestricted','container','remote'].includes(root.boundary))throw Error('原执行边界不符');
   d.plan=p;d.boundary=root.boundary;q(d,'[data-co-host-label]').hidden=d.boundary!=='host_unrestricted';
   q(d,'[data-co-summary]').textContent=p.can_continue?'已核对检查点。可显式创建新尝试；原执行与已完成动作保留。':'当前不能续做：'+p.blockers.map(b=>labels[b]).join('；');
   if(p.budget){const b=p.budget.remaining_limits;q(d,'[data-co-budget]').textContent='可用上限：输入 '+b.max_in_tokens+' token，输出 '+b.max_out_tokens+' token，费用估算 ¥'+b.max_cost_yuan+'。已为可能未确认的请求预留额度。';}
   q(d,'[data-co-details]').textContent=JSON.stringify(p,null,2);const h=recover(d);if(h)q(d,'[data-co-key]').value=h.key;
   say(d,'已只读核对，没有派发、修复或重发。提交时服务还会重新核对来源、版本和独占租约。');controls(d);
  }
  function receipt(d,e){return e?.schema_version===1&&e.project_id===d.project.project_id&&c.id(e.execution_id,'ex')&&e.execution_id!==d.reference.execution_id&&e.revision===1&&c.hash(e.digest)&&c.integer(e.source_seq)
   &&e.attempt_ref&&c.id(e.attempt_ref.attempt_id,'ra')&&e.attempt_ref.revision===1&&c.hash(e.attempt_ref.digest)&&c.integer(e.attempt_ref.source_seq)&&typeof e.session_id==='string'&&/^s-pa-[0-9a-f]{32}$/.test(e.session_id)
   &&e.persisted===true&&e.execution_authorized===true&&c.hash(e.request_digest)&&e.continuation&&same(e.continuation.execution_ref,d.reference)&&c.hash(e.continuation.plan_digest);}
  async function reconcile(d,n,key){
   const h=recover(d),v=await request(d,n,base(d)+'/continuation-submissions/by-key?key='+encodeURIComponent(key));
   if(v?.schema_version!==1||v.project_id!==d.project.project_id||v.submission_key!==key||v.automatic_dispatch!==false)throw Error('原键查询范围不符');
   if(v.status==='not_found'&&v.execution===null){say(d,'尚未找到已确认授权。保留原键，只能显式原键重试；没有自动重发。');return;}
   if(v.status==='attempt_reserved'&&v.execution===null){say(d,'新尝试预留已确认，执行授权仍未确认。保留原键，没有重新派发。');return;}
   const e=v.execution;if(v.status!=='committed'||!receipt(d,e)||e.submission_key!==key)throw Error('续做授权回执不符');
   if(h?.key===key&&(!same(e.continuation.execution_ref,h.body.execution_ref)||e.execution_id!==h.body.execution_id||e.attempt_ref.attempt_id!==h.body.attempt_id
      ||e.continuation.plan_digest!==h.body.plan_digest||e.request_digest!==h.request_digest||await fingerprint(d,h.body)!==h.request_digest))throw Error('原键与原选择摘要不符');
   const result=await request(d,n,base(d)+'/executions/'+e.execution_id);
   if(!receipt(d,result)||result.digest!==e.digest||result.source_seq!==e.source_seq||result.session_id!==e.session_id||!same(result.attempt_ref,e.attempt_ref))throw Error('实际新执行回执不符');
   if(h?.key===key)forget(d,h);d.committed=result;
   q(d,'[data-co-result]').textContent='新尝试 '+result.attempt_ref.attempt_id+'，技术状态：'+result.status+'。这不代表独立业务验收通过。';
   say(d,'原提交键与新执行已精确核对，没有重新派发已确认动作。');controls(d);
  }
  async function send(d,n,h){
   if(!validHint(d,h)||await fingerprint(d,h.body)!==h.request_digest)throw Error('原提交线索摘要不符');await check(d,n);
   const durable=save(d,h);q(d,'[data-co-key]').value=h.key;say(d,'正在提交新尝试。'+(durable?'':'浏览器线索未能持久保存，请记下原键：'+h.key));
   await request(d,n,base(d)+'/executions/'+d.reference.execution_id+'/continuations',{method:'POST',body:JSON.stringify(h.body),headers:{'Idempotency-Key':h.key}});
   await reconcile(d,n,h.key);
  }
  async function operate(d,action){
   if(!live(d)||d.busy||!d.realm)return;const n=++d.version;d.busy=true;controls(d);
   try{
    await check(d,n);
    if(action==='refresh')await refresh(d,n);
    else if(action==='submit'){
     if(!d.plan?.can_continue||recover(d)||d.committed)throw Error('先核对续做条件或原键');
     if(d.boundary==='host_unrestricted'&&!q(d,'[data-co-host]').checked)throw Error('请先确认宿主执行边界');
     const body={schema_version:1,execution_ref:d.reference,plan_digest:d.plan.plan_digest,attempt_id:'ra-'+nonce(),execution_id:'ex-'+nonce(),confirm_host_boundary:q(d,'[data-co-host]').checked};
     const h={realm:d.realm,project:d.project.project_id,execution_ref:d.reference,key:'continue-'+nonce(),body,request_digest:await fingerprint(d,body)};await send(d,n,h);
    }else if(action==='query'){
     const key=q(d,'[data-co-key]').value.trim();if(!/^[A-Za-z0-9._-]{1,128}$/.test(key))throw Error('请输入合法原提交键');await reconcile(d,n,key);
    }else if(action==='retry'){const h=recover(d);if(!h)throw Error('没有经过核对的原提交线索');await send(d,n,h);}
    else if(action==='open'){if(!d.committed||!onOpenAttempt)throw Error('新尝试尚未确认');await check(d,n);await onOpenAttempt(d.project,d.committed.attempt_ref.attempt_id);}
   }catch(e){if(live(d,n)){if(e.status===401||e.status===403)clear(d);say(d,'操作未确认：'+e.message+'。保留原键，不自动重发或另建尝试。');}}
   finally{if(live(d,n)){d.busy=false;controls(d);}}
  }
  async function open(project,ref,trigger=null){
   invalidate();if(!c.id(project?.project_id,'p')||!reference(ref))throw Error('续做目标不完整');
   const el=document.createElement('dialog'),d={el,project,reference:ref,trigger,realm:'',version:0,busy:false,plan:null,boundary:null,committed:null};current=d;
   el.setAttribute('aria-label','断点续做');el.style.cssText='width:min(900px,94vw);max-height:90vh;overflow:auto;padding:24px;font:inherit;box-sizing:border-box';
   el.innerHTML='<h2 data-co-title></h2><button data-co-close>关闭</button> <button data-co-refresh disabled>只读刷新</button><p data-co-status role="status"></p><p data-co-summary></p><p data-co-budget></p><label data-co-host-label hidden><input type="checkbox" data-co-host>我确认使用宿主执行：不提供操作系统、网络或内存隔离</label><p><button data-co-submit disabled>创建新尝试并继续</button></p><p data-co-hint></p><label>原提交键 <input data-co-key maxlength="128" style="max-width:100%;box-sizing:border-box"></label><p><button data-co-query>只读查询原键</button> <button data-co-retry disabled>按原键重试</button></p><section data-co-result></section><p><button data-co-open disabled>查看新尝试</button></p><details><summary>检查点与定位信息</summary><pre data-co-details style="white-space:pre-wrap;overflow-wrap:anywhere"></pre></details>';
   q(d,'[data-co-title]').textContent='断点续做 · '+project.name;main.append(el);el.showModal();el.oncancel=e=>{e.preventDefault();invalidate();};
   q(d,'[data-co-close]').onclick=invalidate;q(d,'[data-co-host]').onchange=()=>controls(d);
   for(const action of ['refresh','submit','query','retry','open'])q(d,'[data-co-'+action+']').onclick=()=>operate(d,action);
   try{const n=d.version,realm=await identity();if(!live(d,n))return;d.realm=realm;d.busy=true;controls(d);await refresh(d,n);}
   catch(e){if(live(d))say(d,'预检未确认：'+e.message);}
   finally{if(live(d)){d.busy=false;controls(d);}}
  }
  return {open,invalidate};
 }};
})(window);
