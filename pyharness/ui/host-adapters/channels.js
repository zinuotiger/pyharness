/* Channel setup for the local operator. Credentials remain write-only. */
function createChannelUI({get,post,esc,btn,modal,toast,render}) {
  const states={not_bound:'尚未绑定',bound:'已绑定，尚未收信',receiving:'正在收信',reconnecting:'正在重连',rebind_required:'需要重新绑定',waiting_scan:'等待扫码',waiting_confirmation:'等待微信确认',login_expired:'二维码已过期',verification_code_required:'需要配对码',verification_blocked:'验证受限',pending:'待处理',accepted:'任务已接收',reply_pending:'等待反馈',delivered:'已送达',feedback_failed:'反馈失败，等待处理',configured:'已配置',disabled:'已禁用'};
  let loginGeneration=0;
  window.addEventListener('pyharness-panel-close',()=>loginGeneration++);
  window.addEventListener('pagehide',()=>loginGeneration++);
  async function renderChannels(){
    const [data,history]=await Promise.all([get('channels'),get('channels/deliveries?after='+historyAfter)]),wx=data.wechat;
    return `<h1>飞书与个人微信</h1><p class="muted">外部消息使用现有任务队列、助手权限和审批。配置成功不代表账号已联通。</p>
      <div class="list-grid" style="margin-top:20px"><section class="card"><h2>个人微信 · ClawBot</h2>
      <p>${esc(wx.detail)}</p><p>状态：${esc(states[wx.status]||wx.status)}${wx.error?' · '+esc(wx.error):''}</p>
      <p>账号${wx.bound?'已绑定':'尚未绑定'} · ${wx.connected?'正在收信':'尚未确认收信联通'}</p>
      <div class="toolbar">${btn('扫码绑定','wechat-login')}${btn(wx.enabled?'暂停自动收发':'启用自动收发','wechat-toggle',String(!wx.enabled))}${btn('选择执行助手','wechat-agent')}</div>
      <p class="muted">只接收扫码用户及明确授权用户的私聊文本，不读取历史聊天。任务结果只回复入站消息对应的对话。</p></section>
      <section class="card"><h2>飞书应用入口</h2>${data.feishu.map(c=>`<p>${esc(c.name)}：${c.enabled?'已启用':'已禁用'} · ${esc(c.status)}</p><small>回调路径：${esc(c.callback_path)}</small>`).join('')||'<p>尚未配置飞书应用。</p>'}
      <div class="toolbar">${btn('配置飞书应用','feishu-setup')}</div><p class="muted">需要应用凭据、加密策略、用户与聊天允许列表。外网回调由反向代理转发到本机专用路径。</p></section></div>
      <section class="card" style="margin-top:20px"><h2>消息处理与反馈</h2>${data.delivery.map(r=>`<p>${esc(states[r.state]||r.state)}：${r.count}</p>`).join('')||'<p>尚无渠道消息。</p>'}<p class="muted">发送失败会有限重试并保留状态；未送达不会显示已送达。</p>${history.deliveries.map(row=>`<div class="row">${esc(row.connection)} · ${esc(states[row.state]||row.state)} · ${row.attempts} 次重试 ${row.run?'<small>'+esc(row.run)+'</small>':''}${row.state==='feedback_failed'?btn('重试反馈','channel-retry',row.id):''}</div>`).join('')}<div class="toolbar">${btn('刷新状态','refresh')}${historyAfter?btn('返回第一页','channel-first'):''}${history.cursor?btn('下一页','channel-next',history.cursor):''}</div></section>`;
  }
  async function agents(){const d=await get('agents');return d.agents.filter(a=>a.status!=='deprecated'&&a.current_version>0)}
  function protect(form){const submit=form.onsubmit;form.onsubmit=async event=>{event.preventDefault();const button=form.querySelector('button[type="submit"],button.primary,button');if(button)button.disabled=true;try{await submit(event)}catch(error){toast(error.message)}finally{if(button)button.disabled=false}}}
  async function login(){
    loginGeneration++;const gen=loginGeneration;
    const result=await post('channels/wechat/login');
    modal('绑定个人微信',`<p>请使用要绑定的微信扫描二维码，并在微信中确认授权。无需在这里输入微信密码。</p><img src="${esc(result.image)}" alt="微信官方绑定二维码" style="width:240px;max-width:100%"><p id="wechatLoginStatus" role="status">等待扫码；二维码约 5 分钟有效。</p><form id="wechatCode" hidden><label>微信插件显示的配对码<input name="code" inputmode="numeric" autocomplete="off" pattern="[0-9]{4,12}" required></label><button>提交配对码</button></form>`);
    const codeForm=document.getElementById('wechatCode');let code;
    codeForm.onsubmit=e=>{e.preventDefault();code=codeForm.elements.code.value;codeForm.elements.code.value=''};
    async function poll(){
      if(gen!==loginGeneration||!document.getElementById('wechatLoginStatus'))return;
      try{
        const status=await post('channels/wechat/login/'+encodeURIComponent(result.login_id),code?{verification_code:code}:{});code=undefined;
        if(gen!==loginGeneration)return;
        document.getElementById('wechatLoginStatus').textContent=({waiting_scan:'等待扫码',waiting_confirmation:'请在微信中确认',bound:'已绑定；返回后可启用自动收发',receiving:'已绑定，正在接收消息',verification_code_required:'请填写微信插件显示的配对码',login_expired:'二维码已过期，请返回后重新绑定',verification_blocked:'验证暂时受限，请按微信提示处理',rebind_required:'需要重新绑定'})[status.status]||status.status;
        codeForm.hidden=status.status!=='verification_code_required';
        if(!['bound','receiving','login_expired','verification_blocked','rebind_required'].includes(status.status))setTimeout(poll,1500);
      }catch(error){if(gen===loginGeneration){document.getElementById('wechatLoginStatus').textContent='状态读取失败：'+error.message;setTimeout(poll,3000)}}
    }
    poll();
  }
  async function selectAgent(){
    const items=await agents();
    modal('个人微信执行助手',`<form id="wechatAgent"><label class="field">使用已发布助手<select name="agent"><option value="">通用助手（禁止执行代码）</option>${items.map(a=>`<option value="${esc(a.agent_id)}" data-version="${a.current_version}">${esc(a.name)} · v${a.current_version}</option>`).join('')}</select></label><label><input name="approvals" type="checkbox">允许绑定用户通过 /approve 和 /reject 人工裁决审批</label><p class="muted">查询 /status、取消 /cancel、待审批 /approvals。审批不能扩大助手权限。</p><button class="primary">保存</button></form>`);
    document.getElementById('wechatAgent').onsubmit=async e=>{e.preventDefault();const form=e.target,option=form.elements.agent.selectedOptions[0];await post('channels/wechat',{agent_id:option.value||null,agent_version:option.value?Number(option.dataset.version):null,allow_approvals:form.elements.approvals.checked});toast('微信助手已更新')};
    protect(document.getElementById('wechatAgent'));
  }
  async function feishu(){
    const items=await agents();
    modal('配置飞书应用',`<form id="feishuConfig">${[['name','连接名称（小写英文）','text'],['app_id','App ID','text'],['tenant_key','飞书 Tenant Key','text'],['users','允许的用户 Open ID（逗号分隔）','text'],['chats','允许的 Chat ID（逗号分隔）','text'],['app_secret','App Secret','password'],['encrypt_key','Encrypt Key','password'],['verification_token','Verification Token','password']].map(([name,label,type])=>`<label class="field">${label}<input name="${name}" type="${type}" autocomplete="off" required></label>`).join('')}
      <label class="field">执行助手<select name="agent"><option value="">通用助手</option>${items.map(a=>`<option value="${esc(a.agent_id)}" data-version="${a.current_version}">${esc(a.name)} · v${a.current_version}</option>`).join('')}</select></label>
      <label><input name="approvals" type="checkbox">允许此名单用户进行人工审批</label><label><input name="enabled" type="checkbox">启用自动接收与反馈</label><p class="muted">密钥只写入项目秘密存储，不回显。保存后需在飞书配置消息接收事件和专用回调地址。</p><button class="primary">保存连接</button></form>`);
    document.getElementById('feishuConfig').onsubmit=async e=>{e.preventDefault();const f=e.target,v=Object.fromEntries(new FormData(f)),option=f.elements.agent.selectedOptions[0];
      const body={name:v.name,app_id:v.app_id,tenant_key:v.tenant_key,allowed_users:v.users.split(',').map(x=>x.trim()).filter(Boolean),allowed_chats:v.chats.split(',').map(x=>x.trim()).filter(Boolean),enabled:f.elements.enabled.checked,allow_approvals:f.elements.approvals.checked,agent_id:option.value||null,agent_version:option.value?Number(option.dataset.version):null,secrets:{app_secret:v.app_secret,encrypt_key:v.encrypt_key,verification_token:v.verification_token}};
      try{await post('channels/feishu',body);f.reset();toast('配置已保存；尚未验证真实收发')}finally{delete body.secrets}
    };
    protect(document.getElementById('feishuConfig'));
  }
  async function codex(){
    const runtime=await get('executors/codex');
    modal('添加 Codex 执行器',`<p>当前客户端：${esc(runtime.version||'尚未安装')}。状态检查不代表任务执行已通过。</p><form id="codexSetup"><label class="field">助手名称<input name="name" value="Codex 助手" required></label><label class="field">模型名称<input name="model" placeholder="使用当前 Codex 默认模型"></label><label class="field">Codex 可执行文件（可选）<input name="executable" value="${esc(runtime.executable||'')}" placeholder="留空使用项目配置或系统安装；可指定新版的完整路径"></label><p>Codex 使用本机登录，在任务私有工作副本中执行；可能读取宿主机文件。仅向本地操作者开放。每次启动任务仍需审批，权限扩展默认拒绝。</p><p class="muted">按工具次数、时限和收到的 Token 用量限制任务；Codex 账号结算金额未知，不提供金额硬上限。</p><label><input type="checkbox" name="host" required>我允许将任务委托给本机 Codex 执行器</label><button class="primary">创建并发布助手</button></form>`);
    document.getElementById('codexSetup').onsubmit=async e=>{e.preventDefault();const f=e.target;if(!f.elements.host.checked)return;const body={name:f.elements.name.value,executor:'codex',codex_tools:'harness',codex_model:f.elements.model.value.trim()||null,codex_executable:f.elements.executable.value.trim()||null,permission_policy:'standard',sandbox_profile:{mode:'host_approved'},allowed_tools:['codex.run','fs.read_file','fs.list_dir','fs.write_file','exec.python_run']};const saved=await post('agents',body);await post('agents/'+saved.agent_id+'/publish');toast('Codex 助手已发布；真实执行需本机登录及模型权限')};
    protect(document.getElementById('codexSetup'));
  }
  let historyAfter=0;
  return {render:renderChannels,actions:{'channel-first':()=>{historyAfter=0;return render()},'channel-next':cursor=>{historyAfter=Number(cursor);return render()},'channel-retry':async id=>{await post('channels/deliveries/'+encodeURIComponent(id)+'/retry');await render()},'codex-setup':codex,'wechat-login':login,'wechat-agent':selectAgent,'feishu-setup':feishu,
    'wechat-toggle':async enabled=>{await post('channels/wechat',{enabled:enabled==='true'});await render()}}};
}
