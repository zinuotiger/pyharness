/* Local operator observations. No remote tool execution or paid model control. */
(()=>{'use strict';
 window.PyharnessExtensionWorkbench={create({api,modal,esc}){
  let active=null,serial=0;
  const base='/api/v1/extension-workbench',fresh=()=>crypto.randomUUID().replaceAll('-','');
  function clear(){serial++;if(!active)return;const d=active;active=null;d.snapshot=null;d.observation=null;d.preview=null;d.pending=null;d.identity=null;if(d.root?.isConnected)d.root.replaceChildren();}
  window.addEventListener('pyharness-panel-close',clear);
  async function identity(){const v=await api('/api/account/me',{cache:'no-store'});if(v.account&&v.account.role!=='operator')throw Error('扩展调试仅向本地操作者开放');return JSON.stringify(v.account?[v.account.user_id,v.account.tenant,v.account.role]:['local-operator']);}
  const live=d=>active===d&&d.root?.isConnected;
  async function checkIdentity(d){try{if(await identity()!==d.identity)throw Error('账户已变化');}catch{clear();if(d.root?.isConnected)d.root.textContent='账户或权限已变化，已清除调试内容';throw Error('账户或权限已变化，已清除调试内容');}if(!live(d))throw Error('调试页面已关闭');}
  async function request(d,path,body){if(!live(d))throw Error('调试页面已关闭');try{await checkIdentity(d);const v=await api(base+path,{cache:'no-store',...(body?{method:'POST',body:JSON.stringify(body)}:{})});if(!live(d))throw Error('调试页面已关闭');await checkIdentity(d);return v;}catch(e){if(e.status===401||e.status===403){clear();if(d.root?.isConnected)d.root.textContent='权限已变化，已清除调试内容';throw Error('权限已变化，已清除调试内容');}throw e;}}
  const q=(d,name)=>d.root.querySelector('[data-ew-'+name+']');
  function text(d,name,value){
   if(!live(d))return;
   q(d,name).textContent=typeof value==='string'?value:JSON.stringify(value,null,2);
   if(name!=='result')return;
   const summary=q(d,'brief');summary.textContent='';
   if(!value||typeof value!=='object')return;
   const label={completed:'已完成',failed:'失败',unconfirmed:'结果未确认'}[value.status]||value.status;
   if(value.mode==='deterministic_controlled_wiring')summary.textContent=`${label} · 技能实际使用 ${value.skill_used_sequences?.length||0} 次 · ${value.next_context_contains_loaded_view?'固定正文已进入下一轮上下文':'下一轮收到原摘要或溢出引用，全文未直接进入'} · 禁止调用${value.forbidden_call_rejected?'已拒绝':'未证实拒绝'} · ${value.carrier_closed?'会话已关闭':'清理未确认'}。无付费模型，不评价模型质量。`;
   else if(d.root.querySelector('[data-ew-rehearse]'))summary.textContent=`${label}。请按原演练键查询；不会自动重新演练。`;
   else summary.textContent=`${label} · 协商版本 ${value.protocol_status==='verified'?'已核对':value.protocol_status==='legacy_unverified'?'旧兼容模式，未证实版本':'未验证'} · ${value.carrier_closed?'发现进程已关闭':'进程清理未确认'} · 当前连通性未验证。`;
  }
  async function act(d,fn){if(!live(d)||d.busy)return;d.busy=true;for(const b of d.root.querySelectorAll('button'))b.disabled=true;try{await fn();}catch(e){text(d,'status',e.message);}finally{if(live(d)){d.busy=false;for(const b of d.root.querySelectorAll('button'))b.disabled=false;}}}
  async function open(title,html){clear();const n=serial,who=await identity();if(n!==serial)return null;modal(title,`<div data-extension-workbench><p class="warning">本地操作者专用。这里只做版本观察与受控演练；实际任务仍使用原审批、预算及执行收据。</p>${html}<p role="status" data-ew-status>尚未执行</p></div>`);const d={identity:who,root:document.querySelector('[data-extension-workbench]'),busy:false,pending:null};active=d;return d;}
  function showMcp(d,v){d.observation=v;const {tools,...meta}=v;text(d,'result',meta);q(d,'tools').replaceChildren();for(const t of tools||[]){const option=document.createElement('option');option.value=t.name;option.textContent=t.name;q(d,'tools').append(option);}q(d,'tool-preview').hidden=v.status!=='completed'||v.stale;const schema=()=>text(d,'schema',(v.tools||[]).find(t=>t.name===q(d,'tools').value)?.schema||{});q(d,'tools').onchange=schema;schema();text(d,'status',v.stale?'配置已变化，旧观测不可用于参数预览':v.status==='completed'?'发现完成；拥有的进程已关闭，此结果是历史观测':v.status==='unconfirmed'?'结果未确认；不会自动重新启动进程':'发现失败，请检查诊断代码');}
  async function mcp(ref){const [source,name]=String(ref).split('~');if(!['managed','runtime'].includes(source)||!/^[a-z][a-z0-9_]{1,40}$/.test(name))throw Error('连接标识不合法');const d=await open('MCP 调试',`<p>${esc(source)} / ${esc(name)}</p><button type="button" data-ew-refresh>刷新零调用预览</button><details><summary>固定版本与配置详情</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere" data-ew-preview></pre></details><label><input type="checkbox" data-ew-confirm> 我确认启动当前配置的宿主进程以发现工具（不发送工具调用）</label><div class="toolbar"><button type="button" data-ew-discover>明确发现</button><button type="button" data-ew-query>查询原命令结果</button></div><p data-ew-key></p><p data-ew-brief></p><details><summary>展开原始诊断证据</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere" data-ew-result></pre></details><section data-ew-tool-preview hidden><label>已验证工具<select data-ew-tools></select></label><details><summary>Schema</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere" data-ew-schema></pre></details><label>参数 JSON（不保存参数值）<textarea data-ew-arguments rows="4">{}</textarea></label><button type="button" data-ew-validate>零调用参数预览</button><pre style="white-space:pre-wrap;overflow-wrap:anywhere" data-ew-validation></pre></section>`);if(!d)return;
   async function refresh(){d.preview=await request(d,`/mcp/${source}/${name}`);d.pending=null;d.observation=null;q(d,'confirm').checked=false;q(d,'tool-preview').hidden=true;text(d,'key','');text(d,'result','');text(d,'preview',d.preview);text(d,'status','配置预览完成：零进程、零工具调用');}
   q(d,'refresh').onclick=()=>act(d,refresh);
   q(d,'discover').onclick=()=>act(d,async()=>{if(d.pending)throw Error('请先查询原命令结果；未确认请求不重发');if(!d.preview||!q(d,'confirm').checked)throw Error('先查看当前配置并勾选启动确认');d.pending='mcp-'+fresh();text(d,'key','命令键：'+d.pending);try{const v=await request(d,`/mcp/${source}/${name}/discoveries`,{command_key:d.pending,confirm_host_process:true,config_sha256:d.preview.config_sha256});showMcp(d,v);}catch(e){text(d,'result','结果尚未确认；保留命令键，查询后再决定是否明确新发现。');throw e;}});
   q(d,'query').onclick=()=>act(d,async()=>{if(!d.pending)throw Error('暂无命令键');showMcp(d,await request(d,'/discoveries/'+d.pending));});
   q(d,'validate').onclick=()=>act(d,async()=>{if(!d.observation||d.observation.status!=='completed')throw Error('先完成发现');let args;try{args=JSON.parse(q(d,'arguments').value);}catch{throw Error('参数必须是合法 JSON 对象');}const v=await request(d,'/discoveries/'+d.pending+'/arguments',{tool:q(d,'tools').value,arguments:args});text(d,'validation',v);text(d,'status','参数结构有效；仍未授权或执行工具');});
   await act(d,refresh);
  }
  async function skill(name){const d=await open('固定技能与隔离演练',`<p>${esc(name)}</p><p>捕获当前技能库的实际加载视图；源文件版本与加载视图摘要分别记录。后续磁盘变化不会改写这个快照。</p><details><summary>固定版本与配置详情</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere" data-ew-preview></pre></details><details><summary>固定加载正文</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere" data-ew-body></pre></details><label><input type="checkbox" data-ew-confirm> 我确认创建独立临时会话，以确定性脚本模型验证原工具、拒绝边界和下一轮上下文（不评价模型质量）</label><div class="toolbar"><button type="button" data-ew-rehearse>隔离演练</button><button type="button" data-ew-query>查询原演练</button></div><p data-ew-key></p><p data-ew-brief></p><details><summary>展开原始诊断证据</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere" data-ew-result></pre></details>`);if(!d)return;
   q(d,'rehearse').onclick=()=>act(d,async()=>{if(!d.snapshot||!q(d,'confirm').checked)throw Error('先查看固定快照并勾选演练确认');if(d.pending)throw Error('请查询原演练；不自动重发');d.pending='skill-'+fresh();text(d,'key','命令键：'+d.pending);const v=await request(d,'/skill-snapshots/'+d.snapshot.snapshot_id+'/rehearsals',{command_key:d.pending,confirm_controlled_rehearsal:true,loaded_sha256:d.snapshot.snapshot.loaded_sha256});text(d,'result',v);text(d,'status',v.context_delivery==='complete_loaded_view'?'受控接线演练完成；固定正文已进入实际下一轮上下文':v.context_delivery==='partial_spill_or_summary'?'受控接线演练完成；下一轮收到原溢出摘要，全文未直接进入上下文':'演练状态：'+v.status);});
   q(d,'query').onclick=()=>act(d,async()=>{if(!d.pending)throw Error('暂无演练键');text(d,'result',await request(d,'/skill-rehearsals/'+d.pending));});
   await act(d,async()=>{d.snapshot=await request(d,'/skill-snapshots',{name});const {body,dir,...meta}=d.snapshot.snapshot;text(d,'preview',{snapshot_id:d.snapshot.snapshot_id,...meta});text(d,'body',body);text(d,'status','固定快照已保存；尚未启动演练或模型');});
  }
  return {mcp,skill,destroy:clear};
 }};
})();
