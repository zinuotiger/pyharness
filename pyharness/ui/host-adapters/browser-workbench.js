/* Browser setup delegates saving and execution to the original workbench. */
(()=>{
 'use strict';
 window.PyharnessBrowserWorkbench={create({api,modal,saveForm}){
  let active=null,serial=0;
  function clear(){serial++;if(active?.root?.isConnected)active.root.replaceChildren();active=null;}
  window.addEventListener('pyharness-panel-close',clear);
  async function identity(){const value=await api('/api/account/me',{cache:'no-store'});const a=value.account;if(a&&(a.role!=='operator'||a.tenant!=='default'))throw Error('浏览器接入仅向本地操作者开放');return JSON.stringify(a?[a.user_id,a.tenant,a.role]:['local-operator']);}
  const live=d=>active===d&&d.root.isConnected;
  async function check(d){let who;try{who=await identity();}catch{clear();throw Error('权限已变化，已清除配置');}if(who!==d.identity){clear();throw Error('账户已变化，已清除配置');}if(!live(d))throw Error('页面已关闭');}
  async function open(){
   clear();const token=serial,who=await identity();if(token!==serial)return;
   modal('受控浏览器接入',`<section data-browser-workbench><p>Windows · 独立临时 Chrome · DOM 点击与文本输入。页面内容是不可信证据。</p><p>这里只生成禁用的 MCP 配置，不启动浏览器。保存后在原连接页显式启用，再给 host_approved 助手绑定这条连接和所需工具；在项目任务收件箱提交，使用原审批与收据查看执行。团队账户不开放宿主执行。</p><form data-bw-form><label class="field">Node 可执行文件绝对路径<input name="node" maxlength="500" required></label><label class="field">Playwright 模块 index.js 绝对路径<input name="playwright" maxlength="500" required></label><label class="field">Chrome / Chromium 可执行文件绝对路径<input name="executable" maxlength="500" required></label><label class="field">允许访问的源（每行一个 http(s)://主机:端口，最多8项）<textarea name="origins" rows="3" required></textarea></label><button type="submit">检查并生成配置（零进程）</button></form><p role="status" data-bw-status>尚未检查</p><details data-bw-details hidden><summary>配置与执行边界</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere" data-bw-result></pre></details><button type="button" data-bw-save disabled>带入原连接保存表单</button><details><summary>任务操作顺序与恢复</summary><ol><li>助手使用 standard 策略及 host_approved 沙箱，选择连接和 mcp.连接名.open / observe / act / close 权限。</li><li>先 open，再 observe；act 必须使用这次观察的 session、generation、target 和精确 label。每次 DOM 变化都需要重新观察。</li><li>动作保留原高危险审批。审批中核对准确标签与参数，结果在任务观察中读取原工具收据。</li><li>超时、取消或未确认动作不要自动重发；先核对目标系统，再建立新会话。关闭会话清理自有浏览器进程；强杀或操作系统占用可能留下临时文件，不能宣称已删除。</li></ol></details></section>`);
   const root=document.querySelector('[data-browser-workbench]'),d={root,identity:who,generation:0,result:null,busy:false};active=d;
   const form=root.querySelector('form'),status=root.querySelector('[data-bw-status]'),save=root.querySelector('[data-bw-save]'),result=root.querySelector('[data-bw-result]'),details=root.querySelector('details');
   function invalidate(){d.generation++;d.result=null;save.disabled=true;result.textContent='';details.hidden=true;status.textContent='输入已变化，请重新检查';}
   form.addEventListener('input',invalidate);
   form.onsubmit=async event=>{event.preventDefault();if(d.busy)return;d.busy=true;invalidate();const generation=d.generation,submit=form.querySelector('button');submit.disabled=true;
    try{await check(d);const body={};for(const key of ['node','playwright','executable'])body[key]=form.elements[key].value.trim();body.origins=form.elements.origins.value.split('\n').map(s=>s.trim()).filter(Boolean);
     const value=await api('/api/v1/browser-workbench/configuration',{method:'POST',body:JSON.stringify(body),cache:'no-store'});await check(d);if(d.generation!==generation)return;
     if(value.status!=='configuration_only'||value.process_started!==false||value.connection?.config?.enabled!==false)throw Error('配置响应无法核对');
     d.result=value.connection;result.textContent=JSON.stringify(value,null,2);details.hidden=false;save.disabled=false;status.textContent='配置已生成；零进程、未保存、未授权执行';
    }catch(error){if(error.status===401||error.status===403){clear();return;}if(live(d)&&d.generation===generation)status.textContent=error.message;}finally{if(live(d)){d.busy=false;submit.disabled=false;}}};
   save.onclick=async()=>{try{await check(d);if(!d.result)return;const value=d.result;clear();saveForm(value);}catch(error){if(live(d))status.textContent=error.message;}};
  }
  return {open,destroy:clear};
 }};
})();
