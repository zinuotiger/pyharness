/* Local design prototype. No API calls, execution actions, or external assets. */
(() => {
'use strict';
const $ = id => document.getElementById(id);
const esc = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels = {completed:'已完成',running:'运行中',failed:'失败',waiting_approval:'待审批',pending:'未发生',queued:'排队中',cancelled:'已取消',recorded:'已记录',warning:'警告',paused:'已暂停',retrying:'重试中'};
const badge = status => `<span class="badge ${esc(status)}">${labels[status] || esc(status)}</span>`;
const icons = {'agent.plan':'⌘','llm.request':'✦','tool.call':'⌁','approval.requested':'◇','agent.message':'↗','tool.error':'!'};
const fmt = ms => ms == null || !Number.isFinite(ms) ? '未知' : ms < 1000 ? Math.round(ms) + ' ms' : (ms / 1000).toFixed(1) + ' s';
const base = Date.parse('2026-10-13T09:30:00+08:00');
function demoSpan(id,parent,name,type,start,duration,status,summary) {
  return {id,parent,name,type,start,duration,status,summary,started_at:new Date(base+start).toISOString(),finished_at:duration==null?null:new Date(base+start+duration).toISOString(),duration_ms:duration};
}
const demoSpans = [
 demoSpan('plan',null,'任务规划','agent.plan',0,1800,'completed','拆解请求并确定两条并行分支。'),
 demoSpan('model','plan','模型推理','llm.request',1800,6500,'completed','生成工具调用计划。这里仅展示公共元数据，不展示模型私有推理。'),
 demoSpan('search','model','检索项目资料','tool.call',8300,5200,'completed','查找官方参考与项目结构。'),
 demoSpan('read','model','读取本地代码','tool.call',8300,3600,'completed','读取轨迹投影和任务数据结构。'),
 demoSpan('merge','search','汇总与生成','llm.request',13500,11000,'completed','汇总检索结果，生成本地原型。'),
 demoSpan('review','merge','写入权限确认','approval.requested',24500,4000,'completed','演示审批分支；此原型不会申请或执行真实写入。'),
 demoSpan('build','review','构建预览','tool.call',28500,10500,'completed','生成可打开的独立交互原型。'),
 demoSpan('result','build','交付结果','agent.message',39000,3000,'completed','整理成果、验证结果和限制。'),
];
const copySpans = () => structuredClone(demoSpans);
const demos = [
 {id:'DEMO-1042',name:'轨迹可视化 · 首轮原型',status:'running',spans:copySpans(),total:42000,playhead:30000},
 {id:'DEMO-1041',name:'代码质量检查',status:'completed',spans:copySpans(),total:42000,playhead:42000},
 {id:'DEMO-1040',name:'生成数据摘要',status:'waiting_approval',spans:copySpans(),total:42000,playhead:26000},
 {id:'DEMO-1039',name:'执行测试 · 异常样例',status:'failed',spans:copySpans(),total:42000,playhead:35000},
 {id:'DEMO-1038',name:'检索参考文档',status:'completed',spans:copySpans(),total:42000,playhead:42000},
];
demos[2].spans[5].status = 'waiting_approval'; demos[2].spans[5].duration = null;
demos[3].spans[6].status = 'failed'; demos[3].spans[6].duration = 6500; demos[3].spans[6].error_code = 'DEMO_TEST_FAILED'; demos[3].spans[6].summary = '演示失败：检查未通过。不会自动重试。'; demos[3].spans[7].status = 'pending';
const history = window.TRACE_HISTORY || {runs:[],counts:{},total_runs:0,note:'未生成历史快照'};
const histories = history.runs.map(r => {
 const times = r.spans.map(s => Date.parse(s.started_at)).filter(Number.isFinite);
 const start = Date.parse(r.started_at) || Math.min(...times);
 const end = Date.parse(r.finished_at);
 const nodes = r.spans.map(s => ({...s,start:Math.max(0,Date.parse(s.started_at)-start),duration:s.duration_ms ?? (s.finished_at ? Math.max(0,Date.parse(s.finished_at)-Date.parse(s.started_at)) : null),summary:'历史快照仅保留状态、时间、事件类型与父关联。原始消息、参数和输出未复制。'}));
 const total = Math.max(1,Number.isFinite(end)?end-start:0,...nodes.map(s=>s.start+(s.duration||0)));
 return {...r,spans:nodes,total,playhead:total};
});
const state = {source:'demo',runs:demos,run:demos[0],selected:'build',time:30000,playing:false,view:'graph',layout:'horizontal',zoom:1,pan:{x:0,y:0},positions:{},motion:!matchMedia('(prefers-reduced-motion: reduce)').matches,frame:0};
const terminal = new Set(['completed','failed','cancelled']);
function nodeStatus(s) {
 if (state.time < s.start || s.status === 'pending') return 'pending';
 if (s.duration != null && state.time < s.start+s.duration) return s.type.startsWith('approval.') ? 'waiting_approval' : 'running';
 return s.status;
}
function runStatus() { if(state.source === 'history') return state.run.status; if(state.run.status==='running' && state.time>=state.run.total)return 'completed'; return state.run.status; }
function metrics() {
 const counts = state.source==='history' ? history.counts : demos.reduce((o,r)=>(o[r.status]=(o[r.status]||0)+1,o),{});
 const n = state.source==='history' ? history.total_runs : demos.length;
 const completed = counts.completed||0, failed = counts.failed||0;
 const denominator=completed+failed;
 const durations = state.runs.filter(r=>terminal.has(r.status)&&r.finished_at).map(r=>Date.parse(r.finished_at)-Date.parse(r.started_at)).filter(x=>x>=0 && Number.isFinite(x));
 const demoDurations=demos.filter(r=>terminal.has(r.status)).map(r=>r.playhead);
 const avg = state.source==='demo' ? demoDurations.reduce((a,b)=>a+b,0)/demoDurations.length : durations.length ? durations.reduce((a,b)=>a+b,0)/durations.length : null;
 const cards = [ ['任务总量',n,'本机全部记录；列表展示最近 40 项'],['活跃任务',(counts.running||0)+(counts.waiting_approval||0),'其中待审批 '+(counts.waiting_approval||0)+' 项'],['成功率',denominator?(100*completed/denominator).toFixed(1)+'%':'—',`${completed} 成功 / ${denominator} 成败终态；排除取消与进行中`],['平均耗时',fmt(avg),state.source==='demo'?'演示终态任务耗时':'当前展示终态任务；仅统计时间完整记录'] ];
 $('metrics').innerHTML=cards.map(([name,value,foot],i)=>`<article class="metric"><div class="metric-label">${name}<span>0${i+1}</span></div><div class="metric-value">${value}</div><div class="metric-foot">${state.source==='demo'&&i===0?'5 个演示任务 · 不代表真实运行':foot}</div></article>`).join('');
}
function taskList() {
 const q=$('search').value.toLowerCase(), status=$('statusFilter').value;
 const filtered=state.runs.filter(r=>r.name.toLowerCase().includes(q)&&(status==='all'||r.status===status));
 $('taskCount').textContent=`${filtered.length} / ${state.runs.length}`;
 $('taskList').innerHTML=filtered.length?filtered.map(r=>`<button class="task ${r===state.run?'selected':''}" data-run="${esc(r.id)}"><span class="task-title">${esc(r.name)}</span><span class="task-id">${esc(r.id)} · ${r.spans.length} 节点</span><span class="task-bottom">${badge(r.status)}<span>${fmt(r.playhead)}</span></span></button>`).join(''):'<div class="empty">没有匹配的任务</div>';
}
function positionNodes() {
 const spans=state.run.spans, map=new Map(spans.map(s=>[s.id,s])), depth=new Map();
 if(state.source==='demo'&&state.layout==='horizontal'){
  state.world={w:1000,h:470};
  const positions={plan:[30,65],model:[275,65],search:[520,25],read:[520,175],merge:[765,65],review:[765,340],build:[520,340],result:[275,340]};
  for(const s of spans){const p=positions[s.id];state.positions[s.id]={x:p[0],y:p[1]};}
  return;
 }
 function level(s,seen=new Set()) { if(depth.has(s.id))return depth.get(s.id); if(seen.has(s.id))return 0; seen.add(s.id);const p=map.get(s.parent);const l=p?Math.min(20,level(p,seen)+1):0;depth.set(s.id,l);return l; }
 const groups=new Map(); spans.forEach(s=>{const d=level(s);if(!groups.has(d))groups.set(d,[]);groups.get(d).push(s);});
 const horizontal=state.layout==='horizontal';const maxDepth=Math.max(0,...depth.values());const maxCount=Math.max(1,...[...groups.values()].map(g=>g.length));
 state.world = horizontal ? {w:Math.max(1100,(maxDepth+1)*218+80),h:Math.max(430,maxCount*120+100)} : {w:Math.max(800,maxCount*230+70),h:Math.max(540,(maxDepth+1)*135+100)};
 for(const [d,group] of groups)group.forEach((s,i)=>{state.positions[s.id]=horizontal?{x:40+d*218,y:state.world.h/2-group.length*60+i*120}:{x:state.world.w/2-group.length*115+i*230,y:45+d*135};});
}
function graph() {
 const spans=state.run.spans, map=new Map(spans.map(s=>[s.id,s]));
 $('canvas').style.height=state.layout==='vertical'?(innerWidth<=760?'520px':Math.min(1000,state.world.h*.75)+'px'):'';
 if(!spans.length){$('graph').innerHTML='<text x="80" y="180" fill="var(--muted)" font-size="18">此任务没有已记录的轨迹节点</text>';return;}
 const sel=state.selected;
 const paths=spans.filter(s=>map.has(s.parent)).map(s=>{
  const p=state.positions[s.parent],q=state.positions[s.id],hs=state.layout==='horizontal'&&Math.abs(p.x-q.x)>40,forward=q.x>p.x;
  const x=hs?p.x+(forward?180:0):p.x+90,y=hs?p.y+42:p.y+84,xx=hs?q.x+(forward?0:180):q.x+90,yy=hs?q.y+42:q.y;
  const d=hs?`M${x} ${y} C${(x+xx)/2} ${y} ${(x+xx)/2} ${yy} ${xx} ${yy}`:`M${x} ${y} C${x} ${(y+yy)/2} ${xx} ${(y+yy)/2} ${xx} ${yy}`;
  return `<path class="edge ${nodeStatus(s)} ${sel===s.id||sel===s.parent?'highlight':''}" d="${d}"/>`;
 }).join('');
 const nodes=spans.map(s=>{const p=state.positions[s.id],st=nodeStatus(s);return `<g class="node ${st} ${sel===s.id?'selected':''}" data-node="${esc(s.id)}" transform="translate(${p.x},${p.y})" tabindex="0" role="button" aria-label="${esc(s.name)} ${labels[st]||st}"><title>${esc(s.name)} · ${labels[st]||st} · ${fmt(s.duration)}</title><rect class="node-box" width="180" height="84"/><text x="13" y="26" class="node-icon">${icons[s.type]||'•'}</text><text x="38" y="26" class="node-label">${esc(s.name.length>12?s.name.slice(0,11)+'…':s.name)}</text><text x="13" y="48" class="node-sub">${esc(s.type)}</text><text x="13" y="69" class="node-state">${labels[st]||esc(st)}</text><text x="168" y="69" text-anchor="end" class="node-sub">${fmt(s.duration)}</text><circle class="port" cx="${state.layout==='horizontal'?0:90}" cy="${state.layout==='horizontal'?42:0}" r="3"/><circle class="port" cx="${state.layout==='horizontal'?180:90}" cy="${state.layout==='horizontal'?42:84}" r="3"/></g>`;}).join('');
 $('graph').innerHTML=`<g transform="translate(${state.pan.x},${state.pan.y}) scale(${state.zoom})">${paths}${nodes}</g>`;
 $('graph').setAttribute('viewBox',`0 0 ${state.world.w} ${state.world.h}`);
 $('zoomText').textContent=Math.round(state.zoom*100)+'%';
}
function waterfall() {
 const total=state.run.total;
 $('waterfall').innerHTML=`<div class="wf-axis">${[0,.25,.5,.75,1].map(p=>`<span>${fmt(p*total)}</span>`).join('')}</div>`+state.run.spans.map(s=>`<button class="wf-row ${s.id===state.selected?'selected':''} ${nodeStatus(s)}" data-node="${esc(s.id)}"><span class="wf-name" title="${esc(s.name)}">${esc(s.name)}</span><span class="wf-track">${s.duration==null?`<span class="wf-unknown" style="left:${Math.min(80,100*s.start/total)}%">◆ 耗时未知</span>`:`<span class="wf-bar" title="${fmt(s.duration)}" style="left:${100*s.start/total}%;width:${Math.min(100-100*s.start/total,100*s.duration/total)}%"></span>`}</span></button>`).join('');
}
function details() {
 const s=state.run.spans.find(s=>s.id===state.selected);
 if(!s){$('details').innerHTML='<p>选择节点查看详情。</p>';return;}
 const st=nodeStatus(s);
 const parent=state.run.spans.find(p=>p.id===s.parent);
 $('details').innerHTML=`<div><div class="detail-icon">${icons[s.type]||'⌁'}</div><div class="detail-title">${esc(s.name)}</div>${badge(st)}<div class="detail-type">${esc(s.type)} / ${esc(s.id)}</div></div><div class="detail-section"><h3>运行信息</h3><div class="detail-row"><span>开始偏移</span><span class="mono">+ ${fmt(s.start)}</span></div><div class="detail-row"><span>记录耗时</span><span class="mono">${fmt(s.duration)}</span></div><div class="detail-row"><span>父节点</span><span>${parent?esc(parent.name):s.missing_parent?'父节点不在快照内':'未记录'}</span></div><div class="detail-row"><span>错误代码</span><span>${esc(s.error_code||'无')}</span></div></div><div class="detail-section"><h3>公共摘要</h3><div class="detail-summary">${esc(s.summary)}</div>${state.source==='demo'&&s.id==='merge'?'<p>演示中该节点耗时 11.0 s，适合讨论瓶颈强调样式。</p>':''}</div>`;
}
function events() {
 const ordered=[...state.run.spans].sort((a,b)=>a.start-b.start);
 $('eventCount').textContent=ordered.length+' 条';
 $('events').innerHTML=ordered.map(s=>`<button class="event ${s.id===state.selected?'selected':''} ${state.time<s.start?'future':''}" data-node="${esc(s.id)}"><span class="event-time">+${(s.start/1000).toFixed(2)}s</span><span class="${nodeStatus(s)}">●</span><span class="event-label">${esc(s.name)}</span><span class="event-time">${esc(s.type)}</span></button>`).join('');
}
function renderTime() {
 $('playTime').textContent=`${(state.time/1000).toFixed(1)} / ${(state.run.total/1000).toFixed(1)} s`;
 $('scrub').value=Math.round(1000*state.time/state.run.total);
 $('runStatus').innerHTML=badge(runStatus());
 graph();waterfall();details();events();
}
function selectRun(r) {
 stop();state.run=r;state.time=r.playhead;state.selected=r.spans.find(s=>s.start<=state.time&&(s.duration==null||s.start+s.duration>state.time))?.id||r.spans[0]?.id;
 state.positions={};state.zoom=1;state.pan={x:0,y:0};positionNodes();
 $('runName').textContent=r.name;
 $('runMeta').textContent=`${r.id} · ${r.spans.length} 个记录节点 · ${state.source==='demo'?'演示数据':r.total_spans>r.spans.length?'快照已截断':'匿名历史记录'}`;
 taskList();renderTime();
}
function changeSource(source) {
 stop();state.source=source;state.runs=source==='demo'?demos:histories;
 $('demo').classList.toggle('selected',source==='demo');$('history').classList.toggle('selected',source==='history');
 $('sourceNote').textContent=source==='demo'?'虚构任务 · 回放不执行任何操作':`只读快照 · ${history.total_runs} 项任务 · ${history.event_count||0} 条事件 · 跳过 ${history.skipped_files||0} 份日志 · 无实时连接`;
 $('search').value='';$('statusFilter').value='all';metrics();
 if(state.runs.length)selectRun(state.runs[0]);
 else {state.run={id:'empty',name:'暂无历史任务',spans:[],total:1,status:'recorded',playhead:0};selectRun(state.run);}
}
function stop(){state.playing=false;cancelAnimationFrame(state.frame);$('play').textContent='▶ 回放';}
function play(){if(state.playing){stop();return;}if(state.time>=state.run.total)state.time=0;state.playing=true;$('play').textContent='Ⅱ 暂停';let last=performance.now(),draw=0;function tick(now){if(!state.playing)return;state.time=Math.min(state.run.total,state.time+(now-last)*Number($('speed').value));last=now;if(now-draw>80){renderTime();draw=now;}if(state.time>=state.run.total){stop();renderTime();return;}state.frame=requestAnimationFrame(tick);}state.frame=requestAnimationFrame(tick);}
function selectNode(id){if(!state.run.spans.some(s=>s.id===id))return;state.selected=id;graph();waterfall();details();events();}
document.addEventListener('click',event=>{const task=event.target.closest('[data-run]');if(task)selectRun(state.runs.find(r=>r.id===task.dataset.run));const node=event.target.closest('[data-node]');if(node&&!state.dragMoved)selectNode(node.dataset.node);state.dragMoved=false;});
$('graph').addEventListener('keydown',e=>{if((e.key==='Enter'||e.key===' ')&&e.target.dataset.node){e.preventDefault();selectNode(e.target.dataset.node);}});
$('theme').onchange=()=>{document.documentElement.dataset.theme=$('theme').value;save();};
$('layout').onchange=()=>{state.layout=$('layout').value;state.positions={};state.zoom=1;state.pan={x:0,y:0};positionNodes();graph();save();};
$('demo').onclick=()=>changeSource('demo');$('history').onclick=()=>changeSource('history');
$('search').oninput=taskList;$('statusFilter').onchange=taskList;
$('play').onclick=play;$('reset').onclick=()=>{stop();state.time=0;renderTime();};
$('scrub').oninput=()=>{stop();state.time=Number($('scrub').value)/1000*state.run.total;renderTime();};
function view(v){state.view=v;$('canvas').hidden=v!=='graph';$('waterfall').hidden=v!=='waterfall';$('graphTab').classList.toggle('selected',v==='graph');$('waterfallTab').classList.toggle('selected',v==='waterfall');for(const id of ['fit','zoomIn','zoomOut'])$(id).disabled=v!=='graph';}
$('graphTab').onclick=()=>view('graph');$('waterfallTab').onclick=()=>view('waterfall');
function zoom(f){state.zoom=Math.min(2.5,Math.max(.4,state.zoom*f));graph();}
$('zoomIn').onclick=()=>zoom(1.2);$('zoomOut').onclick=()=>zoom(1/1.2);
$('fit').onclick=()=>{state.zoom=1;state.pan={x:0,y:0};positionNodes();graph();};
$('expand').onclick=()=>{document.body.classList.toggle('focus');$('expand').setAttribute('aria-pressed',document.body.classList.contains('focus'));$('expand').textContent=document.body.classList.contains('focus')?'退出专注':'专注';};
function motion(){document.body.classList.toggle('no-motion',!state.motion);$('motion').textContent=`动效：${state.motion?'开启':'关闭'}`;$('motion').setAttribute('aria-pressed',state.motion);}
$('motion').onclick=()=>{state.motion=!state.motion;motion();save();};
function save(){try{localStorage.setItem('pyharness-trace-lab',JSON.stringify({theme:$('theme').value,layout:state.layout,motion:state.motion}));}catch{}}
try{const pref=JSON.parse(localStorage.getItem('pyharness-trace-lab')||'null');if(pref){if(['dark','light','blue'].includes(pref.theme)){$('theme').value=pref.theme;document.documentElement.dataset.theme=pref.theme;}if(['horizontal','vertical'].includes(pref.layout)){state.layout=pref.layout;$('layout').value=pref.layout;}if(typeof pref.motion==='boolean')state.motion=pref.motion;}}catch{}
let drag=null;
function point(e){return new DOMPoint(e.clientX,e.clientY).matrixTransform($('graph').getScreenCTM().inverse());}
$('graph').addEventListener('pointerdown',e=>{if(e.button!==0)return;stop();const n=e.target.closest('[data-node]');drag={id:n?.dataset.node,point:point(e),origin:n?{...state.positions[n.dataset.node]}:{...state.pan}};state.dragMoved=false;$('graph').setPointerCapture(e.pointerId);});
$('graph').addEventListener('pointermove',e=>{if(!drag)return;const p=point(e),dx=p.x-drag.point.x,dy=p.y-drag.point.y;if(Math.abs(dx)+Math.abs(dy)>3)state.dragMoved=true;if(drag.id){state.positions[drag.id]={x:drag.origin.x+dx/state.zoom,y:drag.origin.y+dy/state.zoom};}else state.pan={x:drag.origin.x+dx,y:drag.origin.y+dy};graph();});
$('graph').addEventListener('pointerup',()=>{if(drag?.id&&!state.dragMoved)selectNode(drag.id);drag=null;});
$('graph').addEventListener('pointercancel',()=>{drag=null;});
document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
motion();changeSource('demo');selectRun(demos[0]);
})();
