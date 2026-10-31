# 05 — UI Architecture

## 1. Scope

The only UI in v1 is an **internal review console** for a handful of trained reviewers. It is not a
customer portal. It has two jobs, and they are deliberately separate:

1. **Extraction review** — the platform misread a value. Our error; our fix.
2. **Conflict review** — the documents genuinely disagree. The client's business call.

The console is served by the API process with Jinja2 and HTMX. There is no SPA, no Node build step,
no client state store and no second deployment. Page images are served by an authenticated API route;
no object-storage URL is exposed to the browser.

## 2. Principles

- **Two queues, never one list.** Different owners, different SLAs, different metrics.
- **The page is the source of truth.** Every field is shown next to its box on the page.
- **No hidden edits.** A correction stores the prior value, the corrected value, the reviewer and a
  reason. The original extraction record is immutable; a correction creates a resolution record.
- **The platform does not resolve conflicts.** A reviewer records the client's decision; neither
  document's stated value is changed.
- **Fast on a laptop.** Server-rendered HTML, one CSS file, one small JS file, no external CDN.
- **Accessible by default.** Keyboard-first field navigation, visible focus, WCAG 2.1 AA contrast,
  no color-only status.

## 3. Runtime shape

```text
Browser
  │  HTTPS, session cookie
  ▼
api process
  ├── /review/*            Jinja2 + HTMX routes
  ├── /review/static/*     review.css, review.js
  ├── /review/media/*      authenticated page images and thumbnails
  └── services/*           same domain services as the JSON API
        │
        ▼
   PostgreSQL + object storage
```

The review UI never queries the database directly. It calls `services.review`, `services.documents`
and `services.conflicts`, which enforce tenant and role rules.

## 4. Routes

### 4.1 Authentication

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/review/login` | Login form. |
| `POST` | `/review/login` | Verify username/password; create session; rotate CSRF token. |
| `POST` | `/review/logout` | Clear session; increment nothing (single-session logout). |
| `POST` | `/review/logout-all` | Increment `reviewers.session_epoch`, revoking every session. |

### 4.2 Queues

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/review` | Dashboard: counts, aging, unreadable, failed jobs. |
| `GET` | `/review/queues/extraction` | Extraction review queue, full page. |
| `GET` | `/review/queues/extraction/rows` | HTMX fragment for filters and pagination. |
| `GET` | `/review/queues/conflicts` | Conflict review queue, full page. |
| `GET` | `/review/queues/conflicts/rows` | HTMX fragment. |
| `GET` | `/review/queues/unreadable` | Unreadable pages and extraction failures. |
| `POST` | `/review/queues/{item_id}/claim` | Assign the item to the current reviewer. |
| `POST` | `/review/queues/{item_id}/release` | Return the item to the queue. |

### 4.3 Document workspace

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/review/documents/{document_id}` | Workspace: viewer, field list, checks, candidates. |
| `GET` | `/review/documents/{document_id}/fields` | HTMX fragment: field list filtered by section/status. |
| `GET` | `/review/documents/{document_id}/fields/{field_path}` | Field detail: provenance, candidates, edit form. |
| `GET` | `/review/documents/{document_id}/checks` | HTMX fragment: check results. |
| `GET` | `/review/documents/{document_id}/candidates` | HTMX fragment: reader comparison for a field. |
| `POST` | `/review/documents/{document_id}/fields/{field_path}` | Resolve an extraction error: correct or confirm. |
| `POST` | `/review/documents/{document_id}/confirm-unreadable` | Confirm an unreadable outcome. |
| `POST` | `/review/documents/{document_id}/escalate` | Escalate to an admin with a note. |

### 4.4 Conflict workspace

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/review/conflicts/{conflict_id}` | Side-by-side source documents and values. |
| `GET` | `/review/conflicts/{conflict_id}/sources` | HTMX fragment: source cards. |
| `POST` | `/review/conflicts/{conflict_id}/resolve` | Record the client's decision. |

Conflict resolution outcomes:

- `accepted` — the client accepts one named document's stated value as authoritative for their
  downstream process. The platform records the choice; it does not edit the other document.
- `waived` — the client accepts the discrepancy and wants no further action.
- `client_notified` — the reviewer has informed the client and is waiting; the item stays open.

### 4.5 Jobs and admin

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/review/jobs/{job_id}` | Job timeline: submissions, documents, decisions, webhooks. |
| `GET` | `/review/jobs/{job_id}/timeline` | HTMX fragment. |
| `GET` | `/review/admin/policies` | Policy versions and activation. |
| `POST` | `/review/admin/policies` | Create a draft policy version. |
| `POST` | `/review/admin/policies/{policy_id}/activate` | Activate a policy; requires reason. |
| `GET` | `/review/admin/clients` | Clients, API keys, webhook configuration. |
| `POST` | `/review/admin/clients/{client_id}/keys` | Create a key; raw key shown once. |
| `POST` | `/review/admin/clients/{client_id}/keys/{key_id}/revoke` | Revoke a key. |
| `POST` | `/review/admin/clients/{client_id}/webhook` | Set URL and rotate secret. |
| `GET` | `/review/admin/reviewers` | Reviewer accounts and roles. |
| `POST` | `/review/admin/reviewers` | Create a reviewer. |
| `GET` | `/review/admin/retention` | Retention sweep status, overdue objects, failures. |
| `POST` | `/review/admin/tasks/{task_id}/retry` | Requeue a dead task. |
| `POST` | `/review/admin/webhooks/{delivery_id}/replay` | Replay a dead webhook. |

### 4.6 Media

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/review/media/documents/{document_id}/pages/{page_index}` | Full-resolution page image. |
| `GET` | `/review/media/documents/{document_id}/pages/{page_index}/thumb` | Thumbnail for the page strip. |
| `GET` | `/review/media/documents/{document_id}/candidates/{candidate_id}/crop` | Native-resolution crop for a reader candidate. |

Media routes are session-authenticated, check that the document belongs to an authorized client,
stream from object storage and set `Cache-Control: no-store`. They never expose a storage key or a
presigned URL. If the file has been purged, they return a clear “media expired” placeholder rather
than a broken image.

## 5. Screens

### 5.1 Dashboard

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ Trade Extract Review                    Reviewer: A. Mwangi   [Logout]       │
├──────────────────────────────────────────────────────────────────────────────┤
│  EXTRACTION REVIEW (our error)          CONFLICT REVIEW (client decision)     │
│  ┌──────────────────────────────┐       ┌──────────────────────────────┐      │
│  │ 12 open   3 unassigned       │       │ 4 open    1 unassigned       │      │
│  │ Oldest: 2h 14m               │       │ Oldest: 1d 03h               │      │
│  │ [Open queue]                 │       │ [Open queue]                 │      │
│  └──────────────────────────────┘       └──────────────────────────────┘      │
│                                                                              │
│  UNREADABLE / FAILED                     TODAY                               │
│  2 pages awaiting confirmation           38 documents extracted              │
│  1 extraction failure                    31 passed    7 reviewed              │
│                                                                              │
│  Webhook backlog: 0        Retention overdue: 0        Provider errors: 1    │
└──────────────────────────────────────────────────────────────────────────────┘
```

The two queue cards are not a combined list. They have separate colors, counts and links. A reviewer
can work one track without seeing the other.

### 5.2 Extraction queue

Columns: document type, client, job ref, failed check count, reader disagreement, age, assignee,
status. Filters: document type, client, age, assignment, failure reason. Sort: priority, age,
document type.

Rows are HTMX-swapped on filter change and auto-refreshed every 30 seconds while the tab is visible.
The row link opens the workspace and claims the item in one action.

### 5.3 Document workspace

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ ← Queue   COMMERCIAL INVOICE · BL-2026-00417 · Acme Clearing    [Claimed]    │
├───────────────────────────────────────┬──────────────────────────────────────┤
│ PAGE 2 / 3                            │ FIELDS                               │
│ ┌───────────────────────────────────┐ │  Invoice                             │
│ │                                   │ │   number        INV-2026-0417   ✓    │
│ │        [ page image ]             │ │   issue date    2026-10-14      ✓    │
│ │      ┌─────────────┐              │ │   currency      USD             ✓    │
│ │      │ 18,450.00   │ ← bbox       │ │  Shipment                            │
│ │      └─────────────┘              │ │   gross weight  18,450.00 kg    ⚠    │
│ │                                   │ │   packages      1,200 CTN       ✓    │
│ │                                   │ │  Totals                              │
│ │   [−] [100%] [+]   Page 1 2 3     │ │   payable       18,540.00       ✗    │
│ └───────────────────────────────────┘ │                                      │
│                                       │ CHECKS                               │
│ READER COMPARISON                     │  INV-003  FAIL  expected 18,450.00   │
│  text_layer  18,450.00   [show crop]  │           actual 18,540.00           │
│  vision      18,450.00   [show crop]  │  INV-010  PASS  container check digit│
│  paddle      —                        │                                      │
│                                       │ [Correct field] [Confirm] [Escalate] │
└───────────────────────────────────────┴──────────────────────────────────────┘
```

Behaviour:

- Clicking a field highlights its bbox on the page and scrolls it into view.
- Hovering a bbox highlights the field. Both directions work.
- The reader comparison panel shows every candidate, its reader, its crop and its confidence.
  Confidence is display-only; it does not imply correctness.
- `Correct field` opens an inline form with prior value, corrected value, and a required reason.
- `Confirm` records `outcome=confirmed` with a reason; it does not rewrite the value.
- The page strip loads thumbnails lazily; the full page loads at 300 DPI.
- If a page is unreadable, the viewer shows the reason and disables field selection for that page.
- Keyboard: `j`/`k` next/previous field, `Enter` open, `c` confirm, `e` edit, `Esc` close.

### 5.4 Conflict workspace

```text
┌──────────────────────────────────────────────────────────────────────────────┐
│ ← Queue   CONFLICT · Gross weight · BL-2026-00417                            │
├──────────────────────────────────────────────────────────────────────────────┤
│  COMMERCIAL INVOICE                    PACKING LIST                          │
│  ┌──────────────────────────────┐      ┌──────────────────────────────┐      │
│  │ [page crop]                  │      │ [page crop]                  │      │
│  │ GROSS WEIGHT 18,450.00 KGS   │      │ TOTAL GROSS 18,540.00 KGS    │      │
│  └──────────────────────────────┘      └──────────────────────────────┘      │
│  Stated: 18,450.00                     Stated: 18,540.00                     │
│  Page 2, text_layer, 100%              Page 4, vision, 94%                  │
│                                                                              │
│  The platform does not choose a winner. Record the client's decision.        │
│  ( ) Accept commercial invoice value as authoritative                        │
│  ( ) Accept packing list value as authoritative                              │
│  ( ) Waive — client accepts the discrepancy                                  │
│  ( ) Client notified — keep open                                             │
│  Reason: [________________________________________________________]           │
│                                                   [Cancel] [Record decision] │
└──────────────────────────────────────────────────────────────────────────────┘
```

The form records `review_resolutions` with `outcome=accepted|waived|client_notified`, the chosen
document id (for `accepted`), the reviewer and the reason. It never edits either document's stated
value. The conflict status becomes `resolved` or `waived`; `client_notified` leaves it open.

### 5.5 Job detail

A vertical timeline:

```text
09:12:44  Submission res_… created (3 files)
09:12:46  invoice.pdf prepared, 2 pages, text layer present
09:12:47  invoice.pdf segmented: commercial_invoice pages 1–2
09:12:48  packing-list.pdf segmented: packing_list pages 1–2
09:13:02  extraction completed; 2 checks failed
09:13:03  conflict opened: shipment.gross_weight_kg
09:13:04  decision: review (extraction + conflict)
09:13:05  webhook delivered (200)
```

Each entry links to the document, conflict or webhook delivery. The timeline is assembled from
`submissions`, `documents`, `decisions`, `checks`, `conflicts`, `review_items` and
`webhook_deliveries`; it is not a separate event log.

### 5.6 Admin: policies

A policy editor with:

- Version (read-only, next integer).
- Name and effective date.
- Invoice pass toggle.
- Trust-ramp toggle (off in v1; visible but disabled).
- Per-document-type review depth.
- Thresholds: legibility minimum, reader-disagreement blocking, table unresolved ratio.
- Limits: documents per group, file bytes, pages.
- A JSON preview and a diff against the active policy.
- “Save draft” and “Activate”. Activation requires a reason and writes an audit event.

The form is a typed projection of the policy JSON; the JSON preview is read-only. An invalid
combination (for example, invoice pass enabled while trust ramp forces full review) is rejected with
a clear message.

### 5.7 Admin: retention

Shows:

- Last sweep time, duration, objects deleted, bytes deleted, failures.
- Jobs due for purge, oldest overdue age, and a sample of object keys (hashed).
- A “Run sweep now” button for operators, with confirmation.
- An alert banner if overdue objects exceed the 30-minute grace.

This screen exists because a purge that fails quietly is worse than a long policy.

## 6. HTMX interaction model

| Pattern | Use |
|---|---|
| Full page on direct navigation | Every route renders a full page when `HX-Request` is absent. |
| Fragment on HTMX request | Queue rows, field lists, checks, candidates, timeline. |
| `hx-get` + `hx-target` | Filters, pagination, field selection, candidate comparison. |
| `hx-post` + `hx-target` | Claim, release, resolve, escalate, admin actions. |
| `hx-push-url="true"` | Filter state and selected document, so refresh/back works. |
| `hx-trigger="every 30s [document.visibilityState === 'visible']"` | Queue refresh only while visible. |
| `hx-indicator` | Spinner on the affected panel, not the whole page. |
| `HX-Trigger` response header | Toast and refresh events after a successful mutation. |
| `hx-confirm` | Destructive actions: revoke key, replay webhook, run sweep. |

No client-side router. No JSON API calls from the browser except HTMX fragments. No optimistic
updates: the server is the source of truth, and the fragment that comes back is the new state.

### Example template

```html
{# review/templates/documents/_fields.html #}
<section id="field-list" class="field-list">
  {% for section in sections %}
    <h3>{{ section.label }}</h3>
    <ul>
      {% for field in section.fields %}
        <li class="field-row {% if field.disputed %}is-disputed{% endif %}">
          <button
            hx-get="/review/documents/{{ document.id }}/fields/{{ field.path }}"
            hx-target="#field-detail"
            hx-swap="innerHTML"
            hx-push-url="false"
            data-bbox="{{ field.bbox_json }}"
            aria-label="Inspect {{ field.label }}, value {{ field.value }}">
            <span class="field-label">{{ field.label }}</span>
            <span class="field-value">{{ field.value }}</span>
            <span class="field-status">{{ field.status_icon }}</span>
          </button>
        </li>
      {% endfor %}
    </ul>
  {% endfor %}
</section>
```

### Example mutation

```html
<form
  hx-post="/review/documents/{{ document.id }}/fields/{{ field.path }}"
  hx-target="#field-detail"
  hx-swap="innerHTML"
  hx-headers='{"X-CSRF-Token": "{{ csrf_token }}"}'>
  <input type="hidden" name="outcome" value="corrected">
  <label>Prior value <output>{{ field.value }}</output></label>
  <label>Corrected value <input name="corrected_value" value="{{ field.value }}" required></label>
  <label>Reason <textarea name="reason" required minlength="4"></textarea></label>
  <button type="submit">Save correction</button>
</form>
```

## 7. Authentication and authorization

- **Login:** username + password. Passwords are hashed with `hashlib.scrypt` using a per-user salt and
  a versioned parameter string (`scrypt$N$r$p$salt$hash`). No plaintext or reversible storage.
- **Session:** signed cookie using `itsdangerous`, containing reviewer id, role, session epoch and
  issue time. `HttpOnly`, `Secure` in production, `SameSite=Lax`, 8-hour absolute lifetime, 30-minute
  idle timeout.
- **Revocation:** increment `reviewers.session_epoch`; every token with an older epoch is rejected.
- **CSRF:** a random token per session, embedded in forms and sent as `X-CSRF-Token` for HTMX. Every
  unsafe request validates it in constant time. Login rotates the token.
- **Roles:**
  - `reviewer` — queues, document workspace, conflict workspace, claim/release, resolve.
  - `admin` — everything a reviewer can do, plus policies, clients, keys, reviewers, retention.
  - `auditor` — read-only access to jobs, documents, conflicts and audit; no mutations.
- **Tenant scope:** reviewers are internal and may see multiple clients, but every route still
  passes the client id through the service layer. Admin actions are audited.
- **Login hardening:** rate limit 5 attempts/minute/IP and 10/hour/username; generic error message;
  no account enumeration; optional TOTP is a later addition, not v1.

## 8. Visual system

A small, explicit system rather than a component framework:

| Token | Value | Use |
|---|---|---|
| `--bg` | `#f7f7f5` | Page background. |
| `--surface` | `#ffffff` | Cards, tables. |
| `--border` | `#d9d9d4` | Dividers. |
| `--text` | `#1d1d1b` | Primary text. |
| `--muted` | `#6b6b66` | Secondary text. |
| `--accent` | `#1f5f8b` | Links, primary actions. |
| `--danger` | `#a12b2b` | Failed checks, destructive actions. |
| `--warning` | `#8a5a00` | Disputed values, aging. |
| `--success` | `#1f6b45` | Passed checks. |
| `--focus` | `#0b5fff` | Focus ring. |

- System font stack: `ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif`.
- Monospace for values, codes and coordinates: `ui-monospace, "Cascadia Code", monospace`.
- 8 px spacing scale; 4 px for dense table rows.
- Status is always text + icon + color, never color alone.
- Tables use sticky headers; long field lists use section anchors.
- Print stylesheet for a document review summary (no images by default).

## 9. Accessibility

- All interactive elements are native buttons, links, inputs or selects.
- Field navigation is keyboard-complete; focus moves to the selected field detail.
- Bbox overlays have text alternatives (`aria-label` with field label and value).
- Focus is never trapped except in a deliberate modal; `Esc` closes.
- Contrast meets WCAG 2.1 AA; status colors are paired with text.
- Page viewer supports zoom with buttons and keyboard; no drag-only interaction.
- HTMX updates announce changes through an `aria-live="polite"` region.
- Reduced-motion media query disables non-essential transitions.

## 10. Performance

- Queue queries are indexed and paginated; no full-table scans.
- Fragments are small; the page shell is cached by the browser, data is not.
- Page images are loaded lazily and served with `Cache-Control: private, max-age=300` for the
  authenticated session, except full-resolution pages which use `no-store`.
- Thumbnails are generated once during preparation.
- N+1 queries are caught in integration tests by asserting a query budget per route.
- The console targets p95 < 300 ms for queue fragments and < 800 ms for a document workspace on a
  100-page job.

## 11. Error, empty and loading states

| State | Presentation |
|---|---|
| Queue empty | “No open extraction reviews. The last one was resolved 14 minutes ago.” |
| Conflict empty | “No open conflicts. Documents in this job agree on every shared field.” |
| Media purged | “Source media expired six hours after the job reached a terminal state. The extraction record remains available.” |
| Unreadable page | Reason, page thumbnail, “Confirm unreadable” action. |
| Stale claim | Banner: “This item was claimed by another reviewer”; refresh the queue. |
| Mutation conflict | 409 fragment explaining the item changed; reload action. |
| HTMX failure | Inline error with retry; no silent failure. |
| Session expired | Redirect to login with `next`; no lost form data where avoidable. |

## 12. Security notes specific to the UI

- The review console is not exposed to the public internet without SSO/VPN in production; at
  minimum, put it behind an identity-aware proxy or IP allowlist.
- Page images are sensitive. Do not log URLs, do not send them to analytics, and do not cache them in
  a shared proxy.
- `Content-Security-Policy: default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'`
  with no inline scripts. HTMX is vendored locally, not loaded from a CDN.
- `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`.
- Every resolution writes an audit event with reviewer, action, target and reason. Audit metadata
  never contains document text beyond the corrected field value that the resolution record already
  owns.
- The browser never receives a storage key or a presigned URL. Media is proxied by the API.
