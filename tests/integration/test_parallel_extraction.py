from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier

import pytest

from tests.integration.test_lifecycle import docs, drain, post_files
from tests.support import make_pdf
from tests.unit.test_invoice_gate_conflicts import invoice
from trade_extract.domain.inference import ExtractionOutcome
from trade_extract.schemas.invoice import CommercialInvoice
from trade_extract.workers.handlers import extract_segment
from trade_extract.workers.runner import run_once

pytestmark = pytest.mark.integration


def test_ten_workers_extract_different_documents_at_the_same_time(api, tmp_path, monkeypatch):
    template = invoice(tmp_path).model_dump(mode="json")
    at_extraction = Barrier(10, timeout=30)

    def concurrent_extract(provider, *, source, segment_id, **kwargs):
        at_extraction.wait()
        payload = deepcopy(template)
        payload["source"] = source.model_dump(mode="json")
        payload["segment_id"] = segment_id
        for field in payload["fields"].values():
            field["provenance"]["document_hash"] = source.document_hash
        return ExtractionOutcome("extracted", document=CommercialInvoice.model_validate(payload))

    monkeypatch.setattr(extract_segment, "extract", concurrent_extract)

    def submit(index):
        pdf = make_pdf([["COMMERCIAL INVOICE", f"Seller Buyer Goods {index} USD Total 10"]])
        accepted = post_files(api, {"job_ref": f"parallel-{index}"}, docs(pdf))
        assert accepted.status_code == 202, accepted.text
        return accepted.json()["result_id"]

    def work(index, kind):
        return run_once(
            settings=api.settings,
            session_factory_=api.session_factory,
            clock=api.app.state.clock,
            worker_id=f"parallel-{kind}-{index}",
            kinds=[kind],
        )

    with ThreadPoolExecutor(10) as workers:
        result_ids = list(workers.map(submit, range(10)))
        # Production runs PDF rendering in separate processes. This test has
        # one process and exercises its thread-safe PDFium wrapper as well.
        assert all(workers.map(lambda i: work(i, "prepare_file"), range(10)))
        assert all(workers.map(lambda i: work(i, "extract_segment"), range(10)))

    drain(api)
    assert len(set(result_ids)) == 10
    for result_id in result_ids:
        result = api.client.get(f"/v1/results/{result_id}", headers=api.headers).json()
        assert result["status"] == "completed", result["progress"]
        assert result["documents"][0]["fields"]["totals.payable_amount"]["value"] == "10"
