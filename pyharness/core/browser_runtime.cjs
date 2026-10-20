'use strict';
// Private carrier protocol, not a general JavaScript execution tool.
const readline = require('node:readline');
const crypto = require('node:crypto');
const options = JSON.parse(process.argv[2]);
const {chromium} = require(options.playwright);
const origins = new Set(options.origins);
const session = crypto.randomUUID();
let browser, context, page, retired = false, targets = new Map(), observed;
const safe = value => Array.from(String(value || '').replace(/[\u0000-\u001f]/g, ' '),
  char => char.length === 1 && char.charCodeAt(0) >= 0xd800 && char.charCodeAt(0) <= 0xdfff
    ? '\ufffd' : char).slice(0, 80).join('');
function allowed(value) {
  try {
    const u = new URL(value);
    return ['http:', 'https:'].includes(u.protocol) && !u.username && !u.password && origins.has(u.origin);
  } catch { return false; }
}
function problem(code, fatal = false) { return {error: code, fatal}; }
async function dispose() {
  retired = true; targets.clear(); observed = undefined;
  if (context) await context.close().catch(() => {});
  if (browser) await browser.close().catch(() => {});
}
async function start(url) {
  if (page) return problem('browser_already_open');
  if (!allowed(url)) return problem('browser_origin_denied');
  browser = await chromium.launch({executablePath: options.executable, headless: true,
    args: ['--disable-extensions', '--disable-background-networking', '--disable-sync',
           '--no-first-run', '--disable-component-update']});
  context = await browser.newContext({acceptDownloads: false, serviceWorkers: 'block',
    permissions: [], javaScriptEnabled: true});
  context.setDefaultTimeout(options.timeout);
  context.setDefaultNavigationTimeout(options.timeout);
  page = await context.newPage();
  await context.route('**/*', async route => {
    let permitted = allowed(route.request().url());
    try { permitted = permitted && route.request().frame().page() === page; }
    catch { permitted = false; }
    if (permitted) await route.continue();
    else await route.abort('blockedbyclient');
  });
  // Chromium/Playwright route does not intercept WebSocket without this API.
  if (typeof context.routeWebSocket !== 'function') throw Error('websocket_control_unavailable');
  await context.routeWebSocket('**/*', socket => socket.close());
  context.on('page', other => { if (other !== page) other.close().catch(() => {}); });
  page.on('dialog', dialog => dialog.dismiss().catch(() => {}));
  page.on('filechooser', () => {}); // No file paths are ever supplied.
  await context.addInitScript(() => {
    // getRandomValues is also available on allowed insecure HTTP origins;
    // randomUUID is restricted to secure contexts.
    const ident = Array.from(crypto.getRandomValues(new Uint8Array(16)),
      byte => byte.toString(16).padStart(2, '0')).join('');
    let epoch = 0;
    const state = Object.freeze({get document() { return ident; }, get epoch() { return epoch; }});
    Object.defineProperty(window, '__pyhBrowserGeneration', {value: state, configurable: false});
    new MutationObserver(() => { epoch++; }).observe(document, {
      subtree: true, childList: true, attributes: true, characterData: true});
  });
  await page.goto(url, {waitUntil: 'domcontentloaded'});
  if (!allowed(page.url())) return problem('browser_navigation_denied', true);
  return observe(0);
}
async function generation() {
  if (!page || page.isClosed()) throw Error('browser_not_open');
  if (!allowed(page.url())) throw Error('browser_navigation_denied');
  return page.evaluate(() => ({document: window.__pyhBrowserGeneration.document,
    epoch: window.__pyhBrowserGeneration.epoch}));
}
function same(a, b) { return a && b && a.document === b.document && a.epoch === b.epoch; }
async function clearTargets() {
  for (const row of targets.values()) await row.handle.dispose().catch(() => {});
  targets.clear(); observed = undefined;
}
async function observe(offset) {
  if (!page) return problem('browser_not_open');
  const before = await generation();
  await clearTargets();
  const locator = page.locator('button,a[href],input:not([type=password]):not([type=hidden]):not([type=file]),textarea,select,[role=button]');
  // The page owns its DOM. Return only four bounded indices, never a handle
  // for every matching element or unbounded page strings across the protocol.
  const scan = await locator.evaluateAll((elements, start) => {
    let total = 0;
    const chosen = [];
    for (let i = 0; i < Math.min(elements.length, 1000); i++) {
      const el = elements[i], rect = el.getBoundingClientRect(), style = getComputedStyle(el);
      if (rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility === 'visible') {
        if (total >= start && chosen.length < 4) chosen.push(i);
        total++;
      }
    }
    return {matched: elements.length, total, chosen};
  }, offset);
  const handles = [], rows = [];
  try {
    for (const index of scan.chosen) {
      const handle = await locator.nth(index).elementHandle();
      if (!handle) throw Error('browser_observation_changed');
      handles.push(handle);
      const rawLabel = await handle.evaluate(el => String(el.getAttribute('aria-label') ||
        el.innerText || el.getAttribute('placeholder') || el.getAttribute('name') || el.tagName).slice(0, 160));
      const label = safe(rawLabel), target = crypto.randomUUID();
      const tag = await handle.evaluate(el => el.tagName.toLowerCase());
      targets.set(target, {handle, label, rawLabel});
      rows.push({target, label, tag});
    }
    const after = await generation();
    if (!same(before, after)) { await clearTargets(); return problem('browser_observation_changed'); }
    observed = after;
    const u = new URL(page.url());
    const text = await page.locator('body').evaluate(el => String(el.innerText || '').slice(0, 160), null, {timeout: options.timeout}).catch(() => '');
    return {session, generation: after.epoch, document: after.document,
      url: (u.origin + u.pathname).slice(0, 160), text: safe(text),
      targets: rows, offset, total: scan.total, capped: scan.matched > 1000,
      has_more: offset + rows.length < scan.total, untrusted: true};
  } finally {
    const kept = new Set([...targets.values()].map(row => row.handle));
    for (const handle of handles) if (!kept.has(handle)) await handle.dispose().catch(() => {});
  }
}
async function performAndReadBack(action, run) {
  // Observe lifecycle before the irreversible action, including same-URL
  // reloads and redirect chains. Never repeat the action to obtain readback.
  let pending = false, ready = Promise.resolve(), resolveReady, timer;
  const deadline = Date.now() + options.timeout;
  const requested = request => {
    if (!request.isNavigationRequest() || request.frame() !== page.mainFrame()) return;
    if (!pending) {
      pending = true;
      ready = new Promise(resolve => { resolveReady = resolve; });
    }
  };
  const loaded = () => {
    pending = false;
    if (resolveReady) resolveReady();
    resolveReady = undefined;
  };
  page.on('request', requested);
  page.on('domcontentloaded', loaded);
  try {
    const result = await run();
    if (result) return problem(result);
    // Give immediate DOM handlers and navigation request events a turn.
    await page.waitForTimeout(50);
    while (pending) {
      const remaining = deadline - Date.now();
      if (remaining <= 0) throw Error('browser_navigation_unconfirmed');
      try {
        await Promise.race([ready, new Promise((_, reject) => {
          timer = setTimeout(() => reject(Error('browser_navigation_unconfirmed')), remaining);
        })]);
      } finally { clearTimeout(timer); }
    }
    return {acted: action, readback: await observe(0)};
  } finally {
    clearTimeout(timer);
    page.removeListener('request', requested);
    page.removeListener('domcontentloaded', loaded);
  }
}
async function act(args) {
  const row = targets.get(args.target);
  const current = await generation();
  if (args.session !== session || !row || !same(current, observed) ||
      args.generation !== current.epoch || args.label !== row.label)
    return problem('browser_target_stale');
  const old = observed;
  targets.delete(args.target);
  await clearTargets(); // Consume every reference before the irreversible boundary.
  try {
    return await performAndReadBack(args.action, async () => {
      await row.handle.scrollIntoViewIfNeeded();
      return row.handle.evaluate((el, input) => {
      const state = window.__pyhBrowserGeneration;
      if (state.document !== input.document || state.epoch !== input.epoch || !el.isConnected)
        return 'browser_target_stale';
      const rect = el.getBoundingClientRect(), style = getComputedStyle(el);
      const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
      if (!rect.width || !rect.height || style.visibility !== 'visible' || style.display === 'none' ||
          !hit || !(hit === el || el.contains(hit)) || el.matches(':disabled,[aria-disabled=true]') ||
          el.closest('[inert]')) return 'browser_target_unavailable';
      const rawLabel = String(el.getAttribute('aria-label') || el.innerText ||
        el.getAttribute('placeholder') || el.getAttribute('name') || el.tagName).slice(0, 160);
      if (rawLabel !== input.raw_label) return 'browser_target_stale';
      if (input.action === 'fill') {
        if (!['INPUT', 'TEXTAREA'].includes(el.tagName) || el.readOnly ||
            (el.tagName === 'INPUT' && !['text','search','email','url','tel','number'].includes(el.type)))
          return 'browser_input_not_supported';
        const proto = el.tagName === 'INPUT' ? HTMLInputElement.prototype : HTMLTextAreaElement.prototype;
        Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, input.text);
        el.dispatchEvent(new Event('input', {bubbles:true}));
        el.dispatchEvent(new Event('change', {bubbles:true}));
      } else {
        if (el.tagName === 'INPUT' && ['file','password'].includes(el.type))
          return 'browser_input_not_supported';
        el.click();
      }
      return null;
      }, {...args, ...old, raw_label: row.rawLabel});
    });
  } finally { await row.handle.dispose().catch(() => {}); }
}
async function dispatch(req) {
  if (retired) return problem('browser_carrier_closed', true);
  if (req.name === 'open') return start(req.arguments.url);
  if (req.name === 'observe') return observe(req.arguments.offset || 0);
  if (req.name === 'act') return act(req.arguments);
  return problem('unknown_browser_tool');
}
async function main() {
  const source = readline.createInterface({input: process.stdin, crlfDelay: Infinity});
  try {
    for await (const line of source) {
      if (Buffer.byteLength(line) > 16384) break;
      let req, result;
      try {
        req = JSON.parse(line);
        result = await dispatch(req);
      } catch {
        result = problem('browser_carrier_unconfirmed', true);
      }
      const output = JSON.stringify({id: req && req.id, result});
      if (output.length > 1900) {
        process.stdout.write(JSON.stringify({id: req && req.id, result: problem('browser_output_limit', true)}) + '\n');
        break;
      }
      process.stdout.write(output + '\n');
      if (result.fatal) break;
    }
  } finally { await dispose(); }
}
main().catch(() => {process.exitCode = 1;});
