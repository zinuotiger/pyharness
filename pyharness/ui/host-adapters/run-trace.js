/* Read-only observer of authenticated public Run / TraceSpan / RunStep DTOs. */
(function (scope) {
 'use strict';
 const terminal = new Set(['completed', 'failed', 'cancelled']);
 const live = new Set(['running', 'queued', 'waiting_approval', 'paused', 'retrying']);
 const names = {unknown:'状态未知',recorded:'已记录',warning:'警告',pending:'尚未发生',running:'运行中',completed:'已完成',failed:'失败',cancelled:'已取消',waiting_approval:'等待审批',paused:'已暂停',queued:'排队中',retrying:'重试中',skipped:'已跳过'};
 const time = value => { const n = value ? Date.parse(value) : NaN; return Number.isFinite(n) ? n : null; };
 const duration = value => value == null || !Number.isFinite(value) ? '未知' : value < 1000 ? Math.round(value)+' ms' : (value/1000).toFixed(2)+' s';
 function normalize(data, now = Date.now()) {
  const steps = new Map((data.steps || []).map(s => [s.step_id, s]));
  const starts = (data.trace || []).map(s => time(s.started_at)).filter(n => n != null);
  const origin = time(data.started_at) ?? time(data.queued_at) ?? (starts.length ? Math.min(...starts) : now);
  const spans = (data.trace || []).map(s => {
   const step = steps.get(s.span_id), start = time(s.started_at), finish = time(s.finished_at) ?? time(step?.finished_at);
   const explicit = typeof s.duration_ms === 'number' && Number.isFinite(s.duration_ms) && s.duration_ms >= 0;
   const measured = explicit ? s.duration_ms : start != null && finish != null && finish >= start ? finish-start : null;
   return {...s, status:step?.status || s.status, offset:start == null ? null : Math.max(0,start-origin), measured,
    duration_source:explicit ? '轨迹记录' : measured != null ? (s.finished_at ? '轨迹起止时间' : '同标识步骤起止时间') : '未记录闭合时间',
    end:measured != null && start != null ? Math.max(0,start-origin)+measured : null};
  });
  const ends = spans.flatMap(s => [s.offset,s.end]).filter(n => n != null);
  const finish = time(data.finished_at);
  const elapsed = finish != null && finish >= origin ? finish-origin : live.has(data.status) ? Math.max(0,now-origin) : null;
  return {spans, origin, elapsed, total:Math.max(1,elapsed ?? 0,...ends)};
 }
 function create({get, esc, badge, onApprove, onArtifact, onClose}) {
  let root=null, data=null, model=null, selected=null, positions=new Map(), frame=0, timer=0, fetching=false, generation=0;
  let scopeInfo=null, stream=null, overlay=null, child=null, connection='未连接', errorMessage='', follow=true, originalRecord=null;
  let playing=false, historical=false, playhead=0, speed=1, view='graph', pageIndex=0, zoom=1;
  let motion=!scope.matchMedia('(prefers-reduced-motion: reduce)').matches, layout='horizontal';
  const size=40;
  const q = selector => root?.querySelector(selector);
  const safe = value => esc(value ?? '');
  const statusName = s => names[s] || s;
  function stateAt(s) {
   if(!historical)return s.status;
   if(s.offset==null)return s.status;
   if(s.offset>playhead)return 'pending';
   if(s.transitions?.length){const transitions=s.transitions.filter(t=>time(t.ts)!=null&&time(t.ts)-model.origin<=playhead);if(transitions.length)return transitions.at(-1).status;}
   if(s.end!=null && playhead<s.end)return s.type.startsWith('approval.')?'waiting_approval':'running';
   // Unknown-duration events retain their recorded status; no synthetic completion.
   return s.status;
  }
  function stop(){playing=false;cancelAnimationFrame(frame);if(q('[data-trace-play]'))q('[data-trace-play]').textContent='▶ 回放';}
  function destroy(){generation++;stop();clearTimeout(timer);document.removeEventListener('visibilitychange',visibility);root?.dispatchEvent(new Event('trace-destroy'));if(stream){stream.close();stream=null;}child?.destroy();child=null;root=null;data=null;model=null;fetching=false;positions.clear();if(overlay){const old=overlay;overlay=null;old.remove();} }
  function endpoint(after=0){const params=new URLSearchParams({after_seq:String(after),limit:'200'});if(scopeInfo.run_id)params.set('run_id',scopeInfo.run_id);if(scopeInfo.workflow)params.set('workflow',scopeInfo.workflow);return 'sessions/'+encodeURIComponent(scopeInfo.session_id)+'/trajectory?'+params;}
  function collect(next, append=false) {
   const existing=append && data ? data.trace : [];
   const spans=new Map(existing.map(s=>[s.span_id,s]));for(const s of next.trace || [])spans.set(s.span_id,s);
   data={...next,trace:[...spans.values()]};model=normalize(data);
   if(!selected || !spans.has(selected))selected=model.spans.find(s=>['waiting_approval','running','failed'].includes(s.status))?.span_id||model.spans.at(-1)?.span_id||null;
   if(!historical)playhead=model.total;
  }
  async function page(id) {
   if(!id)return '<section class="card"><h1>运行轨迹</h1><p>请先选择一个会话或任务。</p></section>';
   scopeInfo=typeof id==='string'?{session_id:id.split('~')[0],run_id:id}:{...id};
   const token=generation;
   const next=await get(endpoint());
   if(token!==generation)return '';
   collect(next);historical=false;pageIndex=0;zoom=1;positions.clear();errorMessage='';follow=true;connection='连接中';originalRecord=null;
   return `<section class="run-trace" aria-label="运行轨迹"><div class="page-title"><div><h1>运行轨迹</h1><p>来自当前账户的真实任务与持久化事件</p></div><button data-nav="runs">返回任务中心</button></div><div class="trace-run-heading"><div><h2 data-trace-name></h2><small data-trace-id></small></div><div data-trace-run-status></div></div><div class="trace-metrics" data-trace-metrics></div><div class="trace-toolbar"><div class="trace-tabs"><button data-trace-view="graph" aria-pressed="true">节点流程</button><button data-trace-view="waterfall" aria-pressed="false">耗时瀑布</button></div><div class="trace-options"><label>布局 <select data-trace-layout aria-label="轨迹布局"><option value="horizontal">横向流程</option><option value="vertical">纵向依赖</option></select></label><button data-trace-motion aria-pressed="true">动效：开启</button><button data-trace-refresh>刷新真实数据</button></div></div><p class="trace-notice" data-trace-notice role="status"></p><div class="trace-workspace"><section class="trace-main"><div class="trace-graph-tools"><button data-trace-zoom="out" aria-label="缩小轨迹">−</button><span data-trace-zoom-label>100%</span><button data-trace-zoom="in" aria-label="放大轨迹">＋</button><button data-trace-fit>恢复布局</button><span>拖动节点 · 点击查看详情</span></div><div class="trace-stage" data-trace-stage><svg data-trace-graph role="group" aria-label="真实调用关系"></svg></div><div class="trace-waterfall" data-trace-waterfall hidden></div><div class="trace-pagination"><button data-trace-page="previous">上一组节点</button><span data-trace-page-info></span><button data-trace-page="next">下一组节点</button><button data-trace-more>读取后续事件</button></div><div class="trace-playback"><button class="primary" data-trace-play>▶ 回放</button><button data-trace-live>返回最新记录</button><label>回放进度<input data-trace-scrub type="range" min="0" max="1000" value="1000" aria-label="轨迹回放进度"></label><span data-trace-time></span><select data-trace-speed aria-label="轨迹回放速度"><option value="1">1×</option><option value="2">2×</option><option value="4">4×</option></select></div><div class="trace-event-heading"><h3>事件轨迹</h3><span>回放只改变观察时间，不执行任务</span></div><div class="trace-events" data-trace-events></div></section><aside class="trace-inspector" aria-label="节点详情" data-trace-details></aside></div><details class="trace-raw"><summary>原有结构化详情（JSON）</summary><pre data-trace-json></pre></details><p class="trace-footnote">连线仅依据 parent_span_id；未记录关联的事件独立展示。耗时仅采用轨迹或同标识步骤的起止时间，缺失值保持未知。</p></section>`;
  }
  async function open(id,{container=null}={}){
   destroy();const token=generation;
   if(container){container.innerHTML='<p>正在读取真实轨迹…</p>';}else{overlay=document.createElement('dialog');overlay.className='trace-overlay'+(document.querySelector('#timeline-pane')?' trace-classic':'');overlay.innerHTML='<button class="trace-overlay-close" aria-label="关闭轨迹">关闭 ✕</button><div class="trace-overlay-body"><p>正在读取真实轨迹…</p></div>';document.body.append(overlay);overlay.showModal();overlay.querySelector('.trace-overlay-close').onclick=()=>{destroy();onClose?.();};overlay.addEventListener('cancel',e=>{e.preventDefault();destroy();onClose?.();});container=overlay.querySelector('.trace-overlay-body');}
   try{const html=await page(id);if(token!==generation||!container.isConnected)return;container.innerHTML=html;mount(container.querySelector('.run-trace'));}catch(e){if(token===generation&&container.isConnected){container.innerHTML='<p class="trace-load-error"></p><button data-trace-retry>重试读取</button>';container.querySelector('p').textContent='轨迹读取失败：'+e.message;container.querySelector('button').onclick=()=>open(id);}}
  }
  const visible = () => model.spans.slice(pageIndex*size,(pageIndex+1)*size);
  function geometry() {
   const nodes=visible(), ids=new Map(nodes.map(s=>[s.span_id,s])), levels=new Map(), groups=new Map();
   function depth(s,seen=new Set()){if(levels.has(s.span_id))return levels.get(s.span_id);if(seen.has(s.span_id))return 0;seen.add(s.span_id);const parent=ids.get(s.parent_span_id),d=parent?Math.min(12,depth(parent,seen)+1):0;levels.set(s.span_id,d);return d;}
   for(const s of nodes){const d=depth(s);if(!groups.has(d))groups.set(d,[]);groups.get(d).push(s);}
   for(const [d,rows] of groups)rows.forEach((s,i)=>{if(!positions.has(s.span_id))positions.set(s.span_id,layout==='horizontal'?{x:25+d*275,y:25+i*120}:{x:25+i*260,y:25+d*135});});
   const ps=nodes.map(s=>positions.get(s.span_id));
   return {nodes,ids,w:Math.max(800,...ps.map(p=>p.x+265)),h:Math.max(420,...ps.map(p=>p.y+120))};
  }
  function graph() {
   const svg=q('[data-trace-graph]');if(!svg)return;
   const g=geometry();svg.setAttribute('viewBox',`0 0 ${g.w} ${g.h}`);svg.style.width=g.w*zoom+'px';svg.style.height=g.h*zoom+'px';
   const paths=g.nodes.filter(s=>g.ids.has(s.parent_span_id)).map(s=>{const a=positions.get(s.parent_span_id),b=positions.get(s.span_id),horizontal=layout==='horizontal',x=horizontal?a.x+230:a.x+115,y=horizontal?a.y+45:a.y+90,xx=horizontal?b.x:b.x+115,yy=horizontal?b.y+45:b.y;const d=horizontal?`M${x} ${y} C${(x+xx)/2} ${y} ${(x+xx)/2} ${yy} ${xx} ${yy}`:`M${x} ${y} C${x} ${(y+yy)/2} ${xx} ${(y+yy)/2} ${xx} ${yy}`;return `<path class="trace-edge ${safe(stateAt(s))}" d="${d}"/>`;}).join('');
   svg.innerHTML=paths+g.nodes.map(s=>{const p=positions.get(s.span_id),st=stateAt(s),label=s.name||s.type;return `<g class="trace-node ${safe(st)} ${selected===s.span_id?'selected':''}" transform="translate(${p.x},${p.y})" data-trace-node="${safe(s.span_id)}" role="button" tabindex="0" aria-label="${safe(label)}，${safe(statusName(st))}"><title>${safe(label)} · ${safe(s.span_id)}</title><rect width="230" height="90" rx="10"/><text class="trace-node-title" x="14" y="26">${safe(label.length>23?label.slice(0,22)+'…':label)}</text><text class="trace-node-type" x="14" y="46">${safe(s.type)}</text><text class="trace-node-state" x="14" y="70">${safe(statusName(st))}</text><text class="trace-node-type" x="216" y="70" text-anchor="end">${duration(s.measured)}</text></g>`;}).join('');
   if(!g.nodes.length)svg.innerHTML='<text x="30" y="70" fill="var(--muted)">此任务暂无已记录的公共轨迹事件。</text>';
   q('[data-trace-zoom-label]').textContent=Math.round(zoom*100)+'%';
  }
  function details(){
   const s=model.spans.find(s=>s.span_id===selected),box=q('[data-trace-details]');
   if(!s){box.innerHTML='<h3>节点详情</h3><p>暂无轨迹节点。可展开下方结构化详情查看已有任务与步骤。</p>';return;}
   const parent=model.spans.find(p=>p.span_id===s.parent_span_id);
   box.innerHTML=`<h3>节点详情</h3><h2>${safe(s.name||s.type)}</h2>${badge(stateAt(s))}<small>${safe(s.span_id)}</small><dl><dt>事件类型</dt><dd>${safe(s.type)}</dd><dt>开始偏移</dt><dd>${s.offset==null?'未知':'+ '+duration(s.offset)}</dd><dt>耗时</dt><dd>${duration(s.measured)}</dd><dt>耗时来源</dt><dd>${safe(s.duration_source)}</dd><dt>父节点</dt><dd>${parent?safe(parent.name||parent.type):s.parent_span_id?'未载入（'+safe(s.parent_span_id)+'）':'未记录关联'}</dd><dt>错误代码</dt><dd>${safe(s.error_code||'无')}</dd><dt>Token</dt><dd>${Number.isFinite(s.token_usage)?s.token_usage:'未知'}</dd><dt>成本</dt><dd>${s.cost==null?'未知':safe(s.cost)}</dd></dl><h3>公共输入摘要</h3><pre>${safe(s.input_summary||'未记录')}</pre><h3>公共输出摘要</h3><pre>${safe(s.output_summary||'未记录')}</pre>`;
   if(s.waits?.length)box.insertAdjacentHTML('beforeend','<h3>审批记录</h3>'+s.waits.map(w=>`<p>${safe(w.status)} · ${safe(w.approval_id)}</p>`).join('')+(s.waits.some(w=>w.status==='pending')?'<button data-trace-approval>定位现有审批</button>':''));
   if(s.child_session_id)box.insertAdjacentHTML('beforeend',`<button data-trace-child="${safe(s.child_session_id)}">${child?'收起':'展开'}子任务轨迹</button>`);
   if(s.type==='workflow.step')box.insertAdjacentHTML('beforeend',s.linked_run_id?`<button data-trace-linked-run="${safe(s.linked_run_id)}">查看对应任务</button>`:'<p>历史记录未关联任务，无法确定对应运行。</p>');
   if(s.artifacts?.length)box.insertAdjacentHTML('beforeend','<h3>有关产物</h3>'+s.artifacts.map(a=>`<button data-trace-artifact="${safe(a.artifact_id)}">${safe(a.filename||a.artifact_id)}</button>`).join(''));
  }
  function waterfall(){
   const lanes=[...new Set(visible().map(s=>s.lane||data.session_id))];
   q('[data-trace-waterfall]').innerHTML=`<div class="trace-axis"><span>0 s</span><span>${duration(model.total/2)}</span><span>${duration(model.total)}</span></div>`+lanes.map(lane=>`<h4 class="trace-lane">${lane===data.session_id?'主会话':'子任务 '+safe(lane)} · 时间区间重叠可见并行</h4>`+visible().filter(s=>(s.lane||data.session_id)===lane).map(s=>{const left=s.offset==null?0:Math.min(92,100*s.offset/model.total),width=s.measured==null?0:Math.min(100-left,100*s.measured/model.total);return `<button class="trace-wf-row ${safe(stateAt(s))} ${selected===s.span_id?'selected':''}" data-trace-node="${safe(s.span_id)}"><span class="trace-wf-name" title="${safe(s.name)}">${safe(s.name||s.type)}</span><span class="trace-wf-track">${s.measured==null?`<span class="trace-wf-unknown" style="left:${left}%" title="没有闭合耗时">◆ 未知</span>`:`<span class="trace-wf-bar" style="left:${left}%;width:${width}%" title="${duration(s.measured)}"></span>`}</span></button>`;}).join('')).join('');
  }
  function draw(){if(!root?.isConnected || !model)return;
   graph();waterfall();details();
   q('[data-trace-events]').innerHTML=visible().map(s=>`<button class="trace-event ${safe(stateAt(s))} ${selected===s.span_id?'selected':''}" data-trace-node="${safe(s.span_id)}"><span>${s.offset==null?'未知':'+ '+duration(s.offset)}</span><strong>${safe(s.name||s.type)}</strong><span>${safe(statusName(stateAt(s)))}</span></button>`).join('');
   q('[data-trace-time]').textContent=duration(playhead)+' / '+duration(model.total);q('[data-trace-scrub]').value=Math.round(1000*playhead/model.total);
   const n=model.spans.length;q('[data-trace-page-info]').textContent=n?`${pageIndex*size+1}–${Math.min(n,(pageIndex+1)*size)} / 已载入 ${n} 条`:'0 条事件';
   q('[data-trace-page="previous"]').disabled=pageIndex===0;q('[data-trace-page="next"]').disabled=(pageIndex+1)*size>=n;
   q('[data-trace-more]').hidden=!data.trace_truncated;q('[data-trace-more]').disabled=fetching;
   q('[data-trace-play]').disabled=!n;q('[data-trace-scrub]').disabled=!n;
   q('[data-trace-name]').textContent=data.name||'运行任务';q('[data-trace-id]').textContent=data.run_id;q('[data-trace-run-status]').innerHTML=badge(data.status);
   const ownSteps=model.spans, done=ownSteps.filter(s=>s.status==='completed').length;
   const runs=data.runs||[],completed=runs.filter(r=>r.status==='completed').length,failed=runs.filter(r=>r.status==='failed').length;
   q('[data-trace-metrics]').innerHTML=[['当前范围状态',statusName(data.status)],['记录耗时',duration(model.elapsed)],['已载入完成节点',done+' / '+ownSteps.length],['任务成功率（本范围）',completed+failed?(100*completed/(completed+failed)).toFixed(1)+'%':'未知']].map(([k,v])=>`<div><small>${safe(k)}</small><b>${safe(v)}</b></div>`).join('');
   q('[data-trace-json]').textContent=JSON.stringify(originalRecord||data,null,2);
   root.classList.toggle('trace-static',!motion);
   q('[data-trace-motion]').textContent='动效：'+(motion?'开启':'关闭');q('[data-trace-motion]').setAttribute('aria-pressed',String(motion));
   q('[data-trace-notice]').textContent=errorMessage || (historical?'回放观察时间 · 暂停跟随':'实时连接：'+connection)+(follow?' · 跟随最新':' · 保持当前观察位置')+` · 已载入 ${model.spans.length} / ${data.trace_total} 个主要步骤`+(data.trace_truncated?'，可继续读取':'');
  }
  function schedule(){clearTimeout(timer);if(!root)return;timer=setTimeout(async()=>{if(!root?.isConnected)return;if(!historical&&follow&&!document.hidden)await refresh();schedule();},10000);}
  async function refresh(more=false){if(fetching||!root)return;const token=generation,activeRoot=root;fetching=true;q('[data-trace-refresh]').disabled=true;if(q('[data-trace-more]'))q('[data-trace-more]').disabled=true;
   try{const cursor=data.to_seq||0;const next=await get(endpoint(cursor));if(token!==generation||root!==activeRoot)return;
    errorMessage='';collect(next,true);
    draw();
   }catch(e){if(token===generation&&root===activeRoot){errorMessage='读取失败：'+e.message+'。已载入数据保留；可手动刷新。';q('[data-trace-notice]').textContent=errorMessage;}}
   finally{if(token===generation&&root===activeRoot){fetching=false;q('[data-trace-refresh]').disabled=false;q('[data-trace-more]').disabled=false;}}
  }
  function play(){if(playing){stop();return;}historical=true;if(playhead>=model.total)playhead=0;playing=true;q('[data-trace-play]').textContent='Ⅱ 暂停';let last=performance.now(),paint=0;
   function tick(now){if(!playing||!root?.isConnected)return;playhead=Math.min(model.total,playhead+(now-last)*speed);last=now;if(now-paint>90){draw();paint=now;}if(playhead>=model.total){stop();draw();return;}frame=requestAnimationFrame(tick);}frame=requestAnimationFrame(tick);
  }
  function connect(){if(stream)stream.close();const owned=root;stream=new EventSource('/api/stream?sid='+encodeURIComponent(scopeInfo.session_id)+'&after_seq='+(data.to_seq||0));let pending=0;
   const update=()=>{if(root!==owned||historical||!follow||document.hidden)return;clearTimeout(pending);pending=setTimeout(()=>{if(root===owned)refresh();},220);};
   ['task.enqueued','task.started','task.completed','task.failed','segment.end','llm.request','llm.response','llm.error','llm.retry','llm.usage','tool.call','tool.result','tool.error','approval.requested','approval.granted','approval.denied','approval.timeout','queue.suspended','queue.resumed','user.question','user.answer','subagent.spawned','subagent.joined','subagent.failed','workflow.step','agent.message','platform.artifact','system.error','system.cancelled'].forEach(k=>stream.addEventListener(k,update));
   stream.onopen=()=>{if(root===owned){connection='已连接';draw();update();}};stream.onerror=()=>{if(root===owned){connection='断线重连中；补取持久化记录';draw();}};
   owned.addEventListener('trace-destroy',()=>clearTimeout(pending),{once:true});
  }
  function mount(element){root=element||document.querySelector('.run-trace');if(!root || !data)return;root.querySelector('[data-trace-layout]').value=layout;
   q('.page-title h1').textContent=scopeInfo.workflow?'工作流轨迹':scopeInfo.run_id?'任务运行轨迹':'Agent 会话轨迹';
   q('.trace-footnote').textContent+=' 本范围成功率只统计已完成与失败任务，排除取消及进行中。';
   root.addEventListener('click',e=>{if(e.target.closest('.run-trace')!==root)return;const el=e.target.closest('button');if(!el)return;const node=el.dataset.traceNode;if(node){selected=node;draw();return;}if(el.hasAttribute('data-trace-play'))play();if(el.hasAttribute('data-trace-live')){stop();historical=false;playhead=model.total;draw();refresh();}if(el.hasAttribute('data-trace-refresh'))refresh();if(el.hasAttribute('data-trace-more'))refresh(true);if(el.hasAttribute('data-trace-motion')){motion=!motion;draw();}if(el.dataset.traceZoom){zoom=Math.min(2,Math.max(.4,zoom*(el.dataset.traceZoom==='in'?1.2:1/1.2)));graph();}if(el.hasAttribute('data-trace-fit')){positions.clear();zoom=1;graph();q('[data-trace-stage]').scrollTo(0,0);}if(el.dataset.tracePage){pageIndex+=el.dataset.tracePage==='next'?1:-1;positions.clear();draw();}if(el.dataset.traceView){view=el.dataset.traceView;q('[data-trace-stage]').hidden=view!=='graph';q('[data-trace-waterfall]').hidden=view!=='waterfall';root.querySelectorAll('[data-trace-view]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.traceView===view)));}});
   root.addEventListener('input',e=>{if(e.target.closest('.run-trace')!==root)return;if(e.target.hasAttribute('data-trace-scrub')){stop();historical=true;playhead=Number(e.target.value)*model.total/1000;draw();}});
   root.addEventListener('change',e=>{if(e.target.closest('.run-trace')!==root)return;if(e.target.hasAttribute('data-trace-layout')){layout=e.target.value;positions.clear();graph();}if(e.target.hasAttribute('data-trace-speed'))speed=Number(e.target.value);});
   const svg=q('[data-trace-graph]');let drag=null;
   const point=e=>new DOMPoint(e.clientX,e.clientY).matrixTransform(svg.getScreenCTM().inverse());
   svg.addEventListener('pointerdown',e=>{const n=e.target.closest('[data-trace-node]');if(!n||e.button!==0)return;stop();selected=n.dataset.traceNode;drag={id:selected,p:point(e),origin:{...positions.get(selected)},moved:false};svg.setPointerCapture(e.pointerId);});
   svg.addEventListener('pointermove',e=>{if(!drag)return;const p=point(e),dx=p.x-drag.p.x,dy=p.y-drag.p.y;drag.moved=drag.moved||Math.abs(dx)+Math.abs(dy)>3;positions.set(drag.id,{x:Math.max(0,drag.origin.x+dx),y:Math.max(0,drag.origin.y+dy)});graph();});
   svg.addEventListener('pointerup',()=>{if(drag){drag=null;draw();}});svg.addEventListener('pointercancel',()=>{drag=null;});
   svg.addEventListener('keydown',e=>{const n=e.target.closest('[data-trace-node]');if(n&&(e.key==='Enter'||e.key===' ')){e.preventDefault();selected=n.dataset.traceNode;draw();}});
   root.querySelector('[data-nav="runs"]')?.remove();
   const followButton=document.createElement('button');followButton.textContent='跟随最新：开启';followButton.dataset.traceFollow='';q('.trace-options').append(followButton);
   if(scopeInfo.run_id){const original=document.createElement('button');original.textContent='读取原有公共 Trace / 步骤 JSON';original.dataset.traceOriginal='';q('.trace-raw').append(original);}
   root.addEventListener('click',async e=>{if(e.target.closest('.run-trace')!==root)return;const el=e.target.closest('button');if(!el)return;
    if(el.hasAttribute('data-trace-follow')){follow=!follow;el.textContent='跟随最新：'+(follow?'开启':'关闭');draw();if(follow)refresh();}
    if(el.hasAttribute('data-trace-approval'))onApprove?.();
    if(el.hasAttribute('data-trace-original')){const owned=root,token=generation;el.disabled=true;try{const record=await get('runs/'+encodeURIComponent(scopeInfo.run_id)+'?limit=500');if(root===owned&&token===generation){originalRecord=record;q('[data-trace-json]').textContent=JSON.stringify(record,null,2);}}catch(error){if(root===owned)q('[data-trace-notice]').textContent=error.message;}finally{if(el.isConnected)el.disabled=false;}}
    if(el.dataset.traceArtifact)onArtifact?.(el.dataset.traceArtifact);
    if(el.dataset.traceLinkedRun){const id=el.dataset.traceLinkedRun;open({session_id:scopeInfo.session_id,run_id:id});}
    if(el.dataset.traceChild){if(child){child.destroy();child=null;q('.trace-child-pane')?.remove();details();return;}
     const pane=document.createElement('section');pane.className='trace-child-pane';root.append(pane);child=create({get,esc,badge,onApprove,onArtifact});await child.open({session_id:el.dataset.traceChild},{container:pane});details();}
   });
   document.addEventListener('visibilitychange',visibility);view='graph';draw();connect();schedule();
  }
  function visibility(){if(document.hidden)stop();else if(root&&follow&&!historical)refresh();}
  return {page,mount,open,destroy};
 }
 scope.PyharnessRunTrace={create,normalize,duration,statusLabel:s=>names[s]||s};
})(typeof window==='undefined'?globalThis:window);
