/* Project metadata only: no implicit directory grant, model call or host write. */
(function (global) {
  'use strict';
  global.PyharnessProjects = {
    create({main, api, esc, date, render}) {
      let rows = [], editing = null, busy = false, pending = null, message = '';
      const id = () => crypto.randomUUID().replaceAll('-', '').slice(0, 16);
      const grants = global.PyharnessSourceGrants.create({main, api, esc, date});
      const productTasks = global.PyharnessProductTasks?.create({main, api, esc});
      const documents = global.PyharnessDocuments?.create({main, api, esc});
      const inbox = global.PyharnessTaskInbox?.create({main, api, esc});
      const graphs = global.PyharnessTaskGraphs?.create({main, api, esc});
      const deliveries = global.PyharnessDeliveries?.create({main, api, esc});
      const writebacks = global.PyharnessWritebacks?.create({main, api, esc});
      const observations = global.PyharnessObservations?.create({main, api, esc});
      const regressions = global.PyharnessRegressionData?.create({main, api, esc});
      const experiments = global.PyharnessExperiments?.create({main, api, esc});
      let auditGeneration = 0, auditTrigger = null, auditFilter = null;
      let auditPages = [], auditIndex = 0, auditBusy = false;
      function closeAudit() {
        auditGeneration++;
        main.querySelector('#auditDialog')?.close();
        auditTrigger?.focus();
        auditBusy = false;
      }
      async function loadAudit(cursor = null, index = 0, cached = null) {
        const dialog = main.querySelector('#auditDialog');
        const generation = ++auditGeneration;
        auditBusy = true;
        dialog.querySelector('#auditStatus').textContent = '加载审计中…';
        dialog.querySelector('#auditRows').replaceChildren();
        dialog.querySelectorAll('[data-audit-action=previous], [data-audit-action=next]').forEach(el => el.disabled = true);
        const params = new URLSearchParams({pagination_version: '1', limit: '50'});
        if (cursor) params.set('cursor', cursor);
        if (auditFilter) params.set('project_id', auditFilter);
        try {
          // Previous redisplays an already received page. Reissuing the initial
          // cursor-less GET would silently replace the frozen watermark.
          const page = cached || await api('/api/v1/workspace/audit?' + params);
          if (generation !== auditGeneration || !dialog.isConnected || !dialog.open) return;
          auditPages[index] = {cursor, page};
          auditPages.length = index + 1;
          auditIndex = index;
          dialog.querySelector('#auditStatus').textContent = `第 ${index + 1} 页 · 本页 ${page.page_count} 条 · 固定水位总计 ${page.total} 条${page.total ? '' : ' · 暂无审计记录'}`;
          dialog.querySelector('#auditWatermark').textContent = `固定水位 seq=${page.source_watermark.seq} · ${page.source_watermark.digest}。刷新将建立新水位。仅响应有界，当前每页仍校验完整日志。`;
          // Text is escaped; seq/revision are strict integers from the versioned DTO.
          dialog.querySelector('#auditRows').innerHTML = page.events.map(e => `<article data-audit-seq="${e.seq}" style="border-top:1px solid var(--line);padding:12px 0;overflow-wrap:anywhere"><strong>seq=${e.seq} · ${esc(e.type)}</strong><p>${esc(date(e.ts))} · ${esc(e.resource_kind || '控制会话')} · ${esc(e.resource_id || '—')} · ${e.revision ? 'r' + e.revision : '—'}</p><code>${esc(e.digest || '—')}</code></article>`).join('');
          dialog.querySelector('[data-audit-action=previous]').disabled = index === 0;
          dialog.querySelector('[data-audit-action=next]').disabled = !page.has_more;
        } catch (error) {
          if (generation !== auditGeneration || !dialog.isConnected || !dialog.open) return;
          const detail = error.status === 422 ? '审计水位过期或游标无效，请刷新重新建立水位。' : '审计读取失败，请刷新重试；未启动任何执行。';
          dialog.querySelector('#auditStatus').textContent = `${detail} ${error.message}`;
        } finally {
          if (generation === auditGeneration) auditBusy = false;
        }
      }
      const setMessage = text => {
        message = text;
        const status = main.querySelector('#projectStatus');
        if (status) status.textContent = text;
      };
      async function save(body) {
        if (busy) return;
        const serialized = JSON.stringify(body);
        if (pending && pending.body !== serialized) {
          setMessage('上一命令结果尚未确认。请先重试原命令或刷新列表核对，不能直接改正文重发。');
          return;
        }
        pending ??= {body: serialized, key: 'project-' + id()};
        busy = true;
        main.querySelectorAll('[data-project-action], #projectForm button').forEach(el => el.disabled = true);
        try {
          await api('/api/v1/projects', {method: 'POST', body: pending.body, headers: {'Idempotency-Key': pending.key}});
          pending = null;
          editing = null;
          message = '项目修订已持久保存。归档不会删除历史，未启动模型或授权宿主目录。';
          await render();
          main.querySelector('#projectName')?.focus();
        } catch (error) {
          if (error.status && error.status < 500) pending = null;
          setMessage(error.status === 409 ? '版本或幂等冲突：未覆盖新内容，请刷新后核对。' : error.message + '；结果未确认时请重试原命令，不要重复新建。');
        } finally {
          busy = false;
          main.querySelectorAll('[data-project-action], #projectForm button').forEach(el => el.disabled = false);
        }
      }
      main.addEventListener('submit', event => {
        const form = event.target.closest('#projectForm');
        if (!form) return;
        event.preventDefault();
        const values = new FormData(form);
        const body = pending ? JSON.parse(pending.body) : {
          project_id: editing?.project_id || 'p-' + id(), expected_revision: editing?.revision || 0,
          name: String(values.get('name')), description: String(values.get('description')), archived: editing?.archived || false
        };
        save(body);
      });
      main.addEventListener('click', async event => {
        const auditAction = event.target.closest('[data-audit-action]')?.dataset.auditAction;
        if (auditAction === 'close') return closeAudit();
        if (auditAction === 'refresh') { auditPages = []; return loadAudit(); }
        if (auditAction && !auditBusy) {
          if (auditAction === 'previous' && auditIndex > 0) return loadAudit(auditPages[auditIndex - 1].cursor, auditIndex - 1, auditPages[auditIndex - 1].page);
          if (auditAction === 'next') return loadAudit(auditPages[auditIndex].page.next_cursor, auditIndex + 1);
        }
        const button = event.target.closest('[data-project-action]');
        if (!button || busy) return;
        const action = button.dataset.projectAction;
        if (action === 'source-grants') return grants.open(button.dataset.projectId, button);
        if (action === 'observations') { const project = rows.find(r => r.project.project_id === button.dataset.projectId)?.project; if (project && observations) return observations.open(project, button); return; }
        if (action === 'regression-data') { const project = rows.find(r => r.project.project_id === button.dataset.projectId)?.project; if (project && regressions) return regressions.open(project, button); return; }
        if (action === 'experiments') { const project = rows.find(r => r.project.project_id === button.dataset.projectId)?.project; if (project && experiments) return experiments.open(project, button); return; }
        if (action === 'writebacks') { const project = rows.find(r => r.project.project_id === button.dataset.projectId)?.project; if (project && writebacks) return writebacks.open(project, button); return; }
        if (action === 'deliveries') { const project = rows.find(r => r.project.project_id === button.dataset.projectId)?.project; if (project && deliveries) return deliveries.open(project, button); return; }
        if (action === 'task-graphs') { const project = rows.find(r => r.project.project_id === button.dataset.projectId)?.project; if (project && graphs) return graphs.open(project, button); return; }
        if (action === 'product-tasks') { const project = rows.find(r => r.project.project_id === button.dataset.projectId)?.project; if (project && productTasks) return productTasks.open(project, button); return; }
        if (action === 'task-inbox') { const project = rows.find(r => r.project.project_id === button.dataset.projectId)?.project; if (project && inbox) return inbox.open(project, button); return; }
        if (action === 'documents') { const project = rows.find(r => r.project.project_id === button.dataset.projectId)?.project; if (project && documents) return documents.open(project, button); return; }
        if (action === 'audit' || action === 'audit-project') {
          auditTrigger = button;
          auditFilter = action === 'audit-project' ? button.dataset.projectId : null;
          auditPages = [];
          const dialog = main.querySelector('#auditDialog');
          dialog.showModal();
          dialog.oncancel = event => { event.preventDefault(); closeAudit(); };
          return loadAudit();
        }
        if (action === 'retry' && pending) return save(JSON.parse(pending.body));
        if (action === 'refresh') return render();
        if (pending) { setMessage('请先核对未确认命令。'); return; }
        if (action === 'new') { editing = null; message = ''; return render(); }
        const project = rows.find(row => row.project.project_id === button.dataset.projectId)?.project;
        if (!project) return;
        if (action === 'edit') { editing = project; await render(); main.querySelector('#projectName')?.focus(); }
        if (action === 'archive') return save({...project, expected_revision: project.revision, revision: undefined, schema_version: undefined, archived: !project.archived});
      });
      return {
        async render() {
          auditGeneration++;
          grants.invalidate();
          productTasks?.invalidate();
          documents?.invalidate();
          deliveries?.invalidate();
          writebacks?.invalidate();
          observations?.invalidate();
          regressions?.invalidate();
          experiments?.invalidate();
          inbox?.invalidate();
          graphs?.invalidate();
          const response = await api('/api/v1/projects');
          rows = response.projects;
          const p = editing || {};
          return `<div class="page-title"><div><h1>项目空间</h1><p>项目、来源与私有输入 · 正式任务、资料引用和交付要求。创建定义不会执行模型。</p></div><div><button data-project-action="audit">控制审计</button><button data-project-action="new">新建项目</button></div></div>
            <p id="projectStatus" role="status">${esc(message)}</p>${pending ? '<button data-project-action="retry">核对 / 重试原命令</button>' : ''}
            <section class="card" style="padding:20px"><h2>${editing ? '编辑项目 r' + editing.revision : '创建项目'}</h2>
              <form id="projectForm"><p><label>项目名称 <input id="projectName" name="name" maxlength="100" required value="${esc(p.name || '')}"></label></p>
                <p><label>项目说明 <textarea name="description" maxlength="2000">${esc(p.description || '')}</textarea></label></p>
                <button class="primary" type="submit">保存项目</button><button type="button" data-project-action="refresh">刷新核对</button></form></section>
            <section class="card" style="padding:20px;margin-top:16px"><h2>全部项目（${response.total}）</h2>${rows.length ? rows.map(row => `<article style="border-top:1px solid var(--line);padding:16px 0"><h3>${esc(row.project.name)} · r${row.project.revision} · ${row.project.archived ? '已归档' : '活动'}</h3><p style="white-space:pre-wrap">${esc(row.project.description)}</p><small>${esc(row.project.project_id)} · ${esc(date(row.created_at))}</small><p><button data-project-action="edit" data-project-id="${row.project.project_id}">编辑</button><button data-project-action="archive" data-project-id="${row.project.project_id}">${row.project.archived ? '取消归档' : '归档'}</button><button data-project-action="audit-project" data-project-id="${row.project.project_id}">项目审计</button><button data-project-action="source-grants" data-project-id="${row.project.project_id}">只读源授权</button><button data-project-action="product-tasks" data-project-id="${row.project.project_id}" ${row.project.archived ? 'disabled' : ''}>正式产品任务</button><button data-project-action="task-graphs" data-project-id="${row.project.project_id}" ${row.project.archived ? 'disabled' : ''}>依赖任务图</button><button data-project-action="task-inbox" data-project-id="${row.project.project_id}" ${row.project.archived ? 'disabled' : ''}>任务收件箱</button><button data-project-action="regression-data" data-project-id="${row.project.project_id}" ${row.project.archived ? 'disabled' : ''}>失败用例与数据集</button><button data-project-action="experiments" data-project-id="${row.project.project_id}">版本实验与证据</button><button data-project-action="observations" data-project-id="${row.project.project_id}" ${row.project.archived ? 'disabled' : ''}>上下文与运行观测</button><button data-project-action="writebacks" data-project-id="${row.project.project_id}" ${row.project.archived ? 'disabled' : ''}>合并与安全写回</button><button data-project-action="deliveries" data-project-id="${row.project.project_id}" ${row.project.archived ? 'disabled' : ''}>交付中心</button><button data-project-action="documents" data-project-id="${row.project.project_id}" ${row.project.archived ? 'disabled' : ''}>资料与交付要求</button></p><details><summary>持久化证据</summary><p>控制日志 seq=${row.source_seq}</p><code>${esc(row.digest)}</code></details></article>`).join('') : '<p>暂无项目。先创建元数据，不会扫描宿主目录。</p>'}</section>${grants.html()}
            <dialog id="auditDialog" aria-labelledby="auditTitle" style="width:min(800px,calc(100vw - 32px));max-height:85vh;padding:20px;border:1px solid var(--line);border-radius:12px;color:var(--text);background:var(--panel,#fff);overflow-wrap:anywhere">
              <h2 id="auditTitle">控制审计 · 只读</h2><button data-audit-action="close" autofocus>关闭审计</button>
              <p id="auditStatus" role="status" aria-live="polite"></p><p id="auditWatermark" style="font-size:12px"></p>
              <nav aria-label="审计分页"><button data-audit-action="previous" disabled>上一页</button><button data-audit-action="next" disabled>下一页</button><button data-audit-action="refresh">刷新水位</button></nav>
              <div id="auditRows"></div></dialog>`;
        }
      };
    }
  };
})(window);
