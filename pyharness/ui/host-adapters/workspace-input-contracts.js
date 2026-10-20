/* Strict public input contracts. Hash checks establish consistency, not server authority. */
(function(global){
  'use strict';
  const integer=(v,min=1,max=Number.MAX_SAFE_INTEGER)=>Number.isSafeInteger(v)&&v>=min&&v<=max;
  const hash=v=>typeof v==='string'&&/^[0-9a-f]{64}$/.test(v),oid=v=>typeof v==='string'&&/^(?:[0-9a-f]{40}|[0-9a-f]{64})$/.test(v);
  const id=(v,p)=>typeof v==='string'&&new RegExp('^'+p+'-[0-9a-f]{16}$').test(v);
  const keys=(v,names)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).sort().join('|')===names.split(' ').sort().join('|');
  const canonical=v=>JSON.stringify(v,(_,x)=>x&&typeof x==='object'&&!Array.isArray(x)?Object.fromEntries(Object.entries(x).sort(([a],[b])=>a<b?-1:a>b?1:0)):x);
  async function digest(v){return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical(v)))),n=>n.toString(16).padStart(2,'0')).join('');}
  const path=p=>typeof p==='string'&&p.length>0&&p.length<=1024&&!/[\x00-\x1f\\:*?"<>|]/.test(p)&&!p.split('/').some(s=>!s||s==='.'||s==='..'||/[. ]$/.test(s));
  const unique=values=>new Set(values.map(v=>v.toLowerCase())).size===values.length;
  const time=v=>typeof v==='string'&&Number.isFinite(Date.parse(v));
  const ref=v=>keys(v,'product_task_id revision digest source_seq')&&id(v.product_task_id,'pt')&&integer(v.revision,1,200)&&hash(v.digest)&&integer(v.source_seq);
  const task=v=>keys(v,'schema_version mode product_task_id project_id project_revision revision title retired digest source_seq created_at status executable')
    &&v.schema_version===1&&v.mode==='context_reference_only'&&id(v.product_task_id,'pt')&&id(v.project_id,'p')&&integer(v.project_revision)
    &&integer(v.revision,1,200)&&typeof v.title==='string'&&v.title.length>0&&v.title.length<=100&&typeof v.retired==='boolean'
    &&hash(v.digest)&&integer(v.source_seq)&&time(v.created_at)&&['current','stale','retired'].includes(v.status)&&v.executable===false;
  const grant=v=>keys(v,'grant_id revision root_identity actions expires_at revoked digest source_seq')&&id(v.grant_id,'sg')&&integer(v.revision)
    &&hash(v.root_identity)&&hash(v.digest)&&integer(v.source_seq)&&canonical(v.actions)==='["read"]'&&time(v.expires_at)&&typeof v.revoked==='boolean';
  const gitFields='schema_version status repository_kind project_id grant_id grant_revision source_seq metadata_grant_id metadata_grant_revision head_oid index_digest items file_count unique_content_size_bytes policy capture_stability scope persisted executable observation_digest';
  const snapshotFields='schema_version snapshot_id revision project_id project_revision root_identity metadata_root_identity metadata_grant_source_seq tracked_paths confirmation_digest scope change_basis exclusions repository_kind grant_id grant_revision metadata_grant_id metadata_grant_revision head_oid index_digest file_count unique_content_size_bytes policy capture_stability observation_digest items grant_source_seq source_seq digest created_at selected_untracked_paths total_size_bytes status stale_reasons persisted executable live_source_freshness receipt_digest';
  const manifestFields='schema_version manifest_id project_revision task_ref revision project_id source_ref items total_size_bytes policy file_count digest source_seq created_at status stale_reasons binding_mode executable receipt_digest';
  const sourceFields='snapshot_id revision digest source_seq captured_project_revision grant_id grant_revision grant_source_seq root_identity metadata_grant_id metadata_grant_revision metadata_grant_source_seq metadata_root_identity head_oid index_digest confirmation_digest source_kind';
  const itemFields='schema_version input_index path head index working deletion_marker index_deletion_marker selected_untracked';
  const layer=(v,working)=>v===null||(keys(v,'schema_version git_oid digest size_bytes mode native_mode')&&v.schema_version===1&&oid(v.git_oid)&&hash(v.digest)
    &&integer(v.size_bytes,0,10485760)&&[33188,33261].includes(v.mode)&&(working?integer(v.native_mode,0,511):v.native_mode===null));
  const meta=v=>id(v.grant_id,'sg')&&integer(v.grant_revision)&&((v.metadata_grant_id===null&&v.metadata_grant_revision===null)
    ||(id(v.metadata_grant_id,'sg')&&v.metadata_grant_id!==v.grant_id&&integer(v.metadata_grant_revision)));
  function gitShape(v,paths,flags=false){
    if(v.schema_version!==1||!id(v.project_id,'p')||!meta(v)||!integer(v.source_seq)||!oid(v.head_oid)||!hash(v.index_digest)||!hash(v.observation_digest)
      ||!['ordinary','linked_worktree'].includes(v.repository_kind)||(v.repository_kind==='linked_worktree'&&v.metadata_grant_id===null)
      ||v.policy!=='reject-secrets-utf8-v1'||v.capture_stability!=='stable_observed'||!integer(v.file_count,1,200)||!integer(v.unique_content_size_bytes,0,10485760)
      ||!Array.isArray(v.items)||v.items.length!==v.file_count||(paths&&canonical(v.items.map(i=>i.path))!==canonical(paths)))return false;
    const sizes=new Map();
    for(let n=0;n<v.items.length;n++){
      const i=v.items[n];
      if(!keys(i,itemFields+(flags?' staged unstaged working_mode_differs':''))||i.schema_version!==1||i.input_index!==n||!path(i.path)
        ||!layer(i.head,false)||!layer(i.index,false)||!layer(i.working,true)||!(i.head||i.index||i.working)
        ||i.deletion_marker!==(i.working===null)||i.index_deletion_marker!==(i.head!==null&&i.index===null)||i.selected_untracked!==(i.index===null&&i.working!==null))return false;
      if(flags){
        const staged=i.head===null?i.index!==null:i.index===null||(i.head.git_oid!==i.index.git_oid||i.head.mode!==i.index.mode);
        const unstaged=i.index===null?i.working!==null:i.working===null||i.index.digest!==i.working.digest;
        if(i.staged!==staged||i.unstaged!==unstaged||i.working_mode_differs!==Boolean(i.index&&i.working&&i.index.mode!==i.working.mode))return false;
      }
      for(const x of [i.head,i.index,i.working])if(x){if(sizes.has(x.digest)&&sizes.get(x.digest)!==x.size_bytes)return false;sizes.set(x.digest,x.size_bytes);}
    }
    return unique(v.items.map(i=>i.path))&&[...sizes.values()].reduce((a,b)=>a+b,0)===v.unique_content_size_bytes;
  }
  async function observation(v){const raw={};for(const k of gitFields.split(' '))if(k!=='observation_digest')raw[k]=v[k];return await digest(raw)===v.observation_digest;}
  async function preview(v,body,binding){
    if(!keys(v,gitFields+' project_revision tracked_paths confirmation_digest')||v.status!=='workspace_snapshot_confirmation_required'
      ||v.scope!=='tracked_plus_selected_untracked'||v.persisted!==false||v.executable!==false||v.project_revision!==body.project_revision||!hash(v.confirmation_digest)
      ||v.project_id!==binding.project||v.grant_id!==binding.grantId||v.grant_revision!==binding.grantRevision||v.source_seq!==binding.sourceSeq
      ||v.metadata_grant_id!==body.metadata_grant_id||v.metadata_grant_revision!==body.metadata_grant_revision||!gitShape(v,body.paths))return false;
    const tracked=v.items.filter(i=>i.head||i.index).map(i=>i.path);
    if(!Array.isArray(v.tracked_paths)||!unique(v.tracked_paths)||v.tracked_paths.length!==tracked.length||!v.tracked_paths.every(p=>tracked.includes(p)))return false;
    return observation({...v,status:'git_layers_observed',scope:'selected_git_layers'});
  }
  async function receipt(v){const raw={...v};delete raw.receipt_digest;return hash(v.receipt_digest)&&new TextEncoder().encode(canonical(v)).length<=524288&&await digest(raw)===v.receipt_digest;}
  const reasons=(v,allowed)=>Array.isArray(v.stale_reasons)&&new Set(v.stale_reasons).size===v.stale_reasons.length&&v.stale_reasons.every(r=>allowed.includes(r))
    &&(v.status==='stale')===(v.stale_reasons.length>0);
  const metadataRoot=v=>(v.metadata_grant_id===null?v.metadata_root_identity===null&&v.metadata_grant_source_seq===null:hash(v.metadata_root_identity)&&integer(v.metadata_grant_source_seq));
  async function snapshot(v,binding,detail=true){
    const names=snapshotFields.split(' ').filter(k=>detail||!['items','tracked_paths','selected_untracked_paths'].includes(k)).join(' ');
    if(!keys(v,names)||v.schema_version!==1||!id(v.snapshot_id,'ws')||v.revision!==1||v.project_id!==binding.project||v.grant_id!==binding.grantId
      ||v.grant_revision!==binding.grantRevision||v.grant_source_seq!==binding.sourceSeq||!integer(v.project_revision)||!meta(v)||!metadataRoot(v)
      ||!hash(v.root_identity)||!hash(v.confirmation_digest)||!oid(v.head_oid)||!hash(v.index_digest)||!hash(v.observation_digest)||!hash(v.digest)||!integer(v.source_seq)
      ||v.source_seq<=v.grant_source_seq||!time(v.created_at)||v.scope!=='tracked_plus_selected_untracked'||v.change_basis!=='raw_layer_comparison'
      ||canonical(v.exclusions)!=='[]'||v.persisted!==true||v.executable!==false||v.live_source_freshness!=='not_asserted'
      ||v.policy!=='reject-secrets-utf8-v1'||v.capture_stability!=='stable_observed'||!['captured','stale'].includes(v.status)
      ||!reasons(v,['project_revision_changed','workspace_snapshot_superseded'])||!integer(v.file_count,1,200)||!integer(v.total_size_bytes,0,10485760)
      ||!integer(v.unique_content_size_bytes,0,10485760)||!['ordinary','linked_worktree'].includes(v.repository_kind))return false;
    if(detail){
      if(!gitShape({...v,source_seq:v.grant_source_seq},null,true)||!Array.isArray(v.tracked_paths)||!unique(v.tracked_paths))return false;
      const tracked=v.items.filter(i=>i.head||i.index).map(i=>i.path);
      if(v.tracked_paths.length!==tracked.length||!v.tracked_paths.every(p=>tracked.includes(p))||canonical(v.selected_untracked_paths)!==canonical(v.items.filter(i=>i.selected_untracked).map(i=>i.path))
        ||v.total_size_bytes!==v.items.reduce((a,i)=>a+(i.working?.size_bytes||0),0))return false;
      const items=v.items.map(i=>Object.fromEntries(itemFields.split(' ').map(k=>[k,i[k]])));
      if(!await observation({...v,items,source_seq:v.grant_source_seq,status:'git_layers_observed',scope:'selected_git_layers',persisted:false}))return false;
    }
    return receipt(v);
  }
  function source(v,binding){return keys(v,sourceFields)&&id(v.snapshot_id,'ws')&&v.revision===1&&hash(v.digest)&&integer(v.source_seq)&&integer(v.captured_project_revision)
    &&v.grant_id===binding.grantId&&v.grant_revision===binding.grantRevision&&v.grant_source_seq===binding.sourceSeq&&meta(v)&&metadataRoot(v)&&hash(v.root_identity)
    &&oid(v.head_oid)&&hash(v.index_digest)&&hash(v.confirmation_digest)&&v.source_kind==='workspace_snapshot';}
  async function manifest(v,binding,detail=true){
    if(!keys(v,manifestFields.split(' ').filter(k=>detail||k!=='items').join(' '))||v.schema_version!==2||!id(v.manifest_id,'cm')||!integer(v.revision,1,200)
      ||v.project_id!==binding.project||!integer(v.project_revision)||!source(v.source_ref,binding)||v.source_ref.captured_project_revision>v.project_revision
      ||(v.task_ref!==null&&!ref(v.task_ref))||!hash(v.digest)||!integer(v.source_seq)||v.source_seq<=v.source_ref.source_seq||!time(v.created_at)
      ||!integer(v.file_count,0,200)||!integer(v.total_size_bytes,0,10485760)||v.policy!=='reject-secrets-utf8-v1'||v.executable!==false
      ||v.binding_mode!==(v.task_ref?'context_task_reference':'project_only')||!['current','stale'].includes(v.status)
      ||!reasons(v,['project_revision_changed','task_revision_changed','manifest_revision_superseded','workspace_snapshot_superseded']))return false;
    if(detail){
      if(!Array.isArray(v.items)||(v.status==='stale'?v.items.length!==0:v.items.length!==v.file_count))return false;
      let last=-1,total=0;const names=[];
      for(const i of v.items){if(!keys(i,'input_index path size_bytes content_digest')||!integer(i.input_index,0,199)||i.input_index<=last||!path(i.path)||!hash(i.content_digest)||!integer(i.size_bytes,0,10485760))return false;last=i.input_index;total+=i.size_bytes;names.push(i.path);}
      if(!unique(names)||(v.status==='current'&&total!==v.total_size_bytes))return false;
    }
    return receipt(v);
  }
  async function summary(value,kind){const row={...value};delete row.receipt_digest;delete row.items;if(kind==='snapshots'){delete row.tracked_paths;delete row.selected_untracked_paths;}row.receipt_digest=await digest(row);return row;}
  function sourceReference(s){return {snapshot_id:s.snapshot_id,revision:s.revision,digest:s.digest,source_seq:s.source_seq,captured_project_revision:s.project_revision,
    grant_id:s.grant_id,grant_revision:s.grant_revision,grant_source_seq:s.grant_source_seq,root_identity:s.root_identity,
    metadata_grant_id:s.metadata_grant_id,metadata_grant_revision:s.metadata_grant_revision,metadata_grant_source_seq:s.metadata_grant_source_seq,
    metadata_root_identity:s.metadata_root_identity,head_oid:s.head_oid,index_digest:s.index_digest,confirmation_digest:s.confirmation_digest,source_kind:'workspace_snapshot'};}
  function page(v,kind){return keys(v,`schema_version ${kind} total page_count has_more next_cursor source_watermark view_digest`)&&v.schema_version===1
    &&Array.isArray(v[kind])&&integer(v.total,0,200)&&v.page_count===v[kind].length&&integer(v.page_count,0,50)&&v.page_count<=v.total
    &&typeof v.has_more==='boolean'&&(v.has_more?(typeof v.next_cursor==='string'&&v.next_cursor.length<=160):v.next_cursor===null)&&integer(v.source_watermark)&&hash(v.view_digest);}
  async function inventory(v,body,binding){
    const fields='schema_version status project_id project_revision grant_id grant_revision grant_source_seq metadata_grant_id metadata_grant_revision metadata_grant_source_seq repository_kind head_oid index_digest scope ignored_requested ignore_policy body_policy content_safety capture_stability persisted executable required_tracked_count excluded_required_count capture_file_limit items total page_count has_more next_cursor view_digest page_digest';
    if(!keys(v,fields)||v.schema_version!==1||v.status!=='git_candidates_observed'||v.project_id!==binding.project||v.project_revision!==body.project_revision
      ||v.grant_id!==binding.grantId||v.grant_revision!==binding.grantRevision||v.grant_source_seq!==binding.sourceSeq||!meta(v)
      ||v.metadata_grant_id!==body.metadata_grant_id||v.metadata_grant_revision!==body.metadata_grant_revision
      ||(v.metadata_grant_id===null?v.metadata_grant_source_seq!==null:!integer(v.metadata_grant_source_seq))
      ||!['ordinary','linked_worktree'].includes(v.repository_kind)||(v.repository_kind==='linked_worktree'&&v.metadata_grant_id===null)
      ||!oid(v.head_oid)||!hash(v.index_digest)||!hash(v.view_digest)||!hash(v.page_digest)||v.scope!=='tracked_and_untracked_candidates'
      ||v.ignored_requested!==body.include_ignored||v.ignore_policy!=='worktree_gitignore_only'||v.body_policy!=='git_metadata_and_ignore_rules_only'
      ||v.content_safety!=='not_evaluated'||v.capture_stability!=='stable_observed'||v.persisted!==false||v.executable!==false||v.capture_file_limit!==200
      ||!integer(v.total,0,2000)||!integer(v.required_tracked_count,0,v.total)||!integer(v.excluded_required_count,0,v.required_tracked_count)
      ||!Array.isArray(v.items)||v.page_count!==v.items.length||typeof v.has_more!=='boolean')return false;
    const start=body.cursor?Number(body.cursor.split(':')[2])+1:0;
    if(!integer(start,0,2000)||v.page_count!==Math.min(body.limit,v.total-start)||v.has_more!==(start+v.page_count<v.total)
      ||v.next_cursor!==(v.has_more?`1:${body.limit}:${start+v.page_count-1}:${v.view_digest}`:null))return false;
    const layer=x=>x===null||(keys(x,'git_oid mode')&&oid(x.git_oid)&&[33188,33261].includes(x.mode));
    const reasons=['sensitive_name','path_rejected','duplicate_path','not_regular','hard_link','file_too_large','unsupported_mode','file_unavailable'];
    for(let n=0;n<v.items.length;n++){
      const i=v.items[n];
      if(!keys(i,'entry_index path path_digest origin required head index working staged deletion_marker index_deletion_marker status reason')
        ||i.entry_index!==start+n||!hash(i.path_digest)||!['tracked','untracked','ignored'].includes(i.origin)||typeof i.required!=='boolean'
        ||i.required!==(i.head!==null||i.index!==null)||i.required!==(i.origin==='tracked')||!layer(i.head)||!layer(i.index)
        ||i.staged!==(canonical(i.head)!==canonical(i.index))||typeof i.deletion_marker!=='boolean'||i.index_deletion_marker!==(i.head!==null&&i.index===null)
        ||(i.origin==='ignored'&&!body.include_ignored)||!['candidate','excluded'].includes(i.status))return false;
      if(i.working!==null&&(!keys(i.working,'size_bytes mtime_ns native_mode')||!integer(i.working.size_bytes,0)||!integer(i.working.native_mode,0,511)
        ||typeof i.working.mtime_ns!=='string'||!/^\d{1,24}$/.test(i.working.mtime_ns)))return false;
      if(i.status==='candidate'){
        if(!path(i.path)||i.reason!==null||i.deletion_marker!==(i.required&&i.working===null)||(!i.required&&i.working===null))return false;
        const actual=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(i.path))),n=>n.toString(16).padStart(2,'0')).join('');
        if(actual!==i.path_digest)return false;
      }else if(i.path!==null||!reasons.includes(i.reason))return false;
    }
    if(!unique(v.items.filter(i=>i.path!==null).map(i=>i.path)))return false;
    const raw={...v};delete raw.page_digest;
    return new TextEncoder().encode(canonical(v)).length<=524288&&await digest(raw)===v.page_digest;
  }
  global.PyharnessWorkspaceContracts={integer,hash,id,keys,canonical,digest,path,unique,task,grant,ref,preview,snapshot,manifest,summary,page,sourceReference,inventory};
})(window);
