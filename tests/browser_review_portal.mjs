// Real-server browser end-to-end test for the label review & verification portal.
//
// It drives the shipped server (tests/portal_browser_harness.py) in headless
// Chrome through CDP: no mocks of the page, the save/receipt/journal path, the
// campaign tracker, the revision queue or the launcher preflight. Only the paid
// HF/OpenRouter edges are seamed by the harness.
//
//   node tests/browser_review_portal.mjs [evidence-dir]
//
// Requires Chrome and the repo .venv (PYTHON=... to override).
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import {spawn} from 'node:child_process';
import assert from 'node:assert/strict';

const repo = path.resolve(new URL('..', import.meta.url).pathname);
const out = process.argv[2] || path.join(os.tmpdir(), 'opencode', 'review-portal-e2e');
fs.mkdirSync(out, {recursive: true});
const root = path.join(out, 'portal-root');
fs.rmSync(root, {recursive: true, force: true});
const PYTHON = process.env.PYTHON || path.join(repo, '.venv', 'bin', 'python');
const IDENTITY = 'Haiku-5.5'; // a model integration identifies with its short display slug, not a provider route
const results = [];
const checks = [];
const step = async (name, fn) => {
  try { await fn(); results.push({name, ok: true}); console.log('PASS  ' + name); }
  catch (exc) { results.push({name, ok: false, error: String(exc && (exc.message || exc))}); console.log('FAIL  ' + name + ' — ' + (exc && (exc.message || exc))); }
};
const check = (name) => checks.push(name);

// ---------------------------------------------------------------- server
const harness = spawn(PYTHON, [path.join(repo, 'tests', 'portal_browser_harness.py'), '--root', root], {
  cwd: repo, env: {...process.env, PYTHONPATH: path.join(repo, 'src')}, stdio: ['ignore', 'pipe', 'pipe']});
let harnessErr = '';
harness.stderr.on('data', (d) => { harnessErr += d; });
const firstLine = await new Promise((resolve, reject) => {
  let buffer = '';
  harness.stdout.on('data', (d) => {
    buffer += d;
    const line = buffer.split('\n').find((x) => x.trim().startsWith('{'));
    if (line) resolve(line);
  });
  harness.on('exit', (code) => reject(Error(`harness exited ${code}: ${harnessErr.slice(-2000)}`)));
  setTimeout(() => reject(Error(`harness did not start: ${harnessErr.slice(-2000)}`)), 20000);
});
const server = JSON.parse(firstLine);
const portalURL = server.portal;
const controlURL = server.control;
async function control(action, body = {}) {
  const res = await fetch(`${controlURL}/${action}`, {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)});
  const value = await res.json();
  if (!res.ok) throw Error(`${action}: ${value.error}`);
  return value;
}
const snapshot = async () => (await fetch(`${controlURL}/snapshot`)).json();

// API-level contract and security checks run before the browser; each is part of
// what the shipped server promises to every client (page or agent).
await step('server: routes, agent contract and revision queue', async () => {
  const contract = await (await fetch(`${portalURL}/api/contract`)).json();
  assert.equal(contract.schema, 'mailroom-review-agent-contract/v1');
  assert.equal(contract.read.packet, '/review-data.json');
  assert.equal(contract.write.save_decisions, '/api/reviews');
  const rules = contract.rules.join(' ');
  for (const phrase of ['manual_source_verification', 'verified_models', 'state_revision', 'never publishes',
    'per-label', 'initials']) assert.ok(rules.toLowerCase().includes(phrase), `contract rule missing: ${phrase}`);
  const data = await (await fetch(`${portalURL}/review-data.json`)).json();
  assert.equal(data.schema, 'mailroom-review-packet/v1');
  assert.equal(data.decision_key.ACCEPT, "Accept the displayed reviewed candidate, NOT a raw-model precision 'correct'.");
  assert.equal(data.records.length, 35);
  const state = await (await fetch(`${portalURL}/review-state.json`)).json();
  assert.equal(state.packet_id, data.packet_id);
  assert.ok(!('records' in state), 'state route stays slim');
  const queue = await (await fetch(`${portalURL}/revision-queue.json`)).json();
  assert.equal(queue.schema, 'mailroom-v10-manual-revisions/v1');
  assert.deepEqual(queue.revisions, []);
});

await step('server: loopback, origin, content-type, size and route guards', async () => {
  const call = (method, p, {headers = {}, body = null, host} = {}) => new Promise((resolve, reject) => {
    const req = http.request({hostname: '127.0.0.1', port: new URL(portalURL).port, path: p, method,
      headers: {...(host ? {Host: host} : {}), ...headers}}, (res) => {
      let data = '';
      res.on('data', (d) => { data += d; });
      res.on('end', () => resolve({status: res.statusCode, body: data, headers: res.headers}));
    });
    req.on('error', reject);
    if (body !== null) req.write(body);
    req.end();
  });
  assert.equal((await call('GET', '/nope')).status, 404);
  assert.equal((await call('GET', '/review-data.json', {host: 'evil.invalid'})).status, 403);
  assert.equal((await call('POST', '/api/reviews', {body: JSON.stringify({})})).status, 403,
    'writes need a same-origin Origin header');
  assert.equal((await call('POST', '/api/reviews', {headers: {Origin: 'https://evil.invalid'},
    body: JSON.stringify({})})).status, 403);
  assert.equal((await call('POST', '/api/reviews', {headers: {Origin: portalURL, 'Content-Type': 'text/plain'},
    body: '{}'})).status, 415);
  assert.equal((await call('POST', '/api/checkpoint', {headers: {Origin: portalURL, 'Content-Type': 'application/json'},
    body: JSON.stringify({packet_id: 'x', confirm: false})})).status, 400);
  const huge = await call('POST', '/api/reviews', {headers: {Origin: portalURL, 'Content-Type': 'application/json',
    'Content-Length': String(10 * 1024 * 1024 + 1)}, body: ''});
  assert.equal(huge.status, 413);
  const page = await call('GET', '/review.html');
  assert.match(page.headers['content-security-policy'], /default-src 'self'/);
  assert.match(page.headers['content-security-policy'], /frame-ancestors 'none'/);
  assert.equal(page.headers['cache-control'], 'no-store');
  assert.equal(page.headers['x-content-type-options'], 'nosniff');
});

// ---------------------------------------------------------------- chrome
const chromePath = process.env.CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const chrome = spawn(chromePath, ['--headless=new', ...(process.env.CHROME_NO_SANDBOX ? ['--no-sandbox'] : []), '--remote-debugging-port=0',
  '--user-data-dir=' + path.join(out, 'chrome-profile'), '--no-first-run', '--no-default-browser-check',
  '--disable-background-networking', '--disable-component-update', '--window-size=1440,1100', 'about:blank'],
{stdio: ['ignore', 'ignore', 'pipe']});
let ws, session, sequence = 0;
const pending = new Map(), consoleErrors = [];
const call = (method, params = {}, sid = session) => {
  const id = ++sequence;
  return new Promise((resolve, reject) => {
    pending.set(id, {resolve, reject});
    ws.send(JSON.stringify({id, method, params, ...(sid ? {sessionId: sid} : {})}));
  });
};
async function evaluate(expression) {
  for (let attempt = 0; attempt < 40; attempt++) {
    try {
      const r = await call('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true});
      if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails));
      return r.result.value;
    } catch (exc) {
      if (!/navigated or closed|context/i.test(String(exc))) throw exc;
      await new Promise((r) => setTimeout(r, 60));
    }
  }
  throw Error('target context unavailable');
}
async function waitFor(expression, timeout = 12000) {
  await evaluate(`new Promise((resolve,reject)=>{const end=Date.now()+${timeout};
    function check(){try{if(${expression})resolve(true);else if(Date.now()>end)reject(Error('timeout: ${expression.replace(/'/g, '"')}'));else requestAnimationFrame(check);}catch(e){reject(e);}}
    check();})`);
}
async function screenshot(name) {
  const r = await call('Page.captureScreenshot', {format: 'png', captureBeyondViewport: true});
  fs.writeFileSync(path.join(out, name + '.png'), Buffer.from(r.data, 'base64'));
}
async function input(id, value) {
  await evaluate(`$(${JSON.stringify(id)}).value=${JSON.stringify(value)};
    $(${JSON.stringify(id)}).dispatchEvent(new Event('input',{bubbles:true}));`);
}
async function goto(field, needle = '') {
  const index = await evaluate(`records.findIndex((r)=>r.field===${JSON.stringify(field)}&&r.document_id.includes(${JSON.stringify(needle)}))`);
  assert.ok(index >= 0, `target not found: ${field} ${needle}`);
  await evaluate(`$('target').value=${JSON.stringify(String(index))};$('target').dispatchEvent(new Event('change'));`);
  return index;
}
async function decide(code) {
  await evaluate(`$('decision').value=${JSON.stringify(code)};$('decision').dispatchEvent(new Event('input'));`);
}
const text = (id) => evaluate(`$(${JSON.stringify(id)}).textContent`);
async function saveWait() {
  await waitFor(`!busy`);
  await evaluate(`$('save').click()`);
  try { await waitFor(`!busy && /Saved\. Receipt|Nothing unsaved|No change to store/.test($('message').textContent)`); }
  catch (exc) { console.log('      save message:', await text('message')); throw exc; }
}

let chromeReady = false;
try {
  const address = await new Promise((resolve, reject) => {
    let buffer = '';
    chrome.stderr.on('data', (d) => {
      buffer += d;
      const m = buffer.match(/DevTools listening on (ws:\/\/[^\s]+)/);
      if (m) resolve(m[1]);
    });
    chrome.on('error', reject);
    chrome.on('exit', (code) => reject(Error('Chrome exit ' + code)));
  });
  ws = new WebSocket(address);
  await new Promise((resolve) => ws.addEventListener('open', resolve, {once: true}));
  ws.addEventListener('message', (event) => {
    const m = JSON.parse(event.data);
    if (m.id) {
      const p = pending.get(m.id); pending.delete(m.id);
      m.error ? p.reject(Error(JSON.stringify(m.error))) : p.resolve(m.result);
    } else if (m.method === 'Runtime.exceptionThrown') consoleErrors.push(m.params);
    else if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') consoleErrors.push(m.params);
    else if (m.method === 'Page.javascriptDialogOpening') {
      try { call('Page.handleJavaScriptDialog', {accept: true}); } catch (exc) { /* tab closing */ }
    }
  });
  const target = await call('Target.createTarget', {url: 'about:blank'}, null);
  session = (await call('Target.attachToTarget', {targetId: target.targetId, flatten: true}, null)).sessionId;
  await call('Page.enable'); await call('Runtime.enable');
  await call('Browser.setDownloadBehavior', {behavior: 'allow', downloadPath: out}, null);
  await call('Page.navigate', {url: `${portalURL}/review.html`});
  await waitFor(`document.body.dataset.reviewReady==='1'`);
  chromeReady = true;

  // ------------------------------------------------------------ page load
  await step('page: embedded packet, unicode source and evidence highlight', async () => {
    await goto('jurisdiction', '01ccf3f1');
    const data = await (await fetch(`${portalURL}/review-data.json`)).json();
    const probe = await evaluate(`(()=>{const r=records[current];
      return {text:$('source').textContent, marks:[...document.querySelectorAll('#source mark')].map((m)=>m.textContent),
        expected:r.full_document_text, quote:r.evidence[0].quote, binding:$('binding').textContent,
        imgs:document.querySelectorAll('#source img').length, html:/<img\\b/i.test(document.documentElement.outerHTML)};})()`);
    assert.equal(probe.text, probe.expected, 'the complete source renders byte-for-byte');
    assert.ok(probe.marks.includes(probe.quote), 'evidence is highlighted');
    assert.equal(probe.imgs, 0);
    assert.equal(probe.html, false, 'no injected HTML markup');
    assert.match(probe.binding, new RegExp(data.packet_id.slice(0, 12)));
    assert.match(probe.binding, /35 targets/);
    assert.equal(await evaluate(`Array.from($('source').textContent).length`),
      await evaluate(`Array.from(records[current].full_document_text).length`),
      'code-point offsets (not UTF-16 units) drive the source map');
  });

  await step('page: source links never execute javascript: URLs', async () => {
    const idx = await evaluate(`records.findIndex((r)=>r.field==='sender'&&r.document_id.includes('UNSAFE'))`);
    assert.ok(idx >= 0);
    await evaluate(`$('target').value=${JSON.stringify(String(idx))};$('target').dispatchEvent(new Event('change'));`);
    assert.equal(await evaluate(`document.querySelectorAll('#links a').length`), 0);
    assert.match(await text('links'), /unverified or unavailable/);
    assert.equal(await evaluate(`window.injected`), undefined);
  });

  // ------------------------------------------------------------ tabs / a11y
  await step('page: tabs, ARIA state and keyboard roving tabindex', async () => {
    assert.equal(await evaluate(`document.querySelectorAll('main').length`), 1);
    assert.equal(await evaluate(`document.querySelectorAll('[role="tabpanel"]').length`), 3);
    assert.equal(await evaluate(`document.querySelectorAll('[role="tab"][aria-selected="true"]').length`), 1);
    assert.equal(await evaluate(`$('tab-review').tabIndex`), 0);
    await evaluate(`$('tab-launch').focus();$('tab-launch').dispatchEvent(Object.assign(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true,cancelable:true})))`);
    assert.equal(await evaluate(`$('tab-review').getAttribute('aria-selected')`), 'true');
    assert.equal(await evaluate(`document.activeElement.id`), 'tab-review');
    await evaluate(`document.activeElement.dispatchEvent(Object.assign(new KeyboardEvent('keydown',{key:'Home',bubbles:true,cancelable:true})))`);
    assert.equal(await evaluate(`$('tab-launch').getAttribute('aria-selected')`), 'true');
    await evaluate(`document.activeElement.dispatchEvent(Object.assign(new KeyboardEvent('keydown',{key:'ArrowLeft',bubbles:true,cancelable:true})))`);
    assert.equal(await evaluate(`$('tab-tracker').getAttribute('aria-selected')`), 'true');
    assert.equal(await evaluate(`$('tracker-view').hidden`), false);
    const labelled = await evaluate(`[...document.querySelectorAll('input,select,textarea')].filter((el)=>!el.closest('label')&&!el.getAttribute('aria-label')&&!document.querySelector('label[for="'+el.id+'"]')&&el.type!=='hidden').map((el)=>el.id||el.name||el.tagName)`);
    assert.deepEqual(labelled, [], 'every form control has an accessible name');
  });

  await step('page: tab choice survives a reload', async () => {
    await evaluate(`showTab('review');window.onbeforeunload=null`);
    await call('Page.reload');
    await waitFor(`document.body.dataset.reviewReady==='1'`);
    assert.equal(await evaluate(`$('review-view').hidden`), false);
    check('tab persistence');
  });

  // ------------------------------------------------------------ search / navigation
  await step('navigation: search by id, field, class and batch; empty state keeps the cursor', async () => {
    await evaluate(`showTab('review')`);
    const index = await goto('jurisdiction', '01ccf3f1');
    await input('target-search', '01ccf3f1 jurisdiction');
    await waitFor(`$('target').options.length>0`);
    assert.equal(await evaluate(`$('target').options.length`), 1);
    assert.match(await text('target-search-status'), /1 of 35 targets/);
    await input('target-search', 'corporate');
    assert.ok(await evaluate(`$('target').options.length`) > 5, 'class token matches the corporate batch');
    await input('target-search', '8a');
    assert.ok(await evaluate(`$('target').options.length`) >= 17, 'batch token matches');
    await input('target-search', 'no-such-target');
    await waitFor(`$('target').options.length===0`);
    assert.equal(await evaluate(`current`), index, 'empty search leaves the open review unchanged');
    assert.match(await text('target-search-status'), /No matching targets/);
    await evaluate(`$('clear-target-search').click()`);
    await waitFor(`$('target').options.length===35`);
    assert.equal(await evaluate(`document.activeElement.id`), 'target-search');
  });

  await step('navigation: batch optgroups, counts, jump and fully-decided notice', async () => {
    const groups = await evaluate(`Object.fromEntries([...$('target').querySelectorAll('optgroup')].map((g)=>[g.dataset.gid,g.label]))`);
    assert.equal(groups['8a'], '8a · Corporate / pilot — 0/17 decided');
    assert.equal(groups['7a'], '7a · Correspondence / pilot — 0/15 decided');
    assert.equal(groups['8b'], '8b · Claim checklist / remainder — 0/2 decided');
    assert.equal(await evaluate(`$('target').querySelectorAll('optgroup option[value]').length`), 34,
      'every batched record is grouped (the production record has no batch)');
    const jumps = await evaluate(`[...$('batch-jump').options].map((o)=>o.text+':'+o.value)`);
    assert.ok(jumps.some((x) => x.startsWith('8a — 17 pending of 17:8a')), jumps.join(' | '));
    await evaluate(`$('batch-jump').value='8b';$('batch-jump').dispatchEvent(new Event('change'));`);
    assert.equal(await evaluate(`records[current].batch`), '8b');
    // Fully decided batch: decide both targets then jump back. The page says it stays put.
    await evaluate(`decisions.forEach((d,k)=>{ if (String(records.find((r)=>KEY(r)===k).batch)==='8b'){d.decision='ACCEPT';} });batchProgress();`);
    await evaluate(`$('batch-jump').value='7a';$('batch-jump').dispatchEvent(new Event('change'));`);
    await evaluate(`$('batch-jump').value='8b';$('batch-jump').dispatchEvent(new Event('change'));`);
    assert.equal(await evaluate(`records[current].batch`), '7a', 'a fully decided batch does not move the cursor');
    assert.match(await text('message'), /no pending targets/i, 'the jump says why it stayed put');
    await evaluate(`decisions.forEach((d,k)=>{ if (String(records.find((r)=>KEY(r)===k).batch)==='8b'){d.decision='NOT_REVIEWED';} });batchProgress();`);
    check('batch jump and decided counts');
  });

  await step('navigation: per-document field outcomes stay isolated', async () => {
    await goto('jurisdiction', '01ccf3f1');
    const fields = await evaluate(`[...$('document-fields').querySelectorAll('button')].map((b)=>b.textContent)`);
    assert.equal(fields.length, 7, 'the corporate document shows its seven fields');
    assert.match(await text('field-outcome'), /Jurisdiction: Not reviewed/);
    await evaluate(`document.querySelector('[data-outcome="ACCEPT"]').click()`);
    await input('initials', IDENTITY);
    assert.match(await text('field-outcome'), /Jurisdiction: Draft — Approved \(not saved\)/);
    const other = await evaluate(`[...$('document-fields').querySelectorAll('button')].find((b)=>b.textContent.startsWith('Registered agent')).textContent`);
    assert.match(other, /Not reviewed/, 'a decision cannot approve a sibling field');
    check('granular field outcomes');
  });

  // ------------------------------------------------------------ model answers / picker
  await step('verify: model answers table, pass-level votes and Use value', async () => {
    await goto('jurisdiction', '01ccf3f1');
    const rows = await evaluate(`document.querySelectorAll('#model-answers tr').length`);
    assert.equal(rows, 3, 'one header plus two model rows');
    assert.match(await text('model-answers'), /z-ai\/glm-5\.3-flashx-legal/);
    assert.match(await text('model-answers'), /deepseek\/deepseek-v4\.1-flash/);
    await evaluate(`[...document.querySelectorAll('#model-answers input')][0].click()`);
    assert.deepEqual(JSON.parse(await evaluate(`JSON.stringify(decision().verified_outputs)`)),
      [{model: 'z-ai/glm-5.3-flashx-legal', pass: null}]);
    await evaluate(`[...document.querySelectorAll('#model-answers button')][0].click()`);
    assert.equal(await evaluate(`$('decision').value`), 'CORRECT');
    assert.equal(JSON.parse(await evaluate(`$('correction').value`)), 'Wisconsin');
    assert.equal(await evaluate(`JSON.stringify(decision().verified_models)`), '["z-ai/glm-5.3-flashx-legal"]');
    check('model answers and attribution');
  });

  await step('verify: pass-level rows are distinct and kept per run', async () => {
    await goto('sender', '0051fe51');
    const saved = await evaluate(`JSON.stringify(records[current].model_outputs)`);
    await evaluate(`records[current].model_outputs=[{model:'deepseek/deepseek-v4.1-flash',pass:1,value:'Kevin G Moore',status:'present',evidence:''},{model:'deepseek/deepseek-v4.1-flash',pass:2,value:'Kevin G. Moore',status:'present',evidence:''}];renderTarget();`);
    assert.match(await text('model-answers'), /pass 1/);
    assert.match(await text('model-answers'), /pass 2/);
    await evaluate(`document.querySelectorAll('#model-answers input')[0].click()`);
    await evaluate(`document.querySelectorAll('#model-answers input')[1].click()`);
    assert.deepEqual(JSON.parse(await evaluate(`JSON.stringify(decision().verified_outputs)`)),
      [{model: 'deepseek/deepseek-v4.1-flash', pass: 1}, {model: 'deepseek/deepseek-v4.1-flash', pass: 2}]);
    await evaluate(`records[current].model_outputs=${saved};decisions.set(KEY(records[current]),blank(records[current]));renderTarget();`);
    check('pass-level model attribution');
  });

  await step('verify: per-label keep/drop/edit, guards and saved-correction protection', async () => {
    await goto('signatories', 'PICKER');
    assert.match(await evaluate(`document.querySelector('#item-picker summary').textContent`), /3 of 3 kept/);
    await evaluate(`document.querySelectorAll('#item-picker .chips .chip')[0].click()`);
    assert.match(await evaluate(`document.querySelector('#item-picker summary').textContent`), /2 of 3 kept/);
    await evaluate(`$('target').value='0';$('target').dispatchEvent(new Event('change'));`);
    await goto('signatories', 'PICKER');
    assert.match(await evaluate(`document.querySelector('#item-picker summary').textContent`), /2 of 3 kept/,
      'manual keep/drop survives navigation');
    await evaluate(`document.querySelectorAll('#item-picker .chips .chip')[1].click();document.querySelectorAll('#item-picker .chips .chip')[2].click()`);
    assert.match(await evaluate(`document.querySelector('#item-picker summary').textContent`), /0 of 3 kept/);
    await evaluate(`[...document.querySelectorAll('#item-picker button')].at(-1).click()`);
    assert.match(await text('message'), /Every label is dropped/);
    await evaluate(`document.querySelectorAll('#item-picker .chips .chip')[2].click()`);
    await evaluate(`(()=>{const i=document.querySelectorAll('#item-picker input')[2];i.value='';i.dispatchEvent(new Event('input',{bubbles:true}));})()`);
    await evaluate(`[...document.querySelectorAll('#item-picker button')].at(-1).click()`);
    assert.match(await text('message'), /Drop empty-edited items/);
    await evaluate(`(()=>{const i=document.querySelectorAll('#item-picker input')[2];i.value='Carol White';i.dispatchEvent(new Event('input',{bubbles:true}));})()`);
    await evaluate(`[...document.querySelectorAll('#item-picker button')].at(-1).click()`);
    assert.equal(await evaluate(`$('decision').value`), 'CORRECT');
    assert.deepEqual(JSON.parse(await evaluate(`$('correction').value`)), ['Carol White']);
    await evaluate(`decisions.get(KEY(records[current])).corrected_labels_json='["hand edited"]';itemState.delete(KEY(records[current]));renderTarget();`);
    assert.match(await evaluate(`$('item-picker').textContent`), /Saved correction preserved/);
    assert.equal(await evaluate(`$('item-picker').querySelectorAll('button,input').length`), 0);
    // Leave no ungrounded draft behind: later autosaves must not carry it.
    await evaluate(`decisions.set(KEY(records[current]),blank(records[current]));itemState.delete(KEY(records[current]));renderTarget();`);
    check('per-label picker and safety guards');
  });

  // ------------------------------------------------------------ decisions / save
  await step('save: initials validation, blocked ACCEPT and real receipt/journal', async () => {
    await evaluate(`autosaveEnabled=false`);
    await evaluate(`localStorage.removeItem(storageKey+'/initials')`);
    await goto('jurisdiction', '01ccf3f1');
    await evaluate(`decisions.set(KEY(records[current]),blank(records[current]));renderTarget();`);
    await decide('ACCEPT');
    await input('initials', 'ab');
    assert.equal(await evaluate(`validate()`), false);
    assert.match(await text('message'), /model's short display name/);
    await input('initials', 'Anthropic/Haiku-5.5');
    assert.equal(await evaluate(`validate()`), false, 'provider-qualified routes are not reviewer identities');
    assert.match(await text('message'), /not Anthropic\/Haiku-5\.5/);
    await input('initials', IDENTITY);
    await evaluate(`$('save').click()`);
    await waitFor(`!busy && /Saved\. Receipt/.test($('message').textContent)`);
    const snap = await snapshot();
    assert.equal(snap.journal.length, 1, 'one journal event');
    assert.equal(snap.journal[0].decision, 'ACCEPT', 'journal event decision');
    assert.equal(snap.journal[0].reviewer, IDENTITY, 'journal reviewer');
    assert.equal(snap.journal[0].reviewer_kind, 'model', 'journal reviewer kind');
    assert.equal(snap.journal[0].human_confirmed, true, 'human confirmed');
    assert.equal(snap.journal[0].value, 'Wisconsin', 'journal value');
    assert.equal(snap.receipts.length, 1, 'one receipt');
    const receipt = JSON.parse(fs.readFileSync(path.join(server.out, 'returns', snap.receipts[0]), 'utf8'));
    assert.equal(receipt.schema, 'mailroom-human-review/v1');
    assert.ok(!('slim' in receipt));
    assert.equal(receipt.decisions.find((d) => d.field === 'jurisdiction' && d.document_id.includes('01ccf3f1')).decision, 'ACCEPT', 'receipt decision');
    assert.equal(await evaluate(`dirty`), false, 'a complete save clears the dirty flag');
    assert.equal(await evaluate(`$('review-progress').value`), 1, 'progress count');
    check('real receipt and journal');
  });

  await step('save: an unchanged decision adds no receipt or journal entry', async () => {
    const before = await snapshot();
    await evaluate(`$('save').click()`);
    await waitFor(`!busy`);
    assert.match(await text('message'), /Nothing unsaved/);
    const after = await snapshot();
    assert.equal(after.journal.length, before.journal.length);
    assert.equal(after.receipts.length, before.receipts.length);
    check('idempotent save');
  });

  await step('verify: empty proposals cannot be ACCEPTed, blocked values need manual verification', async () => {
    // The insurance claim_checklist target has no candidate at all: ACCEPT is meaningless
    // and the page must say so instead of letting the server reject it.
    await goto('claim_checklist', '000b010d');
    await evaluate(`showTab('review')`);
    await evaluate(`decisions.set(KEY(records[current]),blank(records[current]));renderTarget();`);
    await input('initials', IDENTITY);
    await decide('ACCEPT');
    await evaluate(`$('manual-verify').click()`);
    assert.equal(await evaluate(`validate()`), false);
    assert.match(await text('message'), /no candidate value to approve/);
    // The invalid decision must be cleared, not left to poison a later save elsewhere.
    await evaluate(`decisions.set(KEY(records[current]),blank(records[current]));renderTarget();`);
    // A needs_source_review target that does carry a value can be accepted after the tick.
    await goto('jurisdiction', 'BLOCKED');
    assert.equal(await evaluate(`records[current].candidate_status`), 'needs_source_review');
    await evaluate(`decisions.set(KEY(records[current]),blank(records[current]));renderTarget();`);
    await input('initials', IDENTITY);
    await decide('ACCEPT');
    assert.equal(await evaluate(`$('manual-wrap').hidden`), false);
    assert.equal(await evaluate(`validate()`), false);
    assert.match(await text('message'), /did not pass automated source checks/);
    await evaluate(`$('manual-verify').click()`);
    assert.equal(await evaluate(`validate()`), true);
    await evaluate(`$('save').click()`);
    try { await waitFor(`!busy && /Saved\. Receipt/.test($('message').textContent)`); }
    catch (exc) { console.log('      manual save message:', await text('message')); throw exc; }
    const snap = await snapshot();
    const event = snap.journal.at(-1);
    assert.equal(event.field, 'jurisdiction');
    assert.equal(event.manual_source_verification, true);
    assert.equal(event.human_confirmed, true);
    check('manual source verification');
  });

  await step('save: an invalid decision elsewhere is named, never an opaque server 400', async () => {
    const before = await snapshot();
    let message = '';
    try {
      await evaluate(`(()=>{const i=records.findIndex((r)=>r.field==='claim_checklist'&&r.document_id.includes('000b'));
        decisions.set(KEY(records[i]),{...blank(records[i]),decision:'ACCEPT',reviewer_initials:${JSON.stringify(IDENTITY)},updated_at:now()});dirty=true;})()`);
      await goto('registered_agent', '01ccf3f1');
      await evaluate(`$('save').click()`);
      await waitFor(`/Cannot save: there is no candidate value to approve/.test($('message').textContent)`, 8000);
      message = await text('message');
      assert.match(message, /Cannot save: there is no candidate value to approve.*Claim checklist/s);
      assert.equal(await evaluate(`records[current].field`), 'claim_checklist', 'the page jumps to the offending target');
      assert.equal((await snapshot()).journal.length, before.journal.length, 'nothing reached the server');
    } finally {
      await evaluate(`(()=>{const i=records.findIndex((r)=>r.field==='claim_checklist'&&r.document_id.includes('000b'));
        decisions.set(KEY(records[i]),blank(records[i]));dirty=false;})()`);
      await evaluate(`$('target').value=String(records.findIndex((r)=>r.field==='registered_agent'&&r.document_id.includes('01ccf3f1')));$('target').dispatchEvent(new Event('change'));`);
    }
  });

  await step('save: CORRECT validation, REJECT and NEEDS_SOURCE', async () => {
    await goto('registered_agent', '01ccf3f1');
    await evaluate(`decisions.set(KEY(records[current]),blank(records[current]));renderTarget();`);
    await input('initials', IDENTITY);
    await decide('CORRECT');
    await input('correction', 'not json');
    assert.equal(await evaluate(`validate()`), false);
    assert.match(await text('message'), /valid JSON/);
    await input('correction', '"CT Corporation System"');
    assert.equal(await evaluate(`validate()`), true);
    await evaluate(`$('save').click()`);
    await waitFor(`!busy && /Saved\. Receipt/.test($('message').textContent)`);
    assert.equal((await snapshot()).journal.at(-1).decision, 'CORRECT');
    await goto('effective_date', '00193d0c');
    await evaluate(`decisions.set(KEY(records[current]),blank(records[current]));renderTarget();`);
    await input('initials', IDENTITY);
    await decide('REJECT');
    await evaluate(`$('save').click()`);
    await waitFor(`!busy && /Saved\. Receipt/.test($('message').textContent)`);
    await goto('filing_number', '01ccf3f1');
    await evaluate(`decisions.set(KEY(records[current]),blank(records[current]));renderTarget();`);
    await input('initials', IDENTITY);
    await decide('NEEDS_SOURCE');
    await evaluate(`$('save').click()`);
    await waitFor(`!busy && /Saved\. Receipt/.test($('message').textContent)`);
    const events = (await snapshot()).journal.slice(-2);
    assert.deepEqual(events.map((e) => e.decision), ['REJECT', 'NEEDS_SOURCE']);
    assert.match(await text('review-progress-text'), /targets reviewed and saved/);
  });

  await step('save: schema-pending ACCEPT stores a blocker, never gold', async () => {
    await goto('proposed_new_field', 'PROPOSAL');
    await evaluate(`decisions.set(KEY(records[current]),blank(records[current]));renderTarget();`);
    await input('initials', IDENTITY);
    await decide('ACCEPT');
    await evaluate(`$('save').click()`);
    await waitFor(`!busy && /Saved\. Receipt/.test($('message').textContent)`);
    await evaluate(`$('reload').click()`);
    await waitFor(`!busy && $('message').textContent.includes('Loaded')`);
    await goto('proposed_new_field', 'PROPOSAL');
    assert.match(await text('field-outcome'), /schema approval still required/);
    const event = (await snapshot()).journal.at(-1);
    assert.match(event.schema_blocker, /not[_ ]pinned|not applicable/);
    assert.equal(event.human_confirmed, false);
    check('schema blocker is not a confirmation');
  });

  await step('resume: reload restores receipts and advances to the next pending target', async () => {
    await evaluate(`window.onbeforeunload=null`);
    await call('Page.reload');
    await waitFor(`document.body.dataset.reviewReady==='1'`);
    const progress = await text('review-progress-text');
    assert.match(progress, /[1-9]\d* \/ 35 targets reviewed and saved/);
    assert.ok(Number(await evaluate(`$('review-progress').value`)) >= 3);
    const statuses = await evaluate(`packet.review_states.filter((s)=>s.status==='human_confirmed').length`);
    assert.ok(statuses >= 3, 'server state restored');
    assert.match(await text('binding'), /decisions restored from receipts/);
    assert.notEqual(await evaluate(`stateOf(records[current]).status`), 'human_confirmed', 'reopening never lands on a saved decision');
    check('resume next pending after reload');
  });

  await step('verify: sticky initials and export produces a server-valid return', async () => {
    await goto('approval_date', '00193d0c');
    assert.equal(await evaluate(`$('initials').value`), IDENTITY, 'the model identity is remembered');
    fs.rmSync(path.join(out, 'human-review-decisions.json'), {force: true});
    await evaluate(`$('export').click()`);
    for (let i = 0; i < 40 && !fs.existsSync(path.join(out, 'human-review-decisions.json')); i++) {
      await new Promise((r) => setTimeout(r, 100));
    }
    assert.ok(fs.existsSync(path.join(out, 'human-review-decisions.json')), 'export downloaded');
    const exported = JSON.parse(fs.readFileSync(path.join(out, 'human-review-decisions.json'), 'utf8'));
    assert.equal(exported.schema, 'mailroom-human-review/v1');
    assert.equal(exported.decisions.length, 35);
    assert.ok(exported.decisions.some((d) => d.decision === 'ACCEPT' && d.reviewer_initials === IDENTITY));
    const received = await control('receive', {payload: exported});
    assert.equal(received.valid, true);
    assert.equal(received.decisions, 35);
    check('machine-readable export accepted by review_page.receive');
  });

  // ------------------------------------------------------------ autosave / conflict
  await step('autosave: a complete decision reaches the server without Save', async () => {
    await evaluate(`autosaveEnabled=true`);
    await goto('prior_entity_name', '00193d0c');
    await evaluate(`decisions.set(KEY(records[current]),blank(records[current]));renderTarget();message('Waiting for the autosave probe');`);
    await input('initials', IDENTITY);
    await decide('ACCEPT');
    await waitFor(`$('message').textContent.includes('Auto-saved at')`, 15000);
    let snap = await snapshot();
    for (let i = 0; i < 40 && snap.journal.at(-1)?.field !== 'prior_entity_name'; i++) {
      await new Promise((r) => setTimeout(r, 100)); snap = await snapshot();
    }
    assert.equal(snap.journal.at(-1).field, 'prior_entity_name');
    assert.match(await text('target-status'), /auto-saved/);
    assert.equal(await evaluate(`dirty`), false);
    assert.match(await text('review-progress-text'), /targets reviewed and saved/);
    check('autosave persists complete decisions');
  });

  await step('autosave: an unfinished correction stays local and is reported loudly', async () => {
    await evaluate(`autosaveEnabled=false`);
    await goto('authorized_capital', '01ccf3f1');
    await evaluate(`decisions.set(KEY(records[current]),blank(records[current]));renderTarget();`);
    await input('initials', IDENTITY);
    await decide('CORRECT');
    await input('correction', 'not JSON yet');
    await evaluate(`autosaveEnabled=true;scheduleAutoSave();`);
    await waitFor(`$('message').textContent.includes('Auto-saved at')`, 15000);
    assert.equal(await evaluate(`dirty`), true, 'the unfinished correction is not declared durable');
    assert.equal(await evaluate(`$('save').disabled`), false);
    assert.equal(await evaluate(`$('checkpoint').disabled`), true, 'checkpoint waits for a clean save');
    assert.match(await text('target-status'), /autosaving/);
    assert.match(await text('review-progress-text'), /1 local drafts/);
    await evaluate(`autosaveEnabled=false`);
    await evaluate(`decisions.set(KEY(records[current]),blank(records[current]));renderTarget();`);
    check('unfinished drafts are never reported as durable');
  });

  await step('conflict: a second writer makes Save 409 and keeps the local draft', async () => {
    await goto('jurisdiction', '01ccf3f1');
    await evaluate(`decisions.set(KEY(records[current]),blank(records[current]));renderTarget();`);
    await input('notes', 'Local draft that must survive.');
    await decide('ACCEPT');
    await input('initials', IDENTITY);
    await control('touch-state');
    await waitFor(`!busy`);
    await evaluate(`$('save').click()`);
    await waitFor(`!busy && /409/.test($('message').textContent)`);
    assert.match(await text('message'), /Draft retained/);
    assert.equal(await evaluate(`$('notes').value`), 'Local draft that must survive.');
    await evaluate(`window.confirm=()=>true;$('reload').click()`);
    await waitFor(`!busy`);
    assert.equal(await evaluate(`$('discard').hidden`), false, 'stale draft offers Discard');
    assert.equal(await evaluate(`$('save').disabled`), true);
    await screenshot('01-stale-draft');
    await evaluate(`$('discard').click()`);
    await waitFor(`!busy && !pendingData && $('discard').hidden`);
    assert.equal(await evaluate(`$('notes').value`), '', 'Discard loads the server state');
    check('409 keeps the draft; Discard is explicit');
  });

  // ------------------------------------------------------------ checkpoint
  await step('checkpoint: success byte-verifies, mirrors locally and shows checkpointed state', async () => {
    await control('checkpoint-fail', {fail: false});
    await evaluate(`window.confirm=()=>true;$('checkpoint').click()`);
    await waitFor(`!busy && /Private checkpoint verified/.test($('message').textContent)`, 30000);
    const snap = await snapshot();
    assert.equal(snap.checkpoints.at(-1).verified, true);
    assert.equal(snap.checkpoints.at(-1).repo, 'Lucius-Morningstar/mailroom-dataset-v10-staging');
    assert.ok(snap.hub_files.some((f) => f.endsWith('confirmed-fields.jsonl')), 'journal uploaded');
    assert.ok(snap.hub_files.some((f) => f.includes('browser-corporate-review-data.json')), 'packet uploaded');
    assert.ok(snap.mirror_files.some((f) => f.includes('local-backup.json')), 'local mirror written');
    assert.match(await text('message'), /Local mirror:/);
    assert.equal(await evaluate(`packet.review_states.some((s)=>s.status==='checkpointed')`), true);
    assert.match(await text('review-progress-text'), /checkpointed/);
    await screenshot('03-checkpointed');
    check('checkpoint verified + local backup');
  });

  await step('checkpoint: failure leaves new confirmations pending and says why', async () => {
    await goto('approval_date', '00193d0c');
    await evaluate(`decisions.set(KEY(records[current]),blank(records[current]));renderTarget();`);
    await input('initials', IDENTITY);
    await decide('ACCEPT');
    await saveWait();
    await control('checkpoint-fail', {fail: true});
    await evaluate(`window.confirm=()=>true;$('checkpoint').click()`);
    await waitFor(`!busy && /Checkpoint failed|Checkpoint not verified/.test($('message').textContent)`, 20000);
    const snap = await snapshot();
    assert.equal(snap.checkpoints.at(-1).verified, false);
    assert.ok(snap.checkpoints.at(-1).error_type);
    assert.equal(await evaluate(`stateOf(records[current]).status`), 'human_confirmed',
      'the failed upload leaves the new confirmation pending');
    await screenshot('02-checkpoint-failed');
    await control('checkpoint-fail', {fail: false});
  });

  // ------------------------------------------------------------ revision queue
  await step('revision queue: confirmed production fields are exported, REJECT removes them', async () => {
    await goto('jurisdiction', 'PROD');
    await evaluate(`decisions.set(KEY(records[current]),blank(records[current]));renderTarget();`);
    await input('initials', IDENTITY);
    await decide('ACCEPT');
    await saveWait();
    const queue = await (await fetch(`${portalURL}/revision-queue.json`)).json();
    const row = queue.revisions.find((r) => r.document_id === 'public-v9.3:PROD-0001');
    assert.ok(row, 'confirmed production field is queued');
    assert.equal(row.field, 'jurisdiction');
    assert.equal(row.source_revision, '7fd99b406ab1b627f43602dd6a3af5fb5b7a3ac5');
    assert.equal(row.deployment_status, 'pending v10 gates; production unchanged');
    await evaluate(`showTab('tracker')`);
    assert.match(await text('revision-queue'), /PROD-0001/);
    await evaluate(`showTab('review');$('target').value=''+records.findIndex((r)=>r.document_id.includes('PROD'));$('target').dispatchEvent(new Event('change'));`);
    await decide('REJECT');
    await saveWait();
    const after = await (await fetch(`${portalURL}/revision-queue.json`)).json();
    assert.ok(!after.revisions.some((r) => r.document_id === 'public-v9.3:PROD-0001'));
    assert.ok((await snapshot()).journal.some((e) => e.document_id === 'public-v9.3:PROD-0001' && e.decision === 'REJECT'),
      'history is kept even though the active queue dropped the field');
    check('revision queue lifecycle');
  });

  // ------------------------------------------------------------ tracker
  await step('tracker: real campaign tasks, filters, inventories and scope text', async () => {
    await evaluate(`showTab('tracker')`);
    const table = await text('tasks');
    assert.match(table, /8a/); assert.match(table, /7a/); assert.match(table, /9b/);
    assert.match(table, /blocked/);
    assert.doesNotMatch(table, /skipped/, 'the To-do filter hides finished tasks');
    assert.match(table, /inventory unavailable/i);
    assert.match(await text('tracker-scope'), /loaded finished packets only/);
    assert.match(await text('tracker-source'), /browser\/campaign\.yaml/);
    assert.match(await text('summary'), /live: \d+\/35 saved reviews/);
    await evaluate(`$('task-filter').value='completed';$('task-filter').dispatchEvent(new Event('change'));`);
    assert.match(await text('tasks'), /skipped/);
    assert.doesNotMatch(await text('tasks'), /blocked/);
    await evaluate(`$('task-filter').value='all';$('task-filter').dispatchEvent(new Event('change'));`);
    check('campaign tracker');
  });

  // ------------------------------------------------------------ launcher
  await step('launch: options, kind labels and target notes come from the campaign', async () => {
    await evaluate(`showTab('launch')`);
    await waitFor(`!$('launch-panel').hidden`);
    const groups = await evaluate(`[...document.querySelectorAll('#launch-target optgroup')].map((g)=>g.label)`);
    assert.deepEqual(groups, ['Campaign', 'Public trial']);
    assert.match(await text('launch-target'), /2a .* \(blocked\)/);
    await evaluate(`$('launch-target').value='campaign:1';$('launch-target').dispatchEvent(new Event('change'));`);
    assert.match(await text('launch-info'), /Qualification — re-labels gold documents to score models/);
    assert.equal(await evaluate(`$('launch-cap').value`), '0.30');
    await evaluate(`$('launch-target').value='trial:correspondence';$('launch-target').dispatchEvent(new Event('change'));`);
    assert.match(await text('launch-info'), /Labeling — documents that need labels/);
    assert.match(await text('launch-info'), /DeepSeek v4\.1 Flash runs at reasoning off/);
    assert.equal(await evaluate(`$('launch-trial-opts').hidden`), false);
    await screenshot('04-launch-options');
  });

  await step('launch: preflight fails loudly, clears the key and never stores it', async () => {
    const secret = 'sk-or-v1-BROWSER-E2E-SECRET-9911';
    await input('launch-initials', 'x');
    await input('launch-key', secret);
    await evaluate(`$('launch-preflight').click()`);
    await waitFor(`document.querySelector('#launch-checklist .check.fail')`);
    assert.match(await text('launch-checklist'), /FAIL.*Operator initials.*is not 2-6 capital letters.*enter your own initials/s);
    assert.equal(await evaluate(`$('launch-key').value`), '', 'key field cleared');
    assert.equal(await evaluate(`JSON.stringify([localStorage,sessionStorage]).includes(${JSON.stringify(secret)})`), false);
    assert.equal(await evaluate(`document.documentElement.outerHTML.includes(${JSON.stringify(secret)})`), false);
    assert.equal(await evaluate(`$('launch-start').disabled`), true);
    await screenshot('05-launch-fail');
  });

  await step('launch: a passing preflight starts the real trial and reloads its packet', async () => {
    const secret = 'sk-or-v1-BROWSER-E2E-SECRET-9911';
    await input('launch-initials', 'AB');
    await input('launch-key', secret);
    await evaluate(`$('launch-preflight').click()`);
    try { await waitFor(`!$('launch-start').disabled`, 30000); }
    catch (exc) {
      console.log('      launch checklist:', await text('launch-checklist'));
      console.log('      launch message:', await text('launch-message'));
      const probe = await fetch(`${portalURL}/api/launch/preflight`, {method: 'POST',
        headers: {'Content-Type': 'application/json', Origin: portalURL},
        body: JSON.stringify({target: 'trial:correspondence', initials: 'AB', cap_usd: '0.25', documents: 2, api_key: secret})});
      console.log('      server preflight:', JSON.stringify(await probe.json()).slice(0, 4000));
      throw exc;
    }
    assert.match(await text('launch-projection'), /Projection: \d+ documents, \d+ calls, about \$/);
    // Wrong typed cap is refused by the server and its failing item is rendered.
    await input('launch-key', secret); // a pasted key is wiped after preflight; Launch needs it again
    await evaluate(`window.prompt=()=>'0.20';$('launch-start').click()`);
    await waitFor(`/Not started \\(409\\)/.test($('launch-message').textContent)`, 30000);
    assert.match(await text('launch-checklist'), /Typed confirmation matches the cap/);
    await input('launch-key', secret); // a 409 clears the key field; the next preflight needs it again
    await evaluate(`$('launch-preflight').click()`);
    await waitFor(`!$('launch-start').disabled`, 30000);
    await input('launch-key', secret);
    await evaluate(`window.prompt=()=>'0.25';$('launch-start').click()`);
    await waitFor(`/^running/.test($('launch-status').textContent)`, 30000);
    await waitFor(`/^succeeded/.test($('launch-status').textContent)`, 90000);
    assert.match(await text('launch-status'), /spent \$|packet trials\//);
    let launchReceipts = (await snapshot()).launches;
    for (let i = 0; i < 50 && launchReceipts.at(-1)?.outcome !== 'succeeded'; i++) {
      await new Promise((r) => setTimeout(r, 100));
      launchReceipts = (await snapshot()).launches;
    }
    assert.equal(launchReceipts.at(-1).outcome, 'succeeded');
    assert.equal(launchReceipts.at(-1).initials, 'AB');
    assert.equal(launchReceipts.at(-1).target, 'trial:correspondence');
    assert.ok(!JSON.stringify(launchReceipts).includes(secret), 'the key never reaches a receipt');
    await waitFor(`$('message').textContent.includes('Loaded')`, 30000);
    assert.ok(await evaluate(`records.some((r)=>r.document_id.startsWith('public-v9.3:mail-'))`),
      'the new trial packet is loaded');
    await screenshot('06-launch-succeeded');
    check('end-to-end trial launch and packet reload');
  });

  // ------------------------------------------------------------ export / contract
  await step('page: loading performance and no horizontal overflow', async () => {
    const metrics = await evaluate(`(()=>{const t=performance.getEntriesByType('navigation')[0];
      return {load:Math.round(t.loadEventEnd), targets:records.length,
        width:document.documentElement.scrollWidth, viewport:window.innerWidth};})()`);
    assert.ok(metrics.load < 8000, `page load ${metrics.load}ms`);
    assert.ok(metrics.width <= metrics.viewport + 1, `page overflows: ${metrics.width} > ${metrics.viewport}`);
    await call('Emulation.setDeviceMetricsOverride', {width: 900, height: 1000, deviceScaleFactor: 1, mobile: false});
    await evaluate(`window.dispatchEvent(new Event('resize'))`);
    const narrow = await evaluate(`({width:document.documentElement.scrollWidth, viewport:window.innerWidth})`);
    assert.ok(narrow.width <= narrow.viewport + 1, `narrow layout overflows: ${narrow.width} > ${narrow.viewport}`);
    await call('Emulation.clearDeviceMetricsOverride');
    check('performance and responsive layout');
  });

  await step('resilience: a failing localStorage never blocks a save', async () => {
    await goto('effective_date', '00193d0c');
    await evaluate(`window.__setItem=Storage.prototype.setItem;Storage.prototype.setItem=function(){throw new DOMException('quota','QuotaExceededError');};`);
    await evaluate(`decisions.set(KEY(records[current]),blank(records[current]));renderTarget();`);
    await input('initials', IDENTITY);
    await decide('ACCEPT');
    assert.match(await text('message'), /Local draft storage failed|Local draft/);
    await saveWait();
    assert.equal((await snapshot()).journal.at(-1).field, 'effective_date');
    await evaluate(`Storage.prototype.setItem=window.__setItem;delete window.__setItem;`);
    check('storage failure degrades to server save');
  });

  if (process.env.AXE_PATH && fs.existsSync(process.env.AXE_PATH)) {
    await step('a11y: axe-core WCAG A/AA pass on the live review page', async () => {
      const source = fs.readFileSync(process.env.AXE_PATH, 'utf8');
      const raw = await evaluate(`${source}\n;typeof axe!=='undefined'`);
      assert.equal(raw, true, 'axe-core loaded');
      const report = await evaluate(`axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','best-practice']}}).then((r)=>({violations:r.violations.map((v)=>({id:v.id,impact:v.impact,nodes:v.nodes.length,target:v.nodes[0]&&v.nodes[0].target})),incomplete:r.incomplete.map((v)=>({id:v.id,nodes:v.nodes.length,target:v.nodes[0]&&v.nodes[0].target})),passes:r.passes.length}))`);
      fs.writeFileSync(path.join(out, 'axe.json'), JSON.stringify(report, null, 2));
      assert.deepEqual(report.violations, []);
    });
  }

  else console.log('AXE skipped: no axe-core at ' + (process.env.AXE_PATH || '(unset)'));
  assert.deepEqual(consoleErrors, [], 'no console exceptions');
} catch (exc) {
  results.push({name: 'driver', ok: false, error: String(exc && (exc.stack || exc))});
  console.log('FAIL  driver — ' + (exc && (exc.stack || exc)));
} finally {
  if (chromeReady) {
    try { await screenshot('99-final'); } catch (exc) { /* keep the report */ }
  }
  // Fixed denominator: deleting a browser step is a test change and must be
  // visible in review, not a silently smaller green run.
  const axeRan = !!(process.env.AXE_PATH && fs.existsSync(process.env.AXE_PATH));
  const expectedSteps = axeRan ? 33 : 32;
  if (results.filter((r) => r.name !== 'driver').length !== expectedSteps) {
    results.push({name: 'coverage: expected browser steps', ok: false,
      error: `expected ${expectedSteps} steps, ran ${results.filter((r) => r.name !== 'driver').length}`});
  }
  const passed = results.filter((r) => r.ok).length;
  const report = {schema: 'mailroom-review-browser-e2e/v1', portal: portalURL, root: server.root,
    passed, total: results.length, results, console_errors: consoleErrors, checks,
    evidence: out, harness_error: harnessErr.slice(-4000)};
  fs.writeFileSync(path.join(out, 'results.json'), JSON.stringify(report, null, 2));
  console.log(`\n${passed}/${results.length} browser steps passed; evidence ${out}`);
  try { ws?.close(); } catch (exc) { /* ignore */ }
  chrome.kill();
  try { await control('shutdown'); } catch (exc) { /* already gone */ }
  harness.kill();
  if (passed !== results.length) process.exitCode = 1;
}
