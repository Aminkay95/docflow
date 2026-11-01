# 06 — Document Schemas

## 1. Design rules

- **Fields are keyed by canonical path.** `fields` is a map, not a deeply nested object. A path is
  stable across layouts: `invoice.number`, `shipment.gross_weight_kg`,
  `line_items[0].quantity`.
- **Every leaf has provenance.** A scalar without a page index and bounding box is a contract
  violation, not a warning.
- **Values are stated strings.** `value` preserves the page's characters. `value_type` tells the
  client how to parse. Internal normalized values used by checks are never emitted.
- **Standard spines, local extensions.** Invoice family follows UBL 2.1 / Peppol BIS Billing 3.0
  semantics; custom entry follows the WCO Data Model; the local types put market detail in
  `extension`.
- **National context is a field value, never a field name.** No `kenya_vat`; use
  `tax.category` and `tax.rate` with the country in the address.
- **Unknown is explicit.** A page that cannot be classified yields `document_type: "unknown"` and a
  review item, never a guessed schema.

## 2. Output envelope

```json
{
  "document_id": "doc_01J9Z4A1",
  "result_id": "res_01J9Z3K4T7Q8M2",
  "job_id": "job_01J9Z3K4T7Q8M2",
  "job_ref": "BL-2026-00417",
  "file_id": "file_01J9Z3K5",
  "segment_id": "seg_01J9Z4A0",
  "document_type": "commercial_invoice",
  "document_type_confidence": 0.99,
  "schema_version": "invoice.peppol-1.0",
  "status": "review_required",
  "source": {
    "filename": "invoice.pdf",
    "content_type": "application/pdf",
    "file_hash": "sha256:0f3c…",
    "document_hash": "sha256:91ab…",
    "page_start": 0,
    "page_end": 1,
    "page_count": 2
  },
  "fields": {
    "invoice.number": {
      "kind": "scalar",
      "value": "INV-2026-0417",
      "value_type": "string",
      "raw_text": "INVOICE NO: INV-2026-0417",
      "provenance": {
        "page_index": 0,
        "page_number": 1,
        "bbox": { "x0": 0.61, "y0": 0.08, "x1": 0.82, "y1": 0.11 },
        "coordinate_space": "normalized_top_left",
        "page_width_pt": 595.0,
        "page_height_pt": 842.0,
        "reader": "text_layer",
        "reader_version": "pypdfium2-4.30.0",
        "model": null,
        "confidence": 1.0,
        "document_hash": "sha256:91ab…"
      },
      "agreement": "unanimous"
    },
    "totals.payable_amount": {
      "kind": "scalar",
      "value": "18,540.00",
      "value_type": "decimal",
      "raw_text": "TOTAL PAYABLE USD 18,540.00",
      "provenance": {
        "page_index": 1,
        "page_number": 2,
        "bbox": { "x0": 0.58, "y0": 0.79, "x1": 0.86, "y1": 0.82 },
        "coordinate_space": "normalized_top_left",
        "page_width_pt": 595.0,
        "page_height_pt": 842.0,
        "reader": "vision",
        "reader_version": "deepseek-flash",
        "model": "deepseek-flash",
        "confidence": 0.93,
        "document_hash": "sha256:91ab…"
      },
      "agreement": "majority"
    }
  },
  "tables": {
    "line_items": {
      "columns": [
        { "key": "line_number", "label": "Line" },
        { "key": "description", "label": "Description" },
        { "key": "quantity", "label": "Quantity" },
        { "key": "unit_code", "label": "Unit" },
        { "key": "unit_price", "label": "Unit price" },
        { "key": "line_extension_amount", "label": "Amount" }
      ],
      "row_count_stated": 24,
      "row_count_extracted": 24,
      "truncated": false,
      "unresolved_rows": []
    }
  },
  "extension": {},
  "checks": [
    {
      "code": "INV-003",
      "tier": 1,
      "status": "fail",
      "field_path": "totals.payable_amount",
      "expected": "18450.00",
      "actual": "18540.00",
      "severity": "blocking",
      "message": "The sum of line extension amounts is 18,450.00, but the stated payable amount is 18,540.00."
    }
  ],
  "conflicts": [],
  "decision": {
    "value": "review",
    "route": "extraction",
    "reasons": ["check_failed:INV-003"]
  },
  "review": {
    "extraction_open": true,
    "conflict_open": false
  },
  "created_at": "2026-10-31T09:12:44Z",
  "extracted_at": "2026-10-31T09:14:50Z"
}
```

| Envelope field | Type | Rule |
|---|---|---|
| `document_id` | string | Prefixed id. |
| `result_id` | string | Submission that produced this view. |
| `job_id` | string | Logical job. |
| `job_ref` | string \| null | Caller reference. |
| `file_id` | string | Source container. |
| `segment_id` | string | Page range and type. |
| `document_type` | enum | Includes `unknown`. |
| `document_type_confidence` | number 0–1 | Segmentation confidence; ordering only. |
| `schema_version` | string | Immutable per document type. |
| `status` | enum | `queued`, `processing`, `extracted`, `review_required`, `resolved`, `unreadable`, `failed`. |
| `source` | object | File hash, document hash, page range. |
| `fields` | map | Canonical path → `Field`. |
| `tables` | map | Table metadata and completeness. |
| `extension` | map | Market-specific paths → `Field`. |
| `checks` | array | Deterministic outcomes. |
| `conflicts` | array | Conflicts involving this document. |
| `decision` | object \| null | `value`, `route`, `reasons`. |
| `review` | object | Open extraction/conflict flags. |
| `created_at` | timestamp | Submission time. |
| `extracted_at` | timestamp \| null | Extraction completion. |

## 3. Provenance

```json
{
  "page_index": 1,
  "page_number": 2,
  "bbox": { "x0": 0.612, "y0": 0.441, "x1": 0.781, "y1": 0.459 },
  "coordinate_space": "normalized_top_left",
  "page_width_pt": 595.0,
  "page_height_pt": 842.0,
  "reader": "vision",
  "reader_version": "deepseek-flash",
  "model": "deepseek-flash",
  "confidence": 0.94,
  "document_hash": "sha256:91ab…"
}
```

- `page_index` is zero-based within the source file.
- `bbox` is normalized to 0–1, origin top-left. The client multiplies by the rendered page width and
  height. `page_width_pt` and `page_height_pt` preserve the original PDF points for exact overlays.
- `document_hash` is the stable segment identity:
  `sha256("<file_sha256_hex>:<page_start>:<page_end>:<document_type>")`, where `file_sha256_hex` is
  the bare 64-character file hash and the page range is 0-based inclusive. Stored hashes are bare
  hex; the API renders them as `sha256:<hex>`. `source.file_hash` identifies the original file.
  Together they let a client overlay provenance on its own copy after the platform has purged the
  file.
- `reader` is `text_layer`, `paddle`, `vision` or `human`.
- `confidence` is evidence for ordering, never for the pass gate.

### Field union

```json
{
  "kind": "scalar",
  "value": "18,450.00",
  "value_type": "decimal",
  "raw_text": "GROSS WEIGHT 18,450.00 KGS",
  "provenance": { "...": "..." },
  "agreement": "unanimous",
  "alternatives": []
}
```

`kind` is `scalar` in v1. Table rows are individual paths (`line_items[0].quantity`); table-level
metadata lives in `tables`. `alternatives` is present only when `agreement` is `disputed`; each
entry has `value`, `reader`, `confidence` and `provenance`.

`value_type` is one of `string`, `decimal`, `integer`, `date`, `datetime`, `boolean`, `enum`.
`value` is always a string, including numbers and dates, to preserve what the page says. The client
parses using the declared type.

## 4. Checks and conflicts in the envelope

```json
{
  "code": "INV-010",
  "tier": 1,
  "status": "fail",
  "field_path": "shipment.container_numbers[0]",
  "expected": "valid ISO 6346 check digit",
  "actual": "KEMU1234567",
  "severity": "blocking",
  "message": "Container number KEMU1234567 has an invalid ISO 6346 check digit."
}
```

| Check field | Rule |
|---|---|
| `code` | Stable, uppercase, type-prefixed (`INV-`, `BL-`, `CE-`, `PL-`, `DO-`, `PP-`, `VM-`). |
| `tier` | 1 deterministic; 2 falsifiable signal; 3 advisory. |
| `status` | `pass`, `fail`, `warn`, `skipped`. |
| `severity` | `info`, `warning`, `blocking`. |
| `expected` / `actual` | For display; never emitted as document data. |
| `message` | Human-readable, names the field and both values where relevant. |

Conflict object:

```json
{
  "conflict_id": "cnf_01J9Z5B2",
  "canonical_key": "shipment.gross_weight_kg",
  "field_label": "Gross weight",
  "status": "open",
  "sources": [
    {
      "document_id": "doc_01J9Z4A1",
      "document_type": "commercial_invoice",
      "value": "18,450.00",
      "provenance": { "page_index": 1, "bbox": { "x0": 0.62, "y0": 0.44, "x1": 0.78, "y1": 0.46 } }
    }
  ]
}
```

Each document still reports its own `fields["shipment.gross_weight_kg"]`. The conflict is a separate
record; it never overwrites either document.

## 5. Commercial invoice

**Spine:** UBL 2.1 / Peppol BIS Billing 3.0. **Schema version:** `invoice.peppol-1.0`.
**Review depth:** gate. The only type that can pass without human review in v1.

### Parties and references

| Path | Req. | Type | Notes |
|---|---|---|---|
| `invoice.number` | Yes | string | Supplier's invoice number. |
| `invoice.issue_date` | Yes | date | ISO 8601. |
| `invoice.due_date` | No | date | Payment due date. |
| `invoice.type_code` | No | enum | UBL `380` commercial invoice, `389` self-billed, etc. |
| `invoice.currency` | Yes | enum | ISO 4217. |
| `invoice.tax_point_date` | No | date | If stated. |
| `invoice.note` | No | string | Free-text note; one path per note. |
| `seller.name` | Yes | string | Supplier. |
| `seller.identifier` | No | string | Tax/PIN/VAT identifier. |
| `seller.address.line1` | No | string | |
| `seller.address.city` | No | string | |
| `seller.address.country` | No | enum | ISO 3166-1 alpha-2 if stated. |
| `buyer.name` | Yes | string | |
| `buyer.identifier` | No | string | |
| `buyer.address.line1` | No | string | |
| `buyer.address.city` | No | string | |
| `buyer.address.country` | No | enum | |
| `ship_to.name` | No | string | Delivery party. |
| `ship_to.address.line1` | No | string | |
| `ship_to.address.city` | No | string | |
| `ship_to.address.country` | No | enum | |
| `order.reference` | No | string | Buyer's purchase order. |
| `despatch_advice.reference` | No | string | |
| `receiving_advice.reference` | No | string | |
| `bill_of_lading.number` | No | string | Cross-document anchor when present. |
| `payment.terms` | No | string | Free text. |
| `payment.means_code` | No | enum | UBL payment means code if stated. |
| `payment.iban` | No | string | |
| `incoterm.code` | No | enum | Incoterms 2020. |
| `incoterm.location` | No | string | Named place. |

### Shipment

| Path | Req. | Type | Notes |
|---|---|---|---|
| `shipment.gross_weight_kg` | No | decimal | As stated; unit in `shipment.gross_weight_unit`. |
| `shipment.gross_weight_unit` | No | enum | `KGM`, `LBR`, etc. |
| `shipment.net_weight_kg` | No | decimal | |
| `shipment.net_weight_unit` | No | enum | |
| `shipment.package_count` | No | integer | |
| `shipment.package_type` | No | enum | UN/ECE Recommendation 21 if stated. |
| `shipment.container_numbers[0]` | No | string | Indexed; ISO 6346 checked. |
| `shipment.seal_numbers[0]` | No | string | Indexed. |
| `shipment.vessel` | No | string | |
| `shipment.voyage` | No | string | |
| `shipment.port_of_loading` | No | string | |
| `shipment.port_of_discharge` | No | string | |
| `shipment.country_of_origin` | No | enum | ISO 3166-1 alpha-2 if stated. |
| `shipment.marks_and_numbers` | No | string | Free text. |

### Line items

Each row is a set of indexed paths. Every cell has its own provenance.

| Path pattern | Req. | Type | Notes |
|---|---|---|---|
| `line_items[n].line_number` | No | string | As printed. |
| `line_items[n].description` | Yes | string | Goods description. |
| `line_items[n].quantity` | Yes | decimal | |
| `line_items[n].unit_code` | No | enum | UN/ECE Recommendation 20. |
| `line_items[n].unit_price` | Yes | decimal | |
| `line_items[n].line_extension_amount` | Yes | decimal | Quantity × unit price as stated. |
| `line_items[n].hs_code` | No | string | 6–10 digits; structure checked. |
| `line_items[n].tax_category` | No | enum | UBL tax category (`S`, `Z`, `E`, `AE`, …). |
| `line_items[n].tax_rate` | No | decimal | Percent as stated. |
| `line_items[n].tax_amount` | No | decimal | |
| `line_items[n].allowance_charge` | No | decimal | Negative for allowance if stated. |
| `line_items[n].country_of_origin` | No | enum | |
| `line_items[n].note` | No | string | |

### Totals and tax

| Path | Req. | Type | Notes |
|---|---|---|---|
| `totals.line_extension_amount` | Yes | decimal | Sum of line amounts as stated. |
| `totals.tax_exclusive_amount` | Yes | decimal | |
| `totals.tax_amount` | No | decimal | |
| `totals.tax_inclusive_amount` | Yes | decimal | |
| `totals.allowance_total` | No | decimal | |
| `totals.charge_total` | No | decimal | |
| `totals.prepaid_amount` | No | decimal | |
| `totals.payable_amount` | Yes | decimal | |
| `totals.amount_in_words` | No | string | If printed. |
| `tax_breakdown[n].taxable_amount` | No | decimal | |
| `tax_breakdown[n].tax_amount` | No | decimal | |
| `tax_breakdown[n].tax_category` | No | enum | |
| `tax_breakdown[n].tax_rate` | No | decimal | |

### Extension

Market-specific invoice detail only:

| Path | Type | Notes |
|---|---|---|
| `extension.kenya.pin` | string | Supplier or buyer PIN if printed; country is data. |
| `extension.kenya.vat_number` | string | |
| `extension.kenya.idf_reference` | string | Import declaration factor reference. |
| `extension.kenya.pvoc_reference` | string | |
| `extension.kenya.remarks` | string | |
| `extension.any.other` | string | Free-form, one path per note. |

## 6. Proforma invoice

**Spine:** UBL 2.1 / Peppol BIS Billing 3.0. **Schema version:** `proforma.peppol-1.0`.
**Review depth:** full review. It shares the invoice structure, but it is issued before shipment, so
it never carries a bill-of-lading number.

Fields are identical to the commercial invoice with these differences:

| Path | Rule |
|---|---|
| `proforma.number` | Required. |
| `proforma.issue_date` | Required. |
| `proforma.currency` | Required. ISO 4217. |
| `proforma.valid_until` | Optional; a proforma-specific field. |
| `bill_of_lading.number` | Forbidden; if printed, the value is an extension note and a check warns. |
| `shipment.vessel`, `shipment.voyage` | Optional; often absent before booking. |
| `invoice.*` paths | Replaced by `proforma.*` for number, issue date, due date, type code and currency. |
| All line, party, totals and tax paths | Same as invoice. |

The schema registry maps `proforma_invoice` to `proforma.peppol-1.0`; the model reuses the invoice
base classes and overrides the document-number and B/L rules.

## 7. Bill of lading

**Spine:** DCSA eBL structure. **Schema version:** `bl.dcsa-1.0`. **Review depth:** full review.
Highest layout variance and least arithmetic in the set. Master versus house form is not resolved in
v1; the schema carries `bl.form` so the distinction is data, not a separate model.

| Path | Req. | Type | Notes |
|---|---|---|---|
| `bl.number` | Yes | string | Bill of lading number. |
| `bl.form` | No | enum | `master`, `house`, `sea_waybill`, `original`, `copy`. |
| `bl.issue_date` | Yes | date | |
| `bl.place_of_issue` | No | string | |
| `bl.original_count` | No | integer | Number of originals. |
| `bl.terms` | No | string | Free text. |
| `carrier.name` | Yes | string | |
| `carrier.scac` | No | string | Carrier code if printed. |
| `shipper.name` | Yes | string | |
| `shipper.address.line1` | No | string | |
| `shipper.address.city` | No | string | |
| `shipper.address.country` | No | enum | |
| `consignee.name` | Yes | string | |
| `consignee.address.line1` | No | string | |
| `consignee.address.city` | No | string | |
| `consignee.address.country` | No | enum | |
| `notify_party.name` | No | string | |
| `notify_party.address.line1` | No | string | |
| `booking.number` | No | string | |
| `service.contract` | No | string | |
| `vessel.name` | No | string | |
| `vessel.imo` | No | string | IMO number if printed. |
| `voyage.number` | No | string | |
| `routing.port_of_loading` | Yes | string | |
| `routing.port_of_discharge` | Yes | string | |
| `routing.place_of_receipt` | No | string | |
| `routing.place_of_delivery` | No | string | |
| `dates.shipped_on_board` | No | date | |
| `dates.arrival_estimated` | No | date | |
| `freight.terms` | No | enum | `prepaid`, `collect`, `as_arranged`. |
| `freight.amount` | No | decimal | As stated. |
| `freight.currency` | No | enum | ISO 4217. |
| `containers[n].number` | No | string | ISO 6346 checked. |
| `containers[n].seal` | No | string | |
| `containers[n].type` | No | string | ISO 6346 size/type if stated. |
| `containers[n].package_count` | No | integer | |
| `containers[n].package_type` | No | enum | |
| `containers[n].gross_weight_kg` | No | decimal | |
| `containers[n].net_weight_kg` | No | decimal | |
| `containers[n].volume_m3` | No | decimal | |
| `containers[n].marks` | No | string | |
| `containers[n].description` | No | string | |
| `containers[n].hs_code` | No | string | |
| `clauses[n].text` | No | string | Free-text clauses, indexed. |
| `signature.place` | No | string | |
| `signature.date` | No | date | |
| `signature.signed_by` | No | string | |
| `agent.name` | No | string | Issuing agent. |

Checks are advisory: container check digits, date ordering (issue ≤ shipped ≤ arrival), required
party presence, port consistency. Everything routes to review.

## 8. Delivery order

**Spine:** proprietary extension block. **Schema version:** `delivery_order.local-1.0`.
**Review depth:** full review. Issued against the bill of lading; there is no international standard.

| Path | Req. | Type | Notes |
|---|---|---|---|
| `delivery_order.number` | Yes | string | |
| `delivery_order.issue_date` | Yes | date | |
| `delivery_order.valid_until` | No | date | |
| `bill_of_lading.number` | Yes | string | Cross-document anchor. |
| `carrier.name` | No | string | |
| `agent.name` | No | string | Issuing agent. |
| `consignee.name` | Yes | string | |
| `consignee.address.line1` | No | string | |
| `notify_party.name` | No | string | |
| `release.terminal` | No | string | |
| `release.warehouse` | No | string | |
| `release.reference` | No | string | |
| `release.free_time_days` | No | integer | |
| `containers[n].number` | No | string | ISO 6346 checked. |
| `containers[n].seal` | No | string | |
| `containers[n].type` | No | string | |
| `containers[n].gross_weight_kg` | No | decimal | |
| `containers[n].package_count` | No | integer | |
| `containers[n].package_type` | No | enum | |
| `cargo.description` | No | string | |
| `cargo.marks` | No | string | |
| `charges[n].description` | No | string | |
| `charges[n].amount` | No | decimal | As stated. |
| `charges[n].currency` | No | enum | |
| `instructions` | No | string | Free text. |
| `signature.name` | No | string | |
| `extension.kenya.port` | string | Country is data; port name is the value. |
| `extension.kenya.kwatos_reference` | string | |

Checks are advisory: B/L presence, container check digits, date ordering, free-time plausibility.

## 9. Port pass

**Spine:** proprietary extension block. **Schema version:** `port_pass.local-1.0`.
**Review depth:** full review. Supplied by the caller; the platform never fetches it from KPA or a
terminal.

| Path | Req. | Type | Notes |
|---|---|---|---|
| `port_pass.number` | Yes | string | |
| `port_pass.issue_date` | Yes | date | |
| `port_pass.valid_from` | No | datetime | |
| `port_pass.valid_to` | No | datetime | |
| `port.name` | No | string | |
| `port.terminal` | No | string | |
| `port.gate` | No | string | |
| `vehicle.plate` | No | string | |
| `vehicle.type` | No | string | |
| `driver.name` | No | string | |
| `driver.license` | No | string | |
| `transporter.name` | No | string | |
| `container.number` | No | string | ISO 6346 checked. |
| `container.seal` | No | string | |
| `container.size_type` | No | string | |
| `container.gross_weight_kg` | No | decimal | |
| `cargo.description` | No | string | |
| `release.reference` | No | string | |
| `bill_of_lading.number` | No | string | |
| `delivery_order.number` | No | string | |
| `movement.direction` | No | enum | `in`, `out`, `transfer`. |
| `escort.required` | No | boolean | |
| `remarks` | No | string | |
| `extension.kenya.kwatos_reference` | string | |
| `extension.kenya.terminal_code` | string | |

Checks are advisory: container check digits, validity window, direction consistency, required
release reference.

## 10. Custom entry

**Spine:** WCO Data Model, provisionally derived from EAC import-declaration practice.
**Schema version:** `custom_entry.wco-1.0`. **Review depth:** full review.

> The field set is an educated guess pending confirmation from a working clearing agent. It is
> implemented as data, not validated truth. Confirm every field against a live iCMS declaration
> before treating any of it as contractual.

### Declaration

| Path | Req. | Type | Notes |
|---|---|---|---|
| `declaration.number` | Yes | string | |
| `declaration.type` | No | enum | `home_use`, `warehousing`, `transit`, `export`. |
| `declaration.registration_date` | Yes | date | |
| `declaration.customs_office` | No | string | Mombasa, ICD Nairobi, JKIA, etc. |
| `declarant.agent_name` | No | string | |
| `declarant.license` | No | string | |
| `importer.name` | Yes | string | |
| `importer.pin` | No | string | |
| `importer.address.line1` | No | string | |

### Consignment

| Path | Req. | Type | Notes |
|---|---|---|---|
| `consignment.bl_number` | No | string | Or airway bill. |
| `consignment.awb_number` | No | string | |
| `consignment.vessel` | No | string | |
| `consignment.flight` | No | string | |
| `consignment.voyage` | No | string | |
| `consignment.arrival_date` | No | date | |
| `consignment.port_of_loading` | No | string | |
| `consignment.port_of_discharge` | No | string | |
| `consignment.country_of_origin` | No | enum | |
| `consignment.country_of_export` | No | enum | |
| `consignment.incoterm` | No | enum | |
| `consignment.invoice_number` | No | string | |
| `consignment.invoice_date` | No | date | |
| `consignment.invoice_currency` | No | enum | |
| `consignment.exchange_rate` | No | decimal | As stated. |
| `consignment.fob_value` | No | decimal | As stated. |
| `consignment.freight_value` | No | decimal | As stated. |
| `consignment.insurance_value` | No | decimal | As stated. |
| `consignment.cif_value` | No | decimal | As stated. Never computed. |

### Goods lines

Each row is indexed.

| Path pattern | Req. | Type | Notes |
|---|---|---|---|
| `goods_lines[n].item_number` | No | string | |
| `goods_lines[n].hs_code` | No | string | EAC CET 8-digit plus national extension. |
| `goods_lines[n].description` | Yes | string | |
| `goods_lines[n].quantity` | No | decimal | |
| `goods_lines[n].unit_of_measure` | No | enum | |
| `goods_lines[n].gross_weight_kg` | No | decimal | |
| `goods_lines[n].net_weight_kg` | No | decimal | |
| `goods_lines[n].package_count` | No | integer | |
| `goods_lines[n].package_type` | No | enum | |
| `goods_lines[n].marks_and_numbers` | No | string | |
| `goods_lines[n].container_numbers[0]` | No | string | Indexed. |
| `goods_lines[n].seal_numbers[0]` | No | string | Indexed. |

### Assessment

Every figure is read as stated. The platform does not compute duty, levies, VAT or CIF.

| Path | Req. | Type | Notes |
|---|---|---|---|
| `assessment[n].line_value` | No | decimal | |
| `assessment[n].customs_value` | No | decimal | |
| `assessment[n].duty_rate` | No | decimal | |
| `assessment[n].duty_amount` | No | decimal | |
| `assessment[n].import_declaration_fee` | No | decimal | |
| `assessment[n].railway_development_levy` | No | decimal | |
| `assessment[n].excise` | No | decimal | |
| `assessment[n].vat` | No | decimal | |
| `assessment[n].total_payable` | No | decimal | |

### Permits and extension

| Path | Type | Notes |
|---|---|---|
| `permits.idf_reference` | string | |
| `permits.pvoc_reference` | string | Certificate of conformity. |
| `permits.import_permits[0]` | string | Indexed. |
| `permits.certificate_of_origin` | string | |
| `permits.preferential_treatment` | string | As stated. |
| `extension.kenya.officer_remarks` | string | |
| `extension.kenya.verification_notes` | string | |
| `extension.kenya.release_references[0]` | string | |

Advisory checks: stated CIF equals stated FOB + freight + insurance; duty/levy/VAT arithmetic where
all components are stated; line values sum to customs value; HS structure; container check digits;
date ordering. Disagreement with invoiced components is reported as a conflict, never as an
assessment of the platform's own.

## 11. Verification memo

**Spine:** proprietary extension block. **Schema version:** `verification_memo.local-1.0`.
**Review depth:** full review. Internal CFS document or client-supplied; highest layout variance of
the set. The schema tolerates near-free-form content.

| Path | Req. | Type | Notes |
|---|---|---|---|
| `memo.number` | No | string | |
| `memo.date` | Yes | date | |
| `memo.type` | No | enum | `cfs_verification`, `damage`, `shortage`, `condition`. |
| `cfs.name` | No | string | Container freight station. |
| `cfs.location` | No | string | |
| `container.number` | No | string | ISO 6346 checked. |
| `container.seal` | No | string | |
| `bill_of_lading.number` | No | string | |
| `delivery_order.number` | No | string | |
| `packages.stated` | No | integer | |
| `packages.received` | No | integer | |
| `packages.damaged` | No | integer | |
| `packages.short` | No | integer | |
| `packages.over` | No | integer | |
| `condition.seal_intact` | No | boolean | |
| `condition.cargo` | No | string | Free text. |
| `findings[n].text` | No | string | Indexed free-text findings. |
| `officer.name` | No | string | |
| `officer.signature` | No | string | |
| `remarks` | No | string | |
| `extension.kenya.port` | string | |
| `extension.kenya.cfs_code` | string | |

The model allows unknown additional paths under `extension.any.*` with provenance, so a memo that
does not fit the named fields is still representable without inventing a schema.

## 12. Packing list (proposed)

> **Spec gap.** The SPEC names seven document types but CAP-2 requires a PDF containing an invoice,
> a packing list and a B/L to yield three documents, and CAP-11 requires a 200-row packing list to be
> read completely. A packing list is therefore a first-class type. This document proposes
> `packing_list` as an eighth type; if the product must hold to seven, CAP-2 and CAP-11 need
> amendment. See [ADR-013](08-operations-and-decisions.md#adr-013-packing-list-is-a-proposed-eighth-type).

**Spine:** proprietary extension block. **Schema version:** `packing_list.local-1.0`.
**Review depth:** full review.

| Path | Req. | Type | Notes |
|---|---|---|---|
| `packing_list.number` | No | string | |
| `packing_list.date` | No | date | |
| `invoice.number` | No | string | Cross-document anchor. |
| `bill_of_lading.number` | No | string | |
| `shipper.name` | No | string | |
| `consignee.name` | No | string | |
| `marks_and_numbers` | No | string | |
| `lines[n].line_number` | No | string | |
| `lines[n].description` | Yes | string | |
| `lines[n].hs_code` | No | string | |
| `lines[n].quantity` | Yes | decimal | |
| `lines[n].unit_code` | No | enum | |
| `lines[n].package_count` | No | integer | |
| `lines[n].package_type` | No | enum | |
| `lines[n].gross_weight_kg` | No | decimal | |
| `lines[n].net_weight_kg` | No | decimal | |
| `lines[n].volume_m3` | No | decimal | |
| `lines[n].dimensions` | No | string | As stated. |
| `lines[n].marks` | No | string | |
| `totals.package_count` | No | integer | |
| `totals.gross_weight_kg` | No | decimal | |
| `totals.net_weight_kg` | No | decimal | |
| `totals.volume_m3` | No | decimal | |
| `extension.kenya.cfs` | string | |

Table completeness is mandatory:

```json
"tables": {
  "lines": {
    "columns": [
      { "key": "line_number", "label": "Line" },
      { "key": "description", "label": "Description" },
      { "key": "quantity", "label": "Quantity" },
      { "key": "package_count", "label": "Packages" },
      { "key": "gross_weight_kg", "label": "Gross kg" }
    ],
    "row_count_stated": 200,
    "row_count_extracted": 200,
    "truncated": false,
    "unresolved_rows": []
  }
}
```

If 3 of 200 rows cannot be resolved, the output must say so:

```json
{
  "row_count_stated": 200,
  "row_count_extracted": 197,
  "truncated": false,
  "unresolved_rows": [14, 87, 132]
}
```

A truncated result is never presented as complete. A document with unresolved rows routes to
extraction review.

## 13. Canonical conflict keys

Canonical keys are the only place where two document types are compared. They map document-specific
paths to one shared key.

| Canonical key | Sources | Comparison |
|---|---|---|
| `shipment.bl_number` | B/L `bl.number`; custom entry `consignment.bl_number`; delivery order `bill_of_lading.number`; port pass `bill_of_lading.number` | Trim, uppercase, remove spaces. |
| `shipment.invoice_number` | Invoice `invoice.number`; proforma `proforma.number`; custom entry `consignment.invoice_number`; packing list `invoice.number` | Trim, uppercase. |
| `shipment.invoice_date` | Invoice `invoice.issue_date`; proforma `proforma.issue_date`; custom entry `consignment.invoice_date` | ISO date. |
| `shipment.currency` | Invoice `invoice.currency`; proforma `proforma.currency`; custom entry `consignment.invoice_currency` | ISO 4217 uppercase. |
| `shipment.incoterm` | Invoice `incoterm.code`; custom entry `consignment.incoterm` | Uppercase. |
| `shipment.gross_weight_kg` | Invoice `shipment.gross_weight_kg`; packing list `totals.gross_weight_kg`; B/L container sum; custom entry goods sum | Decimal, tolerance 0.5% or 0.01 kg, whichever is larger. |
| `shipment.net_weight_kg` | Same family | Decimal tolerance. |
| `shipment.package_count` | Invoice, packing list, B/L, custom entry | Integer exact. |
| `shipment.container_numbers` | All container arrays | Set equality after normalization. |
| `shipment.seal_numbers` | All seal arrays | Set equality. |
| `shipment.consignee_name` | B/L, delivery order, packing list, custom entry | Case/whitespace normalized. |
| `shipment.shipper_name` | Invoice seller, B/L shipper, packing list shipper | Case/whitespace normalized. |
| `shipment.vessel` | B/L, custom entry, port pass if present | Uppercase, punctuation normalized. |
| `shipment.voyage` | B/L, custom entry | Uppercase. |
| `shipment.port_of_loading` | B/L, custom entry, port pass | Normalized place name. |
| `shipment.port_of_discharge` | B/L, custom entry, port pass | Normalized place name. |
| `shipment.arrival_date` | B/L, custom entry, port pass | ISO date. |
| `invoice.total_amount` | Invoice `totals.payable_amount`; proforma; custom entry `consignment.invoice_total` if printed | Decimal exact to currency minor unit. |
| `invoice.freight_amount` | Invoice `totals.charge_total`? Only when explicitly labelled freight; custom entry `consignment.freight_value` | Decimal. |
| `invoice.insurance_amount` | Invoice stated insurance; custom entry `consignment.insurance_value` | Decimal. |
| `goods.hs_codes` | Invoice lines, packing list lines, custom entry lines | Set of normalized 6–10 digit codes. |

Rules:

- A missing value is not a conflict. It may be a completeness warning.
- A canonical key with more than one distinct normalized value creates one conflict with every
  disagreeing source.
- The fingerprint is `sha256(job_id + canonical_key + sorted(normalized values) + sorted(document
  ids))`; repeated submissions with the same disagreement do not create duplicate open conflicts.
- The platform never picks a winner. Resolution is a human record, not a mutation.

## 14. Schema registry and validation

```python
DOCUMENT_SCHEMAS = {
    "commercial_invoice": SchemaSpec(
        model=CommercialInvoice,
        version="invoice.peppol-1.0",
        prompt="invoice.md",
        checks="invoice_full",
        tables={"line_items"},
    ),
    "proforma_invoice": SchemaSpec(
        model=ProformaInvoice,
        version="proforma.peppol-1.0",
        prompt="proforma.md",
        checks="advisory",
        tables={"line_items"},
    ),
    "bill_of_lading": SchemaSpec(
        model=BillOfLading,
        version="bl.dcsa-1.0",
        prompt="bill_of_lading.md",
        checks="advisory",
        tables={"containers"},
    ),
    "delivery_order": SchemaSpec(
        model=DeliveryOrder,
        version="delivery_order.local-1.0",
        prompt="delivery_order.md",
        checks="advisory",
        tables={"containers"},
    ),
    "port_pass": SchemaSpec(
        model=PortPass,
        version="port_pass.local-1.0",
        prompt="port_pass.md",
        checks="advisory",
        tables={},
    ),
    "custom_entry": SchemaSpec(
        model=CustomEntry,
        version="custom_entry.wco-1.0",
        prompt="custom_entry.md",
        checks="advisory",
        tables={"goods_lines", "assessment"},
    ),
    "verification_memo": SchemaSpec(
        model=VerificationMemo,
        version="verification_memo.local-1.0",
        prompt="verification_memo.md",
        checks="advisory",
        tables={"findings"},
    ),
    "packing_list": SchemaSpec(
        model=PackingList,
        version="packing_list.local-1.0",
        prompt="packing_list.md",
        checks="advisory",
        tables={"lines"},
    ),
}
```

Validation loop:

1. Provider returns JSON in JSON mode.
2. `model_validate` against the registered Pydantic model.
3. On `ValidationError`, build a repair prompt from the error paths and the original evidence,
   capped at two attempts.
4. Validate provenance coverage separately: every leaf path must have a `provenance` object with a
   page index and bbox.
5. Validate table completeness: `row_count_extracted` must equal `row_count_stated` unless
   `unresolved_rows` names the gap; `truncated` must be explicit.
6. On success, persist `field_values` and the immutable `documents.extraction` snapshot.
7. On failure, persist an extraction review item with the validation errors. No partial output is
   promoted to a completed document.

Schema changes follow the same rule as the public API: additive optional fields may ship within the
same schema version; a change in meaning or a removed field requires a new `schema_version`.

## 15. Worked custom-entry excerpt

```json
{
  "document_type": "custom_entry",
  "schema_version": "custom_entry.wco-1.0",
  "fields": {
    "declaration.number": {
      "kind": "scalar",
      "value": "C-2026-118422",
      "value_type": "string",
      "raw_text": "ENTRY NO C-2026-118422",
      "provenance": {
        "page_index": 0,
        "page_number": 1,
        "bbox": { "x0": 0.08, "y0": 0.07, "x1": 0.31, "y1": 0.09 },
        "coordinate_space": "normalized_top_left",
        "page_width_pt": 595.0,
        "page_height_pt": 842.0,
        "reader": "text_layer",
        "reader_version": "pypdfium2-4.30.0",
        "model": null,
        "confidence": 1.0,
        "document_hash": "sha256:ab12…"
      },
      "agreement": "unanimous"
    },
    "consignment.cif_value": {
      "kind": "scalar",
      "value": "184,500.00",
      "value_type": "decimal",
      "raw_text": "CIF VALUE USD 184,500.00",
      "provenance": {
        "page_index": 1,
        "page_number": 2,
        "bbox": { "x0": 0.55, "y0": 0.62, "x1": 0.82, "y1": 0.64 },
        "coordinate_space": "normalized_top_left",
        "page_width_pt": 595.0,
        "page_height_pt": 842.0,
        "reader": "paddle",
        "reader_version": "paddleocr-2.9.0",
        "model": null,
        "confidence": 0.91,
        "document_hash": "sha256:ab12…"
      },
      "agreement": "majority"
    }
  },
  "tables": {
    "goods_lines": {
      "columns": [
        { "key": "item_number", "label": "Item" },
        { "key": "hs_code", "label": "HS" },
        { "key": "description", "label": "Description" },
        { "key": "quantity", "label": "Qty" },
        { "key": "unit_of_measure", "label": "UOM" }
      ],
      "row_count_stated": 8,
      "row_count_extracted": 8,
      "truncated": false,
      "unresolved_rows": []
    }
  },
  "extension": {
    "extension.kenya.officer_remarks": {
      "kind": "scalar",
      "value": "Verified against invoice and packing list.",
      "value_type": "string",
      "raw_text": "OFFICER REMARKS: Verified against invoice and packing list.",
      "provenance": {
        "page_index": 2,
        "page_number": 3,
        "bbox": { "x0": 0.08, "y0": 0.84, "x1": 0.91, "y1": 0.89 },
        "coordinate_space": "normalized_top_left",
        "page_width_pt": 595.0,
        "page_height_pt": 842.0,
        "reader": "vision",
        "reader_version": "deepseek-flash",
        "model": "deepseek-flash",
        "confidence": 0.88,
        "document_hash": "sha256:ab12…"
      },
      "agreement": "single_reader"
    }
  }
}
```

