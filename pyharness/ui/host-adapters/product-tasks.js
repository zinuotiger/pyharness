/* Formal task definitions: revisions and input bindings, without executing a task. */
(function (global) {
  'use strict';
  global.PyharnessProductTasks = {create({main, api, esc}) {
    const pending = new Map(), c = global.PyharnessWorkspaceContracts;
    let el = null, epoch = 0, trigger = null, project = null, realm = '', scope = '';
    let rows = [], inputs = [], editing = null, sending = false, cursor = null;
    const node = selector => el?.querySelector(selector);
    const id = () => crypto.randomUUID().replaceAll('-', '').slice(0, 16);
    const root = () => '/api/v1/projects/' + project.project_id;
    const tasks = () => root() + '/product-tasks';
    const ref = (v, key) => Object.fromEntries([key, 'revision', 'digest', 'source_seq'].map(k => [k, v[k]]));
    const same = (a, b) => c.canonical(a) === c.canonical(b);
    const live = (dialog, version) => el === dialog && dialog.isConnected && dialog.open && version === epoch;
    const say = text => { if (node('[data-pt-status]')) node('[data-pt-status]').textContent = text; };
    const clear = () => node('[data-pt-result]')?.replaceChildren();
    async function identity() {
      const who = await api('/api/account/me'), a = who.account;
      return JSON.stringify(a ? [a.tenant, a.user_id || a.username] : ['local', who.username || 'local']);
    }
    function controls() {
      const unknown = pending.has(scope);
      node('#productTaskForm')?.querySelectorAll('input, textarea, select, button').forEach(n => n.disabled = !realm || sending || unknown);
      for (const action of ['retry', 'reconcile']) if (node(`[data-pt-action=${action}]`)) node(`[data-pt-action=${action}]`).disabled = sending || !unknown;
      if (node('[data-pt-action=more]')) node('[data-pt-action=more]').disabled = sending || !cursor;
    }
    function invalidate() { epoch++; el?.close(); el?.remove(); el = null; }
    function close() { invalidate(); trigger?.focus(); }
    function form(task = null) {
      editing = task; clear();
      const form = node('#productTaskForm'); if (!form) return;
      form.elements.title.value = task?.title || '';
      form.elements.objective.value = task?.objective || '';
      form.elements.criteria.value = task?.acceptance_criteria.join('\n') || '';
      form.elements.retired.checked = task?.retired || false;
      const select = form.elements.input;
      select.innerHTML = '<option value="">暂不绑定输入（草稿）</option>' + inputs.map(i => `<option value="${esc(i.input_id)}">${esc(i.input_id)} · 清单 r${i.manifest_ref.revision} · ${esc(i.status)}</option>`).join('');
      if (task?.input_binding) {
        const key = task.input_binding.input_ref.input_id;
        const existing = inputs.find(i => i.input_id === key && same(ref(i, 'input_id'), task.input_binding.input_ref));
        if (!existing) select.insertAdjacentHTML('beforeend', '<option value="__original__">原确切输入绑定（保存时重新核验）</option>');
        select.value = existing ? key : '__original__';
      }
      node('[data-pt-editing]').textContent = task ? `编辑 ${task.product_task_id} r${task.revision}，保存产生新修订` : '新建正式产品任务';
      controls();
    }
    function drawRows() {
      node('[data-pt-rows]').innerHTML = rows.length ? rows.map(t => `<article style="border-top:1px solid var(--line);padding:12px 0"><strong>${esc(t.title)} · r${t.revision}</strong><p>${esc(({draft:'草稿',definition_ready:'任务定义就绪',stale:'引用已失效',retired:'已退役'})[t.status] || t.status)} · ${esc(t.product_task_id)}</p><p style="white-space:pre-wrap">${esc(t.objective)}</p><p>${t.input_binding ? '已绑定确切私有输入和清单版本' : '无输入绑定，仅保存定义'}</p><button data-pt-action="inspect" data-task="${esc(t.product_task_id)}">查看修订</button> <button data-pt-action="edit" data-task="${esc(t.product_task_id)}" ${t.retired ? 'disabled' : ''}>编辑定义</button> <button data-pt-action="preflight" data-task="${esc(t.product_task_id)}" ${t.status !== 'definition_ready' ? 'disabled' : ''}>检查运行前置</button></article>`).join('') : '<p>暂无当前主体可读取的正式任务。被撤销的输入不会显示为可用。</p>';
    }
    async function load() {
      const dialog = el, version = ++epoch;
      rows = []; inputs = []; cursor = null;
      node('[data-pt-rows]')?.replaceChildren(); form();
      say('正在核对主体、任务修订与私有输入…');
      try {
        const observedRealm = await identity();
        if (!live(dialog, version)) return;
        if (observedRealm !== realm) throw Error('主体已变化，请关闭并重新打开；未重发旧命令。');
        const [listing, page] = await Promise.all([api(tasks()), api(root() + '/workspace-inputs?limit=50')]);
        if (!live(dialog, version)) return;
        rows = listing.tasks; inputs = page.inputs.filter(i => i.status === 'input_prepared' && i.project_revision === project.revision); cursor = page.next_cursor;
        drawRows(); form();
        say(pending.has(scope) ? '原命令结果尚未确认。请只读核对或使用原键、原正文重试。' : '已加载。保存定义、绑定输入和检查前置均不会创建运行尝试。');
      } catch (error) { if (live(dialog, version)) say('读取失败：' + error.message); }
    }
    function matches(value, command) {
      const body = JSON.parse(command.body);
      return value && value.mode === 'product_task' && value.schema_version === 2 && value.project_id === command.project
        && value.revision === body.expected_revision + 1 && value.executable === false && value.attempt_reserved === false
        && c.hash(value.digest) && c.integer(value.source_seq) && value.execution_policy === 'governed-attempt-only'
        && Object.entries(body).filter(([k]) => k !== 'expected_revision').every(([k, v]) => same(value[k], v));
    }
    async function send(command, readOnly = false) {
      if (sending) return;
      const dialog = el, version = ++epoch, commandScope = scope; clear(); sending = true; controls();
      try {
        if (await identity() !== command.realm) throw Error('主体已变化，禁止重发原命令。');
        const base = '/api/v1/projects/' + command.project + '/product-tasks', body = JSON.parse(command.body);
        let receipt = null;
        if (!readOnly) { receipt = await api(base, {method: 'POST', body: command.body, headers: {'Idempotency-Key': command.key}}); command.uncertain = true; }
        const exact = await api(`${base}/${body.product_task_id}/revisions/${body.expected_revision + 1}`);
        if (!matches(exact, command) || (receipt && (!matches(receipt, command) || !same(ref(receipt, 'product_task_id'), ref(exact, 'product_task_id'))))) throw Error('持久化回读与原命令不一致，结果仍未确认。');
        pending.delete(commandScope);
        if (live(dialog, version)) {
          node('[data-pt-result]').textContent = JSON.stringify(exact, null, 2);
          say('正式任务修订已精确持久确认；本次仅保存定义。可到任务收件箱显式提交运行尝试。');
        }
      } catch (error) {
        const knownRejected = !readOnly && !command.uncertain && error.status >= 400 && error.status < 500;
        if (knownRejected) pending.delete(commandScope); else command.uncertain = true;
        // A failed retry cannot establish whether the original command committed.
        if (live(dialog, version)) say(knownRejected ? '命令被明确拒绝：' + error.message + '。请核对输入、权限或版本后再保存。' : '结果未确认：' + error.message + '。保留原键与原正文；可只读核对，禁止自动新建。');
      } finally { sending = false; if (el === dialog) controls(); }
    }
    async function act(action, button) {
      if (action === 'close') return close();
      if (sending) return;
      if (action === 'refresh') return load();
      if (action === 'new') { epoch++; if (!pending.has(scope)) { form(); say('正在填写新定义；尚未保存，旧前置检查已失效。'); } return; }
      if (action === 'retry' || action === 'reconcile') { const cmd = pending.get(scope); if (cmd) return send(cmd, action === 'reconcile'); return; }
      if (action === 'more') {
        if (!cursor) return;
        const dialog = el, version = ++epoch; clear();
        const page = await api(root() + '/workspace-inputs?limit=50&cursor=' + encodeURIComponent(cursor));
        if (!live(dialog, version)) return;
        inputs.push(...page.inputs.filter(i => i.status === 'input_prepared' && i.project_revision === project.revision)); cursor = page.next_cursor;
        form(editing); return;
      }
      const task = rows.find(t => t.product_task_id === button.dataset.task); if (!task) return;
      if (action === 'edit') { epoch++; if (!pending.has(scope)) { form(task); say('正在编辑定义；保存将产生新修订，旧前置检查已失效。'); } return; }
      const dialog = el, version = ++epoch; clear();
      if (action === 'inspect') {
        say('正在读取确切历史修订…');
        const result = await api(`${tasks()}/${task.product_task_id}/revisions/${task.revision}`);
        if (live(dialog, version)) node('[data-pt-result]').textContent = JSON.stringify(result, null, 2);
      }
      if (action === 'preflight') {
        say('正在检查当前授权、确切输入字节与版本…');
        const result = await api(`${tasks()}/${task.product_task_id}/preflight`, {method: 'POST', body: JSON.stringify({schema_version: 1, task_ref: ref(task, 'product_task_id')})});
        if (!live(dialog, version)) return;
        const p = result.prerequisites;
        if (result.status !== 'prerequisites_checked' || result.execution_authorized !== false || result.attempt_reserved !== false
          || result.persisted !== false || !c.hash(result.binding_digest) || !same(p.task_ref, ref(task, 'product_task_id'))
          || !same(p.input_binding, task.input_binding) || p.project_id !== project.project_id || p.project_revision !== task.project_revision) throw Error('前置检查收据绑定不一致');
        node('[data-pt-result]').textContent = JSON.stringify(result, null, 2);
        say('运行前置检查通过；仅为本次只读观察，未授权执行、未预留运行尝试，提交时必须重新校验。');
      }
    }
    async function open(value, button) {
      invalidate(); project = value; trigger = button; realm = ''; scope = ''; rows = []; inputs = []; editing = null; cursor = null;
      const dialog = document.createElement('dialog'); el = dialog;
      dialog.setAttribute('aria-label', '正式产品任务'); dialog.className = 'product-task-dialog'; dialog.style.cssText = 'width:min(1050px,94vw);max-height:90vh;overflow:auto;padding:24px;font:inherit;box-sizing:border-box';
      dialog.innerHTML = `<style>.product-task-dialog form{display:grid;gap:10px}.product-task-dialog form p{margin:0}.product-task-dialog form input:not([type=checkbox]),.product-task-dialog select{max-width:100%;box-sizing:border-box}.product-task-dialog textarea{display:block;width:100%;height:82px;min-height:82px;max-height:160px;resize:vertical;box-sizing:border-box}.product-task-dialog details{margin-top:12px}.product-task-dialog [data-pt-status]{line-height:1.6}</style><h2>正式产品任务 · ${esc(project.name)}</h2><p>保存目标、验收项和确切输入版本。可到“任务收件箱”显式提交运行尝试；本页仅保存定义，不执行模型或工具。</p><button data-pt-action="close">关闭</button> <button data-pt-action="refresh">刷新核对</button> <button data-pt-action="new">新建定义</button><p data-pt-status role="status"></p><button data-pt-action="reconcile">只读核对原命令</button> <button data-pt-action="retry">原键原正文重试</button><h3 data-pt-editing></h3><form id="productTaskForm"><p><label>任务标题 <input name="title" maxlength="100" required></label></p><p><label>任务目标 <textarea name="objective" maxlength="8000" required></textarea></label></p><p><label>验收标准（每行一项，最多30项） <textarea name="criteria" required></textarea></label></p><p><label>私有输入版本 <select name="input"><option value="">暂不绑定输入（草稿）</option></select></label> <button type="button" data-pt-action="more">加载更多输入</button></p><label><input type="checkbox" name="retired"> 退役此任务（不可恢复）</label><p><button type="submit">保存正式任务定义</button></p></form><section data-pt-rows></section><details><summary>版本与校验证据</summary><pre data-pt-result style="white-space:pre-wrap;overflow-wrap:anywhere"></pre></details>`;
      main.append(dialog); dialog.showModal(); controls();
      dialog.oncancel = event => { event.preventDefault(); close(); };
      dialog.oninput = () => { epoch++; clear(); say('定义内容已改变；尚未保存，旧前置检查已失效。'); };
      dialog.onclick = event => { const b = event.target.closest('[data-pt-action]'); if (b) {
        if (!realm && b.dataset.ptAction !== 'close') return;
        const version = epoch + (['inspect', 'preflight', 'more'].includes(b.dataset.ptAction) ? 1 : 0);
        act(b.dataset.ptAction, b).catch(error => { if (live(dialog, version)) { clear(); say('操作被拒绝：' + error.message); } });
      } };
      dialog.onsubmit = event => {
        event.preventDefault(); if (sending || pending.has(scope) || !realm) return;
        const formNode = node('#productTaskForm'), selected = formNode.elements.input.value;
        const wi = inputs.find(i => i.input_id === selected);
        const binding = selected === '__original__' ? editing.input_binding : wi ? {manifest_ref: wi.manifest_ref, input_ref: ref(wi, 'input_id')} : null;
        const criteria = formNode.elements.criteria.value.split('\n');
        if (criteria.length > 30 || criteria.some(v => !v.trim() || v.length > 1000)) { say('验收项需为1–30条非空文本，每项不超过1000字。'); return; }
        const body = {schema_version: 2, product_task_id: editing?.product_task_id || 'pt-' + id(), expected_revision: editing?.revision || 0,
          project_revision: project.revision, title: formNode.elements.title.value, objective: formNode.elements.objective.value,
          acceptance_criteria: criteria, input_binding: binding, retired: formNode.elements.retired.checked};
        const cmd = {body: JSON.stringify(body), key: 'product-task-' + id(), project: project.project_id, realm};
        pending.set(scope, cmd); send(cmd);
      };
      try {
        const version = epoch; const observedRealm = await identity(); if (!live(dialog, version)) return;
        realm = observedRealm; scope = JSON.stringify([realm, project.project_id]); await load();
      } catch (error) { if (el === dialog) say('身份核对失败：' + error.message); }
    }
    return {open, invalidate};
  }};
})(window);
