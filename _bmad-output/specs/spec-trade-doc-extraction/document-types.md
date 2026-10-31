# Document types

Seven types. All seven have a defined output schema and live extraction in v1. Only the commercial invoice carries a full deterministic check set; the other six route to full review, which is why their check columns read "review-only".

| Type | Standard spine | v1 extraction | v1 checks | Notes |
|---|---|---|---|---|
| Commercial invoice | UBL 2.1 / Peppol BIS Billing 3.0 | Full | Full Tier-1 set | The only type that can pass without human review in v1. Peppol business rules supply the arithmetic identities. |
| Proforma invoice | UBL 2.1 / Peppol BIS Billing 3.0 | Full | Review-only | Shares the invoice structure; issued before shipment, so it never carries a bill of lading number. |
| Bill of Lading | DCSA eBL structure | Full | Review-only | Highest layout variance and least arithmetic. Build last on the automation path; a thin slice early to surface segmentation risk. Master versus house form is not resolved in v1. |
| Delivery order | Proprietary extension block | Full | Review-only | Issued against the bill of lading. No international standard. |
| Port pass | Proprietary extension block | Full | Review-only | Market-specific, supplied by the caller. The platform never fetches it from KPA or a terminal. |
| Custom entry | WCO Data Model | Full | Review-only | The only document in the set carrying derived values (duty, levies, VAT, assessed value). Becomes the strongest reconciliation anchor once it is covered — see the note below. Provisional field set below, unverified. |
| Verification memo | Proprietary extension block | Full | Review-only | Internal CFS document or client-supplied. Expect the highest layout variance of the seven; schema must tolerate near-free-form content. |

## Rules that apply to every type

- Values are as-stated from the page. Never computed, never corrected, never reconciled into the output.
- Every emitted field carries page index and bounding-box provenance.
- Market-specific detail lives only inside the type's extension block, never in a shared field name.
- Cross-document conflicts are reported per field with every disagreeing source document, and never resolved by the platform.

## Note on the custom entry

The custom entry prints values that were *derived* from the other documents: assessed customs value, duty, levies and VAT. That makes it the only type that can close an arithmetic loop across an entire job. Once a custom entry is present in a group, its derived figures can be checked against the invoiced components — which is the strongest validation available anywhere in this product.

It is nonetheless review-only in v1, because the check requires a rate table the platform does not own and will not own. What the platform may report is the disagreement with its components, never an assessment of its own.

## Custom entry — provisional field set

**Unverified.** Derived from the WCO Data Model and standard EAC import-declaration practice so implementation can begin. Confirm every field against a live iCMS declaration before treating any of it as contractual.

**Declaration** — declaration number, declaration type (home use / warehousing / transit), registration date, customs office (Mombasa, ICD Nairobi, JKIA), declarant agent name and licence, importer name, importer PIN, importer address.

**Consignment** — bill of lading or airway bill number, vessel or flight, voyage number, arrival date, port of loading, port of discharge, country of origin, country of export, terms of delivery (incoterm), invoice number, invoice date, invoice currency, exchange rate, FOB value, freight, insurance, CIF value.

**Goods lines** — item number, HS code (EAC CET 8-digit plus national extension), description, quantity, unit of measure, gross weight, net weight, package count and package type, marks and numbers, container numbers, seal numbers.

**Assessment** — line value, customs value, duty rate, duty amount, import declaration fee, railway development levy, excise, VAT, total payable. Read as stated. Never computed.

**Permits and references** — IDF reference, PVoC certificate of conformity number, import permit numbers, certificate of origin number, preferential treatment claimed.

**Extension block** — anything market-specific not captured above, including officer remarks, verification notes and release references.

