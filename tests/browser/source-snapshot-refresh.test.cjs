/* Real Chrome + production adapter; all API responses and gates are synthetic. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const adapter = path.resolve(process.cwd(), 'pyharness/ui/host-adapters/source-grants.js');

for (const stage of ['post', 'readback']) for (const outcome of ['success', 'unknown']) {
  test(`refresh during capture ${stage} preserves ${outcome} reconciliation`, async () => {
    const browser = await chromium.launch({headless: true, channel: 'chrome'});
    try {
      const page = await browser.newPage(), errors = [];
      page.setDefaultTimeout(5000);
      page.on('pageerror', e => errors.push(e.message));
      await page.setContent('<main></main><button id="trigger">open</button>');
      await page.addScriptTag({path: adapter});
      await page.evaluate(async ({stage, outcome}) => {
        crypto.randomUUID ||= () => '01234567890123456789012345678901';
        const project = 'p-0123456789abcdef', grant = 'sg-0123456789abcdef';
        window.f = {posts: [], gets: 0, gateReached: false, finished: false};
        f.gate = new Promise(resolve => f.release = resolve);
        const summary = {snapshot_id: 'ss-0123456789abcdef', project_id: project, grant_id: grant,
          revision: 1, kind: 'explicit_selection', policy: 'reject-secrets-utf8-v1',
          capture_stability: 'stable_observed', confirmation_digest: 'a'.repeat(64),
          digest: 'b'.repeat(64), source_seq: 4, file_count: 1, total_size_bytes: 11};
        window.a = PyharnessSourceGrants.create({main: document.querySelector('main'), esc: String, date: String,
          api: async (url, opts) => {
            if (url.endsWith('/snapshot-previews')) return {schema_version: 1, project_id: project,
              grant_id: grant, revision: 1, status: 'confirmation_required', policy: summary.policy,
              confirmation_digest: summary.confirmation_digest, file_count: 1, total_size_bytes: 11};
            if (url.endsWith('/snapshots')) {
              if (opts) {
                f.posts.push({body: opts.body, key: opts.headers['Idempotency-Key']});
                summary.snapshot_id = JSON.parse(opts.body).snapshot_id;
                if (stage === 'post' && f.posts.length === 1) {f.gateReached = true; await f.gate;}
                return {...summary};
              }
              f.gets++;
              if (stage === 'readback' && f.gets === 1) {f.gateReached = true; await f.gate;}
              if (outcome === 'unknown' && f.gets === 1) {
                f.finished = true;
                throw Object.assign(Error('synthetic readback unavailable'), {status: 503});
              }
              f.finished = true;
              return {snapshots: [{...summary}], total: 1};
            }
            return {total: 1, grants: [{grant_id: grant, revision: 1, revoked: false,
              expires_at: '2099-01-01', root_identity: 'synthetic', source_seq: 3, digest: 'synthetic'}]};
          }});
        // Trusted adapter-owned static shell only.
        document.querySelector('main').innerHTML = a.html();
        await a.open(project, document.querySelector('#trigger'));
      }, {stage, outcome});
      await page.locator('[data-selection-paths]').fill('synthetic.txt');
      await page.locator('[data-grant-action=snapshot-preview]').click();
      await page.waitForFunction(() => document.querySelector('[data-snapshot-result]').textContent.includes('confirmation_required'));
      await page.locator('[data-snapshot-confirm]').check();
      await page.locator('[data-grant-action=snapshot-capture]').click();
      await page.waitForFunction(() => f.gateReached);
      await page.locator('[data-grant-action=refresh]').click();
      await page.waitForFunction(() => document.querySelector('#sourceGrantStatus').textContent.includes('上一命令'));
      assert.equal(await page.locator('[data-snapshot-confirm]').isChecked(), false);
      assert.equal(await page.locator('[data-snapshot-result]').textContent(), '');
      await page.evaluate(() => f.release());
      await page.waitForFunction(() => f.finished);
      await page.waitForFunction(() => !document.querySelector('[data-grant-action=retry]').disabled);
      const text = await page.locator('#sourceGrantStatus').textContent();
      if (outcome === 'success') {
        assert.match(text, /快照已持久确认并精确读回/);
        assert.equal(await page.evaluate(() => f.posts.length), 1);
        assert.equal(await page.evaluate(() => f.gets), 1);
      } else {
        assert.match(text, /快照提交结果或精确读回未确认/);
        await page.locator('[data-grant-action=retry]').click();
        await page.waitForFunction(() => document.querySelector('#sourceGrantStatus').textContent.includes('快照已持久确认'));
        const posts = await page.evaluate(() => f.posts);
        assert.equal(posts.length, 2);
        assert.deepEqual(posts[1], posts[0]);
      }
      assert.deepEqual(errors, []);
    } finally {
      await browser.close();
    }
  });
}
