const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const script = readFileSync(path.join(__dirname, '../src/trade_extract/review/static/review.js'), 'utf8');

function browser(response) {
  const listeners = [];
  const status = {textContent: '', classList: {toggle() {}}};
  const label = {textContent: 'Create job and extract'};
  const classes = new Set();
  const button = {
    disabled: false,
    dataset: {busyLabel: 'Uploading…'},
    classList: {
      toggle(name, on) {on ? classes.add(name) : classes.delete(name);},
      add(name) {classes.add(name);},
      remove(name) {classes.delete(name);},
      contains(name) {return classes.has(name);},
    },
    attributes: {},
    setAttribute(name, value) {this.attributes[name] = value;},
    removeAttribute(name) {delete this.attributes[name];},
    querySelector: selector => selector === 'span' ? label : null,
  };
  const input = {files: [{name: 'invoice.pdf', size: 100}]};
  const form = {
    dataset: {upload: ''},
    action: '/review/jobs/new?client_id=cli_example',
    hasAttribute: name => name === 'data-upload',
    querySelector: selector => ({
      'input[type="file"]': input,
      '[data-submit]': button,
      '[data-upload-status]': status,
    })[selector],
  };
  const requests = [];
  const location = {href: ''};
  vm.runInNewContext(script, {
    document: {
      addEventListener: (name, callback) => {if (name === 'submit') listeners.push(callback);},
      querySelectorAll: () => [],
      querySelector: selector => selector === '#csrf' ? {value: 'csrf-example'} : null,
    },
    location,
    crypto: {randomUUID: () => 'upload-unique-key'},
    FormData: class {constructor(value) {assert.equal(value, form);}},
    fetch: async (url, options) => {requests.push({url, options}); return response;},
  });
  let prevented = false;
  return {
    form, button, input, status, requests, location,
    label,
    submit: async () => {
      for (const callback of listeners) {
        await callback({target: form, preventDefault() {prevented = true;}});
      }
      return prevented;
    },
  };
}

test('empty data-upload attribute intercepts submission, sends CSRF and opens job', async () => {
  const page = browser({ok: true, json: async () => ({job_url: '/review/jobs/job_created'})});
  assert.equal(await page.submit(), true);
  assert.equal(page.requests.length, 1);
  assert.equal(page.requests[0].url, page.form.action);
  assert.equal(page.requests[0].options.headers['X-CSRF-Token'], 'csrf-example');
  assert.equal(page.requests[0].options.headers['Idempotency-Key'], 'upload-unique-key');
  assert.equal(page.location.href, '/review/jobs/job_created');
  assert.equal(page.button.disabled, true);
  assert.equal(page.label.textContent, 'Uploading…');
  assert.equal(page.button.attributes['aria-busy'], 'true');
});

test('invalid uploads show server error and allow retry', async () => {
  const page = browser({ok: false, status: 422, json: async () => ({error: {message: 'Invalid document'}})});
  await page.submit();
  assert.equal(page.status.textContent, 'Invalid document');
  assert.equal(page.button.disabled, false);
  assert.equal(page.label.textContent, 'Create job and extract');
  assert.equal(page.button.attributes['aria-busy'], undefined);
  assert.equal(page.location.href, '');
  await page.submit();
  assert.equal(page.requests[1].options.headers['Idempotency-Key'], page.requests[0].options.headers['Idempotency-Key']);
});

test('empty selection does not submit', async () => {
  const page = browser();
  page.input.files = [];
  await page.submit();
  assert.equal(page.requests.length, 0);
  assert.match(page.status.textContent, /Choose at least one document/);
});

function finishBrowser(response) {
  let click;
  const status = {textContent: ''};
  const preview = {textContent: ''};
  const button = {dataset: {finishJob: '/review/jobs/job_done/done'}, disabled: false};
  const requests = [];
  vm.runInNewContext(script, {
    document: {
      addEventListener: (name, callback) => {if (name === 'click' && !click) click = callback;},
      querySelectorAll: () => [],
      querySelector: selector => ({
        '#csrf': {value: 'review-csrf'},
        '[data-finish-status]': status,
        '[data-result-json]': preview,
      })[selector] || null,
    },
    fetch: async (url, options) => {requests.push({url, options}); return response;},
  });
  return {button, status, preview, requests, click: () => click({target: {closest: () => button}})};
}

test('Done sends CSRF and displays returned reviewed JSON', async () => {
  const result = {job_id: 'job_done', status: 'completed', documents: []};
  const page = finishBrowser({ok: true, json: async () => result});
  await page.click();
  assert.equal(page.requests[0].url, '/review/jobs/job_done/done');
  assert.equal(page.requests[0].options.headers['X-CSRF-Token'], 'review-csrf');
  assert.deepEqual(JSON.parse(page.preview.textContent), result);
  assert.match(page.status.textContent, /sent to the API terminal/);
  assert.equal(page.button.disabled, true);
});

test('Done shows unfinished-review errors and allows retry', async () => {
  const page = finishBrowser({ok: false, json: async () => ({detail: 'Resolve all reviews first.'})});
  await page.click();
  assert.equal(page.status.textContent, 'Resolve all reviews first.');
  assert.equal(page.button.disabled, false);
  assert.equal(page.preview.textContent, '');
});


function workspaceBrowser(box = null, confirm = () => false) {
  const listeners = {};
  const timers = [];
  const requests = [];
  const overlay = {hidden: true, style: {}};
  const imageEvents = {};
  const error = {hidden: true};
  const figure = {id: 'page-0', querySelector: selector => selector === '.bbox' ? overlay : error};
  const image = {hidden: false, addEventListener: (type, callback) => imageEvents[type] = callback,
    closest: () => figure};
  const workspace = {dataset: {documentId: 'doc_selected', pageStart: '0', pageEnd: '2'}};
  const status = {textContent: ''};
  const label = {textContent: ''};
  const location = {href: 'http://localhost/review/jobs/job_test?document_id=doc_selected&page=0',
    reloads: 0, reload() {this.reloads++;}};
  const window = {confirm, setTimeout: callback => timers.push(callback),
    addEventListener: (type, callback) => listeners[type] = [callback]};
  vm.runInNewContext(script, {
    URL, location, window,
    FormData: class {constructor(form) {return Object.entries(form.values);}},
    fetch: async (url, options) => {requests.push({url, options}); return {ok: true};},
    history: {replaceState: (_, title, url) => location.href = String(url)},
    document: {
      addEventListener(type, callback) {(listeners[type] ||= []).push(callback);},
      querySelector: selector => ({'[data-workspace]': workspace, '[data-source-page]': figure,
        '[data-source-image]': image, '[data-media-error]': error,
        '[data-evidence-status]': status, '[data-page-label]': label,
        '[data-autorefresh]': {dataset: {autorefresh: '4000'}}})[selector] ||
        (selector === '#page-2' ? figure : null),
      querySelectorAll: selector => selector === '.page-image img' ? [image] :
        selector.startsWith('.bbox') ? [overlay] : [],
    },
  });
  const field = {dataset: {page: '2', box: JSON.stringify(box)},
    matches: selector => selector === '[data-box]', closest: () => field};
  return {listeners, timers, overlay, image, imageEvents, error, location, status, label, field, requests};
}

test('keyboard field selection retains document, changes page and highlights evidence', () => {
  const page = workspaceBrowser({x0: .1, y0: .2, x1: .4, y1: .6});
  for (const listener of page.listeners.keydown) listener({key: 'Enter', target: page.field, preventDefault() {}});
  assert.match(page.location.href, /document_id=doc_selected&page=2/);
  assert.equal(page.image.src, '/review/media/documents/doc_selected/pages/2');
  assert.equal(page.overlay.hidden, false);
  assert.equal(page.overlay.style.left, '10%');
  assert.equal(page.label.textContent, 'Page 3');
});

test('missing boxes and expired images remain readable', () => {
  const page = workspaceBrowser();
  for (const listener of page.listeners.focusin) listener({target: page.field});
  assert.match(page.status.textContent, /No bounding box/);
  assert.equal(page.overlay.hidden, true);
  page.imageEvents.error();
  assert.equal(page.error.hidden, false);
  assert.equal(page.image.hidden, true);
});

test('unsaved correction suspends refresh and warns before navigation', () => {
  const page = workspaceBrowser();
  for (const listener of page.listeners.input) listener({target: {closest: () => ({})}});
  page.timers.shift()();
  assert.equal(page.location.reloads, 0);
  assert.equal(page.timers.length, 1);
  let prevented = false;
  const event = {preventDefault() {prevented = true;}};
  page.listeners.beforeunload[0](event);
  assert.equal(prevented, true);
  assert.equal(event.returnValue, '');
});


test('saving one correction requires consent to discard another dirty form', async () => {
  const page = workspaceBrowser();
  const form = {dataset: {resolve: '/resolve'}, values: {outcome: 'confirmed', reason: 'Checked'},
    querySelector: () => ({disabled: false}), hasAttribute: () => false};
  const other = {};
  for (const dirty of [form, other]) {
    for (const listener of page.listeners.input) listener({target: {closest: () => dirty}});
  }
  for (const listener of page.listeners.submit) await listener({target: form, preventDefault() {}});
  assert.equal(page.requests.length, 0);
  assert.equal(page.location.reloads, 0);
  const approved = workspaceBrowser(null, () => true);
  for (const dirty of [form, other]) {
    for (const listener of approved.listeners.input) listener({target: {closest: () => dirty}});
  }
  for (const listener of approved.listeners.submit) await listener({target: form, preventDefault() {}});
  assert.equal(approved.requests.length, 1);
  assert.equal(approved.location.reloads, 1);
});

test('canceling document switch restores the currently displayed selection', () => {
  const page = workspaceBrowser();
  for (const listener of page.listeners.input) listener({target: {closest: () => ({})}});
  const picker = {value: 'doc_other', matches: () => true, closest: () => null};
  for (const listener of page.listeners.change) listener({target: picker});
  assert.equal(picker.value, 'doc_selected');
  assert.match(page.location.href, /document_id=doc_selected/);
});


function progressBrowser(fetcher, initialProcessing = true) {
  const listeners = {};
  const timers = new Map();
  let nextTimer = 0;
  const elements = {};
  const node = () => ({textContent: '', hidden: false, dataset: {}, children: [], listeners: {},
    addEventListener(type, callback) {this.listeners[type] = callback;},
    append(...items) {this.children.push(...items);},
    replaceChildren(...items) {this.children = items;}});
  const panel = {dataset: {jobProgress: '/review/jobs/job_test/progress', progressVersion: 'initial',
    processing: String(initialProcessing), startedAt: new Date().toISOString(), terminalAt: ''},
    open: true, querySelector: selector => elements[selector] ||= node()};
  const location = {href: 'http://localhost/review/jobs/job_test?document_id=doc_selected&page=2&tab=items',
    reloads: 0, reload() {this.reloads++;}};
  const workspaceTarget = {focuses: 0, scrolls: 0,
    focus() {this.focuses++;}, scrollIntoView() {this.scrolls++;}};
  const document = {
    hidden: false, createElement: node,
    addEventListener(type, callback) {(listeners[type] ||= []).push(callback);},
    querySelector: selector => selector === '[data-job-progress]' ? panel : selector === '#job-review-workspace' ? workspaceTarget : null,
    querySelectorAll: () => [],
  };
  const window = {setTimeout(callback, delay) {timers.set(++nextTimer, {callback, delay}); return nextTimer;},
    clearTimeout(id) {timers.delete(id);},
    addEventListener(type, callback) {(listeners[type] ||= []).push(callback);}};
  const navigator = {onLine: true};
  vm.runInNewContext(script, {document, window, location, navigator, AbortController, URL,
    fetch: fetcher});
  return {elements, location, panel, timers, listeners, document, navigator, workspaceTarget,
    async poll() {
      const entry = [...timers].find(([, task]) => task.delay >= 4000 && task.delay < 12000);
      assert.ok(entry, 'poll scheduled');
      timers.delete(entry[0]);
      await entry[1].callback();
    },
    dirty(value = 'unsaved correction') {
      const form = {value};
      for (const listener of listeners.input) listener({target: {closest: () => form}});
      return form;
    },
    fire(type) {for (const listener of listeners[type] || []) listener({});},
  };
}

const progressResult = overrides => ({
  processing: true, state: 'processing', title: 'Extracting fields', updated_at: new Date().toISOString(),
  terminal_at: null, documents_ready: 0, documents: 1, fields: 0, reviews: 0,
  files: [{name: 'invoice.pdf', status: 'processing'}],
  stages: [{label: 'Extracting fields', status: 'active', at: new Date().toISOString()}],
  version: 'initial', ...overrides,
});

test('progress updates stages while retaining dirty fields, line items and selection', async () => {
  const page = progressBrowser(async () => ({ok: true, json: async () => progressResult({
    processing: false, state: 'review', title: 'Ready for review', version: 'terminal',
    documents_ready: 1, fields: 4, reviews: 1,
  })}));
  const scalar = page.dirty('corrected invoice number');
  const lineItem = page.dirty('corrected line item amount');
  await page.poll();
  assert.equal(page.elements['[data-progress-title]'].textContent, 'Ready for review');
  assert.equal(page.elements['[data-progress-draft]'].hidden, false);
  assert.equal(page.location.reloads, 0);
  assert.equal(scalar.value, 'corrected invoice number');
  assert.equal(lineItem.value, 'corrected line item amount');
  assert.match(page.location.href, /document_id=doc_selected&page=2&tab=items/);
  assert.equal([...page.timers.values()].filter(task => task.delay >= 4000).length, 0);
});

test('terminal progress safely refreshes clean workspace with its current URL context', async () => {
  const page = progressBrowser(async () => ({ok: true, json: async () => progressResult({
    processing: false, state: 'complete', version: 'complete',
  })}));
  await page.poll();
  assert.equal(page.location.reloads, 1);
  assert.match(page.location.href, /document_id=doc_selected&page=2&tab=items/);
});

test('poll failure retains processing and retries without reporting extraction failure', async () => {
  let calls = 0;
  const page = progressBrowser(async () => {calls++; throw new Error('offline');});
  await page.poll();
  assert.equal(calls, 1);
  assert.match(page.elements['[data-progress-connection]'].textContent, /last known state/);
  assert.equal(page.location.reloads, 0);
  assert.equal(page.panel.dataset.processing, 'true');
  assert.ok([...page.timers.values()].some(task => task.delay === 8000));
});

test('hidden and offline pages pause polling and reconnect without overlap', async () => {
  let calls = 0;
  let complete;
  const page = progressBrowser(() => {calls++; return new Promise(resolve => {complete = resolve;});});
  page.document.hidden = true;
  page.fire('visibilitychange');
  assert.equal([...page.timers.values()].filter(task => task.delay >= 4000).length, 0);
  page.document.hidden = false;
  page.fire('visibilitychange');
  const request = page.poll();
  page.fire('online');
  assert.equal(calls, 1);
  complete({ok: true, json: async () => progressResult()});
  await request;
  page.navigator.onLine = false;
  page.fire('offline');
  assert.match(page.elements['[data-progress-connection]'].textContent, /offline/);
  assert.equal([...page.timers.values()].filter(task => task.delay >= 4000).length, 0);
  page.navigator.onLine = true;
  page.fire('online');
  assert.ok([...page.timers.values()].some(task => task.delay === 4000));
});

test('expired session stops updates and leaves drafts available', async () => {
  const page = progressBrowser(async () => ({status: 401, ok: false}));
  const form = page.dirty();
  await page.poll();
  assert.match(page.elements['[data-progress-connection]'].textContent, /Sign in again/);
  assert.equal(form.value, 'unsaved correction');
  assert.equal(page.location.reloads, 0);
  assert.equal([...page.timers.values()].filter(task => task.delay >= 4000).length, 0);
});

test('stalled progress request is aborted at the bounded timeout', async () => {
  let signal;
  const page = progressBrowser((url, options) => {
    signal = options.signal;
    return new Promise((resolve, reject) => signal.addEventListener('abort', () => reject(new Error('timeout'))));
  });
  const pending = page.poll();
  const timeout = [...page.timers.values()].find(task => task.delay === 12000);
  assert.ok(timeout);
  timeout.callback();
  await pending;
  assert.equal(signal.aborted, true);
  assert.match(page.elements['[data-progress-connection]'].textContent, /reconnecting/);
});

test('initial terminal jobs do not schedule polling', () => {
  const page = progressBrowser(() => {throw new Error('unexpected fetch');}, false);
  assert.equal(page.timers.size, 0);
});

test('review action focuses the existing workspace without navigation or discarding drafts', async () => {
  const page = progressBrowser(async () => ({ok: true, json: async () => progressResult({
    processing: false, state: 'review', version: 'terminal',
    stages: [{label: 'Reading pages', status: 'complete'}, {label: 'Extracting fields', status: 'complete'}],
  })}));
  const correction = page.dirty('retained correction');
  const originalUrl = page.location.href;
  await page.poll();
  const action = page.elements['[data-progress-review]'];
  assert.equal(action.hidden, false);
  assert.equal(page.elements['[data-progress-history]'].open, false);
  action.listeners.click();
  assert.equal(page.workspaceTarget.focuses, 1);
  assert.equal(page.workspaceTarget.scrolls, 1);
  assert.equal(correction.value, 'retained correction');
  assert.equal(page.location.href, originalUrl);
  assert.equal(page.location.reloads, 0);
  assert.match(page.elements['[data-progress-stages]'].children[0].children[1].textContent, /✓ Completed/);
});

test('in-flight offline abort preserves the offline message and resumes on reconnect', async () => {
  const page = progressBrowser((url, options) => new Promise((resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new Error('intentional abort')));
  }));
  const request = page.poll();
  page.navigator.onLine = false;
  page.fire('offline');
  await request;
  assert.match(page.elements['[data-progress-connection]'].textContent, /^You are offline/);
  assert.equal([...page.timers.values()].filter(task => task.delay >= 4000).length, 0);
  page.navigator.onLine = true;
  page.fire('online');
  assert.ok([...page.timers.values()].some(task => task.delay === 4000));
});

test('pending sibling history uses partial completion wording and latest upload timer', async () => {
  const latest = new Date(Date.now() - 65000).toISOString();
  const page = progressBrowser(async () => ({ok: true, json: async () => progressResult({
    started_at: latest, stages: [{label: 'Extracting fields', status: 'partial'}],
    documents_ready: 1, documents: 2,
  })}));
  await page.poll();
  assert.match(page.elements['[data-progress-stages]'].children[0].children[1].textContent,
    /Completed in earlier work/);
  assert.match(page.elements['[data-progress-counts]'].textContent, /1 of 2/);
  assert.match(page.elements['[data-progress-elapsed]'].textContent, /Elapsed since latest upload: 1m/);
  assert.equal(page.location.reloads, 0);
});
