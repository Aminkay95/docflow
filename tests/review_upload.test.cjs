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
