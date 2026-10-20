/* Reference-only recovery hints; the current server remains the sole authority. */
(function(global) {
  'use strict';
  global.PyharnessExecutions = {create({main, api, esc}) {
    const c = global.PyharnessWorkspaceContracts, memory = new Map();
    const same = (a,b) => c.canonical(a) === c.canonical(b);
    const hex = () => crypto.randomUUID().replaceAll('-','').slice(0,16);
    const pick = (v,k) => Object.fromEntries([k,'revision','digest','source_seq'].map(n=>[n,v[n]]));
    const validRef = (v,k,pattern) => c.keys(v,k+' revision digest source_seq') && pattern.test(v[k])
      && v.revision===1 && c.hash(v.digest) && c.integer(v.source_seq);
    const attemptRef = v => validRef(v,'attempt_id',/^ra-[0-9a-f]{16}$/);
    const executionRef = v => validRef(v,'execution_id',/^ex-[0-9a-f]{16}$/);
    const agentRef = v => c.keys(v,'agent_id version sha256') && /^a-[0-9a-f]{32}$/.test(v.agent_id)
      && c.integer(v.version) && c.hash(v.sha256);
    let current = null, correctionPanel = null, recoveryPanel = null, observationPanel = null, regressionPanel = null;
    const live = (d,n=d.version) => current===d && d.el.open && d.el.isConnected && n===d.version;
    const q = (d,s) => d.el.querySelector(s);
    const say = (d,s) => { if(live(d)) q(d,'[data-ex-status]').textContent=s; };
    const base = d => '/api/v1/projects/'+d.project.project_id;
    const slot = (d,kind) => 'pyharness.execution.pending.v1:'+JSON.stringify([d.realm,d.project.project_id,d.attempt,kind]);
    async function identity() {
      let who;try{who=await api('/api/account/me',{cache:'no-store'});}catch(error){error.requestFailed=true;throw error;}const a=who.account;
      return JSON.stringify(a?[a.tenant,a.user_id||a.username]:['local',who.username||'local']);
    }
    function invalidate() {
      regressionPanel?.invalidate();
      observationPanel?.invalidate();
      correctionPanel?.invalidate();
      recoveryPanel?.invalidate();
      if(!current)return;
      const d=current;current=null;d.version++;clearTimeout(d.timer);
      d.el.replaceChildren();d.el.close();d.el.remove();
    }
    async function check(d,n) {
      if(!live(d,n))throw Error('窗口已变化');
      if(await identity()!==d.realm){if(current===d)invalidate();throw Error('主体已变化，已清除私有内容');}
      if(!live(d,n))throw Error('窗口已变化');
    }
    async function request(d,n,path,options={}) {
      await check(d,n);
      try {const value=await api(path,{cache:'no-store',...options});await check(d,n);return value;}
      catch(error){await check(d,n);error.requestFailed=true;throw error;}
    }
    function save(d,r) {
      const key=slot(d,r.kind);memory.set(key,r);
      try{localStorage.setItem(key,JSON.stringify(r));return true;}catch(_){return false;}
    }
    function forget(d,r) {
      const key=slot(d,r.kind);
      if(memory.get(key)?.key===r.key)memory.delete(key);
      try{if(JSON.parse(localStorage.getItem(key))?.key===r.key)localStorage.removeItem(key);}catch(_){}
    }
    function validHint(d,r,kind) {
      if(!c.keys(r,'realm project attempt kind key body digest') || r.realm!==d.realm || r.project!==d.project.project_id
        || r.attempt!==d.attempt || r.kind!==kind || !/^[A-Za-z0-9._-]{1,128}$/.test(r.key)
        || typeof r.body!=='string' || r.body.length>5000 || !c.hash(r.digest))return false;
      try {
        const b=JSON.parse(r.body);
        if(b.schema_version!==1)return false;
        if(kind==='start')return c.keys(b,'schema_version attempt_ref agent_ref execution_id confirmation_digest confirm_host_boundary')
          && attemptRef(b.attempt_ref) && b.attempt_ref.attempt_id===d.attempt && agentRef(b.agent_ref)
          && /^ex-[0-9a-f]{16}$/.test(b.execution_id) && c.hash(b.confirmation_digest) && typeof b.confirm_host_boundary==='boolean';
        return c.keys(b,'schema_version cancellation_id execution_ref') && /^ec-[0-9a-f]{16}$/.test(b.cancellation_id) && executionRef(b.execution_ref);
      }catch(_){return false;}
    }
    function recover(d,kind) {
      const key=slot(d,kind);let r=memory.get(key);
      try{r=r||JSON.parse(localStorage.getItem(key));}catch(_){}
      if(validHint(d,r,kind)){memory.set(key,r);return r;}
      return null;
    }
    const fingerprint = r => c.digest({operation:r.kind==='start'?'execution.start':'execution.cancel',project_id:r.project,command:JSON.parse(r.body)});
    function controls(d) {
      if(!live(d))return;
      const start=recover(d,'start'),cancel=recover(d,'cancel');
      q(d,'[data-ex-hints]').textContent=[start&&'开始待核对键：'+start.key,cancel&&'停止待核对键：'+cancel.key].filter(Boolean).join('\n');
      d.el.querySelectorAll('button,input,select').forEach(n=>{if(n.dataset.exAction!=='close')n.disabled=d.busy;});
      q(d,'[data-ex-action=preview]').disabled=d.busy||!d.attemptRef||!!d.control?.execution_ref||!d.agents.length||!!start;
      q(d,'[data-ex-action=start]').disabled=d.busy||!d.preview||!!d.control?.execution_ref||!!start;
      q(d,'[data-ex-action=stop]').disabled=d.busy||!d.control?.execution_ref||!!d.control?.cancellation||!!cancel;
      q(d,'[data-ex-action=corrections]').disabled=d.busy||!d.control?.execution_ref;
      q(d,'[data-ex-action=recovery-plan]').disabled=d.busy||!d.control?.execution_ref;
      q(d,'[data-ex-action=regression-data]').disabled=d.busy||!d.control?.execution_ref;
      for(const kind of ['start','cancel'])for(const action of ['query','retry'])q(d,`[data-ex-action=${action}-${kind}]`).disabled=d.busy||!recover(d,kind);
    }
    function clearPrivate(d) {
      observationPanel?.invalidate();
      d.preview=null;d.agents=[];
      q(d,'[data-ex-agent]').replaceChildren();q(d,'[data-ex-preview]').textContent='';
      q(d,'[data-ex-result]').textContent='';q(d,'[data-ex-summary]').textContent='';q(d,'[data-ex-answer]').textContent='';
      q(d,'[data-ex-health]').textContent='';
      q(d,'[data-ex-artifacts]').replaceChildren();q(d,'[data-ex-host]').checked=false;
    }
    async function agents(d,n) {
      const listed=await request(d,n,'/api/v1/agents');
      if(!Array.isArray(listed.agents)||listed.agents.length>100)throw Error('Agent 列表超出本界面加载范围');
      const versions=await Promise.all(listed.agents.filter(a=>a.status!=='deprecated').map(async a=>{
        const rows=await request(d,n,'/api/v1/agents/'+a.agent_id+'/versions');
        if(!Array.isArray(rows.versions)||rows.versions.length>200)throw Error('Agent 版本列表超出本界面加载范围');
        return rows.versions.filter(v=>v.snapshot?.status==='published').map(v=>({ref:{agent_id:v.agent_id,version:v.version,sha256:v.sha256},name:v.snapshot.name}));
      }));
      if(!live(d,n))return;
      d.agents=versions.flat().filter(v=>agentRef(v.ref));
      q(d,'[data-ex-agent]').innerHTML='<option value="">请选择已发布版本</option>'+d.agents.map((a,i)=>`<option value="${i}">${esc(a.name)} · v${a.ref.version} · ${esc(a.ref.agent_id)}</option>`).join('');
    }
    function controlMatches(d,v) {
      return v?.schema_version===1 && v.control_only===true && v.project_id===d.project.project_id
        && attemptRef(v.attempt_ref) && v.attempt_ref.attempt_id===d.attempt
        && ((v.status==='not_authorized'&&v.execution_ref===null&&v.cancellation===null)
          ||(v.status==='authorized'&&executionRef(v.execution_ref)));
    }
    function drawStop(d,value) {
      q(d,'[data-ex-stop]').textContent=!value?'尚未请求停止':value.execution_stopped===true?'已核对：实际执行已停'
        :value.execution_stopped===false?'已保存停止请求；执行尚未停止':'已保存停止请求；实际停止状态未知';
    }
    function drawHealth(d,result) {
      const v=result.control_observation,h=v?.heartbeat;
      if(v?.schema_version!==1||v.service_status!=='connected'||v.replay_authorized!==false||v.automatic_dispatch!==false
        ||!['live','absent'].includes(v.execution_owner)||h?.scope!=='service_event_loop'){
        q(d,'[data-ex-health]').textContent='运行观测暂不可用；没有据此恢复或重发。';return;
      }
      const activity={terminal:'执行已结束',interrupted:'当前服务没有这个执行的本地执行者',heartbeat_expired:'服务心跳过期，执行进展未知',heartbeat_unavailable:'服务心跳监视已停止，执行进展未知',waiting_approval:'等待原工具审批',queued:'已在原队列等待',running_unclassified:'原执行仍在运行，具体进展以收据为准'}[v.activity]||'活动未确认';
      const heart={fresh:'当前服务心跳正常',expired:'当前服务心跳过期',inactive:'当前服务心跳监视已停止',not_observed:'没有当前服务心跳证据'}[h.state]||'心跳未知';
      const count=Number.isSafeInteger(v.approval_waits_total)&&v.approval_waits_total>=0?v.approval_waits_total:'未知';
      q(d,'[data-ex-health]').textContent='连接：服务已响应。'+activity+'。\n'+heart+'；待审批 '+count+' 项。'+
        (v.approval_coverage==='partial'?'审批观察不完整。':'')+'\n心跳只观察服务事件循环，不证明模型或工具已完成。失联和过期不会自动重新投递。';
    }
    function drawUnavailable(d,error) {
      q(d,'[data-ex-health]').textContent=error.status===401||error.status===403?'当前读取权限不足；执行进展未知。':error.status?'服务返回错误；执行进展未知，不会自动重新投递。':error.requestFailed?'服务失联或请求未完成；执行进展未知，不会自动重新投递。':'运行观察未确认；不会自动重新投递。';
    }
    async function observe(d,n) {
      clearPrivate(d);d.control=null;d.attemptRef=null;drawStop(d,null);
      const control=await request(d,n,base(d)+'/run-attempts/'+d.attempt+'/execution-control');
      if(!controlMatches(d,control))throw Error('控制收据范围不符');
      d.control=control;d.attemptRef=control.attempt_ref;drawStop(d,control.cancellation);
      if(!control.execution_ref){await agents(d,n);say(d,'尚无执行授权。请选择发布版本并预览，再明确开始。');return;}
      try {
        const result=await request(d,n,base(d)+'/executions/'+control.execution_ref.execution_id);
        if(result.project_id!==d.project.project_id||!same(pick(result,'execution_id'),control.execution_ref)||!same(result.attempt_ref,control.attempt_ref))throw Error('执行收据范围不符');
        drawHealth(d,result);
        q(d,'[data-ex-summary]').textContent=`执行状态：${result.status} · ${result.execution_reason||''}\n`+
          (result.unknown_effects?'存在未确认效果，需要核对。':'当前返回的效果记录未标记未知。')+'\n业务验收：'+({not_evaluated:'尚未评定',passed:'独立验收通过',rejected:'独立验收不通过',pending:'尚无法确认',invalidated:'原验收已失效，需重新核对'}[result.business_acceptance]||'尚未评定')+'。执行结束不代表交付验收通过。'+
          (result.result_truncated?'\n结果摘要已截断。':'')+(result.children_truncated?'\n子任务展示已截断。':'');
        q(d,'[data-ex-result]').textContent=JSON.stringify(result,null,2);
        q(d,'[data-ex-answer]').textContent=result.result_summary||'尚无结果摘要。';
        for(const item of result.artifacts||[]) {
          const line=document.createElement('p');
          line.textContent=item.filename+' · '+({verified:'原字节校验一致',mismatch:'原字节校验不符',unavailable:'原字节无法核对'}[item.integrity]||'校验未知')+
            ' · '+(item.download_allowed?'已有下载许可':'下载尚未获准')+' · '+item.artifact_id;
          q(d,'[data-ex-artifacts]').append(line);
        }
        say(d,'已读取原执行收据。文件哈希是字节观察；产物校验和下载许可分别显示。');
        if(['preparing','queued','running'].includes(result.status))schedule(d);
      }catch(error){
        if(!live(d,n))throw error;
        correctionPanel?.invalidate();
        recoveryPanel?.invalidate();
        clearPrivate(d);say(d,'结果当前不可读取：'+error.message+'。已清除正文；可通过控制入口请求停止。');
        drawUnavailable(d,error);
        if(control.cancellation?.execution_stopped!==true)schedule(d);
      }
    }
    function schedule(d) {
      clearTimeout(d.timer);
      d.timer=setTimeout(()=>{if(live(d)&&!d.busy)operate(d,'refresh');},3000);
    }
    function matches(d,r,value) {
      const b=JSON.parse(r.body);
      if(value?.project_id!==r.project||value.submission_key!==r.key||value.request_digest!==r.digest||value.persisted!==true)return false;
      if(r.kind==='start')return executionRef(pick(value,'execution_id'))&&value.execution_id===b.execution_id&&value.execution_authorized===true
        && same(value.attempt_ref,b.attempt_ref)&&same(value.agent_ref,b.agent_ref);
      return value.cancellation_id===b.cancellation_id&&same(value.execution_ref,b.execution_ref)&&c.hash(value.digest)&&c.integer(value.source_seq);
    }
    async function reconcile(d,n,r) {
      if(await fingerprint(r)!==r.digest)throw Error('恢复线索摘要不符');
      const path=r.kind==='start'?'/execution-submissions/by-key':'/cancellation-submissions/by-key';
      const value=await request(d,n,base(d)+path+'?key='+encodeURIComponent(r.key));
      if(value.project_id!==r.project||value.submission_key!==r.key)throw Error('原键查询范围不符');
      if(value.status==='not_found'){say(d,'尚未找到已确认事实。保留原键原选择；查询不表示先前请求不会稍后提交。');return;}
      const receipt=r.kind==='start'?value.execution:value.cancellation;
      if(value.status!=='committed'||!matches(d,r,receipt))throw Error('原键与封存选择不一致');
      forget(d,r);await observe(d,n);
    }
    async function send(d,n,r) {
      if(await fingerprint(r)!==r.digest)throw Error('恢复线索摘要不符，禁止重发');
      // A cancel hint is never authority to select a different execution.
      if(r.kind==='cancel'&&!same(JSON.parse(r.body).execution_ref,d.control?.execution_ref))throw Error('停止目标尚未由当前服务端控制收据确认');
      const durable=save(d,r);controls(d);
      say(d,'正在提交；已封存原键与选择。'+(durable?'':'浏览器持久线索不可用，请记下原键：'+r.key));
      await request(d,n,base(d)+(r.kind==='start'?'/executions':'/execution-cancellations'),{method:'POST',body:r.body,headers:{'Idempotency-Key':r.key}});
      await reconcile(d,n,r);
    }
    async function makeRecord(d,kind,body) {
      const r={realm:d.realm,project:d.project.project_id,attempt:d.attempt,kind,key:(kind==='start'?'execute-':'stop-')+hex(),body:JSON.stringify(body),digest:null};
      r.digest=await fingerprint(r);return r;
    }
    async function operate(d,action) {
      if(action==='close'){invalidate();return;}
      if(!live(d)||d.busy||!d.realm)return;
      clearTimeout(d.timer);const n=++d.version;d.busy=true;controls(d);
      try {
        await check(d,n);
        if(action==='refresh') {
          const attempt=q(d,'[data-ex-attempt]').value.trim();
          if(!/^ra-[0-9a-f]{16}$/.test(attempt))throw Error('请输入有效的运行尝试 ID');
          d.attempt=attempt;await observe(d,n);
        }else if(action==='preview') {
          d.preview=null;q(d,'[data-ex-preview]').textContent='';q(d,'[data-ex-host]').checked=false;
          const selected=d.agents[Number(q(d,'[data-ex-agent]').value)];
          if(q(d,'[data-ex-agent]').value===''||!selected||!d.attemptRef||d.control?.execution_ref)throw Error('先核对尝试并选择已发布版本');
          const body={schema_version:1,attempt_ref:d.attemptRef,agent_ref:selected.ref};
          const value=await request(d,n,base(d)+'/execution-previews',{method:'POST',body:JSON.stringify(body)});
          if(value.project_id!==d.project.project_id||!same(value.attempt_ref,body.attempt_ref)||!same(value.agent_ref,body.agent_ref)
            ||!c.hash(value.confirmation_digest)||value.status!=='confirmation_required'||!['disabled','host_unrestricted','container','remote'].includes(value.boundary))throw Error('预览收据不完整');
          d.preview=value;q(d,'[data-ex-preview]').textContent='执行边界：'+({disabled:'工具沙箱未启用',host_unrestricted:'宿主执行：不提供操作系统、网络或内存隔离，代码可能访问宿主资源',container:'容器执行',remote:'远程执行'}[value.boundary])+'\n确认摘要：'+value.confirmation_digest;
          say(d,'预览完成，尚未开始执行。');
        }else if(action==='start') {
          if(!d.preview||recover(d,'start')||d.control?.execution_ref)throw Error('先完成新预览或核对原键');
          if(d.preview.boundary==='host_unrestricted'&&!q(d,'[data-ex-host]').checked)throw Error('宿主执行需明确勾选边界确认');
          const body={schema_version:1,attempt_ref:d.preview.attempt_ref,agent_ref:d.preview.agent_ref,execution_id:'ex-'+hex(),confirmation_digest:d.preview.confirmation_digest,confirm_host_boundary:q(d,'[data-ex-host]').checked};
          const r=await makeRecord(d,'start',body);await check(d,n);save(d,r);await send(d,n,r);
        }else if(action==='stop') {
          if(!d.control?.execution_ref||d.control.cancellation||recover(d,'cancel'))throw Error('先核对控制状态或原停止键');
          const r=await makeRecord(d,'cancel',{schema_version:1,cancellation_id:'ec-'+hex(),execution_ref:d.control.execution_ref});
          await check(d,n);save(d,r);await send(d,n,r);
        }else if(action==='regression-data') {
          if(!d.control?.execution_ref)throw Error('先核对实际执行记录');
          if(!global.PyharnessRegressionData)throw Error('回归用例界面尚未加载，请刷新页面');
          regressionPanel=regressionPanel||global.PyharnessRegressionData.create({main,api,esc});
          await regressionPanel.open(d.project,q(d,'[data-ex-action=regression-data]'),{execution_id:d.control.execution_ref.execution_id});
        }else if(action==='observations') {
          if(!global.PyharnessObservations)throw Error('运行观测界面尚未加载，请刷新页面');
          observationPanel=observationPanel||global.PyharnessObservations.create({main,api,esc});
          const selected=d.agents[Number(q(d,'[data-ex-agent]').value)];
          const selection=d.preview?{schema_version:1,attempt_ref:d.preview.attempt_ref,agent_ref:d.preview.agent_ref}
            :q(d,'[data-ex-agent]').value!==''&&selected&&d.attemptRef?{schema_version:1,attempt_ref:d.attemptRef,agent_ref:selected.ref}:null;
          await observationPanel.open(d.project,q(d,'[data-ex-action=observations]'),{selection,execution_id:d.control?.execution_ref?.execution_id});
        }else if(action==='recovery-plan') {
          if(!d.control?.execution_ref)throw Error('先核对已有执行');
          if(!global.PyharnessRecoveryPlans)throw Error('恢复预检界面尚未加载，请刷新页面');
          recoveryPanel=recoveryPanel||global.PyharnessRecoveryPlans.create({main,api,esc,onOpenAttempt:(project,attempt)=>open(project,attempt)});
          await recoveryPanel.open(d.project,d.control.execution_ref,q(d,'[data-ex-action=recovery-plan]'));
          await observe(d,n);
        }else if(action==='corrections') {
          if(!d.control?.execution_ref)throw Error('先核对已有执行');
          if(!global.PyharnessExecutionCorrections)throw Error('纠偏界面尚未加载，请刷新页面');
          correctionPanel=correctionPanel||global.PyharnessExecutionCorrections.create({main,api,esc});
          await correctionPanel.open(d.project,d.control.execution_ref,q(d,'[data-ex-action=corrections]'));
          await observe(d,n);
        }else {
          const [verb,kind]=action.split('-'),r=recover(d,kind);
          if(!r)throw Error('没有可用的原键线索');
          if(verb==='query')await reconcile(d,n,r);
          else if(verb==='retry')await send(d,n,r);
        }
      }catch(error){if(live(d,n)){if(error.status===401||error.status===403||error.requestFailed){clearPrivate(d);correctionPanel?.invalidate();recoveryPanel?.invalidate();drawUnavailable(d,error);}say(d,'操作未确认：'+error.message+'。待核对的原键和原选择保留；没有自动重发。');}}
      finally{if(live(d,n)){d.busy=false;controls(d);}}
    }
    async function open(project,attempt='') {
      invalidate();const el=document.createElement('dialog');
      const d={el,project,attempt,realm:'',version:0,busy:false,agents:[],attemptRef:null,control:null,preview:null,timer:null};current=d;
      el.setAttribute('aria-label','任务执行与收据');el.style.cssText='width:min(1000px,94vw);max-height:90vh;overflow:auto;padding:24px;font:inherit';
      el.innerHTML=`<h2>任务执行与收据 · ${esc(project.name)}</h2><button data-ex-action="close">关闭</button>
        <p>核对既有尝试、选择发布版本并明确开始。页面刷新和状态查询不会自动执行。</p>
        <label>运行尝试 ID <input data-ex-attempt maxlength="19" value="${esc(attempt)}"></label> <button data-ex-action="refresh">核对执行</button>
        <p role="status" data-ex-status></p><pre data-ex-hints style="white-space:pre-wrap"></pre>
        <label>发布的 Agent 版本 <select data-ex-agent></select></label> <button data-ex-action="preview">预览执行</button>
        <pre data-ex-preview style="white-space:pre-wrap;overflow-wrap:anywhere"></pre>
        <label><input type="checkbox" data-ex-host> 我确认宿主执行边界及其资源访问权限</label> <button data-ex-action="start">明确开始执行</button>
        <p>工具需要人工审批时，可<a href="#approvals" target="_blank" rel="noopener">打开审批中心</a>核对并处理；批准后仍会检查权限。</p>
        <p><button data-ex-action="query-start">核对原开始键</button> <button data-ex-action="retry-start">原开始键原选择重试</button></p>
        <p><button data-ex-action="corrections">运行中纠偏 / 查看版本</button> <button data-ex-action="recovery-plan">只读恢复预检</button> <button data-ex-action="observations">上下文 / 权限预演 / 运行比较</button> <button data-ex-action="regression-data">失败记录转回归用例</button></p>
        <h3>停止控制</h3><button data-ex-action="stop">请求停止</button> <button data-ex-action="query-cancel">核对原停止键</button> <button data-ex-action="retry-cancel">原停止键原目标重试</button><p data-ex-stop></p>
        <h3>长任务观察</h3><p data-ex-health style="white-space:pre-wrap"></p>
        <h3>执行结果</h3><p data-ex-summary style="white-space:pre-wrap"></p><pre data-ex-answer style="white-space:pre-wrap;overflow-wrap:anywhere"></pre><section data-ex-artifacts style="overflow-wrap:anywhere"></section><details><summary>原始执行、文件观察、子任务与产物收据</summary><pre data-ex-result style="white-space:pre-wrap;overflow-wrap:anywhere"></pre></details>`;
      main.append(el);el.showModal();controls(d);
      el.oncancel=e=>{e.preventDefault();invalidate();};
      el.onclick=e=>{const b=e.target.closest('[data-ex-action]');if(b)operate(d,b.dataset.exAction);};
      el.onchange=e=>{
        if(d.busy)return;
        if(e.target.matches('[data-ex-agent]')){d.version++;d.preview=null;q(d,'[data-ex-preview]').textContent='';q(d,'[data-ex-host]').checked=false;controls(d);}
        if(e.target.matches('[data-ex-attempt]')){d.version++;clearTimeout(d.timer);d.attempt=e.target.value.trim();clearPrivate(d);d.control=null;d.attemptRef=null;drawStop(d,null);controls(d);}
      };
      try{const n=d.version,realm=await identity();if(!live(d,n))return;d.realm=realm;controls(d);if(attempt)await operate(d,'refresh');else say(d,'请输入运行尝试 ID 后只读核对。');}
      catch(error){if(live(d))say(d,'身份核对失败：'+error.message);}
    }
    return {open,invalidate};
  }};
})(window);
