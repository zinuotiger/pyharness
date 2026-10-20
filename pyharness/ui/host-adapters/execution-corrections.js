/* Trusted root supplements. Durable hints contain references and hashes only. */
(function(global) {
  'use strict';
  global.PyharnessExecutionCorrections={create({main,api,esc}) {
    const c=global.PyharnessWorkspaceContracts,memory=new Map();let current=null;
    const same=(a,b)=>c.canonical(a)===c.canonical(b),id=()=>crypto.randomUUID().replaceAll('-','').slice(0,16);
    const matches=(v,p)=>typeof v==='string'&&p.test(v);
    const rootRef=r=>c.keys(r,'execution_id revision digest source_seq')&&matches(r.execution_id,/^ex-[0-9a-f]{16}$/)&&r.revision===1&&c.hash(r.digest)&&c.integer(r.source_seq);
    const live=(d,n=d.version)=>current===d&&d.el.isConnected&&d.el.open&&n===d.version;
    const q=(d,s)=>d.el.querySelector(s),base=d=>'/api/v1/projects/'+d.project.project_id;
    const path=d=>base(d)+'/executions/'+d.reference.execution_id+'/corrections';
    const slot=d=>'pyharness.correction.pending.v1:'+JSON.stringify([d.realm,d.project.project_id,d.reference.execution_id]);
    const say=(d,t)=>{if(live(d))q(d,'[data-cr-status]').textContent=t;};
    async function identity(){const who=await api('/api/account/me',{cache:'no-store'}),a=who.account;return JSON.stringify(a?[a.tenant,a.user_id||a.username]:['local',who.username||'local']);}
    function invalidate(){if(!current)return;const d=current;current=null;d.version++;clearTimeout(d.timer);d.el.replaceChildren();d.el.close();d.el.remove();d.trigger?.focus();}
    async function check(d,n){if(!live(d,n))throw Error('窗口已变化');if(await identity()!==d.realm){if(current===d)invalidate();throw Error('主体已变化');}if(!live(d,n))throw Error('窗口已变化');}
    async function request(d,n,url,options={}){await check(d,n);try{const r=await api(url,{cache:'no-store',...options});await check(d,n);return r;}catch(e){await check(d,n);throw e;}}
    function validHint(d,h){return c.keys(h,'realm project execution_ref correction_id expected_revision key text_digest request_digest')
      &&h.realm===d.realm&&h.project===d.project.project_id&&rootRef(h.execution_ref)&&same(h.execution_ref,d.reference)
      &&matches(h.correction_id,/^cr-[0-9a-f]{16}$/)&&c.integer(h.expected_revision,0,199)&&matches(h.key,/^[A-Za-z0-9._-]{1,128}$/)&&c.hash(h.text_digest)&&c.hash(h.request_digest);}
    function recover(d){const key=slot(d);let h=memory.get(key);try{h=h||JSON.parse(localStorage.getItem(key));}catch(_){}if(validHint(d,h)){memory.set(key,h);return h;}return null;}
    function save(d,h){memory.set(slot(d),h);try{localStorage.setItem(slot(d),JSON.stringify(h));return true;}catch(_){return false;}}
    function forget(d,h){if(memory.get(slot(d))?.key===h.key)memory.delete(slot(d));try{if(JSON.parse(localStorage.getItem(slot(d)))?.key===h.key)localStorage.removeItem(slot(d));}catch(_){}}
    const textHash=text=>c.digest(text);
    const command=(h,text)=>({schema_version:1,correction_id:h.correction_id,expected_revision:h.expected_revision,execution_ref:h.execution_ref,text});
    const fingerprint=(project,body)=>c.digest({operation:'execution.correct',project_id:project,command:body});
    function clearRows(d,clearDraft=false){d.view=null;q(d,'[data-cr-rows]').replaceChildren();q(d,'[data-cr-version]').textContent='';if(clearDraft){q(d,'[data-cr-text]').value='';d.dirty=false;}}
    function controls(d){
      if(!live(d))return;const hint=recover(d),latest=d.view?.corrections.at(-1),conflict=d.dirty&&d.view&&(d.base!==d.view.current_revision||(latest&&d.document!==latest.correction_id));
      d.el.querySelectorAll('button,textarea,input').forEach(n=>{if(n.dataset.crAction!=='close')n.disabled=d.busy;});
      q(d,'[data-cr-action=submit]').disabled=d.busy||!!hint||!d.view?.can_submit||conflict||d.base>=200;
      q(d,'[data-cr-action=retry]').disabled=d.busy||!hint;
      q(d,'[data-cr-action=abandon]').disabled=d.busy||!hint;
      q(d,'[data-cr-action=adopt]').disabled=d.busy||!!hint||!conflict;
      q(d,'[data-cr-hint]').textContent=hint?'原键待核对：'+hint.key+' · 基准 r'+hint.expected_revision+'。恢复线索不含原文；重试需填写完全相同的原文。':'';
      q(d,'[data-cr-count]').textContent=Array.from(q(d,'[data-cr-text]').value).length+' / 4000 字符'+(conflict?' · 草稿基准已落后，请先核对并采用当前基准。':'');
    }
    function receiptShape(d,r){return r?.schema_version===1&&r.project_id===d.project.project_id&&same(r.execution_ref,d.reference)
      &&matches(r.correction_id,/^cr-[0-9a-f]{16}$/)&&c.integer(r.revision,1,200)&&typeof r.text==='string'&&Array.from(r.text).length<=4000
      &&c.hash(r.digest)&&c.integer(r.source_seq)&&c.hash(r.request_digest)&&matches(r.submission_key,/^[A-Za-z0-9._-]{1,128}$/)
      &&r.persisted===true&&['pending','applied','superseded','unknown'].includes(r.application_status);}
    async function view(d,n){
      const v=await request(d,n,path(d));
      if(v?.schema_version!==1||v.project_id!==d.project.project_id||v.execution_id!==d.reference.execution_id||!same(v.execution_ref,d.reference)
        ||!Array.isArray(v.corrections)||v.corrections.length>200||v.total!==v.corrections.length||v.current_revision!==v.corrections.length||typeof v.can_submit!=='boolean'
        ||!v.corrections.every((r,i)=>receiptShape(d,r)&&r.revision===i+1&&r.correction_id===v.corrections[0].correction_id))throw Error('纠偏版本回执范围或顺序不符');
      return v;
    }
    function render(d,v){
      d.view=v;q(d,'[data-cr-version]').textContent='服务端当前修订 r'+v.current_revision+' · '+(v.can_submit?'运行中，可提交补充':'当前没有可接收新补充的本地执行；仅观察');
      const rows=q(d,'[data-cr-rows]');rows.replaceChildren();
      for(const r of v.corrections){const article=document.createElement('article'),label=document.createElement('strong'),body=document.createElement('pre');
        label.textContent='r'+r.revision+' · '+({pending:'待应用',applied:'上下文已准备',superseded:'已被新修订替代',unknown:'应用情况未确认'}[r.application_status]);
        body.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere';body.textContent=r.text;article.append(label,body);rows.append(article);}
      if(!d.dirty&&!recover(d)){const latest=v.corrections.at(-1);d.document=latest?.correction_id||d.document;d.base=v.current_revision;q(d,'[data-cr-text]').value=latest?.text||'';}
      controls(d);
    }
    async function refresh(d,n){clearRows(d);render(d,await view(d,n));say(d,'已核对服务端版本。补充只在下一轮合法上下文中应用；不撤回在飞工具，也不自动创建新轮。');}
    async function verifyReceipt(d,n,key,r,hint){
      if(!receiptShape(d,r)||r.submission_key!==key)throw Error('原键纠偏回执范围不符');
      const h=hint||{correction_id:r.correction_id,expected_revision:r.revision-1,execution_ref:r.execution_ref};
      const body=command(h,r.text),digest=await fingerprint(d.project.project_id,body);await check(d,n);
      if(r.revision!==h.expected_revision+1||r.correction_id!==h.correction_id||digest!==r.request_digest
        ||(hint&&(digest!==hint.request_digest||await textHash(r.text)!==hint.text_digest)))throw Error('原键与原选择摘要不符');
      await check(d,n);return r;
    }
    async function query(d,n,key){
      if(!/^[A-Za-z0-9._-]{1,128}$/.test(key))throw Error('请输入合法原纠偏键');
      const h=recover(d),v=await request(d,n,base(d)+'/correction-submissions/by-key?key='+encodeURIComponent(key));
      if(v.project_id!==d.project.project_id||v.submission_key!==key)throw Error('原键查询范围不符');
      if(v.status==='not_found'&&v.correction===null){say(d,'尚未找到已确认事实。保留原键原文；这不证明先前请求不会稍后提交。');return;}
      if(v.status!=='committed')throw Error('原键状态未确认');
      await verifyReceipt(d,n,key,v.correction,h?.key===key?h:null);
      if(h?.key===key){forget(d,h);d.dirty=false;}
      await refresh(d,n);say(d,'原纠偏键已从服务端精确核对；没有重发。');
    }
    async function send(d,n,h,text){
      const body=command(h,text);
      if(await textHash(text)!==h.text_digest||await fingerprint(d.project.project_id,body)!==h.request_digest)throw Error('原文或原选择摘要不符，禁止重发');
      await check(d,n);const durable=save(d,h);controls(d);q(d,'[data-cr-key]').value=h.key;
      say(d,'正在提交版本，原键已封存。'+(durable?'':'浏览器持久线索不可用，请记下原键：'+h.key));
      await request(d,n,path(d),{method:'POST',body:JSON.stringify(body),headers:{'Idempotency-Key':h.key}});
      await query(d,n,h.key);
    }
    async function abandon(d,n){
      const h=recover(d);if(!h)throw Error('没有待核对线索');
      const qv=await request(d,n,base(d)+'/correction-submissions/by-key?key='+encodeURIComponent(h.key));
      if(qv.project_id!==d.project.project_id||qv.submission_key!==h.key)throw Error('原键查询范围不符');
      if(qv.status==='committed'){await query(d,n,h.key);return;}
      if(qv.status!=='not_found'||qv.correction!==null)throw Error('原键状态未确认');
      const v=await view(d,n),found=v.corrections.find(r=>r.submission_key===h.key);
      if(found){await verifyReceipt(d,n,h.key,found,h);forget(d,h);d.dirty=false;render(d,v);say(d,'原命令已提交并核对，没有弃置。');return;}
      const latest=v.corrections.at(-1);
      if(v.current_revision<=h.expected_revision||(!same(latest.correction_id,h.correction_id)&&h.expected_revision!==0))throw Error('尚不能证明原命令已失效，线索继续保留');
      // The complete locked history has advanced, lacks this key, and the
      // original expected revision can no longer commit. No POST is issued.
      await check(d,n);forget(d,h);d.dirty=true;d.document=latest.correction_id;d.base=v.current_revision;render(d,v);
      say(d,'原命令已被版本约束阻止提交，已弃置旧线索。草稿保留；需要明确提交新版本。');
    }
    async function operate(d,action){
      if(action==='close'){invalidate();return;}if(!live(d)||d.busy||!d.realm)return;
      const n=++d.version;d.busy=true;clearTimeout(d.timer);controls(d);
      try{
        await check(d,n);
        if(action==='refresh')await refresh(d,n);
        else if(action==='query')await query(d,n,q(d,'[data-cr-key]').value.trim());
        else if(action==='abandon')await abandon(d,n);
        else if(action==='adopt'){
          if(!d.view||recover(d))throw Error('先核对原命令');
          const latest=d.view.corrections.at(-1);d.base=d.view.current_revision;d.document=latest?.correction_id||d.document;
          say(d,'已采用当前版本作为草稿基准，原文草稿保留；尚未提交。');
        }else{
          const text=q(d,'[data-cr-text]').value;
          if(!text.trim()||Array.from(text).length>4000||new TextDecoder().decode(new TextEncoder().encode(text))!==text)throw Error('请输入1–4000个有效Unicode字符');
          let h=recover(d);
          if(action==='submit'){
            if(h||!d.view?.can_submit||d.base!==d.view.current_revision||d.base>=200)throw Error('先核对原命令或最新基准');
            const latest=d.view.corrections.at(-1);
            if(latest&&latest.correction_id!==d.document)throw Error('纠偏文档已变化，请采用当前基准');
            h={realm:d.realm,project:d.project.project_id,execution_ref:d.reference,correction_id:d.document,expected_revision:d.base,
              key:'correct-'+id(),text_digest:await textHash(text),request_digest:await fingerprint(d.project.project_id,command({correction_id:d.document,expected_revision:d.base,execution_ref:d.reference},text))};
            await check(d,n);save(d,h);
          }else if(action!=='retry'||!h)throw Error('没有可用的原命令线索');
          await send(d,n,h,text);
        }
      }catch(e){if(live(d,n)){clearRows(d,e.status===401||e.status===403||!d.dirty);say(d,'操作未确认：'+e.message+'。原键线索保留；没有自动重发。');}}
      finally{if(live(d,n)){d.busy=false;controls(d);}}
    }
    async function open(project,reference,trigger=null){
      invalidate();if(!rootRef(reference))throw Error('执行引用不完整');
      const el=document.createElement('dialog'),d={el,project,reference,trigger,realm:'',version:0,busy:false,dirty:false,base:0,document:'cr-'+id(),view:null,timer:null};current=d;
      el.setAttribute('aria-label','运行中纠偏');el.style.cssText='width:min(900px,94vw);max-height:90vh;overflow:auto;padding:24px;font:inherit;box-sizing:border-box';
      el.innerHTML=`<h2>运行中纠偏 · ${esc(project.name)}</h2><button data-cr-action="close">关闭</button> <button data-cr-action="refresh">核对版本</button>
        <p>补充只用于这个执行的下一轮主任务上下文，不撤回已经在飞的模型或工具。新修订替换先前待应用补充；任务结束后不会自动创建新轮。</p>
        <p data-cr-version></p><p data-cr-status role="status"></p><p data-cr-hint style="overflow-wrap:anywhere"></p>
        <label>本次完整补充文本<textarea data-cr-text rows="6" maxlength="8000" style="width:100%;box-sizing:border-box"></textarea></label><p data-cr-count></p>
        <button data-cr-action="submit">提交新修订</button> <button data-cr-action="adopt">保留草稿并采用当前基准</button>
        <h3>核对原命令</h3><label>原纠偏键 <input data-cr-key maxlength="128"></label> <button data-cr-action="query">只读核对原键</button>
        <button data-cr-action="retry">原键原文重试</button> <button data-cr-action="abandon">核对并弃置已过期命令</button>
        <p>浏览器恢复线索只保存引用和摘要，不保存补充原文。上下文准备成功不代表模型已经返回或业务验收通过。</p>
        <h3>服务端版本与应用观察</h3><section data-cr-rows></section>`;
      main.append(el);el.showModal();controls(d);
      el.oncancel=e=>{e.preventDefault();invalidate();};el.onclick=e=>{const b=e.target.closest('[data-cr-action]');if(b)operate(d,b.dataset.crAction);};
      q(d,'[data-cr-text]').oninput=()=>{if(d.busy)return;d.dirty=true;controls(d);};
      try{const n=d.version,realm=await identity();if(!live(d,n))return;d.realm=realm;const h=recover(d);if(h)q(d,'[data-cr-key]').value=h.key;controls(d);await operate(d,'refresh');}
      catch(e){if(live(d))say(d,'身份核对失败：'+e.message);}
    }
    return {open,invalidate};
  }};
})(window);
