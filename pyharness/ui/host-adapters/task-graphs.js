/* Fixed references, visible edges, explicit submission. GETs never dispatch. */
(function(global){
  'use strict';
  global.PyharnessTaskGraphs={create({main,api,esc}){
    const c=global.PyharnessWorkspaceContracts, memory=new Map(), hex=()=>crypto.randomUUID().replaceAll('-','').slice(0,16);
    const ref=(v,k)=>Object.fromEntries([k,'revision','digest','source_seq'].map(n=>[n,v[n]]));
    const same=(a,b)=>c.canonical(a)===c.canonical(b);
    let current=null;
    const live=(d,n=d.epoch)=>current===d&&d.el.isConnected&&d.el.open&&n===d.epoch;
    const q=(d,s)=>d.el.querySelector(s),base=d=>'/api/v1/projects/'+d.project.project_id;
    const say=(d,s)=>{if(live(d))q(d,'[data-graph-status]').textContent=s;};
    async function identity(){const who=await api('/api/account/me',{cache:'no-store'}),a=who.account;return JSON.stringify(a?[a.tenant,a.user_id||a.username]:['local',who.username||'local']);}
    function invalidate(){if(!current)return;const d=current;current=null;d.epoch++;d.el.replaceChildren();d.el.close();d.el.remove();}
    async function check(d,n){if(!live(d,n))throw Error('窗口已变化');if(await identity()!==d.realm){invalidate();throw Error('主体已变化');}if(!live(d,n))throw Error('窗口已变化');}
    function privateClear(d){d.preview=null;d.graph=null;d.graphs=[];d.tasks=[];d.agents=[];d.docs=[];d.cites=[];d.deliveries=[];d.nodes=[];q(d,'[data-graph-editor]').replaceChildren();q(d,'[data-graph-svg]').replaceChildren();q(d,'[data-graph-result]').replaceChildren();q(d,'[data-graph-graphs]').replaceChildren();q(d,'[data-graph-title]').value='';q(d,'[data-graph-preview]').textContent='';}
    async function read(d,n,path,options={}){
      await check(d,n);
      try{const value=await api(path,{cache:'no-store',...options});await check(d,n);return value;}
      catch(error){if(live(d,n)){await check(d,n);if(error.status===403){privateClear(d);say(d,'权限或来源不可读取；仍可对已知图请求停止。');}}throw error;}
    }
    async function pages(d,n,path,field){let rows=[],cursor=null;for(let i=0;i<4;i++){
      const p=await read(d,n,path+'?limit=50'+(cursor?'&cursor='+encodeURIComponent(cursor):''));
      if(!Array.isArray(p[field]))throw Error('列表收据不完整');rows.push(...p[field]);cursor=p.next_cursor;if(!cursor)return rows;
    }throw Error('列表超过当前界面加载上限');}
    const slot=d=>'pyharness.graph.pending.v1:'+JSON.stringify([d.realm,d.project.project_id]);
    function validHint(d,r){
      if(!c.keys(r,'realm project kind key body digest')||r.realm!==d.realm||r.project!==d.project.project_id||!['start','cancel'].includes(r.kind)||
        !/^[A-Za-z0-9._-]{1,128}$/.test(r.key)||typeof r.body!=='string'||r.body.length>5000||!c.hash(r.digest))return false;
      try{const b=JSON.parse(r.body),k=r.kind==='start'?'graph_ref':'graph_run_ref',v=b[k];
        return b.schema_version===1&&c.keys(v,(k==='graph_ref'?'graph_id':'graph_run_id')+' revision digest source_seq')&&
          new RegExp(k==='graph_ref'?'^tg-[0-9a-f]{16}$':'^gr-[0-9a-f]{16}$').test(v[k==='graph_ref'?'graph_id':'graph_run_id'])&&c.integer(v.revision)&&c.integer(v.source_seq)&&c.hash(v.digest)&&
          (r.kind==='start'?c.keys(b,'schema_version graph_run_id graph_ref confirmation_digest confirm_host_boundary')&&/^gr-[0-9a-f]{16}$/.test(b.graph_run_id)&&c.hash(b.confirmation_digest)&&typeof b.confirm_host_boundary==='boolean':
            c.keys(b,'schema_version cancellation_id graph_run_ref')&&/^gc-[0-9a-f]{16}$/.test(b.cancellation_id));
      }catch(_){return false;}
    }
    const operation=r=>r.kind==='start'?'task_graph.start':'task_graph_cancellation';
    async function pending(d){let r=memory.get(slot(d));try{r=r||JSON.parse(localStorage.getItem(slot(d)));}catch(_){}if(!validHint(d,r))return null;
      if(await c.digest({operation:operation(r),project_id:r.project,command:JSON.parse(r.body)})!==r.digest)throw Error('待核对命令摘要不符，已拒绝提交');memory.set(slot(d),r);return r;}
    function remember(d,r){memory.set(slot(d),r);try{localStorage.setItem(slot(d),JSON.stringify(r));}catch(_){say(d,'浏览器无法保存核对线索，请保留原提交键。');}}
    function forget(d,r){if(memory.get(slot(d))?.key===r.key)memory.delete(slot(d));try{if(JSON.parse(localStorage.getItem(slot(d)))?.key===r.key)localStorage.removeItem(slot(d));}catch(_){}}
    function controls(d){if(!live(d))return;d.el.querySelectorAll('button,input,select').forEach(el=>{if(el.dataset.graphAction!=='close')el.disabled=d.busy;});
      q(d,'[data-graph-action=start]').disabled=d.busy||!d.preview||d.dirty||!!memory.get(slot(d));q(d,'[data-graph-action=preview]').disabled=d.busy||!d.graph||d.dirty||!!memory.get(slot(d));
      q(d,'[data-graph-action=stop]').disabled=d.busy||!d.run||!!d.cancelRequested;q(d,'[data-graph-action=retry]').disabled=d.busy||!memory.get(slot(d));}
    function svg(d,nodes,statuses=[]){
      const root=q(d,'[data-graph-svg]');root.replaceChildren();const ns='http://www.w3.org/2000/svg',positions=new Map(),levels=new Map();
      function depth(id,seen=new Set()){if(seen.has(id))return 0;const n=nodes.find(n=>n.node_id===id);if(!n)return 0;seen.add(id);return n.depends_on.length?1+Math.max(...n.depends_on.map(p=>depth(p,new Set(seen)))):0;}
      for(const node of nodes){const col=Math.min(19,depth(node.node_id)),row=levels.get(col)||0;levels.set(col,row+1);positions.set(node.node_id,[20+col*190,35+row*75]);}
      root.setAttribute('viewBox',`0 0 ${Math.max(440,...[...positions.values()].map(v=>v[0]+185))} ${Math.max(150,...[...positions.values()].map(v=>v[1]+70))}`);
      const add=(name,attributes,text)=>{const el=document.createElementNS(ns,name);for(const [k,v]of Object.entries(attributes))el.setAttribute(k,String(v));if(text!=null)el.textContent=text;root.append(el);};
      for(const node of nodes){const [x,y]=positions.get(node.node_id);for(const dep of node.depends_on){const p=positions.get(dep);if(p)add('path',{d:`M ${p[0]+150} ${p[1]+20} L ${x} ${y+20}`,stroke:'#64748b','stroke-width':2,fill:'none'});}}
      for(const node of nodes){const [x,y]=positions.get(node.node_id),status=statuses.find(n=>n.node_id===node.node_id)?.status||'定义';add('rect',{x,y,width:150,height:48,rx:8,fill:status==='completed'?'#dcfce7':'#e2e8f0',stroke:'#64748b'});add('text',{x:x+10,y:y+20,fill:'#0f172a','font-size':13},node.node_id+' · '+status);add('text',{x:x+10,y:y+38,fill:'#334155','font-size':11},(node.label||node.node_id).slice(0,18));}
    }
    const options=(rows,key,selected,label)=>rows.map(r=>`<option value="${esc(r[key])}" ${selected.includes(r[key])?'selected':''}>${esc(label(r))}</option>`).join('');
    function drawNodes(d){
      q(d,'[data-graph-editor]').innerHTML=d.nodes.map((node,i)=>`<fieldset data-graph-row="${i}" style="margin:12px 0"><legend>节点 ${i+1}</legend>
        <label>节点 ID <input data-node-field="node_id" maxlength="32" value="${esc(node.node_id)}"></label> <label>标签 <input data-node-field="label" maxlength="100" value="${esc(node.label||'')}"></label>
        <label>依赖 ID（逗号分隔）<input data-node-field="depends_on" value="${esc(node.depends_on.join(','))}"></label>
        <p><label>正式任务 <select data-node-field="task"><option value="">请选择</option>${options(d.tasks,'product_task_id',[node.task_ref?.product_task_id],r=>r.title+' · r'+r.revision)}</select></label>
        <label>Agent 固定版本 <select data-node-field="agent"><option value="">请选择</option>${d.agents.map((a,j)=>`<option value="${j}" ${same(a.ref,node.agent_ref)?'selected':''}>${esc(a.name)} · v${a.ref.version}</option>`).join('')}</select></label></p>
        <details><summary>资料、引用与交付版本</summary><label>资料 <select multiple data-node-field="documents">${options(d.docs,'document_id',node.document_refs.map(r=>r.document_id),r=>r.title+' · r'+r.revision)}</select></label>
        <label>引用 <select multiple data-node-field="citations">${options(d.cites,'citation_id',node.citation_refs.map(r=>r.citation_id),r=>r.citation_id+' · r'+r.revision)}</select></label>
        <label>交付定义 <select data-node-field="delivery"><option value="">无</option>${options(d.deliveries,'delivery_id',[node.delivery_ref?.delivery_id],r=>r.delivery_id+' · r'+r.revision)}</select></label></details>
        <button type="button" data-graph-action="remove" data-index="${i}">移除此节点</button></fieldset>`).join('');svg(d,d.nodes);controls(d);
    }
    function collect(d,requireSelections=true){
      for(const row of d.el.querySelectorAll('[data-graph-row]')){const node=d.nodes[Number(row.dataset.graphRow)],field=k=>row.querySelector('[data-node-field='+k+']');
        node.node_id=field('node_id').value.trim();node.label=field('label').value;node.depends_on=field('depends_on').value.split(',').map(v=>v.trim()).filter(Boolean);
      }
      if(requireSelections&&d.nodes.some(n=>!n.task_ref?.product_task_id||!n.agent_ref?.agent_id))throw Error('每个节点需选择正式任务及已发布 Agent 版本');
      return d.nodes;
    }
    async function load(d,n){
      const [tasks,graphs,runs,docs,cites,deliveries,agents]=await Promise.all([read(d,n,base(d)+'/product-tasks'),pages(d,n,base(d)+'/task-graphs','graphs'),pages(d,n,base(d)+'/task-graph-runs','graph_runs'),
        pages(d,n,base(d)+'/documents','documents'),pages(d,n,base(d)+'/citations','citations'),pages(d,n,base(d)+'/delivery-definitions','deliveries'),read(d,n,'/api/v1/agents')]);
      if(!Array.isArray(agents.agents)||agents.agents.length>100)throw Error('Agent 列表超过界面上限');
      const versions=await Promise.all(agents.agents.filter(a=>a.status!=='deprecated').map(async a=>{const p=await read(d,n,'/api/v1/agents/'+a.agent_id+'/versions');if(!Array.isArray(p.versions)||p.versions.length>200)throw Error('版本列表超过界面上限');return p.versions.filter(v=>v.snapshot?.status==='published').map(v=>({name:v.snapshot.name,ref:{agent_id:v.agent_id,version:v.version,sha256:v.sha256}}));}));
      if(!live(d,n))return;d.tasks=(tasks.tasks||[]).filter(t=>t.status==='definition_ready');d.graphs=graphs;d.runs=runs;d.docs=docs;d.cites=cites;d.deliveries=deliveries;d.agents=versions.flat();
      q(d,'[data-graph-graphs]').innerHTML='<option value="">新建任务图</option>'+graphs.map((g,i)=>`<option value="${i}">${esc(g.title)} · r${g.revision}</option>`).join('');
      q(d,'[data-graph-runs]').innerHTML='<option value="">选择已授权运行</option>'+runs.map((r,i)=>`<option value="${i}">${esc(r.graph_run_id)}</option>`).join('');
      const recovered=await pending(d);say(d,recovered?'存在待核对命令：'+recovered.key+'。请主动查询或重试原命令。':'已加载固定版本。保存图定义与执行前预检都不会启动模型。');drawNodes(d);
    }
    function changed(d,dirty=true){d.dirty=dirty;d.preview=null;q(d,'[data-graph-preview]').textContent='';q(d,'[data-graph-host]').checked=false;controls(d);}
    async function observe(d,n,ident){const result=await read(d,n,base(d)+'/task-graph-runs/'+ident);if(!live(d,n))return;
      d.run=ref(result,'graph_run_id');d.cancelRequested=!!result.cancel_requested;q(d,'[data-graph-result]').textContent=`${result.status} · ${result.graph_run_id}\n运行图版本：${result.selection?.graph_ref?.graph_id||'服务端固定引用'} · r${result.selection?.graph_ref?.revision||'?'}\n独立业务验收：${result.business_acceptance}\n`+result.nodes.map(v=>v.node_id+': '+v.status+(v.unknown_effects?'（效果未知）':'')+' · '+v.execution_id).join('\n');
      svg(d,result.nodes,result.nodes);q(d,'[data-graph-edit]').open=false;controls(d);return result;}
    async function mutate(d,n,record){
      await check(d,n);let path=record.kind==='start'?'/task-graph-runs':'/task-graph-cancellations';
      const result=await read(d,n,base(d)+path,{method:'POST',body:record.body,headers:{'Idempotency-Key':record.key}});
      if(result.persisted!==true)throw Error('提交尚未确认持久保存');
      if(result.request_digest!==record.digest)throw Error('提交收据摘要不符');
      if(record.kind==='cancel'&&!same(result.graph_run_ref,JSON.parse(record.body).graph_run_ref))throw Error('停止收据目标不符');
      forget(d,record);if(record.kind==='start'){d.preview=null;d.run=ref(result,'graph_run_id');await observe(d,n,result.graph_run_id);say(d,'图运行已授权；请刷新观察实际节点结果。');}
      else {d.cancelRequested=true;say(d,'停止请求已持久保存；实际停止状态需核对。');}
    }
    async function action(d,name,button){if(d.busy)return;const n=d.epoch;d.busy=true;controls(d);
      try{await check(d,n);const existing=await pending(d);
        if(name==='retry'){if(!existing)throw Error('没有可核对命令');await mutate(d,n,existing);return;}
        if(name==='query'){
          if(!existing)throw Error('没有待核对键');
          if(existing.kind==='cancel'){const r=await read(d,n,base(d)+'/task-graph-cancellation-submissions/by-key?key='+encodeURIComponent(existing.key));
            if(r.status==='committed'){if(r.cancellation.request_digest!==existing.digest)throw Error('原键收据摘要不符');forget(d,existing);d.cancelRequested=true;say(d,'停止请求已按原键确认，实际终态仍须观察。');}
            else {const wanted=JSON.parse(existing.body).graph_run_ref;const control=await read(d,n,base(d)+'/task-graph-runs/'+wanted.graph_run_id+'/control');
              if(control.cancel_requested&&same(control.graph_run_ref,wanted)){forget(d,existing);d.run=wanted;d.cancelRequested=true;say(d,'该图已有停止请求；本键未提交，无需重复。');}else say(d,'原键尚未找到；未重发。');}return;}
          let r;try{r=await read(d,n,base(d)+'/task-graph-run-submissions/by-key?key='+encodeURIComponent(existing.key));}
          catch(error){if(error.status!==403)throw error;const control=await read(d,n,base(d)+'/task-graph-runs/'+JSON.parse(existing.body).graph_run_id+'/control');
            if(control.request_digest!==existing.digest||control.submission_key!==existing.key)throw Error('最小停止引用与原提交不符');d.run=control.graph_run_ref;d.cancelRequested=!!control.cancel_requested;forget(d,existing);say(d,'资料不可读取；原授权已核对，可请求停止。');return;}
          if(r.status==='committed'){if(r.graph_run.request_digest!==existing.digest)throw Error('原键收据摘要不符');forget(d,existing);await observe(d,n,r.graph_run.graph_run_id);say(d,'已按原键核对；未重新投递。');}
          else say(d,'原键尚未找到；未重发，请核对或重试原命令。');return;
        }
        if(name==='refresh'){if(d.run){try{await observe(d,n,d.run.graph_run_id);}catch(error){if(error.status!==403)throw error;const v=await read(d,n,base(d)+'/task-graph-runs/'+d.run.graph_run_id+'/control');d.run=v.graph_run_ref;d.cancelRequested=!!v.cancel_requested;say(d,'来源不可读取；已核对同主体停止引用，可请求停止。');}}else await load(d,n);return;}
        if(existing)throw Error('请先核对待确认的原命令');
        if(name==='add'){collect(d,false);if(d.nodes.length>=20)throw Error('最多20个节点');d.nodes.push({schema_version:1,node_id:'N'+(d.nodes.length+1),label:'',depends_on:[],document_refs:[],citation_refs:[],delivery_ref:null});changed(d);drawNodes(d);return;}
        if(name==='remove'){collect(d,false);d.nodes.splice(Number(button.dataset.index),1);changed(d);drawNodes(d);return;}
        if(name==='save'){
          const nodes=collect(d);if(!nodes.length)throw Error('至少需要一个节点');
          const body={schema_version:1,graph_id:d.graph?.graph_id||'tg-'+hex(),expected_revision:d.graph?.revision||0,project_revision:d.project.revision,title:q(d,'[data-graph-title]').value,
            concurrency:Number(q(d,'[data-graph-concurrency]').value),limits:Object.fromEntries(['input_tokens','output_tokens','tool_calls','cost_microyuan'].map(k=>[k,Number(q(d,'[data-graph-limit='+k+']').value)])),nodes,retired:false};
          d.savePending??={body:JSON.stringify(body),key:'graph-save-'+hex()};
          if(d.savePending.body!==JSON.stringify(body))throw Error('上次定义保存尚未确认，请恢复原正文后重试保存');
          const saved=await read(d,n,base(d)+'/task-graphs',{method:'POST',body:d.savePending.body,headers:{'Idempotency-Key':d.savePending.key}});d.savePending=null;d.graph=saved;d.nodes=structuredClone(saved.nodes);changed(d,false);drawNodes(d);say(d,'图定义已保存 r'+saved.revision+'；尚未授权执行。');return;
        }
        if(name==='preview'){const r=await read(d,n,base(d)+'/task-graph-previews',{method:'POST',body:JSON.stringify({schema_version:1,graph_ref:ref(d.graph,'graph_id')})});d.preview=r;
          q(d,'[data-graph-preview]').textContent=`预检完成 · 并发 ${r.concurrency} · 输入 ${r.allocated_budget.input_tokens} · 输出 ${r.allocated_budget.output_tokens} · 工具 ${r.allocated_budget.tool_calls} · 费用分配 ${r.allocated_budget.cost_microyuan} 微元（运行时估算）\n`+r.nodes.map(v=>v.node_id+' · '+v.boundary).join('\n');say(d,'预检完成，尚未启动模型。');return;}
        let body;if(name==='start'){
          if(!d.preview)throw Error('请先预检固定图版本');if(d.preview.nodes.some(v=>v.boundary==='host_unrestricted')&&!q(d,'[data-graph-host]').checked)throw Error('请明确勾选宿主边界确认');
          body={schema_version:1,graph_run_id:'gr-'+hex(),graph_ref:d.preview.graph_ref,confirmation_digest:d.preview.confirmation_digest,confirm_host_boundary:q(d,'[data-graph-host]').checked};
        }else if(name==='stop'){if(!d.run)throw Error('请选择已授权图运行');body={schema_version:1,cancellation_id:'gc-'+hex(),graph_run_ref:d.run};}else return;
        const record={realm:d.realm,project:d.project.project_id,kind:name==='start'?'start':'cancel',key:'graph-'+name+'-'+hex(),body:JSON.stringify(body),digest:''};
        record.digest=await c.digest({operation:operation(record),project_id:record.project,command:body});await check(d,n);remember(d,record);await mutate(d,n,record);
      }catch(error){if(name==='save'&&error.status===422)d.savePending=null;if(live(d,n))say(d,'操作未确认：'+error.message);}
      finally{if(live(d,n)){d.busy=false;controls(d);}}
    }
    return {invalidate,async open(project,trigger){invalidate();const el=document.createElement('dialog');el.id='taskGraphDialog';el.style.cssText='width:min(1120px,94vw);max-height:92vh;overflow:auto';
      el.innerHTML=`<header><h2>依赖任务图 · ${esc(project.name)}</h2><button data-graph-action="close">关闭</button></header><p data-graph-status role="status"></p>
        <label>图版本 <select data-graph-graphs></select></label> <label>已有运行 <select data-graph-runs></select></label>
        <p><label>图标题 <input data-graph-title maxlength="100" value="新的依赖任务图"></label> <label>并发 <input data-graph-concurrency type="number" min="1" max="4" value="2"></label></p>
        <details><summary>图总额度（各节点已发布 Agent 额度之和不能超过此值）</summary>${Object.entries({input_tokens:2000000,output_tokens:100000,tool_calls:400,cost_microyuan:20000000}).map(([k,v])=>`<label>${esc(k)} <input data-graph-limit="${k}" type="number" min="1" value="${v}"></label>`).join('')}</details>
        <svg data-graph-svg role="img" aria-label="固定节点与依赖关系" style="display:block;width:100%;min-height:150px;max-height:380px"></svg><details data-graph-edit open><summary>编辑节点与依赖</summary><div data-graph-editor></div>
        <p><button data-graph-action="add">增加节点</button> <button data-graph-action="save">保存图版本</button></p></details><p><button data-graph-action="preview">执行前预检</button></p>
        <pre data-graph-preview style="white-space:pre-wrap"></pre><p><label><input data-graph-host type="checkbox">确认本次图中宿主执行边界</label></p>
        <button data-graph-action="start">授权并开始图运行</button> <button data-graph-action="refresh">刷新观察</button> <button data-graph-action="stop">停止当前图</button>
        <button data-graph-action="query">按原键查询</button> <button data-graph-action="retry">重试原命令</button><pre data-graph-result style="white-space:pre-wrap;overflow-wrap:anywhere"></pre>
        <p>技术完成与独立业务验收分别核对；重启后只观察，不会自动重投节点。节点间交付交接按独立验收处理。</p>`;
      const d=current={el,project,realm:'',epoch:1,busy:true,graph:null,preview:null,run:null,nodes:[],tasks:[],agents:[],docs:[],cites:[],deliveries:[]};main.append(el);el.showModal();controls(d);
      el.addEventListener('cancel',()=>{invalidate();trigger?.focus();});el.addEventListener('click',event=>{const b=event.target.closest('[data-graph-action]');if(!b)return;if(b.dataset.graphAction==='close'){invalidate();trigger?.focus();return;}void action(d,b.dataset.graphAction,b);});
      el.addEventListener('input',event=>{if(!event.target.closest('[data-graph-editor]')&&!event.target.matches('[data-graph-title],[data-graph-concurrency],[data-graph-limit]'))return;changed(d);});
      el.addEventListener('change',async event=>{if(d.busy)return;const target=event.target;
        if(target.matches('[data-graph-graphs]')){d.run=null;d.cancelRequested=false;q(d,'[data-graph-result]').replaceChildren();q(d,'[data-graph-edit]').open=true;d.graph=d.graphs[Number(target.value)];if(target.value==='')d.graph=null;d.nodes=structuredClone(d.graph?.nodes||[]);q(d,'[data-graph-title]').value=d.graph?.title||'新的依赖任务图';q(d,'[data-graph-concurrency]').value=d.graph?.concurrency||2;
          if(d.graph)for(const [k,v]of Object.entries(d.graph.limits))q(d,'[data-graph-limit='+k+']').value=v;changed(d,false);drawNodes(d);}
        else if(target.matches('[data-graph-runs]')&&target.value!==''){d.busy=true;controls(d);try{await observe(d,d.epoch,d.runs[Number(target.value)].graph_run_id);}catch(error){say(d,error.message);}finally{if(live(d)){d.busy=false;controls(d);}}}
        else if(target.matches('[data-node-field]')){const node=d.nodes[Number(target.closest('[data-graph-row]').dataset.graphRow)],field=target.dataset.nodeField;
          if(field==='task')node.task_ref=target.value?ref(d.tasks.find(t=>t.product_task_id===target.value),'product_task_id'):null;
          if(field==='agent')node.agent_ref=target.value!==''?d.agents[Number(target.value)]?.ref:null;
          if(field==='documents')node.document_refs=[...target.selectedOptions].map(v=>ref(d.docs.find(r=>r.document_id===v.value),'document_id'));
          if(field==='citations')node.citation_refs=[...target.selectedOptions].map(v=>ref(d.cites.find(r=>r.citation_id===v.value),'citation_id'));
          if(field==='delivery')node.delivery_ref=target.value?ref(d.deliveries.find(r=>r.delivery_id===target.value),'delivery_id'):null;changed(d);
        }
      });
      try{const realm=await identity();if(!live(d))return;d.realm=realm;await load(d,d.epoch);}catch(error){say(d,error.message);}finally{if(live(d)){d.busy=false;controls(d);}}
    }};
  }};
})(window);
