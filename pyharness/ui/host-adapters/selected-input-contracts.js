/* CMv1 selected source contracts. Observed hashes never confer authority. */
(function(global){
  'use strict';
  const c=global.PyharnessWorkspaceContracts;
  const ref=v=>Object.fromEntries(['manifest_id','revision','digest','source_seq'].map(k=>[k,v[k]]));
  const task=v=>v===null||(c.keys(v,'product_task_id revision digest source_seq')&&c.id(v.product_task_id,'pt')&&c.integer(v.revision,1,200)&&c.hash(v.digest)&&c.integer(v.source_seq));
  const item=(v,n)=>c.keys(v,'input_index path size_bytes content_digest')&&v.input_index===n&&c.path(v.path)&&c.integer(v.size_bytes,0,10485760)&&c.hash(v.content_digest);
  function manifest(v,b){
    return c.keys(v,'schema_version manifest_id revision project_id project_revision grant_id grant_revision snapshot_id snapshot_revision snapshot_digest snapshot_source_seq task_ref policy items total_size_bytes file_count digest source_seq created_at status stale_reasons binding_mode executable')
      &&v.schema_version===1&&c.id(v.manifest_id,'cm')&&c.integer(v.revision,1,200)&&v.project_id===b.project&&c.integer(v.project_revision)
      &&v.grant_id===b.grant&&v.grant_revision===b.grantRevision&&c.id(v.snapshot_id,'ss')&&v.snapshot_revision===1&&c.hash(v.snapshot_digest)&&c.integer(v.snapshot_source_seq)
      &&task(v.task_ref)&&v.policy==='reject-secrets-utf8-v1'&&c.integer(v.total_size_bytes,0,10485760)&&c.integer(v.file_count,1,200)&&c.hash(v.digest)&&c.integer(v.source_seq,v.snapshot_source_seq+1)
      &&typeof v.created_at==='string'&&Number.isFinite(Date.parse(v.created_at))&&v.executable===false&&v.binding_mode===(v.task_ref?'context_task_reference':'project_only')
      &&['current','stale'].includes(v.status)&&Array.isArray(v.stale_reasons)&&c.unique(v.stale_reasons)&&v.stale_reasons.every(r=>['project_revision_changed','task_revision_changed','manifest_revision_superseded'].includes(r))
      &&(v.status==='stale')===(v.stale_reasons.length>0)&&Array.isArray(v.items)
      &&(v.status==='stale'?v.items.length===0:v.items.length===v.file_count&&v.items.every(item)&&c.unique(v.items.map(i=>i.path))&&v.items.reduce((n,i)=>n+i.size_bytes,0)===v.total_size_bytes);
  }
  function source(v,cm){
    return c.keys(v,'source_kind snapshot_id revision digest source_seq grant_id grant_revision grant_source_seq root_identity confirmation_digest')
      &&v.source_kind==='source_snapshot'&&v.snapshot_id===cm.snapshot_id&&v.revision===1&&v.digest===cm.snapshot_digest&&v.source_seq===cm.snapshot_source_seq
      &&v.grant_id===cm.grant_id&&v.grant_revision===cm.grant_revision&&c.integer(v.grant_source_seq,1,v.source_seq-1)&&c.hash(v.root_identity)&&c.hash(v.confirmation_digest);
  }
  async function descriptor(v,cm){
    if(!c.keys(v,'schema_version status project_id project_revision manifest_ref source_ref task_ref files deletions total_size_bytes policy file_count deletion_count persisted executable descriptor_digest')
      ||v.schema_version!==1||v.status!=='selected_inputs_observed'||v.project_id!==cm.project_id||v.project_revision!==cm.project_revision
      ||c.canonical(v.manifest_ref)!==c.canonical(ref(cm))||!source(v.source_ref,cm)||c.canonical(v.task_ref)!==c.canonical(cm.task_ref)
      ||v.policy!=='private-selected-copy-v1'||v.total_size_bytes!==cm.total_size_bytes||v.file_count!==cm.file_count||v.deletion_count!==0
      ||c.canonical(v.deletions)!=='[]'||v.persisted!==false||v.executable!==false||!Array.isArray(v.files)||v.files.length!==cm.file_count
      ||!v.files.every((f,n)=>c.keys(f,'input_index path size_bytes content_digest native_mode applied_mode')&&c.integer(f.native_mode,0,511)&&f.applied_mode===384
        &&c.canonical(Object.fromEntries(['input_index','path','size_bytes','content_digest'].map(k=>[k,f[k]])))===c.canonical(cm.items[n]))
      ||!c.hash(v.descriptor_digest)||new TextEncoder().encode(c.canonical(v)).length>524288)return false;
    const raw={...v};delete raw.descriptor_digest;return await c.digest(raw)===v.descriptor_digest;
  }
  global.PyharnessSelectedInputContracts={manifest,source,descriptor};
})(window);
