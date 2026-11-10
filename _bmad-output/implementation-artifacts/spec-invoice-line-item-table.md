---
title: 'Invoice line item review table'
type: 'feature'
created: '2026-11-05'
status: 'done'
route: 'oneshot'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="User requested commercial and proforma invoice line-item tables">

## Intent

Display commercial and proforma invoice line items as a table in job details, with description, quantity, unit price and amount. Preserve any additional extracted item columns. Remove those same cells from the flat field list. Reuse click-to-edit values, required correction reasons, review/read-only state, original values and source highlighting per cell. Keep indexed field paths, corrections, extraction evidence, bulk confirmation and JSON contracts unchanged. Missing cells/rows must be explicit; never derive amounts or invent extraction output. Other document types retain their existing field layout.

</frozen-after-approval>

## Implementation Notes

Small additive view/helper/template change. Both schemas already share line_items[].description/quantity/unit_price/line_extension_amount and optional item fields. Build numerically ordered rows from retained indexed paths in workspace_data, leaving display_fields intact for standalone workspace and bulk review; supply separate scalar_fields to the job view. Core columns always visible, optional columns only when extracted, unknown valid columns preserved. Show empty and incomplete metadata notices where applicable. Reuse a Jinja editor macro for scalar fields and table cells so correction/provenance/security behavior stays consistent. No provider extraction redesign, schema/API changes, migrations or ERP work. Existing staged work remains uncommitted.

Verify grouping/order, missing cells, both invoice types, optional fields, correction overlays, cell editing/highlighting, auditor/read-only state and no duplicate flat line-item fields. Run focused unit/integration checks, existing Node suite, real browser desktop/mobile interaction and local API refresh.

Implemented invoice_line_items view projection, shared review_value editor macro, job table and scoped overflow styles. Four core columns plus optional extracted columns preserve original indexed paths/field objects; scalar list excludes those same cells. Empty/incomplete states remain explicit and corrected values retain original-value disclosure. Other document views keep display_fields unchanged.

Verification: 4 unit tests, 4 focused integration cases (commercial/proforma cell corrections, upload rendering and bulk/auditor behavior), and all 10 Node regressions passed. Real Chromium desktop/mobile flow passed editing a table cell, source highlight, correction save, bulk confirmation, document switching, missing image feedback and no page-wide overflow. Screenshots visually inspected. Ruff format/check, mypy and git diff check passed. Local API rebuilt/refreshed and healthy; served table CSS confirmed. Existing staged/unstaged work preserved and no commit created.

## Review Triage Log

- Independent focused review substantiated no grouping, path preservation, correction, authorization or source-navigation defects. Its browser verification gap was covered by the actual Chromium run.
- Own finding, medium, fixed: screen-reader labels positioned against the viewport escaped the horizontal scroll region and increased mobile document width. Position the table scroll container and cell wrappers relatively so absolute accessibility elements remain contained. Browser regression verified document width remains within viewport and only the table scrolls.
