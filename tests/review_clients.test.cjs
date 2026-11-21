const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const script = readFileSync(path.join(__dirname, '../src/trade_extract/review/static/review.js'), 'utf8');

function browser(response, kind = 'create', options = {}) {
  const events = {};
  function makeForm(formKind) {
    let submit;
    const button = {disabled: false};
    const error = {textContent: ''};
    const fields = new Map([['name', 'Acme'], ['slug', 'acme'], ['scopes', 'results:read']]);
    const form = {
      hidden: false, dataset: {clientForm: formKind, ...(options.confirm ? {confirm: 'Confirm?'} : {})},
      action: '/review/clients',
      querySelector: selector => selector === '.client-form-error' ? error : button,
      addEventListener: (name, callback) => submit = callback,
    };
    return {form, fields, button, error, submit: () => submit({preventDefault() {}})};
  }
  const first = makeForm(kind);
  const other = makeForm(options.otherKind || 'rotate');
  const values = {children: [], textContent: '', appendChild(node) {this.children.push(node);}};
  const panel = {hidden: true, focus() {}, scrollIntoView() {}};
  const next = {href: ''};
  const requests = [];
  const location = {pathname: '/review/clients/new', replacements: [],
    assign(url) {this.pathname = url;}, replace(url) {this.replacements.push(url);}};
  vm.runInNewContext(script, {
    document: {
      addEventListener() {},
      querySelectorAll: selector => selector === '[data-client-form]' ? [first.form, other.form] : [],
      querySelector: selector => ({'#csrf': {value: 'csrf'}, '#client-credential-values': values,
        '#client-credentials': panel, '#client-credentials-continue': next})[selector] || null,
      createElement: () => ({textContent: ''}),
    },
    window: {confirm: options.confirm,
      addEventListener: (name, callback) => (events[name] ||= []).push(callback)},
    history: {replaceState: (state, title, url) => location.pathname = url},
    location,
    FormData: class {
      constructor(form) {this.fields = form === first.form ? first.fields : other.fields;}
      entries() {return Array.from(this.fields, ([key, value]) => [key, Array.isArray(value) ? value[0] : value])[Symbol.iterator]();}
      getAll(key) {return this.fields.has(key) ? [].concat(this.fields.get(key)) : [];}
      get(key) {return this.fields.get(key);}
      has(key) {return this.fields.has(key);}
    },
    fetch: async (url, requestOptions) => {requests.push({url, options: requestOptions}); return response;},
  });
  return {...first, other, events, values, panel, next, requests, location,
    dispatch: (name, event = {}) => (events[name] || []).forEach(callback => callback(event))};
}

test('successful onboarding displays credentials only in memory and refreshes to detail URL', async () => {
  const page = browser({ok: true, json: async () => ({api_key: 'tx_secret', webhook_secret: 'whsec_secret', client_url: '/review/clients/cli_acme'})});
  await page.submit();
  assert.equal(page.panel.hidden, false);
  assert.equal(page.form.hidden, true);
  assert.equal(page.location.pathname, '/review/clients/cli_acme');
  assert.equal(page.next.href, '/review/clients/cli_acme');
  assert.equal(page.values.children[1].textContent, 'tx_secret');
  assert.equal(page.values.children[3].textContent, 'whsec_secret');
  assert.equal(page.requests[0].options.headers['X-CSRF-Token'], 'csrf');
  assert.equal(page.requests[0].options.cache, 'no-store');
  page.dispatch('pagehide');
  assert.equal(page.panel.hidden, true);
  assert.equal(page.values.textContent, '');
});

test('validation failure preserves nonsecret fields, shows server message and permits retry', async () => {
  const page = browser({ok: false, json: async () => ({error: {message: 'Slug already exists'}})});
  await page.submit();
  assert.equal(page.error.textContent, 'Slug already exists');
  assert.equal(page.fields.get('name'), 'Acme');
  assert.equal(page.form.hidden, false);
  assert.equal(page.panel.hidden, true);
  assert.equal(page.button.disabled, false);
});


test('displayed credentials block every further management form until Continue', async () => {
  const page = browser({ok: true, json: async () => ({api_key: 'first-secret'})}, 'key');
  await page.submit();
  await page.other.submit();
  await page.submit();
  assert.equal(page.requests.length, 1);
  assert.equal(page.values.children[1].textContent, 'first-secret');
  assert.equal(page.panel.hidden, false);
  assert.match(page.other.error.textContent, /Continue/);
});

test('cached onboarding restoration reloads its canonical detail page after clearing secrets', async () => {
  const page = browser({ok: true, json: async () => ({api_key: 'secret', client_url: '/review/clients/cli_acme'})});
  await page.submit();
  page.dispatch('pagehide');
  page.dispatch('pageshow', {persisted: false});
  assert.equal(page.location.replacements.length, 0);
  page.dispatch('pageshow', {persisted: true});
  assert.deepEqual(page.location.replacements, ['/review/clients/cli_acme']);
  assert.equal(page.values.textContent, '');
  assert.equal(page.panel.hidden, true);
});

test('confirmation locks submission before awaiting and cancellation releases the lock', async () => {
  let decide;
  let confirmations = 0;
  const page = browser({ok: true, json: async () => ({saved: true})}, 'revoke', {
    confirm: () => {confirmations++; return new Promise(resolve => decide = resolve);},
  });
  const firstSubmit = page.submit();
  await page.submit();
  await page.other.submit();
  assert.equal(confirmations, 1);
  assert.equal(page.requests.length, 0);
  assert.equal(page.button.disabled, true);
  decide(false);
  await firstSubmit;
  assert.equal(page.button.disabled, false);
  const secondSubmit = page.submit();
  assert.equal(confirmations, 2);
  decide(true);
  await secondSubmit;
  assert.equal(page.requests.length, 1);
});

test('key submission sends selected scopes and converts local expiry to an ISO timestamp', async () => {
  const page = browser({ok: true, json: async () => ({api_key: 'secret'})}, 'key');
  page.fields.delete('slug');
  page.fields.set('scopes', ['results:read', 'extract:write']);
  page.fields.set('expires_at', '2030-06-01T14:30');
  await page.submit();
  assert.deepEqual(JSON.parse(page.requests[0].options.body), {
    name: 'Acme', scopes: ['results:read', 'extract:write'],
    expires_at: new Date('2030-06-01T14:30').toISOString(),
  });
});

for (const checked of [true, false]) {
  test(`webhook enabled conversion sends ${checked}`, async () => {
    const page = browser({ok: true, json: async () => ({saved: true})}, 'webhook');
    page.fields.clear();
    page.fields.set('webhook_url', 'https://example.test/hook');
    if (checked) page.fields.set('enabled', 'on');
    await page.submit();
    assert.deepEqual(JSON.parse(page.requests[0].options.body), {
      webhook_url: 'https://example.test/hook', enabled: checked,
    });
  });
  test(`status confirmed conversion sends ${checked}`, async () => {
    const page = browser({ok: true, json: async () => ({saved: true})}, 'status');
    page.fields.clear();
    page.fields.set('status', 'terminated');
    if (checked) page.fields.set('confirmed', 'on');
    await page.submit();
    assert.deepEqual(JSON.parse(page.requests[0].options.body), {status: 'terminated', confirmed: checked});
  });
}

for (const kind of ['revoke', 'rotate']) {
  test(`${kind} sends an empty JSON object`, async () => {
    const page = browser({ok: true, json: async () => ({saved: true})}, kind);
    page.fields.clear();
    await page.submit();
    assert.deepEqual(JSON.parse(page.requests[0].options.body), {});
  });
}
