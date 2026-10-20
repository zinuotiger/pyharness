/* Public configuration metadata only. Never replay a request after a lost reply. */
(()=>{'use strict';
 window.PyharnessConfigurationHistory={create({api,modal,esc}){
  let active=null,serial=0;
  const live=d=>active===d&&d.root?.isConnected;
  const q=(d,name)=>d.root.querySelector('[data-ch-'+name+']');
  const fresh=()=> 'cfg-'+crypto.randomUUID().replaceAll('-','');
  const names={id:'连接标识',label:'显示名称',model:'模型',base_url:'服务地址',temperature:'生成温度',max_tokens:'单次输出上限',
   active_model_connection:'激活的模型连接',fallback_models:'备用模型',vision_capability:'图片输入能力',legacy_output_limit:'旧格式输出上限',
   permission_policy:'执行权限',allowed_tools:'工具范围',mcp_connections:'MCP 连接',skills:'技能范围',knowledge_sources:'知识源范围',
   approval_requirements:'审批要求',require_evidence:'证据要求',strict_knowledge:'严格知识要求',review_mode:'人工审核模式',
   team_memory:'团队记忆',executor:'执行器',codex_tools:'Codex 工具范围',codex_model:'Codex 模型',codex_executable:'Codex 程序',
   model_connection:'模型连接',output_schema:'输出结构校验',max_rounds:'最大执行轮数',timeout:'执行时限',
   budget_input_tokens:'输入预算',budget_output_tokens:'输出预算',budget_cost:'费用预算',budget_tool_calls:'工具次数预算',
   sandbox_mode:'沙箱执行模式',sandbox_remote_connection:'远程沙箱连接',sandbox_workspace_mode:'沙箱工作区模式',sandbox_network:'沙箱网络范围',
   sandbox_image:'沙箱镜像',sandbox_cpus:'沙箱 CPU 配额',sandbox_memory_mb:'沙箱内存配额',sandbox_pids_limit:'沙箱进程上限',
   sandbox_timeout:'沙箱执行时限',sandbox_output_limit:'沙箱输出上限'};
  function increase(code){for(const [suffix,label] of [['_increased','增加'],['_added','扩大'],['_weakened','减弱'],['_widened','放宽'],['_changed','改变'],['_enabled','启用'],['_requires_confirmation','需要核对']]){
   if(code.endsWith(suffix)){const name=code.slice(0,-suffix.length);return (names[name]||'配置范围')+label;}}
   return '配置范围变化，需核对精确预览';}
  const issues={credential_binding_changed:'模型凭据代次或服务地址已变化',resource_missing:'原配置已不存在',
   current_agent_draft:'当前有未发布草稿，请先处理',current_agent_deprecated:'当前 Agent 已废弃',
   target_version_deprecated:'目标 Agent 版本已废弃',target_dependency_changed:'模型、技能、知识或连接依赖已变化',
   account_host_execution_forbidden:'当前团队账户不能启用宿主执行能力',remote_credential_not_active:'远程沙箱凭据不可用',
   skill_dependency_unavailable:'所需技能不可用',mcp_dependency_unavailable:'所需 MCP 配置不可用',
   knowledge_dependency_unavailable:'所需知识源不可用',knowledge_provider_not_active:'知识服务不可用',knowledge_credential_not_active:'知识服务凭据不可用'};
  function clear(message=''){
   serial++;const d=active;active=null;if(!d)return;
   d.identity=null;d.current=null;d.preview=null;d.pending=null;
   if(d.root){d.root.replaceChildren();if(d.root.isConnected)d.root.textContent=message;}
  }
  window.addEventListener('pyharness-panel-close',()=>clear());
  async function identity(){
   const v=await api('/api/account/me',{cache:'no-store'});
   if(v.account){if(!['owner','operator'].includes(v.account.role))throw Error('配置历史仅向当前管理员开放');
    return JSON.stringify([v.account.user_id,v.account.tenant,v.account.role]);}
   if(v.mode!=='local_operator')throw Error('需要当前管理员身份');
   return JSON.stringify(['local_operator',v.username||null]);
  }
  async function verify(d){
   try{if(await identity()!==d.identity)throw Error('changed');}
   catch{if(active===d)clear('账户或权限已变化，已清除配置历史');throw Error('账户或权限已变化');}
   if(!live(d))throw Error('配置历史已关闭');
  }
  async function request(d,path,body){
   if(!live(d))throw Error('配置历史已关闭');
   try{await verify(d);const value=await api(d.base+path,{cache:'no-store',...(body?{method:'POST',body:JSON.stringify(body)}:{})});
    if(!live(d))throw Error('配置历史已关闭');await verify(d);return value;
   }catch(e){if((e.status===401||e.status===403)&&active===d)clear('权限已变化，已清除配置历史');throw e;}
  }
  function text(d,name,value){if(live(d))q(d,name).textContent=typeof value==='string'?value:JSON.stringify(value,null,2);}
  function enabled(d){
   if(!live(d))return;
   for(const el of d.root.querySelectorAll('button,select,input'))el.disabled=d.busy;
   q(d,'capture').disabled=d.busy||!!d.pending||!d.current;
   q(d,'refresh').disabled=d.busy||!!d.pending;
   q(d,'revision').disabled=d.busy||!!d.pending;
   q(d,'preview').disabled=d.busy||!!d.pending||!q(d,'revision').value;
   q(d,'apply').disabled=d.busy||!!d.pending||!d.preview?.compatible||!q(d,'confirm').checked||
    (!!d.preview?.permission_increases?.length&&!q(d,'widen').checked);
  }
  async function act(d,fn){if(!live(d)||d.busy)return;d.busy=true;enabled(d);
   try{await fn();}catch(e){text(d,'status',e.message);}finally{if(live(d)){d.busy=false;enabled(d);}}
  }
  function resetPreview(d){d.preview=null;text(d,'compatibility','尚未预览');text(d,'changes','');
   text(d,'increases','');text(d,'preview-data','');q(d,'confirm').checked=false;q(d,'widen').checked=false;q(d,'widen-label').hidden=true;}
  function receipt(d,v){
   text(d,'result',v);text(d,'status',v.status==='applied'?'配置已应用。此为历史提交回执；新任务仍按原权限和依赖检查执行。':'当前配置版本已保存；尚未应用其他版本。');
   d.pending=null;d.current=null;resetPreview(d);text(d,'key','原命令已确认，可刷新当前状态。');
  }
  async function command(d,key){
   text(d,'key','查询命令：'+key);const result=await request(d,'/commands/'+encodeURIComponent(key));
   receipt(d,result);
  }
  async function refresh(d){
   if(d.pending)throw Error('请先查询未确认的原命令');resetPreview(d);d.current=null;
   const h=await request(d,'/revisions');q(d,'revision').replaceChildren();
   for(const r of h.revisions||[]){const o=document.createElement('option');o.value=r.revision_id;
    o.textContent='版本 '+r.number+' · '+r.created_at;q(d,'revision').append(o);}
   text(d,'history-data',h.revisions||[]);q(d,'recent').replaceChildren();
   for(const c of h.commands||[]){const b=document.createElement('button');b.type='button';
    b.textContent=(c.status==='applied'?'应用':'保存')+' · '+(c.created_at||'旧记录，时间未知');
    b.onclick=()=>act(d,()=>command(d,c.command_key));q(d,'recent').append(b);}
   text(d,'brief','已保存 '+(h.revisions||[]).length+' 个固定版本。历史回执不代表当前依赖仍有效。');
   try{d.current=await request(d,'/current');text(d,'current',d.current);
    text(d,'status','当前状态已刷新；查看差异并明确确认后才会应用。');}
   catch(e){text(d,'current','当前依赖不可用，仍可查询历史回执。');throw e;}
  }
  async function submit(d,path,body){
   d.pending=body.command_key;text(d,'key','原命令键：'+d.pending);text(d,'result','结果尚未确认；查询原命令，不自动重发。');
   try{receipt(d,await request(d,path,body));}
   catch(e){
    const refused=new Set(['configuration_source_stale','configuration_preview_stale','configuration_restore_not_active',
     'configuration_permission_confirmation_required','configuration_confirmation_required','configuration_invalid_request',
     'configuration_revision_history_limit','configuration_command_history_limit','configuration_private_content']);
    if(e.status===422&&refused.has(e.code)){d.pending=null;d.current=null;resetPreview(d);text(d,'result','服务端已明确拒绝本次请求。刷新当前状态并重新预览后再决定；未自动重试。');}
    else text(d,'result','提交结果尚未确认。保留原命令键，仅查询；不要重复应用。');throw e;}
  }
  async function open(kind,resource){
   if(!['model','agent'].includes(kind)||!/^[A-Za-z0-9_-]{1,80}$/.test(resource))throw Error('配置标识不合法');
   clear();const n=serial,who=await identity();if(n!==serial)return;
   modal((kind==='model'?'模型':'Agent')+'配置历史',`<div data-configuration-history>
    <p data-ch-brief>正在读取固定版本…</p><p class="warning">${kind==='model'?'应用后将激活此模型连接。':'应用后创建新的 Agent 发布版本。'}旧任务与旧日志保留；凭据、宿主文件和外部效果不会恢复。兼容检查不代表已执行网络、MCP、Codex 或远程沙箱测试。</p>
    <div class="toolbar"><button type="button" data-ch-refresh>刷新当前状态</button><button type="button" data-ch-capture>保存当前配置版本</button></div>
    <details><summary>当前参数与版本摘要</summary><pre data-ch-current></pre><pre data-ch-history-data></pre></details>
    <label class="field">目标固定版本<select data-ch-revision></select></label><button type="button" data-ch-preview>查看差异与兼容性</button>
    <p data-ch-compatibility>尚未预览</p><p class="warning" data-ch-increases></p><div data-ch-changes></div>
    <details><summary>精确预览证据</summary><pre data-ch-preview-data></pre></details>
    <label class="field"><span><input type="checkbox" data-ch-confirm> 我确认应用预览中的固定配置</span></label>
    <label class="field" data-ch-widen-label hidden><span><input type="checkbox" data-ch-widen> 我另行确认以上权限或资源范围扩大（仍须原执行审批）</span></label>
    <button type="button" class="primary" data-ch-apply disabled>应用已预览的配置</button>
    <p data-ch-key></p><div class="toolbar"><button type="button" data-ch-query>查询本次原命令</button></div>
    <p role="status" aria-live="polite" data-ch-status></p><details><summary>原提交回执</summary><pre data-ch-result></pre></details>
    <details><summary>最近 20 条已提交命令（刷新页面后仍可查询）</summary><div class="toolbar" data-ch-recent></div></details></div>`);
   const d={identity:who,root:document.querySelector('[data-configuration-history]'),base:'/api/v1/configuration-history/'+kind+'/'+encodeURIComponent(resource),busy:false,pending:null,current:null,preview:null};active=d;
   q(d,'refresh').onclick=()=>act(d,()=>refresh(d));
   q(d,'revision').onchange=()=>{resetPreview(d);enabled(d);};
   for(const name of ['confirm','widen'])q(d,name).onchange=()=>enabled(d);
   q(d,'capture').onclick=()=>act(d,async()=>{if(d.pending||!d.current)throw Error('先刷新当前状态');
    await submit(d,'/revisions',{command_key:fresh(),source_sha256:d.current.source_sha256});});
   q(d,'preview').onclick=()=>act(d,async()=>{resetPreview(d);const id=q(d,'revision').value;if(!id||d.pending)throw Error('先选择版本并确认原命令');
    const p=await request(d,'/revisions/'+encodeURIComponent(id)+'/preview');d.preview=p;
    text(d,'compatibility',p.compatible?'兼容：可以在确认后应用':'不兼容，已阻止应用：'+(p.issues||[]).map(i=>issues[i]||(i.includes('credential')?'模型凭据不可用或无法核对原代次':'配置依赖或执行权限已变化，详见精确预览')).join('、'));
    text(d,'increases',p.permission_increases?.length?'需另行确认：'+p.permission_increases.map(increase).join('、'):'未发现需另行确认的权限扩大');
    q(d,'widen-label').hidden=!p.permission_increases?.length;text(d,'preview-data',p);
    q(d,'changes').innerHTML=(p.changes||[]).map(c=>`<details><summary>${esc(names[c.field]||c.field)}</summary><p>当前</p><pre>${esc(JSON.stringify(c.before,null,2))}</pre><p>目标</p><pre>${esc(JSON.stringify(c.after,null,2))}</pre></details>`).join('')||'<p>参数没有差异</p>';
    text(d,'status','预览未保存或执行任何任务。');});
   q(d,'apply').onclick=()=>act(d,async()=>{const p=d.preview;if(d.pending||!p?.compatible||!q(d,'confirm').checked||
     (p.permission_increases?.length&&!q(d,'widen').checked))throw Error('请先预览并明确确认');
    await submit(d,'/revisions/'+encodeURIComponent(p.revision_id)+'/applications',{command_key:fresh(),
     preview_sha256:p.preview_sha256,confirm_apply:true,confirm_permission_increase:q(d,'widen').checked});});
   q(d,'query').onclick=()=>act(d,async()=>{if(!d.pending)throw Error('当前没有未确认命令；可展开最近已提交命令');await command(d,d.pending);});
   await act(d,()=>refresh(d));
  }
  return {open,destroy:clear};
 }};
})();
