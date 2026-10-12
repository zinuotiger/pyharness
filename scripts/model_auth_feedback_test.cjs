const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {test} = require('node:test');

const html = fs.readFileSync(require('node:path').join(__dirname, '../pyharness/ui/platform.html'), 'utf8');
const source = html.slice(html.indexOf('function authFailureMessage('), html.indexOf('\nfunction feedbackFor('));

test('application authentication failure explains recovery without blaming model credentials', async () => {
  const context = vm.createContext({AbortController, setTimeout, clearTimeout, fetch: async () => ({
    status:401, ok:false, json:async () => ({code:'CRED-701',detail:{reason:'api-token'},advice:'raw token implementation detail'})
  })});
  vm.runInContext(source, context);
  await assert.rejects(vm.runInContext("api('/api/settings/models',{method:'POST'})", context), e => {
    assert.equal(e.code, 'CRED-701');
    assert.equal(e.status, 401);
    assert.match(e.message, /本地启动入口/);
    assert.match(e.message, /尚未校验模型 API Key/);
    assert.doesNotMatch(e.message, /raw token/);
    return true;
  });
});

// Draft retention and retry are exercised in platform_model_editor_contract.py.
