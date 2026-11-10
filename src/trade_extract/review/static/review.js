/* Trade Extract review console.
 *
 * Vanilla, dependency-free behaviour for the server-rendered shell. Every
 * query is defensive because the integration tests run this file inside a
 * minimal DOM mock rather than a full browser.
 */

const dirtyReviewForms = new Set();

/* ---------- Shared helpers ---------- */

function query(selector) {
  return typeof document.querySelector === 'function' ? document.querySelector(selector) : null;
}

function queryAll(selector) {
  return typeof document.querySelectorAll === 'function' ? document.querySelectorAll(selector) : [];
}

function feedback(message, type = 'info') {
  const node = query('#feedback');
  if (!node) return;
  node.textContent = message;
  if (node.dataset) node.dataset.type = message ? type : '';
}

function csrfToken() {
  const node = query('#csrf');
  return node ? node.value : '';
}

function setBusy(button, busy, label) {
  if (!button) return;
  button.disabled = busy;
  if (button.classList && typeof button.classList.toggle === 'function') {
    button.classList.toggle('is-busy', busy);
  }
  if (busy) {
    if (typeof button.setAttribute === 'function') button.setAttribute('aria-busy', 'true');
    if (label) swapLabel(button, label);
  } else {
    if (typeof button.removeAttribute === 'function') button.removeAttribute('aria-busy');
    restoreLabel(button);
  }
}

/* Busy labels live on the button's own <span> so the icon and layout stay put.
 * The original text is kept per button and restored if the request fails. */
const busyLabels = new WeakMap();

function labelNode(button) {
  if (typeof button.querySelector !== 'function') return null;
  const node = button.querySelector('span');
  if (node && typeof node.textContent === 'string') return node;
  /* Some compact actions (Claim/Release) have bare text instead of a <span>.
   * Wrap just the text nodes so the label can change while an icon, if any,
   * keeps its place. */
  if (typeof document === 'undefined' || typeof document.createElement !== 'function') return null;
  if (!button.childNodes) return null;
  const text = typeof button.textContent === 'string' ? button.textContent.trim() : '';
  if (!text) return null;
  const span = document.createElement('span');
  span.textContent = text;
  Array.prototype.slice.call(button.childNodes).forEach(child => {
    if (child.nodeType === 3) button.removeChild(child);
  });
  button.appendChild(span);
  return span;
}

function swapLabel(button, label) {
  const node = labelNode(button);
  if (!node) return;
  if (!busyLabels.has(button)) busyLabels.set(button, node.textContent);
  node.textContent = label;
}

function restoreLabel(button) {
  if (!busyLabels.has(button)) return;
  const node = labelNode(button);
  if (node) node.textContent = busyLabels.get(button);
  busyLabels.delete(button);
}

function busyLabel(button, fallback) {
  if (button && button.dataset && button.dataset.busyLabel) return button.dataset.busyLabel;
  return fallback;
}

/* Abort long requests so a stalled connection cannot leave a button disabled
 * forever. The JS test harness has neither AbortController nor setTimeout, so
 * fall back to plain fetch there. */
const REQUEST_TIMEOUT_MS = 30000;

function fetchWithTimeout(url, options) {
  if (typeof AbortController === 'undefined' || typeof setTimeout !== 'function') {
    return fetch(url, options);
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  return fetch(url, {...options, signal: controller.signal}).finally(() => clearTimeout(timer));
}

function timedOut(error) {
  return Boolean(error && error.name === 'AbortError');
}

async function send(url, payload) {
  let response;
  try {
    response = await fetchWithTimeout(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': csrfToken(),
      },
      ...(payload ? {body: JSON.stringify(payload)} : {}),
    });
  } catch (error) {
    feedback(
      timedOut(error)
        ? 'The request took too long. Try again.'
        : 'Network error. Check your connection and try again.',
      'error',
    );
    return false;
  }
  if (!response.ok) {
    let detail = '';
    try {
      const body = await response.json();
      detail = typeof body.detail === 'string' ? ` ${body.detail}` : '';
    } catch (error) {
      detail = '';
    }
    feedback(`Request failed (${response.status}).${detail} Reload if the item changed.`, 'error');
    return false;
  }
  feedback('');
  return true;
}

/* ---------- Confirmation dialog ----------
 *
 * Prefer the shell's native <dialog>. When it is unavailable (including the
 * JS test harness) fall back to window.confirm so behaviour stays synchronous
 * for callers that need it.
 */

function confirmAction(message) {
  const dialog = query('#confirm-dialog');
  if (!dialog || typeof dialog.showModal !== 'function') {
    return typeof window !== 'undefined' && typeof window.confirm === 'function'
      ? window.confirm(message)
      : true;
  }

  const body = dialog.querySelector('#confirm-message');
  if (body) body.textContent = message;

  return new Promise(resolve => {
    let settled = false;
    const finish = value => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    const onClose = () => {
      cleanup();
      finish(dialog.returnValue === 'confirm');
    };
    const onCancel = event => {
      if (event.key === 'Escape') {
        event.preventDefault();
        dialog.close('cancel');
      }
    };
    const cleanup = () => {
      if (typeof dialog.removeEventListener === 'function') {
        dialog.removeEventListener('close', onClose);
        dialog.removeEventListener('cancel', onCancel);
      }
    };

    if (typeof dialog.addEventListener === 'function') {
      dialog.addEventListener('close', onClose, {once: true});
      dialog.addEventListener('cancel', onCancel);
    }
    try {
      dialog.showModal();
    } catch (error) {
      cleanup();
      finish(
        typeof window !== 'undefined' && typeof window.confirm === 'function'
          ? window.confirm(message)
          : true,
      );
    }
  });
}

/* ---------- Navigation feedback ---------- */

function startNavProgress() {
  const bar = query('[data-nav-progress]');
  if (!bar || !bar.classList) return;
  bar.classList.add('is-active');
  if (typeof bar.setAttribute === 'function') bar.setAttribute('aria-hidden', 'false');
}

function stopNavProgress() {
  const bar = query('[data-nav-progress]');
  if (!bar || !bar.classList) return;
  bar.classList.remove('is-active');
  if (typeof bar.setAttribute === 'function') bar.setAttribute('aria-hidden', 'true');
}

/* ---------- Sidebar ---------- */

const SIDEBAR_KEY = 'te.review.sidebar-collapsed';

function readSidebarPreference() {
  try {
    return typeof window !== 'undefined' && window.localStorage
      ? window.localStorage.getItem(SIDEBAR_KEY) === '1'
      : false;
  } catch (error) {
    return false;
  }
}

function writeSidebarPreference(collapsed) {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      window.localStorage.setItem(SIDEBAR_KEY, collapsed ? '1' : '0');
    }
  } catch (error) {
    /* Storage can be unavailable in private browsing; the toggle still works. */
  }
}

function sidebarCollapsed() {
  return typeof document.body !== 'undefined' && document.body
    ? document.body.classList.contains('sidebar-collapsed')
    : false;
}

function mobileSidebarOpen() {
  return typeof document.body !== 'undefined' && document.body
    ? document.body.classList.contains('sidebar-mobile-open')
    : false;
}

function syncSidebarControls() {
  const collapse = query('[data-sidebar-collapse]');
  if (collapse && typeof collapse.setAttribute === 'function') {
    const expanded = !sidebarCollapsed();
    collapse.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    collapse.setAttribute('title', expanded ? 'Collapse sidebar' : 'Expand sidebar');
    collapse.setAttribute(
      'aria-label',
      expanded ? 'Collapse sidebar' : 'Expand sidebar',
    );
  }
  const open = query('[data-sidebar-open]');
  if (open && typeof open.setAttribute === 'function') {
    open.setAttribute('aria-expanded', mobileSidebarOpen() ? 'true' : 'false');
  }
}

function applySidebarPreference() {
  if (typeof document.body === 'undefined' || !document.body) return;
  document.body.classList.toggle('sidebar-collapsed', readSidebarPreference());
  syncSidebarControls();
}

function closeMobileSidebar() {
  if (typeof document.body === 'undefined' || !document.body) return;
  document.body.classList.remove('sidebar-mobile-open');
  syncSidebarControls();
}

function toggleSidebar() {
  if (typeof document.body === 'undefined' || !document.body) return;
  const collapsed = document.body.classList.toggle('sidebar-collapsed');
  writeSidebarPreference(collapsed);
  syncSidebarControls();
}

applySidebarPreference();

/* ---------- Menus ---------- */

function closeMenus(except) {
  queryAll('details[data-menu][open]').forEach(menu => {
    if (menu !== except) menu.open = false;
  });
}

document.addEventListener('keydown', event => {
  if (event.key !== 'Escape') return;
  closeMenus();
  closeMobileSidebar();
});

/* ---------- Shell controls ---------- */

function handleMenuClick(event) {
  const target = event.target;
  if (!target || typeof target.closest !== 'function') return;
  const menu = target.closest('details[data-menu]');
  if (!menu) {
    closeMenus();
    return;
  }
  const actionable = target.closest('a[href], button');
  if (actionable) closeMenus();
  else closeMenus(menu);
}

function handleShellClick(event) {
  const target = event.target;
  if (!target || typeof target.closest !== 'function') return;
  if (target.closest('[data-sidebar-collapse]')) {
    toggleSidebar();
    return;
  }
  if (target.closest('[data-sidebar-open]')) {
    if (typeof document.body !== 'undefined' && document.body) {
      document.body.classList.add('sidebar-mobile-open');
      syncSidebarControls();
    }
    return;
  }
  if (target.closest('[data-sidebar-close]')) {
    closeMobileSidebar();
    return;
  }
  if (target.closest('[data-nav-item]') && mobileSidebarOpen()) closeMobileSidebar();
}

/* Keyboard shortcut: focus global search. */
document.addEventListener('keydown', event => {
  if (!(event.ctrlKey || event.metaKey) || !event.key) return;
  if (event.key.toLowerCase() !== 'k') return;
  const input = query('[data-global-search]');
  if (!input || typeof input.focus !== 'function') return;
  event.preventDefault();
  input.focus();
  if (typeof input.select === 'function') input.select();
});

/* Platform hint for the search shortcut. */
const searchKbd = query('[data-search-kbd]');
if (searchKbd && typeof navigator !== 'undefined') {
  const platform = `${navigator.platform || ''} ${navigator.userAgent || ''}`;
  if (/mac/i.test(platform)) searchKbd.textContent = '⌘ K';
}

/* ---------- Review resolution ---------- */

document.addEventListener('submit', async event => {
  const form = event.target;
  if (!form.dataset || !form.dataset.resolve) return;
  event.preventDefault();
  if (
    [...dirtyReviewForms].some(dirty => dirty !== form) &&
    !(await confirmAction('Saving will discard other unsaved corrections. Continue?'))
  ) {
    return;
  }
  const data = Object.fromEntries(new FormData(form));
  if (data.outcome === 'corrected') {
    data.corrected_value = JSON.parse(form.dataset.field);
    data.corrected_value.value = data.value;
    data.corrected_value.agreement = 'human_corrected';
    data.corrected_value.alternatives = [];
  }
  delete data.value;
  if (data.outcome !== 'accepted') delete data.chosen_document_id;
  const button = form.querySelector('button');
  setBusy(button, true, busyLabel(button, 'Saving…'));
  if (await send(form.dataset.resolve, data)) {
    dirtyReviewForms.clear();
    location.reload();
  } else {
    setBusy(button, false);
  }
});

/* ---------- Global action buttons ---------- */

document.addEventListener('click', async event => {
  const button = event.target.closest('button');
  if (!button) return;

  if (button.dataset.confirmRemaining) {
    if (
      button.dataset.confirmDialog &&
      !(await confirmAction(button.dataset.confirmDialog))
    ) {
      return;
    }
    if (dirtyReviewForms.size && !(await confirmAction('Discard unsaved corrections?'))) {
      return;
    }
    setBusy(button, true, busyLabel(button, 'Confirming…'));
    if (await send(button.dataset.confirmRemaining)) {
      dirtyReviewForms.clear();
      location.reload();
    } else {
      setBusy(button, false);
    }
    return;
  }

  if (button.dataset.finishJob) {
    const status = query('[data-finish-status]');
    setBusy(button, true, busyLabel(button, 'Sending…'));
    if (status) status.textContent = 'Sending reviewed result to the terminal…';
    try {
      const response = await fetchWithTimeout(button.dataset.finishJob, {
        method: 'POST',
        headers: {'X-CSRF-Token': csrfToken()},
      });
      let body = {};
      try {
        body = await response.json();
      } catch (error) {
        body = {};
      }
      if (!response.ok) {
        throw new Error(
          typeof body.detail === 'string'
            ? body.detail
            : 'Could not send the result. Reload and try again.',
        );
      }
      const preview = query('[data-result-json]');
      if (preview) preview.textContent = JSON.stringify(body, null, 2);
      if (status) status.textContent = 'Reviewed JSON sent to the API terminal.';
      if (button.classList && typeof button.classList.remove === 'function') {
        button.classList.remove('is-busy');
      }
      const label = typeof button.querySelector === 'function'
        ? button.querySelector('span')
        : null;
      if (label) label.textContent = 'Done — sent to terminal';
      else button.textContent = 'Done — sent to terminal';
      button.disabled = true;
      feedback('Reviewed JSON sent to the API terminal.', 'success');
    } catch (error) {
      const message = timedOut(error)
        ? 'The request took too long. Try again.'
        : error.message || 'Network error. Try again.';
      if (status) status.textContent = message;
      feedback(message, 'error');
      setBusy(button, false);
    }
    return;
  }

  if (button.dataset.post) {
    if (
      button.dataset.confirmDialog &&
      !(await confirmAction(button.dataset.confirmDialog))
    ) {
      return;
    }
    setBusy(button, true, busyLabel(button, 'Signing out…'));
    if (await send(button.dataset.post)) {
      startNavProgress();
      location.href = '/review/login';
    } else {
      setBusy(button, false);
    }
    return;
  }

  if (button.dataset.action) {
    setBusy(button, true, busyLabel(button, 'Working…'));
    if (await send(button.dataset.action)) {
      location.reload();
    } else {
      setBusy(button, false);
    }
  }
});

/* Shell listeners are registered after the action handler so global actions
 * always receive the event first. */
document.addEventListener('click', handleMenuClick);
document.addEventListener('click', handleShellClick);

/* ---------- Job creation ---------- */

function setUploadStatus(form, message, state) {
  const node = form.querySelector('[data-upload-status]');
  if (!node) return;
  node.textContent = message;
  node.classList.toggle('is-error', state === 'error');
  node.classList.toggle('is-busy', state === 'busy');
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function renderSelectedFiles(input) {
  const zone = input.closest('[data-dropzone]');
  const list = zone ? zone.querySelector('[data-file-list]') : null;
  if (!list) return;
  list.innerHTML = '';
  if (typeof document.createElement !== 'function') return;
  [...input.files].forEach(file => {
    const pill = document.createElement('span');
    pill.className = 'file-pill';
    const name = document.createElement('span');
    name.textContent = file.name;
    const size = document.createElement('span');
    size.className = 'text-muted';
    size.textContent = formatBytes(file.size);
    pill.append(name, size);
    list.append(pill);
  });
}

queryAll('[data-dropzone]').forEach(zone => {
  const input = zone.querySelector('input[type="file"]');
  if (!input) return;
  input.addEventListener('change', () => renderSelectedFiles(input));
  ['dragenter', 'dragover'].forEach(type =>
    zone.addEventListener(type, event => {
      event.preventDefault();
      zone.classList.add('is-dragging');
    }),
  );
  ['dragleave', 'drop'].forEach(type =>
    zone.addEventListener(type, event => {
      event.preventDefault();
      zone.classList.remove('is-dragging');
    }),
  );
  zone.addEventListener('drop', event => {
    if (!event.dataTransfer || !event.dataTransfer.files.length) return;
    input.files = event.dataTransfer.files;
    renderSelectedFiles(input);
  });
});

document.addEventListener('change', event => {
  const upload = event.target.closest('[data-upload]');
  if (upload) delete upload.dataset.idempotencyKey;
  const picker = event.target.closest('[data-client-picker]');
  if (!picker) return;
  const form = query('[data-upload]');
  if (!form) return;
  const url = new URL(form.action, location.origin);
  url.searchParams.set('client_id', picker.value);
  form.action = `${url.pathname}${url.search}`;
});

function newIdempotencyKey() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `upload-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

document.addEventListener('submit', async event => {
  const form = event.target;
  if (!form.hasAttribute || !form.hasAttribute('data-upload')) return;
  event.preventDefault();
  const input = form.querySelector('input[type="file"]');
  const button = form.querySelector('[data-submit]');
  if (!input || !input.files.length) {
    setUploadStatus(form, 'Choose at least one document to upload.', 'error');
    return;
  }
  if (!form.dataset.idempotencyKey) form.dataset.idempotencyKey = newIdempotencyKey();
  setBusy(button, true, busyLabel(button, 'Uploading…'));
  setUploadStatus(form, 'Uploading documents and creating the job…', 'busy');
  try {
    const response = await fetchWithTimeout(form.action, {
      method: 'POST',
      headers: {
        'X-CSRF-Token': csrfToken(),
        'Idempotency-Key': form.dataset.idempotencyKey,
      },
      body: new FormData(form),
    });
    let body = {};
    try {
      body = await response.json();
    } catch (error) {
      body = {};
    }
    if (!response.ok) {
      const detail =
        (body.error && body.error.message) ||
        (typeof body.detail === 'string' ? body.detail : '');
      setUploadStatus(
        form,
        detail || `Upload failed (${response.status}). Check the files and try again.`,
        'error',
      );
      setBusy(button, false);
      return;
    }
    setUploadStatus(form, 'Job created. Opening the extraction page…', 'busy');
    startNavProgress();
    location.href = body.job_url || '/review/jobs';
  } catch (error) {
    setUploadStatus(
      form,
      timedOut(error)
        ? 'The upload took too long. Try again.'
        : 'Network error. Check your connection and try again.',
      'error',
    );
    setBusy(button, false);
  }
});

/* ---------- Live job status ---------- */

const live = query('[data-autorefresh]');
if (live) {
  const delay = Number(live.dataset.autorefresh) || 4000;
  const refresh = () => {
    if (!dirtyReviewForms.size) {
      startNavProgress();
      location.reload();
    } else {
      window.setTimeout(refresh, delay);
    }
  };
  window.setTimeout(refresh, delay);
}

/* ---------- Source evidence workspace ---------- */

function hideBoxes() {
  queryAll('.bbox:not([hidden])').forEach(box => {
    box.hidden = true;
  });
}

function boxFromField(field) {
  if (!field.dataset || !field.dataset.box) return null;
  try {
    return JSON.parse(field.dataset.box);
  } catch (error) {
    return null;
  }
}

function showBox(event) {
  const target = event.target;
  if (!target || typeof target.closest !== 'function') return;
  const field = target.closest('[data-box]');
  if (!field) return;
  const workspace = query('[data-workspace]');
  if (workspace) selectSourcePage(Number(field.dataset.page));
  hideBoxes();
  const box = boxFromField(field);
  const status = query('[data-evidence-status]');
  if (status) {
    status.textContent = box
      ? 'Source evidence highlighted.'
      : 'No bounding box available for this field.';
  }
  if (!box) return;
  const page = query(`#page-${field.dataset.page}`);
  if (!page) return;
  const overlay = page.querySelector('.bbox');
  if (!overlay) return;
  hideBoxes();
  overlay.hidden = false;
  overlay.style.left = `${100 * box.x0}%`;
  overlay.style.top = `${100 * box.y0}%`;
  overlay.style.width = `${100 * (box.x1 - box.x0)}%`;
  overlay.style.height = `${100 * (box.y1 - box.y0)}%`;
}

document.addEventListener('focusin', showBox);
document.addEventListener('click', showBox);
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') hideBoxes();
});

function selectSourcePage(index) {
  const workspace = query('[data-workspace]');
  if (!workspace) return;
  const start = Number(workspace.dataset.pageStart);
  const end = Number(workspace.dataset.pageEnd);
  const page = Math.max(start, Math.min(index, end));
  const figure = query('[data-source-page]');
  const image = query('[data-source-image]');
  if (!figure || !image) return;
  const url = new URL(location.href);
  url.searchParams.set('document_id', workspace.dataset.documentId);
  url.searchParams.set('page', page);
  history.replaceState(null, '', url);
  if (figure.id !== `page-${page}`) {
    figure.id = `page-${page}`;
    image.hidden = false;
    const error = query('[data-media-error]');
    if (error) error.hidden = true;
    image.src = `/review/media/documents/${workspace.dataset.documentId}/pages/${page}`;
    image.alt = `Source page ${page + 1}`;
  }
  const label = query('[data-page-label]');
  if (label) label.textContent = `Page ${page + 1}`;
  queryAll('[data-page-link]').forEach(link => {
    const next = link.dataset.pageLink === 'next' ? page + 1 : page - 1;
    const target = new URL(url);
    target.searchParams.set('page', Math.max(start, Math.min(next, end)));
    link.href = target.pathname + target.search;
  });
}

document.addEventListener('input', event => {
  if (!event.target || typeof event.target.closest !== 'function') return;
  const form = event.target.closest('[data-resolve]');
  if (form) dirtyReviewForms.add(form);
});
document.addEventListener('change', event => {
  if (!event.target || typeof event.target.closest !== 'function') return;
  const form = event.target.closest('[data-resolve]');
  if (form) dirtyReviewForms.add(form);
});

if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
  window.addEventListener('beforeunload', event => {
    if (!dirtyReviewForms.size) return;
    event.preventDefault();
    event.returnValue = '';
  });
}

document.addEventListener('change', event => {
  if (!event.target || !event.target.matches || !event.target.matches('[data-document-picker]')) {
    return;
  }
  if (dirtyReviewForms.size && !window.confirm('Discard unsaved corrections?')) {
    const workspace = query('[data-workspace]');
    if (workspace) event.target.value = workspace.dataset.documentId;
    return;
  }
  dirtyReviewForms.clear();
  const url = new URL(location.href);
  url.searchParams.set('document_id', event.target.value);
  url.searchParams.delete('page');
  startNavProgress();
  location.href = url.pathname + url.search;
});

document.addEventListener('click', event => {
  const target = event.target;
  if (!target || typeof target.closest !== 'function') return;
  const link = target.closest('[data-page-link]');
  if (!link) return;
  event.preventDefault();
  hideBoxes();
  selectSourcePage(Number(new URL(link.href, location.origin).searchParams.get('page')));
});

document.addEventListener('keydown', event => {
  if (
    (event.key === 'Enter' || event.key === ' ') &&
    event.target &&
    typeof event.target.matches === 'function' &&
    event.target.matches('[data-box]')
  ) {
    event.preventDefault();
    showBox(event);
  }
});

queryAll('.page-image img').forEach(image => {
  const unavailable = () => {
    image.hidden = true;
    const page = image.closest('.page');
    const message = page ? page.querySelector('[data-media-error]') : null;
    if (message) message.hidden = false;
    hideBoxes();
  };
  image.addEventListener('error', unavailable);
  if (image.complete && image.naturalWidth === 0) unavailable();
});

const initialWorkspace = query('[data-workspace]');
if (initialWorkspace) {
  const figure = query('[data-source-page]');
  if (figure && figure.id) selectSourcePage(Number(figure.id.replace('page-', '')));
}

/* ---------- Navigation progress for real page loads ---------- */

document.addEventListener('submit', event => {
  if (event.defaultPrevented) return;
  startNavProgress();
});

document.addEventListener('click', event => {
  if (event.defaultPrevented) return;
  if (event.button !== undefined && event.button !== 0) return;
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const target = event.target;
  if (!target || typeof target.closest !== 'function') return;
  const link = target.closest('a[href]');
  if (!link) return;
  if (link.target === '_blank' || link.hasAttribute('download')) return;
  if (typeof location === 'undefined') return;
  let url;
  try {
    url = new URL(link.href, location.href);
  } catch (error) {
    return;
  }
  if (url.origin !== location.origin) return;
  if (url.pathname === location.pathname && url.search === location.search) {
    stopNavProgress();
    return;
  }
  startNavProgress();
});

/* A page restored from the back/forward cache must not keep a button stuck in
 * its busy state. */
if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
  window.addEventListener('pageshow', () => {
    queryAll('.is-busy').forEach(button => setBusy(button, false));
  });
}
