/* Shared model form. Credentials live only in the input and the save request. */
(() => {
  const probes = window.PyharnessModelProbes ??= new Set();
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const normalizeEndpoint = value => value.trim().replace(/\/+$/, '');
  const defaults = {label:'', model:'', base_url:'', temperature:0.7, max_tokens:4096};
  const unknownResults = new Set(['request_timeout', 'network_unavailable', 'invalid_response', 'model_connection_timeout', 'LLM-301']);
  const reasons = {
    'LLM-302':'模型服务认证失败，请检查该接口的 API Key。',
    'CRED-701':'模型密钥未配置或不可用，请检查密钥配置。',
    'LLM-301':'模型服务超时。', 'LLM-303':'模型服务暂时限流，请稍后再试。',
    'LLM-304':'模型服务拒绝请求或返回内容不符合协议，请检查模型名和接口地址。',
    model_probe_contract_failed:'模型回复不符合连接测试要求。',
    model_probe_budget_exceeded:'模型返回的用量超过测试限制。',
    model_probe_busy:'该连接已有测试正在进行，请稍后查看结果。',
    model_connection_failed:'连接测试失败，请检查接口地址、网络和模型配置。'
  };

  function open({row=null, modal, close, refresh, api, post}) {
    let saved = row ? {...row} : null;
    let snapshot = saved ? JSON.stringify(values(saved)) : null;
    let busy = false;
    const initial = {...defaults, ...row};
    const formId = row ? 'modelEditForm' : 'modelForm';
    const field = (name, label, type='text', attrs='') => `<label class="model-field ${['base_url','api_key'].includes(name)?'model-field-wide':''}" data-section="${['temperature','max_tokens'].includes(name)?'parameters':'connection'}" for="model_${name}"><span>${label}</span><input id="model_${name}" name="${name}" type="${type}" ${attrs} value="${escape(initial[name])}"></label>`;
    modal(row?'修改模型':'添加模型', `<form id="${formId}" class="model-editor-form" novalidate data-page="connection">
      <div class="model-editor-fields" id="modelEditorFields">
        ${field('label','名称','text','required autocomplete="off"')}
        ${field('model','模型名','text','required autocomplete="off"')}
        ${field('base_url','API endpoint','url','required autocomplete="off"')}
        ${field('temperature','温度','number','min="0" max="1.5" step="any" required')}
        ${field('max_tokens','最大输出 Token','number','min="1" max="32768" step="1" required')}
        <label class="model-field model-field-wide" data-section="connection" for="model_api_key"><span class="model-key-label"><span data-key-label>${row?'新 API Key':'API Key'}</span><small data-key-hint>${row?'选填 · 留空保留原密钥':'必填 · 密钥仅写入服务端'}</small></span><input id="model_api_key" name="api_key" type="password" autocomplete="new-password" placeholder="${row?'需要替换密钥时填写':'请输入 API Key'}" ${row?'':'required'}></label>
      </div>
      <div class="model-editor-feedback">
        <div id="modelEndpointNotice" hidden><p>接口地址已变更，请填写新 API Key 或清除原密钥。</p><label class="model-clear-key"><input name="clear_api_key" type="checkbox">清除原密钥，稍后填写</label></div>
        <p class="model-editor-status" role="status" aria-live="polite" hidden></p>
        <p class="model-editor-error" role="alert" hidden></p>
        <div class="model-test-confirm" hidden><span>连接测试可能产生少量模型用量，最多 64 输出 Token、60 秒。</span><div><button type="button" class="primary" id="confirmProceed">开始测试</button><button type="button" id="confirmCancel">暂不测试</button></div></div>
        <div class="model-test-followup" hidden><button type="button" data-retest>再次测试</button><button type="button" data-editor-back>返回模型配置</button></div>
      </div>
      <footer class="model-editor-footer"><p>单独保存不发送模型请求<br><span>新配置用于新会话</span></p><div class="model-editor-actions"><button type="button" data-editor-back>取消</button><button class="primary" type="submit" value="save" data-save>${row?'保存修改':'保存'}</button><button class="model-test-button" type="submit" value="test" data-save-test>保存并测试连接</button></div></footer>
    </form>`, {variant:'model'});
    const form = document.getElementById(formId);
    const fields = form.elements;
    const find = selector => form.querySelector(selector);
    const status = find('[role=status]'), error = find('[role=alert]');
    const confirm = find('.model-test-confirm'), followup = find('.model-test-followup');
    const endpointChanged = () => saved && normalizeEndpoint(fields.base_url.value) !== normalizeEndpoint(saved.base_url);
    function values(source) {
      return {label:source.label.trim(), model:source.model.trim(), base_url:normalizeEndpoint(source.base_url),
        temperature:Number(source.temperature), max_tokens:Number(source.max_tokens)};
    }
    const current = () => values(Object.fromEntries(['label','model','base_url','temperature','max_tokens'].map(name=>[name, fields[name].value])));
    const dirty = () => !saved || JSON.stringify(current()) !== snapshot || !!fields.api_key.value.trim() || fields.clear_api_key.checked;
    function showStatus(text, tone='') {status.textContent=text;status.hidden=!text;status.dataset.tone=tone;}
    function showError(text) {error.textContent=text;error.hidden=!text;}
    function locked(value, phase='') {
      busy=value;
      form.setAttribute('aria-busy', String(value));
      form.querySelectorAll('input, button:not([data-editor-back]):not([role=tab])').forEach(control=>control.disabled=value);
      find('[data-save-test]').textContent=phase==='saving'?'正在保存…':phase==='testing'?'正在测试…':'保存并测试连接';
    }
    function changed() {
      find('#modelEndpointNotice').hidden=!endpointChanged();
      fields.api_key.required=!saved;
      if(!confirm.hidden) {confirm.hidden=true;locked(false);}
      followup.hidden=true;showStatus('');showError('');
    }
    async function back() {
      close();
      if(saved) await refresh();
    }
    function askToTest() {
      if(!form.isConnected)return;
      showError('');showStatus('配置已保存。连接测试可能产生少量模型用量。');
      followup.hidden=true;confirm.hidden=false;
      locked(true);
      find('#confirmProceed').disabled=false;find('#confirmCancel').disabled=false;
      find('#confirmProceed').focus({preventScroll:true});
    }
    async function test() {
      if(!saved||confirm.hidden||probes.has(saved.id))return;
      probes.add(saved.id);
      confirm.hidden=true;locked(true,'testing');showStatus('配置已保存，正在测试…');
      try {
        const result=await post('connections/model:'+encodeURIComponent(saved.id)+'/test');
        if(!form.isConnected)return;
        if(result.status==='available') showStatus('配置已保存，连接测试通过。'+(Number.isFinite(result.latency_ms)?` 耗时 ${Math.round(result.latency_ms)} ms。`:''),'success');
        else if(unknownResults.has(result.code)) showStatus('配置已保存，测试结果未确认：请求超时。未自动重试。','warning');
        else showStatus('配置已保存，连接测试失败：'+(reasons[result.code]||'请检查接口地址、网络和模型配置。'),'failure');
      } catch(cause) {
        if(!form.isConnected)return;
        if(unknownResults.has(cause.code))showStatus('配置已保存，测试结果未确认：'+cause.message+'。未自动重试。','warning');
        else if(cause.status===401)showStatus('配置已保存，测试请求的应用登录校验失败。请重新进入管理员页面或重新登录。','failure');
        else showStatus('配置已保存，连接测试失败：'+(reasons[cause.code]||cause.message),'failure');
      } finally {
        probes.delete(saved.id);
        locked(false);if(form.isConnected)followup.hidden=false;
      }
    }
    async function submit(event) {
      event.preventDefault();if(busy)return;
      showError('');followup.hidden=true;
      if(!form.checkValidity()) {
        const invalid=form.querySelector(':invalid');
        showError(invalid.validationMessage);invalid.focus();return;
      }
      const body=current(), key=fields.api_key.value.trim(), changedEndpoint=endpointChanged();
      if(!body.label||!body.model){showError('名称和模型名不能为空。');return;}
      if(!saved&&!key){showError('请输入 API Key。');fields.api_key.focus();return;}
      if(changedEndpoint&&!key&&!fields.clear_api_key.checked){showError('修改接口地址时，请填写新 API Key 或勾选清除原密钥。');fields.api_key.focus();return;}
      if(key&&fields.clear_api_key.checked){showError('填写新 API Key 与清除原密钥只能选择一项。');return;}
      const wantsTest=event.submitter?.value==='test';
      if(wantsTest&&!dirty()){askToTest();return;}
      if(saved)body.id=saved.id;
      if(key)body.api_key=key;
      else if(changedEndpoint&&fields.clear_api_key.checked)body.clear_api_key=true;
      locked(true,'saving');showStatus('正在保存配置…');
      try {
        const response=await api('/api/settings/models',{method:'POST',body:JSON.stringify(body)});
        saved=response.profile;snapshot=JSON.stringify(values(saved));
        fields.api_key.value='';fields.api_key.required=false;fields.clear_api_key.checked=false;
        if(!form.isConnected)return;
        find('#modelEndpointNotice').hidden=true;
        find('[data-key-label]').textContent='新 API Key';
        find('[data-key-hint]').textContent=saved.has_api_key?'密钥已保存 · 选填 · 留空保留原密钥':'密钥已清除或未配置 · 可填写新密钥';
        fields.api_key.placeholder='需要替换密钥时填写';
        const heading=form.closest('.settings-content')?.querySelector('h1');
        if(heading)heading.textContent='修改模型';
        form.closest('.settings-content')?.setAttribute('aria-label','修改模型');
        find('[data-save]').textContent='保存修改';
        if(wantsTest){locked(false);askToTest();}
        else await back();
      } catch(cause) {
        if(form.isConnected){showStatus('');showError('保存失败：'+cause.message);}
      } finally {
        delete body.api_key;
        if(confirm.hidden)locked(false);
      }
    }
    form.addEventListener('submit',submit);
    form.addEventListener('input',event=>{if(event.target.name==='base_url')fields.clear_api_key.checked=false;changed();});
    form.addEventListener('change',changed);
    find('#confirmProceed').onclick=test;
    find('#confirmCancel').onclick=()=>{confirm.hidden=true;locked(false);showStatus('配置已保存，尚未进行连接测试。');followup.hidden=false;};
    find('[data-retest]').onclick=()=>{if(!busy)submit({preventDefault(){},submitter:{value:'test'}});};
    form.querySelectorAll('[data-editor-back]').forEach(button=>button.onclick=back);
    const pane=form.closest('.settings-content');
    if(pane)pane.querySelector('[data-action="panel-back"]').onclick=event=>{event.stopPropagation();back();};
    const lifecycle=new MutationObserver(()=>{
      if(!form.isConnected){fields.api_key.value='';lifecycle.disconnect();}
    });
    lifecycle.observe(document.body,{childList:true,subtree:true});
  }
  window.PyharnessModelEditor={open};
})();
