const {test}=require('node:test');
const assert=require('node:assert/strict');
require('../../pyharness/ui/host-adapters/run-trace.js');
const {normalize}=globalThis.PyharnessRunTrace;
const at='2026-10-13T01:00:00Z';
const span={span_id:'s~1',parent_span_id:null,type:'llm.request',name:'llm.request',status:'recorded',started_at:at,duration_ms:null,finished_at:null};
test('independent events do not acquire causal edges or inferred inter-event duration',()=>{
 const result=normalize({status:'completed',started_at:at,trace:[span,{...span,span_id:'s~2',started_at:'2026-10-13T01:00:10Z'}]});
 assert.equal(result.spans[0].measured,null);
 assert.equal(result.spans[0].parent_span_id,null);
 assert.equal(result.spans[1].parent_span_id,null);
 assert.equal(result.elapsed,null);
});
test('matching RunStep supplies actual request completion and measured duration',()=>{
 const result=normalize({status:'completed',started_at:at,trace:[span],steps:[{step_id:'s~1',status:'completed',started_at:at,finished_at:'2026-10-13T01:00:02Z'}]});
 assert.equal(result.spans[0].measured,2000);
 assert.equal(result.spans[0].status,'completed');
 assert.equal(result.spans[0].duration_source,'同标识步骤起止时间');
});
test('unrelated step is never joined by timestamp or event order',()=>{
 const result=normalize({status:'completed',started_at:at,trace:[span],steps:[{step_id:'different',status:'completed',started_at:at,finished_at:'2026-10-13T01:00:02Z'}]});
 assert.equal(result.spans[0].measured,null);
 assert.equal(result.spans[0].status,'recorded');
});
test('zero-duration event remains measured zero; negative closed intervals remain unknown',()=>{
 const result=normalize({status:'completed',trace:[{...span,duration_ms:0},{...span,span_id:'s~2',finished_at:'2026-10-13T00:59:59Z'}]});
 assert.equal(result.spans[0].measured,0);
 assert.equal(result.spans[1].measured,null);
});
test('live elapsed time and terminal elapsed time use task timestamps',()=>{
 const end=Date.parse('2026-10-13T01:00:05Z');
 assert.equal(normalize({status:'running',started_at:at,trace:[]},end).elapsed,5000);
 assert.equal(normalize({status:'failed',started_at:at,finished_at:'2026-10-13T01:00:03Z',trace:[]},end).elapsed,3000);
});
