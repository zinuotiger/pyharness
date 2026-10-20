/* Original request facts and policy predictions. No execution or mutation controls. */
(function(global){
 'use strict';
 global.PyharnessObservations={create({main,api,esc}){
  const c=global.PyharnessWorkspaceContracts;let active=null,serial=0;
  const same=(a,b)=>c.canonical(a)===c.canonical(b),ex=v=>/^ex-[0-9a-f]{16}$/.test(v),sid=v=>/^s-pa-[0-9a-f]{32}$/.test(v),oid=v=>/^co-[0-9a-f]{16}$/.test(v);
  const hashText=async text=>Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text)))).map(v=>v.toString(16).padStart(2,'0')).join('');
  const selection=v=>v?.schema_version===1&&/^ra-[0-9a-f]{16}$/.test(v.attempt_ref?.attempt_id)&&v.attempt_ref.revision===1&&c.hash(v.attempt_ref.digest)&&c.integer(v.attempt_ref.source_seq)&&/^a-[0-9a-f]{32}$/.test(v.agent_ref?.agent_id)&&c.integer(v.agent_ref.version)&&c.hash(v.agent_ref.sha256);
  const q=(d,s)=>d.el.querySelector(s),base=d=>'/api/v1/projects/'+d.project.project_id;
  const live=(d,n=d.epoch)=>active===d&&d.el.isConnected&&d.el.open&&n===d.epoch;
  const say=(d,s)=>{if(live(d))q(d,'[data-ob-status]').textContent=s;};
  async function who(){const value=await api('/api/account/me',{cache:'no-store'}),a=value.account;return JSON.stringify(a?[a.tenant,a.user_id||a.username]:['local',value.username||'local']);}
  function invalidate(){serial++;if(!active)return;const d=active;active=null;d.epoch++;d.el.replaceChildren();d.el.close();d.el.remove();d.trigger?.focus();}
  function clear(d){for(const key of ['context','permission','comparison','basis'])q(d,'[data-ob-'+key+']')?.replaceChildren();}
  function controls(d){if(live(d))d.el.querySelectorAll('button,input,select,textarea').forEach(e=>{if(e.dataset.obAction!=='close')e.disabled=d.busy;});}
  async function check(d,n){if(!live(d,n))throw Error('观测窗口已变化');let realm;try{realm=await who();}catch(e){invalidate();throw e;}if(realm!==d.realm){invalidate();throw Error('账户或团队已变化，已清除内容');}if(!live(d,n))throw Error('观测窗口已变化');}
  async function read(d,n,path,options={}){await check(d,n);try{const value=await api(path,{cache:'no-store',...options});await check(d,n);return value;}catch(e){if(live(d,n)&&(e.status===401||e.status===403)){clear(d);d.selection=null;d.items=[];q(d,'[data-ob-item]').replaceChildren();}throw e;}}
  function basis(d){q(d,'[data-ob-basis]').textContent=d.selection?'固定尝试 '+d.selection.attempt_ref.attempt_id+' · Agent '+d.selection.agent_ref.agent_id+' v'+d.selection.agent_ref.version:'尚未载入固定尝试和发布的 Agent 版本。';}
  function execution(d){const id=q(d,'[data-ob-execution]').value.trim();if(!ex(id))throw Error('请输入有效执行 ID');return id;}
  function scoped(d,v,id,session){return v?.schema_version===1&&v.project_id===d.project.project_id&&v.execution_id===id&&v.execution_authorized===false&&sid(v.session_id)&&(!session||v.session_id===session);}
  function safeView(v){return c.keys(v,'schema_version layer model method messages tools counts truncated withheld')&&v.schema_version===1&&['adapter_input','transport_payload'].includes(v.layer)&&['chat','chat_stream'].includes(v.method)&&typeof v.model==='string'&&v.model.length<=128&&typeof v.truncated==='boolean'&&typeof v.withheld==='boolean'&&Array.isArray(v.messages)&&v.messages.length<=64&&Array.isArray(v.tools)&&v.tools.length<=64&&v.messages.every(m=>c.keys(m,'role content name tool_call_id tool_calls')&&['system','developer','user','assistant','tool'].includes(m.role)&&typeof m.content==='string')&&new TextEncoder().encode(JSON.stringify(v)).length<=49152;}
  const amount=v=>v===null?'未知':typeof v==='number'&&Number.isFinite(v)?String(v):'未知';
  async function operate(d,action){
   if(action==='close'){invalidate();return;}if(!live(d)||d.busy)return;
   d.busy=true;const n=++d.epoch;controls(d);
   try{
    if(action==='selection'){
     d.selection=null;basis(d);const id=execution(d),v=await read(d,n,base(d)+'/executions/'+id);
     const chosen={schema_version:1,attempt_ref:v.attempt_ref,agent_ref:v.agent_ref};
     if(v.project_id!==d.project.project_id||v.execution_id!==id||!selection(chosen))throw Error('固定选择收据范围不符');
     d.selection=chosen;basis(d);say(d,'已载入该运行的确切尝试和发布版本；没有开始执行。');
    }else if(action==='contexts'){
     q(d,'[data-ob-context]').replaceChildren();q(d,'[data-ob-item]').replaceChildren();d.items=[];
     const id=execution(d),session=q(d,'[data-ob-session]').value;
     const v=await read(d,n,base(d)+'/executions/'+id+'/contexts'+(session?'?session_id='+encodeURIComponent(session):''));
     if(!scoped(d,v,id,session)||!['available','unavailable'].includes(v.context_status)||!Array.isArray(v.items)||v.items.length>20||!Array.isArray(v.child_sessions)||v.child_sessions.length>200||v.items.some(i=>!oid(i.observation_id)||!c.integer(i.source_seq)||i.phase!=='prepared')||v.child_sessions.some(i=>!sid(i.session_id)))throw Error('上下文目录范围或格式不符');
     d.id=id;d.session=v.session_id;d.items=v.items;
     if(!session){q(d,'[data-ob-session]').innerHTML='<option value="">主会话</option>'+v.child_sessions.map(i=>'<option value="'+esc(i.session_id)+'">子会话 '+esc(i.session_id)+'</option>').join('');}
     q(d,'[data-ob-item]').innerHTML=v.items.map(i=>'<option value="'+esc(i.observation_id)+'">seq '+i.source_seq+' · '+esc(i.layer)+' · '+esc(i.role)+'</option>').join('');
     say(d,v.context_status==='unavailable'?'该历史运行没有记录实际上下文，无法事后重建。':'已读取准备记录目录，共 '+v.total+' 条'+(v.truncated?'；仅展示最近 20 条。':'。'));
    }else if(action==='detail'){
     q(d,'[data-ob-context]').replaceChildren();const id=execution(d),ident=q(d,'[data-ob-item]').value;
     const chosen=d.items.find(i=>i.observation_id===ident);if(id!==d.id||!chosen)throw Error('先读取当前会话的上下文目录');
     const v=await read(d,n,base(d)+'/executions/'+id+'/contexts/'+ident+'?session_id='+encodeURIComponent(d.session));
     const raw=v.public_view_canonical;
     if(!scoped(d,v,id,d.session)||v.observation_id!==ident||v.phase!=='prepared'||v.source_seq!==chosen.source_seq||v.public_view_digest!==chosen.public_view_digest||!safeView(v.public_view)||v.layer!==v.public_view.layer||v.model!==v.public_view.model||!c.hash(v.public_view_digest)||typeof raw!=='string'||new TextEncoder().encode(raw).length>49152||await hashText(raw)!==v.public_view_digest||!same(JSON.parse(raw),v.public_view))throw Error('上下文事实范围、内容或摘要不符');
     await check(d,n);
     q(d,'[data-ob-context]').textContent=v.public_view.messages.map(m=>'['+m.role+']'+(m.name?' name='+m.name:'')+(m.tool_call_id?' tool_call_id='+m.tool_call_id:'')+'\n'+m.content).join('\n\n')+'\n\n精确公开记录 JSON（工具参数、数值与转义保持原文）：\n'+raw;
     const confirmation={response_recorded:'已记录提供方响应',error_recorded:'已记录提供方错误',not_confirmed:'提供方接收状态未知',unavailable_for_adapter:'此适配器没有传输确认'}[v.dispatch_confirmation]||'提供方接收状态未知';
     const h=v.derivation?.history_window,window=h?.available?'历史窗口保留 '+h.kept_messages+' / '+h.input_messages+' 条':'历史窗口计数未知';
     say(d,'准备记录；'+confirmation+'。'+window+'；公开副本'+(v.public_view.truncated?'已裁剪':'未裁剪')+'；来源 '+(v.source_content_state||'未知')+'。');
    }else if(action==='permission'){
     q(d,'[data-ob-permission]').replaceChildren();if(!selection(d.selection))throw Error('先载入固定尝试和发布版本');
     const name=q(d,'[data-ob-tool]').value.trim(),raw=q(d,'[data-ob-args]').value;if(raw.length>32768||!/^[a-z][a-z0-9_.]{1,63}$/.test(name))throw Error('工具名或参数超限');
     const args=JSON.parse(raw);if(!args||Array.isArray(args)||typeof args!=='object')throw Error('参数必须是 JSON 对象');
     const body={...d.selection,tool_name:name,arguments:args},v=await read(d,n,base(d)+'/permission-previews',{method:'POST',body:JSON.stringify(body)});
     if(v.schema_version!==1||v.project_id!==d.project.project_id||!same(v.attempt_ref,body.attempt_ref)||!same(v.agent_ref,body.agent_ref)||v.tool_name!==name||!['allow','reject','approval','unknown'].includes(v.prediction)||v.execution_authorized!==false||v.side_effects_executed!==false||v.live_recheck_required!==true||!c.hash(v.policy_fingerprint))throw Error('权限预测范围或边界收据不符');
     q(d,'[data-ob-permission]').textContent=({allow:'预测允许',reject:'预测拒绝',approval:'预测需要审批',unknown:'预测未知'})[v.prediction]+'；'+v.reason+'。未执行工具、模型或审批。真实调用必须重新检查权限。';
     say(d,'权限预演完成；没有执行权限或审批凭证。');
    }else if(action==='compare'){
     q(d,'[data-ob-comparison]').replaceChildren();const left=execution(d),right=q(d,'[data-ob-right]').value.trim();if(!ex(right))throw Error('请输入有效的对照执行 ID');
     const v=await read(d,n,base(d)+'/run-comparisons',{method:'POST',body:JSON.stringify({schema_version:1,left_execution_id:left,right_execution_id:right})});
     if(v.schema_version!==1||v.project_id!==d.project.project_id||v.left?.execution_id!==left||v.right?.execution_id!==right||v.execution_authorized!==false||!['aligned','not_comparable','pending'].includes(v.comparison_status)||!Array.isArray(v.regressions)||v.regressions.length>20)throw Error('运行比较范围或收据不符');
     const line=r=>r.execution_id+' · '+r.status+'\n输入 token：'+amount(r.usage?.input_tokens)+'；输出 token：'+amount(r.usage?.output_tokens)+'；费用估计：'+amount(r.usage?.cost_est)+'\n业务验收：'+r.business_acceptance;
     q(d,'[data-ob-comparison]').textContent=({aligned:'输入与声明验证条件对齐',not_comparable:'不可比：'+(v.incomparable_reasons||[]).join('，'),pending:'执行尚未结束，暂不判定回归'})[v.comparison_status]+'\n\n'+line(v.left)+'\n\n'+line(v.right)+'\n\n'+(v.regressions.some(r=>r.kind==='execution_regression')?'执行回归：此前完成，本次失败。':'没有确认的执行回归。')+'\n业务质量未评估；未知用量和费用保持未知。';
     say(d,'已比较原日志事实；没有重新执行任何运行。');
    }
   }catch(e){if(live(d,n)){clear(d);say(d,'读取或预演未完成：'+e.message);}}
   finally{if(live(d,n)){d.busy=false;controls(d);}}
  }
  return {invalidate,async open(project,trigger,options={}){
   invalidate();const opening=serial,realm=await who();if(opening!==serial)return;
   const el=document.createElement('dialog');el.id='observationsDialog';el.setAttribute('aria-label','上下文与运行观测');el.style.cssText='width:min(1100px,94vw);max-width:1100px;max-height:90vh;overflow:auto;padding:24px;font:inherit';
   const d={el,project,trigger,realm,epoch:0,busy:false,selection:selection(options.selection)?structuredClone(options.selection):null,items:[],id:null,session:null};active=d;
   el.innerHTML=`<h2>上下文与运行观测 · ${esc(project.name)}</h2><button data-ob-action="close">关闭</button><p>查看原请求的脱敏公开副本、预演权限和比较执行。所有操作均不会开始运行。</p><p role="status" data-ob-status></p>
    <label>执行 ID <input data-ob-execution maxlength="19"></label> <button data-ob-action="selection">载入此运行的固定选择</button><pre data-ob-basis></pre>
    <h3>实际上下文</h3><label>会话 <select data-ob-session><option value="">主会话</option></select></label> <button data-ob-action="contexts">读取上下文目录</button><label>准备记录 <select data-ob-item></select></label> <button data-ob-action="detail">查看公开内容</button><pre data-ob-context style="max-height:340px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere"></pre>
    <h3>权限预演</h3><p>仅预测固定策略。实时文件状态、运行预算、容器可用性和未建模能力不构成执行保证。</p><label>工具名 <input data-ob-tool maxlength="64" value="fs.read_file"></label><label>参数 JSON <textarea data-ob-args maxlength="32768">{"path":"sample.txt"}</textarea></label><button data-ob-action="permission">只读权限预演</button><p data-ob-permission></p>
    <h3>运行差异</h3><label>对照执行 ID <input data-ob-right maxlength="19"></label> <button data-ob-action="compare">比较两次运行</button><pre data-ob-comparison style="white-space:pre-wrap;overflow-wrap:anywhere"></pre>`;
   q(d,'[data-ob-execution]').value=ex(options.execution_id)?options.execution_id:'';main.append(el);el.showModal();basis(d);
   el.oncancel=e=>{e.preventDefault();invalidate();};el.onclick=e=>{const button=e.target.closest('[data-ob-action]');if(button)operate(d,button.dataset.obAction);};
   el.oninput=e=>{if(!e.target.matches('input,textarea'))return;d.epoch++;d.busy=false;clear(d);if(e.target.matches('[data-ob-execution]')){d.selection=null;d.items=[];q(d,'[data-ob-item]').replaceChildren();q(d,'[data-ob-session]').innerHTML='<option value="">主会话</option>';basis(d);}controls(d);};
   el.onchange=e=>{if(e.target.matches('[data-ob-session],[data-ob-item]')){d.epoch++;d.busy=false;q(d,'[data-ob-context]').replaceChildren();if(e.target.matches('[data-ob-session]')){d.items=[];q(d,'[data-ob-item]').replaceChildren();}controls(d);}};
  }};
 }};
})(window);
