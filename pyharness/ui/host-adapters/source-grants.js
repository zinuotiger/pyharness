/* Explicit grants/metadata and confirmed bounded text copies; never scans or executes source. */
(function (global) {
  'use strict';
  global.PyharnessSourceGrants = {
    create({main, api, esc, date}) {
      const contexts = global.PyharnessContextManifests?.create({main, api});
      const gitSource = global.PyharnessGitSource?.create({main, api});
      const workspaceInputs = global.PyharnessWorkspaceInputs?.create({main, api});
      let project = null, trigger = null, generation = 0, observationGeneration = 0, dialogGeneration = 0, busy = false, pending = null;
      const dialog = () => main.querySelector('#sourceGrantDialog');
      const status = text => { const el = dialog()?.querySelector('#sourceGrantStatus'); if (el) el.textContent = text; };
      const live = (el, request) => request === generation && el?.isConnected && el.open;
      const commandLive = (el, lifecycle) => lifecycle === dialogGeneration && el?.isConnected && el.open;
      const key = () => crypto.randomUUID().replaceAll('-', '');
      const snapshotPreviews = new WeakMap();
      const snapshotFields = ['snapshot_id','project_id','grant_id','revision','kind','policy','capture_stability','total_size_bytes','confirmation_digest','file_count','digest','source_seq'];
      const quotaMessage = '输入或限额检查未通过（422）：须 1–200 个文件，每项 1–1024 字符，单文件及累计最多 10 MiB，每授权最多 200 个历史快照；不会自动缩减选择。';
      function validSnapshot(s, projectId, grantId) {
        return s && /^ss-[0-9a-f]{16}$/.test(s.snapshot_id) && s.project_id === projectId && s.grant_id === grantId
          && s.revision === 1 && s.kind === 'explicit_selection' && s.policy === 'reject-secrets-utf8-v1'
          && s.capture_stability === 'stable_observed' && /^[0-9a-f]{64}$/.test(s.confirmation_digest)
          && /^[0-9a-f]{64}$/.test(s.digest) && Number.isSafeInteger(s.source_seq) && s.source_seq > 0
          && Number.isSafeInteger(s.file_count) && s.file_count >= 1 && s.file_count <= 200
          && Number.isSafeInteger(s.total_size_bytes) && s.total_size_bytes >= 0 && s.total_size_bytes <= 10485760;
      }
      function validSnapshotList(data, projectId, grantId) {
        return data && Array.isArray(data.snapshots) && data.total === data.snapshots.length && data.total <= 200
          && new Set(data.snapshots.map(s => s?.snapshot_id)).size === data.total
          && data.snapshots.every(s => validSnapshot(s, projectId, grantId));
      }
      function invalidateObservations() {
        contexts?.invalidate();
        gitSource?.invalidate();
        workspaceInputs?.invalidate();
        // Observation lifetime is independent of a durable command's key/readback.
        observationGeneration++;
        const el = dialog();
        el?.querySelectorAll('[data-preflight-result],[data-selection-result],[data-snapshot-result]').forEach(p => p.textContent = '');
        el?.querySelectorAll('[data-context-actions]').forEach(p => p.remove());
        el?.querySelectorAll('[data-grant-action=preflight],[data-grant-action=selection],[data-grant-action=snapshot-preview],[data-grant-action=snapshot-list]').forEach(b => b.disabled = false);
        el?.querySelectorAll('article').forEach(article => {
          snapshotPreviews.delete(article);
          article.querySelector('[data-snapshot-confirm]').checked = false;
        });
      }
      function close() {
        invalidateObservations();
        generation++;
        dialogGeneration++;
        const el = dialog();
        if (el) { el.querySelector('form').reset(); el.querySelectorAll('[data-selection-paths]').forEach(p => p.value = ''); el.close(); }
        trigger?.focus();
      }
      async function load() {
        invalidateObservations();
        const el = dialog(), request = ++generation;
        status('加载授权摘要中…');
        el.querySelector('#sourceGrantRows').replaceChildren();
        try {
          const data = await api(`/api/v1/projects/${project}/source-grants`);
          if (!live(el, request)) return;
          status(`授权记录 ${data.total} 条${data.total ? '' : ' · 暂无授权'}。仅摘要；未扫描或读取源正文。`);
          // All text/attribute strings are escaped; revisions/seq are strict server integers.
          el.querySelector('#sourceGrantRows').innerHTML = data.grants.map(g => {
            const expired = new Date(g.expires_at).getTime() <= Date.now();
            return `<article data-grant-id="${esc(g.grant_id)}" style="border-top:1px solid var(--line);padding:12px 0;overflow-wrap:anywhere"><h3>${esc(g.grant_id)} · r${g.revision} · ${g.revoked ? '已撤销' : expired ? '已过期' : '有效'}</h3><p>动作：read · 到期：${esc(date(g.expires_at))}</p><p>根身份：<code>${esc(g.root_identity)}</code></p><p>控制 seq=${g.source_seq} · digest=<code>${esc(g.digest)}</code></p>${g.revoked ? '' : `<button data-grant-action="preflight" data-grant-id="${esc(g.grant_id)}" data-revision="${g.revision}">检查目录身份</button> <button data-grant-action="revoke" data-grant-id="${esc(g.grant_id)}" data-revision="${g.revision}">撤销只读授权</button><p><label>显式相对文件名（每行一个，1–200 项）<textarea data-selection-paths maxlength="205000" rows="4" autocomplete="off" spellcheck="false" style="width:100%;box-sizing:border-box"></textarea></label></p><button data-grant-action="selection" data-grant-id="${esc(g.grant_id)}" data-revision="${g.revision}">只读预览所选文件 metadata</button>`}<p data-preflight-result role="status" aria-live="polite"></p><pre data-selection-result role="status" aria-live="polite" style="white-space:pre-wrap;overflow-wrap:anywhere"></pre></article>`;
          }).join('');
          el.querySelectorAll('article').forEach(article => {
            const section = document.createElement('section');
            // Adapter-owned static controls only; server/user values never enter this HTML.
            section.innerHTML = `<p>选择性私有快照：只读所选 UTF-8 文本，不扫描/Git/执行/回写。检测到凭据名、已知秘密或二进制即整次拒绝；不是完整秘密检测，不能选择客户材料或未经授权数据。</p><button data-grant-action="snapshot-preview">安全读取并预览快照摘要</button> <button data-grant-action="snapshot-list">读取已有快照摘要</button><p><label><input type="checkbox" data-snapshot-confirm>我确认所选材料有权复制且不含秘密；只创建选择性副本，不是完整 dirty 工作区或 OS 快照。</label></p><button data-grant-action="snapshot-capture">确认创建私有快照</button><pre data-snapshot-result role="status" aria-live="polite" style="white-space:pre-wrap;overflow-wrap:anywhere"></pre>`;
            const limits = document.createElement('p');
            limits.textContent = '单文件及累计最多 10 MiB，每授权最多 200 个历史快照。stable_observed 仅表示观察稳定，不保证 OS 级原子快照。';
            section.prepend(limits);
            article.append(section);
            if (gitSource && article.querySelector('[data-revision]')) {
              const gitButton = document.createElement('button');
              gitButton.dataset.grantAction = 'git-preview';
              gitButton.textContent = '预览所选 Git 版本（只读）';
              const grantRow = data.grants.find(g => g.grant_id === article.dataset.grantId);
              gitButton.dataset.sourceSeq = String(grantRow.source_seq);
              section.append(gitButton);
              if (workspaceInputs) {
                const persist = document.createElement('button');
                persist.dataset.grantAction = 'workspace-inputs';
                persist.dataset.sourceSeq = String(grantRow.source_seq);
                persist.textContent = '持久 Git 输入与上下文清单';
                section.append(persist);
              }
            }
          });
          if (pending) status('上一命令结果未确认。请核对摘要，或使用同一命令键重试；不会自动重发。');
        } catch (error) {
          if (live(el, request)) status(error.status === 403 ? '无权查看此项目授权，请联系管理员。' : '授权摘要读取失败；未启动执行。' + error.message);
        }
      }
      async function preflight(button) {
        if (busy) return;
        invalidateObservations();
        const el = dialog(), request = ++generation;
        const observation = observationGeneration;
        el.querySelectorAll('[data-preflight-result],[data-selection-result]').forEach(p => p.textContent = '');
        const result = button.closest('article').querySelector('[data-preflight-result]');
        result.textContent = '正在检查目录身份 metadata…';
        button.disabled = true;
        try {
          const data = await api(`/api/v1/projects/${project}/source-grants/${button.dataset.grantId}/preflight`,
            {method: 'POST', body: JSON.stringify({schema_version: 1, expected_revision: Number(button.dataset.revision)})});
          if (!live(el, request) || observation !== observationGeneration) return;
          if (data.status !== 'metadata_match' || data.project_id !== project || data.grant_id !== button.dataset.grantId || data.revision !== Number(button.dataset.revision)) {
            throw Error('invalid preflight response');
          }
          result.textContent = `metadata_match · 本次目录身份匹配 · ${date(data.checked_at)} · r${data.revision} · seq=${data.source_seq} · 根身份 ${data.root_identity} · digest ${data.digest}。仅本次 metadata 观察，不是执行或快照授权；实际访问仍须重验。`;
        } catch (error) {
          if (!live(el, request) || observation !== observationGeneration) return;
          result.textContent = error.status === 409 ? '版本冲突，未通过检查；请刷新授权摘要后重试。'
            : error.status === 401 ? '身份已失效，未通过检查；请重新登录。'
            : error.status === 403 ? '检查被拒绝：授权过期、撤销、权限不足或目录身份不可用。未通过检查。'
            : error.status === 503 ? '存储或服务不可用，未通过检查；没有修改授权。'
            : '目录身份检查失败或结果未确认；未通过检查，没有自动重试。';
        } finally {
          if (live(el, request) && observation === observationGeneration) button.disabled = false;
        }
      }
      async function selection(button) {
        if (busy) return;
        invalidateObservations();
        const el = dialog(), request = ++generation, article = button.closest('article');
        const observation = observationGeneration;
        el.querySelectorAll('[data-preflight-result],[data-selection-result]').forEach(p => p.textContent = '');
        const result = article.querySelector('[data-selection-result]');
        const paths = article.querySelector('[data-selection-paths]').value.split(/\r?\n/);
        if (paths.length < 1 || paths.length > 200 || paths.some(p => !p || p.length > 1024)) {
          result.textContent = '请输入 1–200 个相对文件名，每行一个，每项 1–1024 字符；不自动修剪或修复路径。'; return;
        }
        result.textContent = '正在预览所选文件 metadata…';
        button.disabled = true;
        try {
          const data = await api(`/api/v1/projects/${project}/source-grants/${button.dataset.grantId}/selection-previews`,
            {method: 'POST', body: JSON.stringify({schema_version: 1, expected_revision: Number(button.dataset.revision), paths})});
          if (!live(el, request) || observation !== observationGeneration) return;
          if (data.status !== 'metadata_observed' || data.schema_version !== 1 || data.project_id !== project || data.grant_id !== button.dataset.grantId || data.revision !== Number(button.dataset.revision)
              || !Array.isArray(data.items) || data.items.length !== paths.length || data.summary?.requested !== paths.length
              || data.summary.accepted + data.summary.excluded !== paths.length) throw Error('invalid selection response');
          result.textContent = `metadata_observed · ${date(data.checked_at)} · 所选 ${data.summary.requested} · 接受 ${data.summary.accepted} · 排除 ${data.summary.excluded} · 总字节 ${data.summary.total_size_bytes}\n`
            + data.items.map(i => i.status === 'accepted' ? `${i.input_index + 1}. ${i.path} · ${i.size_bytes} 字节 · mtime_ns=${i.mtime_ns}` : `${i.input_index + 1}. 排除 · ${i.reason}`).join('\n')
            + '\n仅本次 metadata 观察；未读取正文、未枚举目录、未创建快照，不是执行或快照授权。实际访问仍须重验。';
        } catch (error) {
          if (!live(el, request) || observation !== observationGeneration) return;
          result.textContent = error.status === 409 ? '版本冲突；请刷新授权后重试。未完成预览。'
            : error.status === 401 ? '身份已失效；请重新登录。未完成预览。'
            : error.status === 403 ? '预览被拒绝：授权过期、撤销、权限不足或根身份变化。'
            : error.status === 503 ? '存储或服务不可用；未完成预览。'
            : '预览失败或结果未确认；没有自动重试。';
        } finally { if (live(el, request) && observation === observationGeneration) button.disabled = false; }
      }
      main.addEventListener('input', event => {
        if (!event.target.matches('[data-selection-paths]')) return;
        // Editing a selection invalidates observations, never a durable command's readback.
        invalidateObservations();
      });
      async function snapshotObserve(button, listing = false) {
        if (busy) return;
        invalidateObservations();
        const el = dialog(), request = ++generation, observation = observationGeneration;
        const article = button.closest('article'), result = article.querySelector('[data-snapshot-result]');
        const grant = article.dataset.grantId, revision = Number(article.querySelector('[data-revision]')?.dataset.revision);
        snapshotPreviews.delete(article); article.querySelector('[data-snapshot-confirm]').checked = false;
        const paths = article.querySelector('[data-selection-paths]')?.value.split(/\r?\n/);
        if (!listing && (!paths || paths.length > 200 || paths.some(p => !p || p.length > 1024))) {
          result.textContent = '请输入 1–200 个显式相对文件名；不自动修复路径。'; return;
        }
        result.textContent = listing ? '正在核对已有快照摘要…' : '正在安全读取所选文本并检查稳定性…';
        button.disabled = true;
        try {
          const url = `/api/v1/projects/${project}/source-grants/${grant}/`;
          const data = await api(url + (listing ? 'snapshots' : 'snapshot-previews'), listing ? undefined :
            {method:'POST',body:JSON.stringify({schema_version:1,expected_revision:revision,paths})});
          if (!live(el, request) || observation !== observationGeneration) return;
          if (listing) {
            if (!validSnapshotList(data, project, grant)) throw Error('invalid snapshots');
            result.textContent = `已有选择性快照 ${data.total} 个\n` + data.snapshots.map(s => `${s.snapshot_id} · ${s.file_count} 文件 · ${s.total_size_bytes} 字节 · ${s.capture_stability} · seq=${s.source_seq} · digest=${s.digest}`).join('\n');
            const actions = document.createElement('div');
            actions.dataset.contextActions = '';
            data.snapshots.forEach(snapshot => {
              const action = document.createElement('button');
              action.dataset.grantAction = 'context-preview';
              action.dataset.snapshotId = snapshot.snapshot_id;
              action.dataset.snapshotDigest = snapshot.digest;
              action.dataset.fileCount = String(snapshot.file_count);
              action.dataset.totalSize = String(snapshot.total_size_bytes);
              action.dataset.sourceSeq = String(snapshot.source_seq);
              action.textContent = `预览快照上下文清单 · ${snapshot.snapshot_id}`;
              actions.append(action);
              if (contexts) {
                const persist = action.cloneNode(true);
                persist.dataset.grantAction = 'context-manifest';
                persist.textContent = `持久绑定快照上下文 · ${snapshot.snapshot_id}`;
                actions.append(persist);
              }
            });
            result.after(actions);
          } else {
            if (data.schema_version !== 1 || data.status !== 'confirmation_required' || data.project_id !== project || data.grant_id !== grant || data.revision !== revision || data.policy !== 'reject-secrets-utf8-v1'
                || !/^[0-9a-f]{64}$/.test(data.confirmation_digest) || data.file_count !== paths.length
                || !Number.isInteger(data.total_size_bytes) || data.total_size_bytes < 0 || data.total_size_bytes > 10485760) throw Error('invalid snapshot preview');
            snapshotPreviews.set(article, {paths, revision, digest:data.confirmation_digest, total_size_bytes:data.total_size_bytes});
            result.textContent = `confirmation_required · ${data.file_count} 文件 · ${data.total_size_bytes} 字节 · 确认摘要 ${data.confirmation_digest}\n尚未创建快照。确认后重新读取并核验；任何源变化拒绝，不自动重试。`;
          }
        } catch (error) {
          if (!live(el, request) || observation !== observationGeneration) return;
          result.textContent = error.status === 409 ? '快照版本或源稳定性冲突；重新预览，不自动重试。'
            : error.status === 401 ? '身份失效；未完成快照核对。'
            : error.status === 403 ? '快照被拒绝：权限、授权、根身份、路径或内容安全检查未通过。'
            : error.status === 422 ? quotaMessage
            : error.status === 503 ? '快照存储或服务不可用；未确认创建。' : '快照结果未确认；没有自动重试。';
        } finally { if (live(el, request) && observation === observationGeneration) button.disabled = false; }
      }
      async function snapshotContext(button) {
        if (busy) return;
        const article = button.closest('article'), selected = {...button.dataset};
        const grant = article.dataset.grantId, revision = Number(article.querySelector('[data-revision]')?.dataset.revision);
        invalidateObservations();
        const el = dialog(), request = ++generation, observation = observationGeneration;
        const result = article.querySelector('[data-snapshot-result]');
        result.textContent = '正在核对已提交快照的上下文清单…';
        try {
          const data = await api(`/api/v1/projects/${project}/source-grants/${grant}/snapshot-context-previews`,
            {method:'POST',body:JSON.stringify({schema_version:1,expected_revision:revision,
              snapshot_id:selected.snapshotId,snapshot_digest:selected.snapshotDigest})});
          if (!live(el, request) || observation !== observationGeneration) return;
          if (data.schema_version !== 1 || data.status !== 'context_observed' || data.project_id !== project
              || data.grant_id !== grant || data.grant_revision !== revision || data.snapshot_revision !== 1
              || data.snapshot_id !== selected.snapshotId || data.snapshot_digest !== selected.snapshotDigest
              || data.policy !== 'reject-secrets-utf8-v1' || !/^[0-9a-f]{64}$/.test(data.context_digest)
              || !Number.isSafeInteger(data.source_seq) || data.source_seq !== Number(selected.sourceSeq)
              || data.file_count !== Number(selected.fileCount) || data.total_size_bytes !== Number(selected.totalSize)
              || !Array.isArray(data.items) || data.items.length !== data.file_count || data.items.length < 1 || data.items.length > 200
              || data.items.some((item, index) => item.input_index !== index || typeof item.path !== 'string'
                || !item.path.length || item.path.length > 1024 || !Number.isSafeInteger(item.size_bytes)
                || item.size_bytes < 0 || item.size_bytes > 10485760 || !/^[0-9a-f]{64}$/.test(item.content_digest))
              || data.items.reduce((sum, item) => sum + item.size_bytes, 0) !== data.total_size_bytes) throw Error('invalid context preview');
          result.textContent = `context_observed · ${data.file_count} 文件 · ${data.total_size_bytes} 字节\n快照 ${data.snapshot_id} · seq=${data.source_seq}\n清单摘要 ${data.context_digest}\n`
            + data.items.map(item => `${item.input_index + 1}. ${item.path} · ${item.size_bytes} 字节 · digest=${item.content_digest}`).join('\n')
            + '\n仅已提交快照的版本清单，未持久绑定任务，未执行或读取宿主正文；不是实际模型上下文或新的访问授权。';
        } catch (error) {
          if (!live(el, request) || observation !== observationGeneration) return;
          result.textContent = error.status === 401 ? '身份失效；未完成上下文清单预览。'
            : error.status === 403 ? '上下文清单被拒绝：权限、授权或快照绑定不可用。'
            : error.status === 409 ? '上下文版本或摘要冲突；请刷新后重新选择。'
            : error.status === 503 ? '快照存储或服务不可用；未完成上下文清单预览。'
            : '上下文清单结果未确认；没有自动重试。';
        }
      }
      function snapshotCapture(button) {
        const article = button.closest('article'), preview = snapshotPreviews.get(article);
        const paths = article.querySelector('[data-selection-paths]')?.value.split(/\r?\n/);
        if (!preview || !article.querySelector('[data-snapshot-confirm]').checked || JSON.stringify(paths) !== JSON.stringify(preview.paths)) {
          article.querySelector('[data-snapshot-result]').textContent = '请先安全预览所选文件，再勾选明确确认；未创建快照。'; return;
        }
        send({url:`/api/v1/projects/${project}/source-grants/${article.dataset.grantId}/snapshots`,
          expectedSummary:{file_count:preview.paths.length,total_size_bytes:preview.total_size_bytes},
          body:JSON.stringify({schema_version:1,expected_revision:preview.revision,paths:preview.paths,
            snapshot_id:'ss-'+key().slice(0,16),confirmation_digest:preview.digest,policy:'reject-secrets-utf8-v1'})});
      }
      async function send(command) {
        if (busy) return;
        if (pending && (pending.url !== command.url || pending.body !== command.body)) {
          status('上一命令尚未确认，请先核对或重试原命令。'); return;
        }
        pending ??= {...command, key: 'source-grant-' + key()};
        invalidateObservations();
        // Refresh replaces observations within this dialog, not a durable command's reconciliation.
        generation++;
        const sent = pending, el = dialog(), lifecycle = dialogGeneration;
        el.querySelectorAll('[data-preflight-result],[data-selection-result]').forEach(p => p.textContent = '');
        busy = true;
        el.querySelectorAll('form button,[data-grant-action=revoke],[data-grant-action=retry]').forEach(b => b.disabled = true);
        status(sent.url.endsWith('/snapshots') ? '正在持久提交快照命令并核对摘要…' : '正在持久提交授权命令…');
        let postAccepted = Boolean(sent.unconfirmed);
        try {
          const response = await api(sent.url, {method: 'POST', body: sent.body, headers: {'Idempotency-Key': sent.key}});
          postAccepted = true;
          const snapshot = sent.url.endsWith('/snapshots');
          if (snapshot) {
            const command = JSON.parse(sent.body), ids = sent.url.match(/\/projects\/(p-[0-9a-f]{16})\/source-grants\/(sg-[0-9a-f]{16})\/snapshots$/);
            if (!ids || !validSnapshot(response, ids[1], ids[2]) || response.snapshot_id !== command.snapshot_id
                || response.confirmation_digest !== command.confirmation_digest || response.file_count !== command.paths.length
                || response.total_size_bytes !== sent.expectedSummary.total_size_bytes) throw Error('invalid snapshot response');
            const readback = await api(sent.url);
            if (!validSnapshotList(readback, ids[1], ids[2])) throw Error('invalid snapshot readback');
            const exact = readback.snapshots?.find(s => s.snapshot_id === command.snapshot_id);
            if (!exact || !snapshotFields.every(field => exact[field] === response[field])) throw Error('snapshot readback unconfirmed');
          }
          pending = null;
          if (commandLive(el, lifecycle)) {
            el.querySelector('form').reset();
            const reconciliation = generation + 1;
            await load();
            if (live(el, reconciliation)) status(snapshot ? `快照已持久确认并精确读回 · ${response.snapshot_id} · ${response.file_count} 文件 · ${response.total_size_bytes} 字节 · digest=${response.digest}。仅选择性副本；未执行或回写。` : '授权命令已持久确认。仅控制元数据；未读取文件或创建快照。');
          }
        } catch (error) {
          // An accepted POST followed by a denied GET does not prove that nothing committed.
          if (!postAccepted && error.status && error.status < 500) pending = null;
          else sent.unconfirmed = true;
          if (commandLive(el, lifecycle)) status(sent.url.endsWith('/snapshots') ? (postAccepted ? '快照提交结果或精确读回未确认；保留原命令，可恢复权限后核对摘要或同键重试，没有自动重发。'
            : error.status === 401 ? '身份已失效；快照未确认创建，请重新登录。'
            : error.status === 409 ? '快照版本、源变化或幂等冲突；未确认创建，请重新预览。'
            : error.status === 403 ? '快照权限、授权或安全策略拒绝；未创建或无法核对。'
            : error.status === 422 ? quotaMessage
            : error.status === 503 ? '快照存储或服务不可用；结果未确认，保留原命令供核对或同键重试。'
            : '快照结果未确认；可核对摘要或同键重试原命令，没有自动重发。') : error.status === 409 ? '版本或幂等冲突，未覆盖。请刷新核对。' : error.status === 403 ? '无权签发或撤销授权；仅本地操作者或团队管理员可管理。' : '授权命令未确认。' + error.message + '；请核对或重试原命令。');
        } finally {
          busy = false;
          if (el?.isConnected) el.querySelectorAll('button').forEach(b => b.disabled = false);
        }
      }
      main.addEventListener('submit', event => {
        if (event.target.id !== 'sourceGrantForm') return;
        event.preventDefault();
        const values = new FormData(event.target), expiry = new Date(String(values.get('expires_at')));
        if (!Number.isFinite(expiry.getTime()) || expiry.getTime() <= Date.now()) { status('请选择未来的到期时间。'); return; }
        const body = {schema_version: 1, grant_id: 'sg-' + key().slice(0, 16), expected_revision: 0,
          source_path: String(values.get('source_path')), actions: ['read'], expires_at: expiry.toISOString()};
        send({url: `/api/v1/projects/${project}/source-grants`, body: JSON.stringify(body)});
      });
      main.addEventListener('click', event => {
        const button = event.target.closest('[data-grant-action]');
        if (!button) return;
        const action = button.dataset.grantAction;
        if (action === 'close') return close();
        if (action === 'refresh') return load();
        if (action === 'retry' && pending) return send(pending);
        if (action === 'preflight') return preflight(button);
        if (action === 'selection') return selection(button);
        if (action === 'snapshot-preview') return snapshotObserve(button);
        if (action === 'snapshot-list') return snapshotObserve(button, true);
        if (action === 'context-preview') return snapshotContext(button);
        if (action === 'git-preview' || action === 'workspace-inputs') {
          if (busy) return;
          const article = button.closest('article');
          const binding = {project, grantId: article.dataset.grantId,
            grantRevision: Number(article.querySelector('[data-revision]').dataset.revision), sourceSeq: Number(button.dataset.sourceSeq)};
          const paths = article.querySelector('[data-selection-paths]').value;
          invalidateObservations();
          return (action === 'workspace-inputs' ? workspaceInputs : gitSource).open(binding, paths, button);
        }
        if (action === 'context-manifest') {
          const article = button.closest('article');
          return contexts.open({project, grant: article.dataset.grantId,
            grantRevision: Number(article.querySelector('[data-revision]').dataset.revision),
            snapshotId: button.dataset.snapshotId, snapshotDigest: button.dataset.snapshotDigest,
            fileCount: Number(button.dataset.fileCount), totalSize: Number(button.dataset.totalSize), sourceSeq: Number(button.dataset.sourceSeq)}, button);
        }
        if (action === 'snapshot-capture') return snapshotCapture(button);
        if (action === 'revoke') return send({url: `/api/v1/source-grants/${button.dataset.grantId}/revoke`,
          body: JSON.stringify({expected_revision: Number(button.dataset.revision)})});
      });
      return {
        invalidate() { invalidateObservations(); generation++; dialogGeneration++; },
        async open(id, button) {
          if (pending && project !== id) { button.textContent = '请先核对上一项目授权命令'; return; }
          dialogGeneration++;
          project = id; trigger = button;
          const el = dialog();
          el.showModal();
          el.oncancel = event => { event.preventDefault(); close(); };
          await load();
        },
        html() {
          return `<dialog id="sourceGrantDialog" aria-labelledby="sourceGrantTitle" style="box-sizing:border-box;width:min(800px,calc(100vw - 32px));max-height:85vh;padding:20px;border:1px solid var(--line);border-radius:12px;background:var(--card);color:var(--ink);overflow-wrap:anywhere">
            <h2 id="sourceGrantTitle">源目录只读授权</h2><button data-grant-action="close" autofocus>关闭授权</button>
            <p>显式签发 read 授权；不会扫描、导入、读取文件、提交任务或回写。源路径仅本次输入，摘要不显示私有根。团队成员/只读成员不能自行扩大目录。</p>
            <p id="sourceGrantStatus" role="status" aria-live="polite"></p>
            <form id="sourceGrantForm"><p><label>源目录绝对路径 <input name="source_path" maxlength="1024" required autocomplete="off" style="width:100%"></label></p>
              <p><label>到期时间（本地时间，提交时转 UTC） <input name="expires_at" type="datetime-local" required></label></p>
              <button type="submit" class="primary">显式签发只读授权</button></form>
            <p><button data-grant-action="refresh">刷新授权摘要</button><button data-grant-action="retry">核对 / 重试原授权命令</button></p>
            <div id="sourceGrantRows"></div></dialog>`;
        }
      };
    }
  };
})(window);
