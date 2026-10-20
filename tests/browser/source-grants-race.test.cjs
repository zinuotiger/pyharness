/* Real Chrome DOM/production adapter; only the API delay/data are synthetic. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const adapterPath = path.resolve(__dirname, '../../pyharness/ui/host-adapters/source-grants.js');

test('selection edits do not cancel delayed revoke reconciliation', async () => {
  const browser = await chromium.launch({headless: true, channel: 'chrome'});
  try {
    const page = await browser.newPage();
    const errors = [], consoleErrors = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()); });
    await page.setContent('<main></main><button id="trigger">open</button>');
    await page.addScriptTag({path: adapterPath});
    const result = await page.evaluate(async () => {
      const main = document.querySelector('main');
      let revoked = false, reads = 0, release, done;
      const gate = new Promise(resolve => release = resolve);
      const settled = new Promise(resolve => done = resolve);
      const adapter = PyharnessSourceGrants.create({main, esc: String, date: String, api: async (url, opts) => {
        if (opts) { await gate; revoked = true; return {revision: 2}; }
        reads++;
        return {total: 1, grants: [{grant_id: 'sg-0123456789abcdef', revision: revoked ? 2 : 1, revoked,
          expires_at: '2099-01-01', root_identity: 'synthetic', source_seq: 1, digest: 'synthetic'}]};
      }});
      // Trusted adapter-owned shell, not user-provided markup.
      main.innerHTML = adapter.html();
      await adapter.open('p-0123456789abcdef', document.querySelector('#trigger'));
      crypto.randomUUID ||= () => '01234567890123456789012345678901';
      const observer = new MutationObserver(() => {
        if (main.querySelector('#sourceGrantStatus').textContent.includes('已持久确认')) done();
      });
      observer.observe(main, {subtree: true, childList: true});
      main.querySelector('[data-grant-action=revoke]').click();
      const input = main.querySelector('[data-selection-paths]');
      input.value = 'another.txt';
      input.dispatchEvent(new Event('input', {bubbles: true}));
      release();
      await Promise.race([settled, new Promise(resolve => setTimeout(resolve, 500))]);
      observer.disconnect();
      return {revoked, reads, status: main.querySelector('#sourceGrantStatus').textContent,
        staleRevoke: !!main.querySelector('[data-grant-action=revoke]'), title: main.querySelector('h3').textContent};
    });
    assert.equal(result.revoked, true);
    assert.equal(result.reads, 2, JSON.stringify(result));
    assert.equal(result.staleRevoke, false);
    assert.match(result.status, /已持久确认/);
    assert.match(result.title, /r2 · 已撤销/);
    assert.deepEqual(errors, []);
    assert.deepEqual(consoleErrors, []);
    console.log(JSON.stringify({scenario: 'revoke-input', result, errors, consoleErrors}));
  } finally { await browser.close(); }
});

for (const scenario of ['issue', 'revoke-403', 'revoke-503', 'close', 'reopen', 'switch-project',
  'selection-edit', 'selection-edit-error', 'preflight-edit', 'preflight-edit-error']) {
  test(`delayed source grant response: ${scenario}`, async () => {
    const browser = await chromium.launch({headless: true, channel: 'chrome'});
    try {
      const page = await browser.newPage();
      const errors = [], consoleErrors = [];
      page.on('pageerror', error => errors.push(error.message));
      page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()); });
      await page.setContent('<main></main><button id="trigger">open</button>');
      await page.addScriptTag({path: adapterPath});
      await page.evaluate(async scenario => {
        const main = document.querySelector('main');
        const state = window.fixture = {reads: 0, writes: 0, revoked: false, issued: false, finished: false};
        const gate = new Promise(resolve => state.release = resolve);
        const project = 'p-0123456789abcdef', grant = 'sg-0123456789abcdef';
        crypto.randomUUID ||= () => '01234567890123456789012345678901';
        state.adapter = PyharnessSourceGrants.create({main, esc: String, date: String, api: async (url, opts) => {
          if (!opts) {
            state.reads++;
            const row = {grant_id: grant, revision: state.revoked ? 2 : 1, revoked: state.revoked,
              expires_at: '2099-01-01', root_identity: 'synthetic', source_seq: 1, digest: 'synthetic'};
            return {total: state.issued ? 2 : 1, grants: state.issued ? [row, {...row, grant_id: 'sg-1111111111111111'}] : [row]};
          }
          state.writes++;
          await gate;
          state.finished = true;
          if (scenario.includes('403') || scenario.includes('503') || scenario.endsWith('-error')) {
            throw Object.assign(Error('synthetic rejection'), {status: scenario.includes('503') ? 503 : 403});
          }
          if (scenario.startsWith('selection-')) return {schema_version: 1, status: 'metadata_observed', project_id: project,
            grant_id: grant, revision: 1, checked_at: '2026-01-01', items: [{input_index: 0, status: 'accepted', path: 'first.txt', size_bytes: 1, mtime_ns: '1'}],
            summary: {requested: 1, accepted: 1, excluded: 0, total_size_bytes: 1}};
          if (scenario.startsWith('preflight-')) return {status: 'metadata_match', project_id: project, grant_id: grant,
            revision: 1, checked_at: '2026-01-01', source_seq: 1, root_identity: 'synthetic', digest: 'synthetic'};
          state.issued = scenario === 'issue';
          state.revoked = !state.issued;
          return {revision: 2};
        }});
        // Trusted adapter-owned shell; fixture responses contain fixed synthetic strings only.
        main.innerHTML = state.adapter.html();
        await state.adapter.open(project, document.querySelector('#trigger'));
        const input = main.querySelector('[data-selection-paths]');
        input.value = 'first.txt';
        if (scenario === 'issue') {
          main.querySelector('[name=source_path]').value = 'C:/synthetic-source';
          main.querySelector('[name=expires_at]').value = '2099-01-01T00:00';
          main.querySelector('form').requestSubmit();
        } else {
          const action = scenario.startsWith('selection-') ? 'selection' : scenario.startsWith('preflight-') ? 'preflight' : 'revoke';
          main.querySelector(`[data-grant-action=${action}]`).click();
        }
        input.value = 'second.txt';
        input.dispatchEvent(new Event('input', {bubbles: true}));
        if (['close', 'reopen', 'switch-project'].includes(scenario)) main.querySelector('[data-grant-action=close]').click();
        if (scenario === 'reopen') await state.adapter.open(project, document.querySelector('#trigger'));
        state.before = main.querySelector('#sourceGrantStatus').textContent;
        state.release();
      }, scenario);
      await page.waitForFunction(() => window.fixture.finished);
      await page.evaluate(() => new Promise(resolve => setTimeout(resolve, 0)));
      if (scenario === 'switch-project') await page.evaluate(() => fixture.adapter.open('p-1111111111111111', document.querySelector('#trigger')));
      const result = await page.evaluate(() => ({reads: fixture.reads, writes: fixture.writes, before: fixture.before,
        open: document.querySelector('dialog').open, status: document.querySelector('#sourceGrantStatus').textContent,
        rows: document.querySelectorAll('article').length, text: document.querySelector('#sourceGrantRows').textContent,
        observation: Array.from(document.querySelectorAll('[data-selection-result],[data-preflight-result]'), p => p.textContent).join(''),
        disabled: Array.from(document.querySelectorAll('button')).some(b => b.disabled)}));
      assert.equal(result.writes, 1);
      assert.equal(result.disabled, false);
      if (scenario === 'issue') {
        assert.equal(result.reads, 2);
        assert.equal(result.rows, 2);
        assert.match(result.status, /已持久确认/);
      } else if (scenario.startsWith('revoke-')) {
        assert.equal(result.reads, 1);
        assert.match(result.text, /r1 · 有效/);
        assert.match(result.status, scenario.endsWith('403') ? /无权签发或撤销/ : /未确认/);
        assert.doesNotMatch(result.status, /正在持久提交|已持久确认/);
      } else if (scenario === 'close') {
        assert.equal(result.open, false);
        assert.equal(result.reads, 1);
        assert.equal(result.status, result.before);
      } else if (scenario === 'reopen') {
        assert.equal(result.open, true);
        assert.equal(result.reads, 2);
        assert.equal(result.status, result.before);
        await page.click('[data-grant-action=refresh]');
        await page.waitForFunction(() => document.querySelector('h3').textContent.includes('已撤销'));
      } else if (scenario === 'switch-project') {
        assert.equal(result.reads, 2);
        assert.doesNotMatch(result.status, /正在持久提交|已持久确认/);
      } else {
        assert.equal(result.observation, '');
        assert.equal(result.reads, 1);
      }
      assert.deepEqual(errors, []);
      assert.deepEqual(consoleErrors, []);
      console.log(JSON.stringify({scenario, result, errors, consoleErrors}));
      if (process.env.SOURCE_GRANT_SCREENSHOT && scenario === 'issue') await page.screenshot({path: process.env.SOURCE_GRANT_SCREENSHOT});
    } finally { await browser.close(); }
  });
}
