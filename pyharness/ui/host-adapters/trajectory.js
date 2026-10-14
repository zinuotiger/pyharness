/* Public task facts from the existing run API. No model reasoning is rendered. */
function createTrajectoryUI({get,esc,btn,modal,toast,date,badge}) {
  let generation=0, stream=null, timer=null;
  const names={'task.enqueued':'任务已接收','task.started':'开始执行','task.completed':'任务完成','task.failed':'任务结束',
    'executor.event':'外部执行器','tool.call':'工具调用','tool.result':'工具结果','tool.error':'工具失败',
    'approval.requested':'等待审批','approval.granted':'审批通过','approval.denied':'审批拒绝','approval.timeout':'审批超时',
    'platform.artifact':'文件与产物','platform.sandbox':'工作环境','agent.message':'最终回答','llm.request':'模型请求','llm.response':'模型响应','llm.usage':'用量记录'};
  const states={recorded:'已记录',running:'进行中',completed:'已完成',failed:'失败',cancelled:'已取消',warning:'提示'};
  function close(){generation++;stream?.close();stream=null;clearTimeout(timer)}
  window.addEventListener('pyharness-panel-close',close);
  window.addEventListener('pagehide',close);
  async function open(id){
    close();const gen=generation;
    let rows=[],cursor=null,run=null,loading=false;
    modal('执行轨迹',`<section id="trajectory" aria-live="polite"><p>正在读取执行事实…</p></section>`);
    const root=()=>document.getElementById('trajectory');
    function draw(){
      const pane=root();if(!pane||gen!==generation)return;
      pane.innerHTML=`<div class="card-head"><h2>${esc(run.name)}</h2>${badge(run.status)}</div>
        <p class="muted">执行器：${run.executor==='codex'?'Codex':'PyHarness'} · ${esc(run.run_id)}</p>
        <p id="trajectoryConnection" role="status">${['completed','failed','cancelled'].includes(run.status)?'已读取持久化终态':'正在自动刷新'}</p>
        <h3 style="margin-top:18px">任务输入</h3><p style="white-space:pre-wrap">${esc(run.input_summary||'无输入摘要')}</p>
        ${run.error_code?`<p class="warning">${esc(run.error_message||run.error_code)}</p>`:''}
        <h3 style="margin-top:18px">事件时间线</h3><ol style="padding-left:24px">${rows.map(row=>`<li style="padding:8px 0;border-bottom:1px solid var(--line)">
          <b>${esc(names[row.type]||row.name)}</b> <small>${date(row.started_at)} · ${esc(states[row.status]||row.status)}</small>
          <details><summary>查看事实详情</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(row.output_summary||row.input_summary||'已记录')}</pre></details></li>`).join('')}</ol>
        <p class="muted">已显示 ${rows.length} / ${run.trace_total} 条；每次最多读取 100 条。</p>
        ${cursor?'<button type="button" id="trajectoryMore">加载更多事件</button>':''}
        <h3 style="margin-top:18px">结果</h3><p style="white-space:pre-wrap">${esc(run.result_summary||'尚无最终回答')}</p>
        <h3 style="margin-top:18px">变更、验证与产物</h3>${(run.artifacts||[]).map(a=>`<p>${esc(a.filename)} · ${esc(a.validation_status)} · ${a.size??0} 字节</p>`).join('')||'<p class="muted">尚无产物；未执行测试时不会显示通过。</p>'}
        <p><small>页面只展示公开执行事实，不展示模型隐藏推理。下载和应用变更仍需原有审批。</small></p>`;
      pane.querySelector('#trajectoryMore')?.addEventListener('click',()=>load(true));
    }
    async function load(more=false){
      if(loading||gen!==generation)return;loading=true;
      try{
        const result=await get('runs/'+encodeURIComponent(id)+'?limit=100'+(more&&cursor?'&cursor='+encodeURIComponent(cursor):''));
        if(gen!==generation||!root())return;
        run=result;
        if(more)rows=[...rows,...result.trace].filter((row,i,all)=>all.findIndex(x=>x.span_id===row.span_id)===i);
        else rows=result.trace;
        cursor=result.trace_cursor;draw();
        if(['completed','failed','cancelled'].includes(run.status)){stream?.close();stream=null}
      }catch(error){if(gen===generation&&root())root().textContent='轨迹读取失败：'+error.message}
      finally{loading=false}
    }
    await load();if(gen!==generation||!run||['completed','failed','cancelled'].includes(run.status))return;
    stream=new EventSource('/api/stream?sid='+encodeURIComponent(run.session_id));
    const refresh=()=>{clearTimeout(timer);timer=setTimeout(()=>load(false),300)};
    ['executor.event','task.started','task.completed','task.failed','agent.message','tool.call','tool.result','tool.error',
      'approval.requested','approval.granted','approval.denied','approval.timeout','platform.artifact'].forEach(type=>stream.addEventListener(type,refresh));
    stream.onopen=refresh;
    stream.onerror=()=>{const text=document.getElementById('trajectoryConnection');if(text)text.textContent='连接中断，正在恢复；重连后读取持久化事实'};
  }
  return {open,close};
}
