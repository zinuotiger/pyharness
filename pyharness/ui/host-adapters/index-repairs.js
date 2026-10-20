/* One explicit bounded repair; durable hints hold only exact references and hashes. */
(function(global){'use strict';
 global.PyharnessIndexRepairs={create({main,api,esc}){
  const c=global.PyharnessWorkspaceContracts,memory=new Map();let current=null;
  const same=(a,b)=>c.canonical(a)===c.canonical(b),id=()=>crypto.randomUUID().replaceAll('-','').slice(0,16);
  const q=(d,s)=>d.el.querySelector(s),base=d=>'/api/v1/projects/'+d.project.project_id;
  const live=(d,n=d.version)=>current===d&&d.el.isConnected&&d.el.open&&d.version===n;
  const slot=d=>'pyharness.index-repair.pending.v1:'+JSON.stringify([d.realm,d.project.project_id,d.reference.execution_id]);
  const say=(d,text)=>{if(live(d))q(d,'[data-ir-status]').textContent=text;};
  async function identity(){const w=await api('/api/account/me',{cache:'no-store'}),a=w.account;return JSON.stringify(a?[a.tenant,a.user_id||a.username]:['local',w.username||'local']);}
  function invalidate(){if(!current)return;const d=current;current=null;d.version++;d.el.replaceChildren();d.el.close();d.el.remove();d.trigger?.focus();}
  async function check(d,n){if(!live(d,n))throw Error('窗口已变化');if(await identity()!==d.realm){if(current===d)invalidate();throw Error('主体已变化');}if(!live(d,n))throw Error('窗口已变化');}
  async function request(d,n,url,options={}){await check(d,n);try{const v=await api(url,{cache:'no-store',...options});await check(d,n);return v;}catch(e){await check(d,n);throw e;}}
  function validHint(d,h){return c.keys(h,'realm project execution_ref key body request_digest')&&h.realm===d.realm&&h.project===d.project.project_id&&same(h.execution_ref,d.reference)
   &&typeof h.key==='string'&&/^[A-Za-z0-9._-]{1,128}$/.test(h.key)&&c.hash(h.request_digest)&&c.keys(h.body,'schema_version repair_id execution_ref plan_digest')
   &&h.body.schema_version===1&&typeof h.body.repair_id==='string'&&/^er-[0-9a-f]{16}$/.test(h.body.repair_id)&&same(h.body.execution_ref,d.reference)&&c.hash(h.body.plan_digest);}
  function recover(d){let h=memory.get(slot(d));try{h=h||JSON.parse(localStorage.getItem(slot(d)));}catch(_){}if(validHint(d,h)){memory.set(slot(d),h);return h;}return null;}
  function save(d,h){memory.set(slot(d),h);try{localStorage.setItem(slot(d),JSON.stringify(h));return true;}catch(_){return false;}}
  function forget(d,h){if(memory.get(slot(d))?.key===h.key)memory.delete(slot(d));try{if(JSON.parse(localStorage.getItem(slot(d)))?.key===h.key)localStorage.removeItem(slot(d));}catch(_){}}
  const fingerprint=(d,body)=>c.digest({operation:'execution.index_repair',project_id:d.project.project_id,command:body});
  function safePlan(d,v){return v?.schema_version===1&&v.project_id===d.project.project_id&&same(v.execution_ref,d.reference)&&c.hash(v.plan_digest)
   &&v.continuation_authorized===false&&v.repair_authorized===false&&v.automatic_dispatch===false&&v.atomic_snapshot===false
   &&Array.isArray(v.continuation_blockers)&&Array.isArray(v.journals)&&v.journals.length<=401&&typeof v.unknown_effects==='boolean'
   &&typeof v.journals_truncated==='boolean'&&typeof v.artifacts_truncated==='boolean'&&c.hash(v.index_observation?.scope_digest);}
  function receipt(d,r){return r?.schema_version===1&&r.project_id===d.project.project_id&&same(r.execution_ref,d.reference)&&r.persisted===true
   &&r.automatic_dispatch===false&&r.continuation_authorized===false&&typeof r.repair_id==='string'&&/^er-[0-9a-f]{16}$/.test(r.repair_id)
   &&c.integer(r.revision,1,2)&&c.integer(r.attempt,1,2)&&c.integer(r.source_seq)&&c.hash(r.digest)&&c.hash(r.plan_digest)&&c.hash(r.fault_digest)&&c.hash(r.request_digest)
   &&typeof r.submission_key==='string'&&/^[A-Za-z0-9._-]{1,128}$/.test(r.submission_key)&&['started','succeeded','failed','unknown'].includes(r.phase)
   &&(r.revision===1?r.phase==='started':r.phase!=='started');}
  function eligible(d){const p=d.plan;if(!p||d.truncated||recover(d)||d.rows.filter(r=>r.fault_digest===d.fault).length>=2)return false;
   return p.repair_implementation==='available'&&!p.unknown_effects&&!p.journals_truncated&&!p.artifacts_truncated&&p.index_observation.state!=='current'
    &&!p.continuation_blockers.some(b=>['execution_live','security_refusal','journal_unconfirmed','child_journal_unconfirmed'].includes(b));}
  function controls(d){if(!live(d))return;const h=recover(d);d.el.querySelectorAll('button,input').forEach(e=>{if(!e.hasAttribute('data-ir-close'))e.disabled=d.busy;});
   q(d,'[data-ir-submit]').disabled=d.busy||!eligible(d);q(d,'[data-ir-retry]').disabled=d.busy||!h;
   q(d,'[data-ir-hint]').textContent=h?'原修复键待核对：'+h.key+'。不确定结果不会自动重新修复。':'';}
  function clear(d){d.plan=null;d.rows=[];d.fault=null;q(d,'[data-ir-rows]').replaceChildren();q(d,'[data-ir-plan]').textContent='';}
  async function refresh(d,n){
   clear(d);const p=await request(d,n,base(d)+'/executions/'+d.reference.execution_id+'/recovery-plan');
   if(!safePlan(d,p))throw Error('预检范围或能力不符');const {plan_digest,...basis}=p;if(await c.digest(basis)!==plan_digest)throw Error('预检摘要不符');await check(d,n);
   const v=await request(d,n,base(d)+'/executions/'+d.reference.execution_id+'/index-repairs');
   if(v?.schema_version!==1||v.project_id!==d.project.project_id||!same(v.execution_ref,d.reference)||!Array.isArray(v.repairs)||v.repairs.length>200
    ||!c.integer(v.total,0)||typeof v.truncated!=='boolean'||!v.repairs.every(r=>receipt(d,r))||new Set(v.repairs.map(r=>r.repair_id)).size!==v.repairs.length||(!v.truncated&&v.total!==v.repairs.length))throw Error('修复历史范围不符');
   const fault=await c.digest({operation:'index.repair',execution_ref:p.execution_ref,journals:p.journals,index_scope_digest:p.index_observation.scope_digest});await check(d,n);
   d.plan=p;d.rows=v.repairs;d.truncated=v.truncated;d.fault=fault;
   const count=d.rows.filter(r=>r.fault_digest===fault).length;
   q(d,'[data-ir-plan]').textContent='派生索引观察：'+p.index_observation.state+'。'+(v.truncated?'历史已截断，禁止据此开始新修复。':'当前同一故障已记录 '+count+' / 2 次尝试。')+' 执行前还须取得精确原日志的独占租约。';
   for(const r of d.rows){const line=document.createElement('p');line.textContent='尝试 '+r.attempt+' · '+({started:'开始已记录，结果未确认',succeeded:'索引水位已核对',failed:'修复失败',unknown:'结果未知'}[r.phase])+' · 原键 '+r.submission_key;q(d,'[data-ir-rows]').append(line);}
   say(d,'已只读核对。原始日志、安全拒绝和未知外部效果不会被索引修复改写或解除。');controls(d);
  }
  async function query(d,n,key){
   if(!/^[A-Za-z0-9._-]{1,128}$/.test(key))throw Error('请输入合法原修复键');
   const h=recover(d),v=await request(d,n,base(d)+'/index-repair-submissions/by-key?key='+encodeURIComponent(key));
   if(v?.project_id!==d.project.project_id||v.submission_key!==key)throw Error('原键查询范围不符');
   if(v.status==='not_found'&&v.repair===null){say(d,'尚未找到已确认开始事实。保留原键原计划；没有自动重新提交。');return;}
   const r=v.repair;if(v.status!=='committed'||!receipt(d,r)||r.submission_key!==key)throw Error('原键修复回执不符');
   const body={schema_version:1,repair_id:r.repair_id,execution_ref:r.execution_ref,plan_digest:r.plan_digest};
   if(await fingerprint(d,body)!==r.request_digest||(h?.key===key&&(!same(h.body,body)||r.request_digest!==h.request_digest)))throw Error('原键与原计划摘要不符');await check(d,n);
   if(h?.key===key&&r.phase!=='started')forget(d,h);
   await refresh(d,n);say(d,r.phase==='started'?'开始事实已确认，结果仍未知。原键继续保留；没有重复修复。':'原修复键及结果已精确核对，没有重新执行。');
  }
  async function operate(d,action){
   if(!live(d)||d.busy||!d.realm)return;const n=++d.version;d.busy=true;controls(d);
   try{
    await check(d,n);
    if(action==='refresh')await refresh(d,n);
    else if(action==='query')await query(d,n,q(d,'[data-ir-key]').value.trim());
    else{
     let h=recover(d);
     if(action==='submit'){
      if(!eligible(d))throw Error('先核对原计划、未确认命令或修复限制');
      const body={schema_version:1,repair_id:'er-'+id(),execution_ref:d.reference,plan_digest:d.plan.plan_digest};
      h={realm:d.realm,project:d.project.project_id,execution_ref:d.reference,key:'index-repair-'+id(),body,request_digest:await fingerprint(d,body)};await check(d,n);save(d,h);
     }else if(action!=='retry'||!h)throw Error('没有可用的原命令');
     if(await fingerprint(d,h.body)!==h.request_digest)throw Error('原命令摘要不符，禁止重新提交');await check(d,n);
     const durable=save(d,h);controls(d);q(d,'[data-ir-key]').value=h.key;say(d,'正在提交本次索引修复。'+(durable?'':'请记下原键：'+h.key));
     await request(d,n,base(d)+'/executions/'+d.reference.execution_id+'/index-repairs',{method:'POST',body:JSON.stringify(h.body),headers:{'Idempotency-Key':h.key}});
     await query(d,n,h.key);
    }
   }catch(e){if(live(d,n)){clear(d);say(d,'操作未确认：'+e.message+'。原键保留，没有自动重新修复。');}}
   finally{if(live(d,n)){d.busy=false;controls(d);}}
  }
  async function open(project,reference,trigger=null){
   invalidate();const el=document.createElement('dialog'),d={el,project,reference,trigger,realm:'',version:0,busy:false,plan:null,rows:[],truncated:false,fault:null};current=d;
   el.setAttribute('aria-label','有限索引修复');el.style.cssText='width:min(900px,94vw);max-height:90vh;overflow:auto;padding:24px;font:inherit;box-sizing:border-box';
   el.innerHTML=`<h2>有限索引修复 · ${esc(project.name)}</h2><button data-ir-close>关闭</button> <button data-ir-refresh>只读核对</button><p data-ir-status role="status"></p><p data-ir-plan></p><p>只重建本次执行的派生索引；每次命令一次尝试，同一故障最多两次。原日志被占用、权限撤销或存在安全拒绝时不得修复。</p><button data-ir-submit>执行本次派生索引修复</button><p data-ir-hint style="overflow-wrap:anywhere"></p><label>原修复键 <input data-ir-key maxlength="128"></label> <button data-ir-query>只读核对原键</button> <button data-ir-retry>原键原计划重试</button><section data-ir-rows style="overflow-wrap:anywhere"></section>`;
   main.append(el);el.showModal();controls(d);el.oncancel=e=>{e.preventDefault();invalidate();};q(d,'[data-ir-close]').onclick=invalidate;
   for(const action of ['refresh','submit','query','retry'])q(d,'[data-ir-'+action+']').onclick=()=>operate(d,action);
   try{const n=d.version,realm=await identity();if(!live(d,n))return;d.realm=realm;const h=recover(d);if(h)q(d,'[data-ir-key]').value=h.key;await operate(d,'refresh');}
   catch(e){if(live(d))say(d,'身份核对未完成：'+e.message);}
  }
  return {open,invalidate};
 }};
})(window);
