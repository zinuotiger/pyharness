/* Durable reference-only submission hints are never authority or success state. */
(function (global) {
  'use strict';
  global.PyharnessTaskInbox = {create({main, api, esc}) {
    const c = global.PyharnessWorkspaceContracts, memory = new Map();
    let executionPanel = null;
    let el = null, epoch = 0, project = null, realm = '', scope = '', trigger = null, busy = false, loading = false;
    let tasks = [], docs = [], cites = [], deliveries = [], attempts = [], cursor = null;
    const node = q => el?.querySelector(q), ident = () => crypto.randomUUID().replaceAll('-', '').slice(0, 16);
    const base = p => '/api/v1/projects/' + p, equal = (a, b) => c.canonical(a) === c.canonical(b);
    const ref = (v, key) => Object.fromEntries([key, 'revision', 'digest', 'source_seq'].map(k => [k, v[k]]));
    const live = (dialog, version) => el === dialog && dialog.isConnected && dialog.open && epoch === version;
    const say = text => { if (node('[data-inbox-status]')) node('[data-inbox-status]').textContent = text; };
    const clear = () => node('[data-inbox-result]')?.replaceChildren();
    async function identity() {
      const who = await api('/api/account/me'), a = who.account;
      return JSON.stringify(a ? [a.tenant, a.user_id || a.username] : ['local', who.username || 'local']);
    }
    function keyFor(s) { return 'pyharness.inbox.pending.v1:' + s; }
    function persist(record, s) {
      memory.set(s, record);
      try { localStorage.setItem(keyFor(s), JSON.stringify(record)); return true; } catch (_) { return false; }
    }
    function forget(record, s) {
      if (memory.get(s)?.key !== record.key) return;
      memory.delete(s);
      try { if (JSON.parse(localStorage.getItem(keyFor(s)))?.key === record.key) localStorage.removeItem(keyFor(s)); } catch (_) {}
    }
    function validHint(v) {
      if (!v || v.realm !== realm || v.project !== project.project_id || !/^[A-Za-z0-9._-]{1,128}$/.test(v.key)
        || !equal(Object.keys(v).sort(), ['realm','project','key','body','digest','uncertain'].sort()) || typeof v.uncertain !== 'boolean'
        || typeof v.body !== 'string' || v.body.length > 20000 || (v.digest != null && !c.hash(v.digest))) return false;
      try {
        const b = JSON.parse(v.body), allowed = ['schema_version','attempt_id','task_ref','document_refs','citation_refs','delivery_ref'];
        const validRef = (r, k, pattern) => r && equal(Object.keys(r).sort(), [k,'revision','digest','source_seq'].sort())
          && pattern.test(r[k]) && c.integer(r.revision) && c.hash(r.digest) && c.integer(r.source_seq);
        return b.schema_version === 1 && equal(Object.keys(b).sort(), allowed.sort()) && /^ra-[0-9a-f]{16}$/.test(b.attempt_id)
          && validRef(b.task_ref, 'product_task_id', /^pt-[0-9a-f]{16}$/)
          && Array.isArray(b.document_refs) && b.document_refs.length <= 20 && b.document_refs.every(r => validRef(r, 'document_id', /^doc-[0-9a-f]{16}$/))
          && Array.isArray(b.citation_refs) && b.citation_refs.length <= 20 && b.citation_refs.every(r => validRef(r, 'citation_id', /^cit-[0-9a-f]{16}$/))
          && (b.delivery_ref === null || validRef(b.delivery_ref, 'delivery_id', /^del-[0-9a-f]{16}$/));
      } catch (_) { return false; }
    }
    function recover() {
      if (memory.has(scope)) return memory.get(scope);
      try { const v = JSON.parse(localStorage.getItem(keyFor(scope))); if (validHint(v)) { memory.set(scope, v); return v; } } catch (_) {}
      return null;
    }
    function controls() {
      const pending = memory.get(scope);
      node('#inboxSubmitForm')?.querySelectorAll('input,select,button').forEach(n => n.disabled = busy || loading || !realm || !!pending);
      node('[data-inbox-action=retry]')?.toggleAttribute('disabled', busy || loading || !pending);
      node('[data-inbox-action=query]')?.toggleAttribute('disabled', busy || loading || !realm);
      node('[data-inbox-action=refresh]')?.toggleAttribute('disabled', busy || loading || !realm);
      node('[data-inbox-action=more]')?.toggleAttribute('disabled', busy || loading || !cursor);
    }
    function invalidate() { epoch++; loading = false; executionPanel?.invalidate(); el?.close(); el?.remove(); el = null; }
    function close() { invalidate(); trigger?.focus(); }
    async function allRows(path, field) {
      const result = []; let next = null;
      for (let i = 0; i < 4; i++) {
        const page = await api(path + '?limit=50' + (next ? '&cursor=' + encodeURIComponent(next) : ''));
        if (!Array.isArray(page[field])) throw Error('列表收据不完整');
        result.push(...page[field]); next = page.next_cursor;
        if (!next) return result;
      }
      throw Error('当前可选上下文超过界面加载上限，请减少有效资料后刷新');
    }
    function deliveryOptions() {
      const selected = tasks.find(t => t.product_task_id === node('#inboxSubmitForm')?.elements.task.value);
      node('#inboxSubmitForm').elements.delivery.innerHTML = '<option value="">不附交付清单</option>' + deliveries.filter(d => d.status === 'defined' && selected && equal(d.task_ref, ref(selected, 'product_task_id'))).map(d => `<option value="${esc(d.delivery_id)}">${esc(d.delivery_id)} · r${d.revision}</option>`).join('');
    }
    function draw() {
      const form = node('#inboxSubmitForm');
      form.elements.task.innerHTML = '<option value="">请选择已绑定输入的正式任务</option>' + tasks.filter(t => t.status === 'definition_ready').map(t => `<option value="${esc(t.product_task_id)}">${esc(t.title)} · r${t.revision}</option>`).join('');
      form.elements.documents.innerHTML = docs.filter(d => d.status === 'current').map(d => `<option value="${esc(d.document_id)}">${esc(d.title)} · r${d.revision}</option>`).join('');
      form.elements.citations.innerHTML = cites.filter(d => d.status === 'current').map(d => `<option value="${esc(d.citation_id)}">${esc(d.citation_id)} · ${d.page == null ? '文本' : '第' + d.page + '页'}</option>`).join('');
      deliveryOptions();
      const drafts = tasks.filter(t => t.status === 'draft').length;
      node('[data-inbox-drafts]').textContent = `${tasks.filter(t => t.status === 'definition_ready').length} 个可提交任务 · ${drafts} 个草稿` + (drafts ? '。草稿需先在“正式产品任务”中绑定输入。' : '');
      drawAttempts(); controls();
    }
    function drawAttempts() {
      node('[data-inbox-attempts]').innerHTML = attempts.length ? attempts.map(a => {
        const task = tasks.find(t => equal(ref(t, 'product_task_id'), a.prerequisites.task_ref));
        return `<article style="border-top:1px solid var(--line);padding:16px 0"><strong>${esc(task?.title || a.prerequisites.task_ref.product_task_id)} · r${a.prerequisites.task_ref.revision}</strong><p>尝试已登记 · 执行状态需核对 · ${a.input_readiness === 'current' ? '绑定仍有效' : '输入或引用已变化'}</p><p>资料 ${a.context.document_refs.length} · 引用 ${a.context.citation_refs.length} · ${esc(new Date(a.created_at).toLocaleString('zh-CN'))}</p><p style="color:var(--muted)">${esc(a.attempt_id)}</p><button data-inbox-action="inspect" data-attempt="${esc(a.attempt_id)}">查看精确提交</button> <button data-inbox-action="execute" data-attempt="${esc(a.attempt_id)}">执行 / 查看收据</button></article>`;
      }).join('') : '<p>暂无当前主体可读取的已提交尝试。</p>';
    }
    async function load() {
      const dialog = el, version = ++epoch, p = project.project_id; clear(); attempts = []; cursor = null;
      loading = true; controls();
      node('[data-inbox-attempts]')?.replaceChildren(); say('正在核对当前主体和提交记录…');
      try {
        if (await identity() !== realm) throw Error('主体已变化，请关闭后重新打开');
        const values = await Promise.all([api(base(p) + '/product-tasks'), allRows(base(p) + '/documents', 'documents'),
          allRows(base(p) + '/citations', 'citations'), allRows(base(p) + '/delivery-definitions', 'deliveries'), api(base(p) + '/run-attempts?limit=25')]);
        if (!live(dialog, version)) return;
        if (await identity() !== realm) throw Error('主体已变化，请关闭后重新打开');
        if (!live(dialog, version)) return;
        tasks = values[0].tasks; docs = values[1]; cites = values[2]; deliveries = values[3]; attempts = values[4].attempts; cursor = values[4].next_cursor;
        draw(); const hint = recover();
        if (hint) { node('[data-inbox-key]').value = hint.key; say('发现待核对的原提交线索。请只读查询，或显式使用原键和原选择重试；没有自动重发。'); }
        else say('记录已核对。提交登记尝试，尚未开始执行。');
        controls();
      } catch (error) { if (live(dialog, version)) { tasks = []; docs = []; cites = []; deliveries = []; attempts = []; draw(); say('读取失败：' + error.message); } }
      finally { if (live(dialog, version)) { loading = false; controls(); } }
    }
    async function fingerprint(record) {
      const bytes = new TextEncoder().encode(c.canonical({operation:'run_attempt.submit', project_id:record.project, command:JSON.parse(record.body)}));
      return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), b => b.toString(16).padStart(2, '0')).join('');
    }
    function matches(value, record) {
      return value && value.project_id === record.project && value.submission_key === record.key && value.status === 'reserved'
        && value.persisted === true && value.attempt_reserved === true && value.execution_authorized === false && value.execution_started === false
        && value.dispatch_policy === 'explicit_governed_start' && c.hash(value.digest) && c.integer(value.source_seq)
        && c.hash(value.binding_digest) && value.request_digest === record.digest && equal(value.selection, JSON.parse(record.body));
    }
    async function send(record) {
      if (busy) return;
      const dialog = el, version = ++epoch, s = scope, wasUnknown = !!record.uncertain;
      busy = true; clear(); say('正在提交并核对持久记录…'); controls(); let accepted = false;
      try {
        if (await identity() !== record.realm) throw Error('主体已变化，禁止重发原提交');
        const digest = await fingerprint(record);
        if (record.digest && record.digest !== digest) throw Error('恢复线索摘要不符，禁止重发');
        record.digest = digest; record.uncertain = true;
        const durableHint = persist(record, s); controls();
        await api(base(record.project) + '/run-attempts', {method:'POST', body:record.body, headers:{'Idempotency-Key':record.key}}); accepted = true;
        const observed = await api(base(record.project) + '/run-submissions/by-key?key=' + encodeURIComponent(record.key));
        if (await identity() !== record.realm) throw Error('主体已变化，保留原提交键');
        if (observed.status !== 'committed' || !matches(observed.attempt, record)) throw Error('原键读回与提交选择不一致');
        forget(record, s);
        if (live(dialog, version)) { node('[data-inbox-result]').textContent = JSON.stringify(observed.attempt, null, 2); say('提交已精确持久确认；尚未开始执行。刷新可查看收件箱。' + (durableHint ? '' : '浏览器恢复线索不可用，请保留提交键。')); }
      } catch (error) {
        const rejected = !wasUnknown && !accepted && error.status >= 400 && error.status < 500;
        if (rejected) forget(record, s); else persist(record, s);
        if (live(dialog, version)) say(rejected ? '提交被明确拒绝：' + error.message + '。可核对版本后重新填写。' : '提交结果未确认：' + error.message + '。保留原键；只读查询或显式原键重试，不自动另建。');
      } finally { busy = false; if (el) controls(); }
    }
    async function query(key) {
      if (busy || !/^[A-Za-z0-9._-]{1,128}$/.test(key)) { if (!busy) say('请输入1–128位合法原提交键'); return; }
      const dialog = el, version = ++epoch, p = project.project_id, s = scope, observedRealm = realm;
      const hint = memory.get(s); busy = true; clear(); say('正在只读查询原提交…'); controls();
      try {
        if (await identity() !== observedRealm) throw Error('主体已变化');
        const result = await api(base(p) + '/run-submissions/by-key?key=' + encodeURIComponent(key));
        if (await identity() !== observedRealm) throw Error('主体已变化');
        if (result.project_id !== p || result.submission_key !== key) throw Error('查询范围不一致');
        if (result.status === 'committed') {
          if (hint?.key === key) {
            const digest = await fingerprint(hint);
            if (hint.digest != null && hint.digest !== digest) throw Error('恢复线索摘要不符');
            hint.digest = digest;
            if (!matches(result.attempt, hint)) throw Error('原选择摘要或持久回读不一致');
          }
          if (hint?.key === key) forget(hint, s);
          if (live(dialog, version)) { node('[data-inbox-result]').textContent = JSON.stringify(result.attempt, null, 2); say('已查询到原提交的持久记录；执行状态请另行核对。'); }
        } else if (result.status === 'not_found' && result.attempt === null) {
          if (live(dialog, version)) say('尚无原键的已确认提交事实。保留原键和原选择；' + (result.writer_requires_reopen ? '写入端需重开后再显式重试。' : '可显式原键重试，不自动另建。'));
        } else throw Error('查询结果不完整');
      } catch (error) { if (live(dialog, version)) say('查询未确认：' + error.message + '。原提交线索保留。'); }
      finally { busy = false; if (el) controls(); }
    }
    async function act(action, button) {
      if (action === 'close') return close();
      if (busy) return;
      if (action === 'refresh') return load();
      if (action === 'query') return query(node('[data-inbox-key]').value);
      if (action === 'retry') { const hint = recover(); if (hint) return send(hint); return; }
      const dialog = el, version = ++epoch, p = project.project_id; clear(); say('正在读取当前提交记录…');
      if (await identity() !== realm) throw Error('主体已变化');
      if (!live(dialog, version)) return;
      if (action === 'execute' || action === 'execution-control') {
        if (!global.PyharnessExecutions) throw Error('执行界面尚未加载，请刷新页面');
        executionPanel = executionPanel || global.PyharnessExecutions.create({main, api, esc});
        return executionPanel.open(project, action === 'execute' ? button.dataset.attempt : '');
      }
      if (action === 'more' && cursor) {
        const page = await api(base(p) + '/run-attempts?limit=25&cursor=' + encodeURIComponent(cursor));
        if (await identity() !== realm || !live(dialog, version)) return;
        attempts.push(...page.attempts); cursor = page.next_cursor; drawAttempts(); controls();
      }
      if (action === 'inspect') {
        const result = await api(base(p) + '/run-attempts/' + button.dataset.attempt);
        if (await identity() === realm && live(dialog, version)) { node('[data-inbox-result]').textContent = JSON.stringify(result, null, 2); say('已读取精确提交，未触发执行。'); }
      }
    }
    async function open(value, button) {
      invalidate(); project = value; trigger = button; realm = ''; scope = ''; tasks = []; docs = []; cites = []; deliveries = []; attempts = []; cursor = null;
      const dialog = document.createElement('dialog'); el = dialog; dialog.setAttribute('aria-label', '任务收件箱'); dialog.className = 'task-inbox-dialog';
      dialog.style.cssText = 'width:min(980px,94vw);max-height:90vh;overflow:auto;padding:24px;font:inherit;box-sizing:border-box';
      dialog.innerHTML = `<style>.task-inbox-dialog h2{font-size:20px;line-height:1.4;margin:0 0 8px}.task-inbox-dialog h3{font-size:16px;margin:18px 0 8px}.task-inbox-dialog p{margin:8px 0;line-height:1.65}.task-inbox-dialog details{margin:12px 0}.task-inbox-dialog summary{cursor:pointer}.task-inbox-dialog button{margin:4px 0}.task-inbox-dialog [data-inbox-key]{width:min(100%,340px)}.task-inbox-dialog form{display:grid;gap:12px}.task-inbox-dialog select{width:100%;box-sizing:border-box}.task-inbox-dialog select[multiple]{min-height:80px;max-height:160px}.task-inbox-dialog input{max-width:100%;box-sizing:border-box}.task-inbox-dialog [data-inbox-status]{line-height:1.6}</style><h2>任务收件箱 · ${esc(project.name)}</h2><p>为任务保存独立尝试，固定这次选择的输入与引用版本。</p><button data-inbox-action="close">关闭</button> <button data-inbox-action="refresh">刷新核对</button> <button data-inbox-action="execution-control">核对执行 / 停止</button><p data-inbox-status role="status"></p><p data-inbox-drafts></p><details><summary>提交新尝试</summary><form id="inboxSubmitForm"><label>正式任务版本 <select name="task" required></select></label><label>资料版本（可多选） <select name="documents" multiple></select></label><label>已有引用（可多选） <select name="citations" multiple></select></label><label>交付清单版本 <select name="delivery"></select></label><p>清单包含的引用和资料会一并锁定；提交时重新核验权限和版本。</p><button type="submit">提交运行尝试</button></form></details><h3>核对原提交</h3><label>原提交键 <input data-inbox-key maxlength="128"></label> <button data-inbox-action="query">只读查询</button> <button data-inbox-action="retry">原键原选择重试</button><h3>已提交尝试</h3><section data-inbox-attempts></section><button data-inbox-action="more">更多尝试</button><details><summary>精确版本与持久证据</summary><pre data-inbox-result style="white-space:pre-wrap;overflow-wrap:anywhere"></pre></details>`;
      main.append(dialog); dialog.showModal(); controls();
      dialog.oncancel = event => { event.preventDefault(); close(); };
      dialog.onchange = event => { if (event.target.name === 'task') deliveryOptions(); epoch++; clear(); say('选择已改变，尚未提交。'); };
      dialog.onclick = event => { const b = event.target.closest('[data-inbox-action]'); if (b) { const version = epoch; act(b.dataset.inboxAction, b).catch(error => { if (live(dialog, version + 1)) { clear(); say('操作未确认：' + error.message); } }); } };
      dialog.onsubmit = event => {
        event.preventDefault(); if (busy || memory.has(scope) || !realm) return;
        const form = node('#inboxSubmitForm'), task = tasks.find(t => t.product_task_id === form.elements.task.value);
        if (!task || task.status !== 'definition_ready') { say('请选择已绑定输入的当前正式任务'); return; }
        const selected = (select, values, key) => Array.from(select.selectedOptions).map(o => ref(values.find(v => v[key] === o.value), key));
        const delivery = deliveries.find(d => d.delivery_id === form.elements.delivery.value);
        const command = {schema_version:1, attempt_id:'ra-' + ident(), task_ref:ref(task, 'product_task_id'),
          document_refs:selected(form.elements.documents, docs, 'document_id'), citation_refs:selected(form.elements.citations, cites, 'citation_id'), delivery_ref:delivery ? ref(delivery, 'delivery_id') : null};
        const record = {realm, project:project.project_id, key:'attempt-' + ident(), body:JSON.stringify(command), digest:null, uncertain:false};
        memory.set(scope, record); node('[data-inbox-key]').value = record.key; send(record);
      };
      try { const version = epoch, who = await identity(); if (!live(dialog, version)) return; realm = who; scope = JSON.stringify([realm, project.project_id]); recover(); await load(); }
      catch (error) { if (el === dialog) say('身份核对失败：' + error.message); }
    }
    return {open, invalidate};
  }};
})(window);
