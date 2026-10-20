/* Explicit, ephemeral Git metadata selection. Never grants or captures bytes. */
(function(global){
  'use strict';
  global.PyharnessGitCandidates={create({main,api}){
    const c=global.PyharnessWorkspaceContracts;
    let el=null,epoch=0,binding=null,baseBody=null,applyNames=null,view=null,cursor=null,header=null;
    const rows=new Map(),selected=new Set();
    const node=s=>el?.querySelector(s),say=t=>{const n=node('[data-gic-status]');if(n)n.textContent=t;};
    const live=(dialog,e)=>dialog===el&&dialog?.isConnected&&dialog.open&&epoch===e;
    function reset(){epoch++;view=null;cursor=null;header=null;rows.clear();selected.clear();node('[data-gic-rows]')?.replaceChildren();
      for(const action of ['read','apply'])if(node(`[data-gic-action=${action}]`))node(`[data-gic-action=${action}]`).disabled=false;
      if(node('[data-gic-action=next]'))node('[data-gic-action=next]').disabled=true;
      say('没有有效候选观察；请显式读取。');}
    function invalidate(){epoch++;el?.close();el?.remove();el=null;rows.clear();selected.clear();view=null;header=null;cursor=null;}
    function body(next=false){const limit=Number(node('[data-gic-limit]').value);if(!c.integer(limit,1,50))throw Error('invalid page limit');
      return {...baseBody,include_ignored:node('[data-gic-ignored]').checked,limit,cursor:next?cursor:null};}
    const url=()=>`/api/v1/projects/${binding.project}/source-grants/${binding.grantId}/git-input-candidates`;
    async function fetchPage(command){const value=await api(url(),{method:'POST',body:JSON.stringify(command)});
      if(!await c.inventory(value,command,binding))throw Error('candidate metadata contract mismatch');return value;}
    function render(items){const target=node('[data-gic-rows]');target.replaceChildren();
      for(const row of items){
        const line=document.createElement('label');line.style.cssText='display:block;margin:8px 0;overflow-wrap:anywhere';
        const box=document.createElement('input');box.type='checkbox';box.disabled=row.status!=='candidate';box.checked=selected.has(row.entry_index);
        box.dataset.gicIndex=String(row.entry_index);box.addEventListener('change',()=>{
          epoch++;if(box.checked)selected.add(row.entry_index);else selected.delete(row.entry_index);
          for(const action of ['read','apply'])node(`[data-gic-action=${action}]`).disabled=false;
          node('[data-gic-action=next]').disabled=!cursor;
          say(`已明确选择 ${selected.size} 项；尚未正文检查或授权捕获。`);
        });
        const detail=document.createElement('span');detail.textContent=row.status==='excluded'?` 已排除 ${row.path_digest.slice(0,12)}… · ${row.reason}`
          :` ${row.path} · ${row.origin}${row.required?' / 必需tracked':''} · HEAD ${row.head?.git_oid.slice(0,12)||'无'} · staged/index ${row.index?.git_oid.slice(0,12)||'无'}${row.staged?'（HEAD/index变化）':''} · working ${row.working?row.working.size_bytes+' bytes / mtime '+row.working.mtime_ns:'不存在'}${row.deletion_marker?' / deletion':''}`;
        line.append(box,detail);target.append(line);
      }
    }
    async function read(next=false){
      if(next&&!cursor)return;
      if(!next)reset();const dialog=el,e=epoch;
      node('[data-gic-action=read]').disabled=true;node('[data-gic-action=next]').disabled=true;say('正在受权读取候选元数据；不捕获正文…');
      try{
        const command=body(next),value=await fetchPage(command);if(!live(dialog,e))return;
        if(next&&view!==value.view_digest)throw Error('candidate view changed');
        view=value.view_digest;cursor=value.next_cursor;header=value;
        for(const row of value.items)rows.set(row.entry_index,row);
        render(value.items);node('[data-gic-action=next]').disabled=!value.has_more;
        say(`项目 r${value.project_revision} · 授权 r${value.grant_revision} · HEAD ${value.head_oid.slice(0,12)} · index ${value.index_digest.slice(0,12)} · 视图 ${view.slice(0,12)}。此页 ${value.page_count} / 共 ${value.total}；已选择 ${selected.size}。必需tracked ${value.required_tracked_count}，排除 ${value.excluded_required_count}。工作区正文与unstaged尚未检查。`);
      }catch(error){if(live(dialog,e)){reset();say(`候选被拒绝或来源失效（${error.status||'未知'}）；选择已清除，请刷新后显式重读。`);}}
      finally{if(live(dialog,e))node('[data-gic-action=read]').disabled=false;}
    }
    async function apply(){
      const chosen=[...selected].sort((a,b)=>a-b).map(n=>rows.get(n));
      if(!header||chosen.length===0||chosen.length>200||header.required_tracked_count>200||header.excluded_required_count>0
        ||chosen.filter(i=>i?.required).length!==header.required_tracked_count||chosen.some(i=>i?.status!=='candidate')){
        say('须明确选择全部可用tracked及所需untracked，合计1–200项；被排除tracked或超限不能导入。');return;
      }
      const dialog=el,e=epoch,expected=view;node('[data-gic-action=apply]').disabled=true;say('正在重新核对所选来源视图…');
      try{
        const fresh=await fetchPage(body());if(!live(dialog,e))return;
        if(fresh.view_digest!==expected)throw Error('candidate source changed before apply');
        const names=chosen.map(i=>i.path);invalidate();applyNames(names);
      }catch(error){if(live(dialog,e)){reset();say(`来源变化或权限未确认（${error.status||'未知'}）；旧选择已清除，没有捕获。`);}}
      finally{if(live(dialog,e))node('[data-gic-action=apply]').disabled=false;}
    }
    return {invalidate,open(definition,command,onApply){
      invalidate();binding=Object.freeze({...definition});baseBody=Object.freeze({...command});applyNames=onApply;
      el=document.createElement('dialog');el.id='gitInputCandidatesDialog';el.setAttribute('aria-label','Git候选元数据与权限预演');
      el.style.cssText='width:min(900px,calc(100vw - 32px));max-height:85vh;overflow:auto;overflow-wrap:anywhere';
      el.innerHTML='<h2>Git 候选元数据与权限预演</h2><button data-gic-action="close">关闭</button><p>显式读取才列出候选，默认不勾选。只观察 HEAD/index 与文件元数据；工作区正文、unstaged内容及秘密策略尚未检查。分类会额外读取有界的工作树 .gitignore 规则文本；不采用全局或仓库私有排除配置。普通候选正文仍未读取。候选不是文件读取或私有写入授权，也不是不可变快照。</p><label><input type="checkbox" data-gic-ignored>显式包含受控规则忽略的候选</label><label>每页 <input type="number" data-gic-limit min="1" max="50" value="25"></label><p><button data-gic-action="read">读取候选元数据</button><button data-gic-action="refresh">清除观察与选择</button><button data-gic-action="next" disabled>下一页候选</button></p><p data-gic-status role="status"></p><section data-gic-rows></section><button data-gic-action="apply">核对来源并使用所选名称</button>';
      main.append(el);reset();el.addEventListener('input',event=>{if(event.target.matches('[data-gic-limit],[data-gic-ignored]'))reset();});
      el.addEventListener('click',event=>{const action=event.target.closest('[data-gic-action]')?.dataset.gicAction;
        if(action==='close')invalidate();else if(action==='read')read();else if(action==='next')read(true);else if(action==='refresh')reset();else if(action==='apply')apply();});
      el.oncancel=event=>{event.preventDefault();invalidate();};el.showModal();
    }};
  }};
})(window);
