/* Selected Git byte-layer observation only. Never captures, executes or writes a repository. */
(function(global){
  'use strict';
  const integer=(v,min=1,max=Number.MAX_SAFE_INTEGER)=>Number.isSafeInteger(v)&&v>=min&&v<=max;
  const hash=v=>typeof v==='string'&&/^[0-9a-f]{64}$/.test(v);
  const oid=v=>typeof v==='string'&&/^(?:[0-9a-f]{40}|[0-9a-f]{64})$/.test(v);
  const keys=(v,expected)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join('|')===expected.split(' ').sort().join('|');
  const canonical=v=>JSON.stringify(v,(_,x)=>x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.entries(x).sort(([a],[b])=>a<b?-1:a>b?1:0)):x);
  async function digest(v){return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical(v)))),n=>n.toString(16).padStart(2,'0')).join('');}
  global.PyharnessGitSource={
    create({main,api}){
      let source=null,trigger=null,epoch=0,lifecycle=0,grants=[],ready=false;
      const dialog=()=>main.querySelector('#gitSourceDialog');
      const node=s=>dialog()?.querySelector(s);
      const text=(s,t)=>{const n=node(s);if(n)n.textContent=t;};
      const live=(el,e,l)=>el?.isConnected&&el.open&&epoch===e&&lifecycle===l;
      function clear(){
        epoch++;text('[data-git-result]','');text('[data-git-status]','观察已失效；请确认当前选择后重新预览。');
        if(node('[data-git-confirm]'))node('[data-git-confirm]').checked=false;
        if(node('[data-git-action=preview]'))node('[data-git-action=preview]').disabled=false;
      }
      function invalidate(){epoch++;lifecycle++;ready=false;grants=[];dialog()?.close();dialog()?.remove();}
      const validGrant=g=>keys(g,'grant_id revision source_seq digest root_identity actions expires_at revoked')
        && /^sg-[0-9a-f]{16}$/.test(g.grant_id)&&integer(g.revision)&&integer(g.source_seq)&&hash(g.digest)&&hash(g.root_identity)
        && Array.isArray(g.actions)&&g.actions.length===1&&g.actions[0]==='read'&&typeof g.revoked==='boolean'&&Number.isFinite(Date.parse(g.expires_at));
      async function load(){
        clear();ready=false;const el=dialog(),e=epoch,l=lifecycle;
        text('[data-git-status]','正在读取授权摘要…');node('[data-git-metadata]').replaceChildren(new Option('无外置元数据授权（仅普通仓库）',''));
        try{
          const data=await api(`/api/v1/projects/${source.project}/source-grants`);
          if(!live(el,e,l))return;
          if(!Array.isArray(data.grants)||data.total!==data.grants.length||data.total>200||!data.grants.every(validGrant)
            ||new Set(data.grants.map(g=>g.grant_id)).size!==data.total)throw Error('invalid grant summaries');
          const current=data.grants.find(g=>g.grant_id===source.grantId);
          if(!current||current.revision!==source.grantRevision||current.source_seq!==source.sourceSeq||current.revoked||Date.parse(current.expires_at)<=Date.now())throw Error('source grant changed');
          grants=data.grants.filter(g=>g.grant_id!==source.grantId&&!g.revoked&&Date.parse(g.expires_at)>Date.now());
          grants.forEach(g=>node('[data-git-metadata]').append(new Option(`${g.grant_id} · r${g.revision}`,g.grant_id)));
          ready=true;text('[data-git-status]','授权摘要已读取；请显式确认所选材料后预览。');
        }catch(error){if(live(el,e,l))text('[data-git-status]',`授权读取被拒绝或未确认（${error.status||'未知'}）；请关闭并刷新源授权。`);}
      }
      function paths(){
        const values=node('[data-git-paths]').value.split(/\r?\n/);if(values.at(-1)==='')values.pop();
        if(!values.length||values.length>200||new Set(values.map(p=>p.toLowerCase())).size!==values.length
          ||values.some(p=>!p||p.length>1024||/[\x00-\x1f\\:*?"<>|]/.test(p)||p.split('/').some(s=>!s||s==='.'||s==='..'||/[. ]$/.test(s))))return null;
        return values;
      }
      function validLayer(v,working){
        return v===null||(keys(v,'schema_version git_oid digest size_bytes mode native_mode')&&v.schema_version===1&&oid(v.git_oid)&&hash(v.digest)
          &&integer(v.size_bytes,0,10485760)&&[33188,33261].includes(v.mode)
          &&(working?integer(v.native_mode,0,511):v.native_mode===null));
      }
      function valid(data,body){
        if(!keys(data,'schema_version status repository_kind project_id grant_id grant_revision source_seq metadata_grant_id metadata_grant_revision head_oid index_digest items file_count unique_content_size_bytes policy capture_stability scope persisted executable observation_digest')
          ||data.schema_version!==1||data.status!=='git_layers_observed'||!['ordinary','linked_worktree'].includes(data.repository_kind)
          ||data.project_id!==source.project||data.grant_id!==source.grantId||data.grant_revision!==body.expected_revision||data.source_seq!==source.sourceSeq
          ||data.metadata_grant_id!==body.metadata_grant_id||data.metadata_grant_revision!==body.metadata_grant_revision
          ||(data.repository_kind==='linked_worktree'&&data.metadata_grant_id===null)||!oid(data.head_oid)||!hash(data.index_digest)||!hash(data.observation_digest)
          ||data.policy!=='reject-secrets-utf8-v1'||data.capture_stability!=='stable_observed'||data.scope!=='selected_git_layers'||data.persisted!==false||data.executable!==false
          ||!Array.isArray(data.items)||data.file_count!==body.paths.length||data.items.length!==body.paths.length||!integer(data.unique_content_size_bytes,0,10485760))return false;
        const sizes=new Map();
        for(let index=0;index<data.items.length;index++){
          const i=data.items[index];
          if(!keys(i,'schema_version input_index path head index working deletion_marker index_deletion_marker selected_untracked')||i.schema_version!==1||i.input_index!==index||i.path!==body.paths[index]
            ||!validLayer(i.head,false)||!validLayer(i.index,false)||!validLayer(i.working,true)||!(i.head||i.index||i.working)
            ||i.deletion_marker!==(i.working===null)||i.index_deletion_marker!==(i.head!==null&&i.index===null)||i.selected_untracked!==(i.index===null&&i.working!==null))return false;
          for(const layer of [i.head,i.index,i.working])if(layer){
            if(sizes.has(layer.digest)&&sizes.get(layer.digest)!==layer.size_bytes)return false;
            sizes.set(layer.digest,layer.size_bytes);
          }
        }
        return [...sizes.values()].reduce((sum,n)=>sum+n,0)===data.unique_content_size_bytes;
      }
      async function preview(){
        if(!ready){text('[data-git-status]','请先刷新并成功读取当前授权。');return;}
        const selected=paths(),confirmed=node('[data-git-confirm]').checked;
        if(!selected||!confirmed){text('[data-git-status]','须1–200个显式相对文件名并确认材料可读；不自动修复路径或缩减选择。');return;}
        const choice=node('[data-git-metadata]').value,metadata=grants.find(g=>g.grant_id===choice);
        if(choice&&!metadata){clear();return;}
        const body={schema_version:1,expected_revision:source.grantRevision,paths:selected,metadata_grant_id:metadata?.grant_id||null,metadata_grant_revision:metadata?.revision||null};
        clear();const el=dialog(),e=epoch,l=lifecycle;node('[data-git-action=preview]').disabled=true;text('[data-git-status]','正在读取所选 Git 版本并检查稳定性…');
        try{
          const data=await api(`/api/v1/projects/${source.project}/source-grants/${source.grantId}/git-source-previews`,{method:'POST',body:JSON.stringify(body)});
          if(!live(el,e,l))return;
          if(new TextEncoder().encode(JSON.stringify(data)).length>524288||!valid(data,body))throw Error('invalid Git preview');
          const content={...data};delete content.observation_digest;
          if(await digest(content)!==data.observation_digest)throw Error('Git observation digest mismatch');
          if(!live(el,e,l))return;
          const label=(name,layer)=>`${name}：${layer?`${layer.size_bytes} 字节 · Git模式 ${layer.mode.toString(8)}${layer.native_mode===null?'':` · 物理权限 ${layer.native_mode.toString(8)}`} · SHA256 ${layer.digest}`:'无此版本'}`;
          text('[data-git-result]',data.items.map(i=>`${i.input_index+1}. ${i.path}${i.deletion_marker?' · 工作区删除标记':''}${i.index_deletion_marker?' · 暂存删除标记':''}${i.selected_untracked?' · 显式未追踪':''}\n${label('HEAD',i.head)}\n${label('暂存',i.index)}\n${label('工作区',i.working)}`).join('\n\n'));
          text('[data-git-status]',`git_layers_observed · ${data.repository_kind} · ${data.file_count}项 · 不同字节版本共${data.unique_content_size_bytes}字节 · ${data.observation_digest}。未持久化、未执行；不是完整工作区或OS原子快照。`);
        }catch(error){if(live(el,e,l))text('[data-git-status]',error.status===409?'版本变化或读取超时，未形成预览，请重新核对。':`预览被拒绝或未确认（${error.status||'未知'}）；请检查授权、Git支持范围及所选材料，没有自动重试。`);}
        finally{if(live(el,e,l))node('[data-git-action=preview]').disabled=false;}
      }
      return{
        invalidate,
        async open(definition,initialPaths,button){
          invalidate();source=Object.freeze({...definition});trigger=button;
          const el=document.createElement('dialog');el.id='gitSourceDialog';el.setAttribute('aria-label','Git来源只读预览');
          el.style.cssText='width:min(850px,calc(100vw - 32px));max-height:85vh;box-sizing:border-box;overflow:auto;overflow-wrap:anywhere';
          el.innerHTML='<h2>Git 来源只读预览</h2><button data-git-action="close">关闭Git预览</button><p>区分所选文件的HEAD、暂存、工作区原字节与删除标记。只读，不复制整个工作区、不创建快照、不执行或回写。</p><p>最多200项；不同字节版本累计最多10MiB；预览预算25秒，超预算拒绝结果；子进程清理另有有界等待。本地同步磁盘I/O无法硬中断。点规则须显式选择，凭据/个人profile不允许。stable_observed仅为本次观察，不保证OS原子性。</p><p><label>显式相对文件名（每行一项）<textarea data-git-paths rows="5" maxlength="205000" spellcheck="false" autocomplete="off" style="width:100%;box-sizing:border-box"></textarea></label></p><p><label>外置公共元数据授权 <select data-git-metadata></select></label></p><p>linked worktree须先由管理员对精确Git公共元数据目录另签同项目只读授权，再在此选择。指针本身不会扩大权限；未支持的链接、稀疏/冲突索引、submodule、LFS等将拒绝。</p><p><label><input type="checkbox" data-git-confirm>我确认所选工作区及Git版本材料有权读取，且不含秘密或客户材料。</label></p><button data-git-action="preview">安全读取并预览所选Git版本</button><button data-git-action="refresh">刷新授权</button><p data-git-status role="status" aria-live="polite"></p><pre data-git-result style="white-space:pre-wrap;overflow-wrap:anywhere"></pre>';
          main.append(el);node('[data-git-paths]').value=initialPaths;
          el.addEventListener('input',event=>{if(!event.target.matches('[data-git-confirm]'))clear();});
          el.addEventListener('change',event=>{if(event.target.matches('[data-git-metadata]'))clear();});
          el.addEventListener('click',event=>{
            const action=event.target.closest('[data-git-action]')?.dataset.gitAction;
            if(action==='close'){invalidate();trigger?.focus();}
            if(action==='refresh')load();
            if(action==='preview')preview();
          });
          el.oncancel=event=>{event.preventDefault();invalidate();trigger?.focus();};el.showModal();await load();
        }
      };
    }
  };
})(window);
