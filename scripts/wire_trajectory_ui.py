"""One-time, exact replacements; preserve the existing templates' line endings."""
from pathlib import Path

def edit(path, replacements):
    target=Path(path)
    text=target.read_bytes().decode('utf-8')
    for old,new in replacements:
        if text.count(old)!=1:
            raise RuntimeError(f'{path}: expected one occurrence of {old[:70]!r}, got {text.count(old)}')
        text=text.replace(old,new,1)
    target.write_bytes(text.encode('utf-8'))

edit('pyharness/ui/platform.html',[
 ('</head><body>','<link rel="stylesheet" href="/assets/theme-kit/styles/run-trace.css"></head><body>'),
 ('<script src="/assets/host-adapters/model-editor.js">','<script src="/assets/host-adapters/run-trace.js"></script><script src="/assets/host-adapters/model-editor.js">'),
 ('let panelStack=[],pendingConfirmation=null,presentation=0;',"const trajectoryView=window.PyharnessRunTrace.create({get,esc,badge,onApprove:()=>{trajectoryView.destroy();go('approvals')},onArtifact:id=>{trajectoryView.destroy();return actions['artifact-preview'](id)}});\r\nlet panelStack=[],pendingConfirmation=null,presentation=0;"),
 ('function resetPanels(){presentation++;', 'function resetPanels(){trajectoryView.destroy();presentation++;'),
 ("'run-detail':async id=>showJSON('运行详情与 Trace',await get('runs/'+encodeURIComponent(id))),", "'run-detail':id=>trajectoryView.open(id),\r\n'session-trajectory':id=>trajectoryView.open({session_id:id}),\r\n'workflow-trajectory':id=>{const [session_id,workflow]=JSON.parse(id);return trajectoryView.open({session_id,workflow})},"),
 ("btn('查看结构化 Trace','run-detail',selected.run_id)","btn('查看动态轨迹','run-detail',selected.run_id)"),
 ("state.sid?btn('子任务','subtasks',state.sid)","state.sid?btn('会话动态轨迹','session-trajectory',state.sid)+btn('子任务','subtasks',state.sid)"),
 ("${w.status==='running'?btn('取消','workflow-cancel',w.workflow_id):''}","${btn('查看轨迹','workflow-trajectory',JSON.stringify([w.session_id,w.workflow_id]))}${w.status==='running'?btn('取消','workflow-cancel',w.workflow_id):''}"),
 ("window.addEventListener('beforeunload',()=>{rememberCompose();", "window.addEventListener('beforeunload',()=>{trajectoryView.destroy();rememberCompose();"),
])
edit('pyharness/ui/index.html',[
 ('<title>PyHarness Desktop</title>','<title>PyHarness Desktop</title><link rel="stylesheet" href="/assets/theme-kit/styles/run-trace.css"><script src="/assets/host-adapters/run-trace.js"></script>'),
 ('<h3>Agent 轨迹时间线 <span id="tl-cursor" style="opacity:.6"></span></h3>', '<h3>Agent 轨迹时间线 <span id="tl-cursor" style="opacity:.6"></span><button id="open-agent-trajectory" title="展开当前会话的动态轨迹">展开动态轨迹</button></h3>'),
 ('async function loadTimeline(after = 0) {', "const trajectoryView=window.PyharnessRunTrace.create({get:p=>api('/api/v1/'+p),esc,badge:s=>'<span class=\"badge\">'+esc(window.PyharnessRunTrace.statusLabel(s))+'</span>',onApprove:()=>{trajectoryView.destroy();checkApprovals();},onArtifact:id=>{trajectoryView.destroy();window.location.href='/#artifacts';}});\r\n$('#open-agent-trajectory').onclick=()=>state.sid?trajectoryView.open({session_id:state.sid}):appendMsg({role:'system',content:'请先选择一个会话'});\r\nasync function loadTimeline(after = 0) {\r\n  if(after===0)trajectoryView.destroy();"),
 ("const d = await api(`/api/sessions/${state.sid}/workflow`, {", "const workflowName=document.getElementById('wf-name').value.trim()||'desktop';\r\n      const inspect=document.getElementById('wf-inspect');inspect.hidden=false;inspect.onclick=()=>trajectoryView.open({session_id:state.sid,workflow:workflowName});\r\n      const d = await api(`/api/sessions/${state.sid}/workflow`, {"),
 ("<pre id='wf-result' class='raw' style='margin-top:10px'>尚未运行</pre>","<button id='wf-inspect' hidden>查看工作流动态轨迹</button><pre id='wf-result' class='raw' style='margin-top:10px'>尚未运行</pre>"),
])
edit('pyharness/ui/host-adapters/run-trace.js',[
 ("root.addEventListener('click',e=>{const el=", "root.addEventListener('click',e=>{if(e.target.closest('.run-trace')!==root)return;const el="),
 ("root.addEventListener('click',async e=>{const el=", "root.addEventListener('click',async e=>{if(e.target.closest('.run-trace')!==root)return;const el="),
 ("root.addEventListener('input',e=>{if(", "root.addEventListener('input',e=>{if(e.target.closest('.run-trace')!==root)return;if("),
 ("root.addEventListener('change',e=>{if(", "root.addEventListener('change',e=>{if(e.target.closest('.run-trace')!==root)return;if("),
])
