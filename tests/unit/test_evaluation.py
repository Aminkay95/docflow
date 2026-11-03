import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from trade_extract.schemas.invoice import INVOICE_FIELD_SET
from trade_extract.schemas.registry import DOCUMENT_SCHEMAS
from trade_extract.services.evaluation import LabelJob, evaluate, load_corpus, metric, write_reports


def job(origin="synthetic", identifier="job_1"):
    raw = {
        "job_id": identifier,
        "origin": origin,
        "complete": True,
        "documents": [
            {
                "document_id": "doc_1",
                "file": "invoice.txt",
                "document_type": "commercial_invoice",
                "fields": {
                    "invoice.number": {
                        "value": "INV-1",
                        "page_index": 0,
                        "bbox": {"x0": 0.1, "y0": 0.1, "x1": 0.3, "y1": 0.2},
                    },
                    "invoice.due_date": {"value": None, "value_type": "date"},
                },
                "table_rows": {"line_items": 2},
            }
        ],
        "conflicts": ["invoice.currency"],
    }
    document = raw["documents"][0]
    for spec in INVOICE_FIELD_SET.specs:
        if not spec.indexed:
            document["fields"].setdefault(
                spec.path, {"value": None, "value_type": spec.value_type.value}
            )
        elif spec.table == "line_items" and spec.path.count("[]") == 1:
            for row in range(2):
                document["fields"][spec.path.replace("[]", f"[{row}]")] = {
                    "value": None,
                    "value_type": spec.value_type.value,
                }
    return LabelJob.model_validate(raw)


def prediction(label, root):
    return {
        "documents": [
            {
                "document_id": "doc_1",
                "document_type": "commercial_invoice",
                "status": "extracted",
                "decision": {"value": "review"},
                "fields": {
                    "invoice.number": {
                        "value": "INV-1",
                        "provenance": {
                            "page_index": 0,
                            "bbox": {"x0": 0.1, "y0": 0.1, "x1": 0.3, "y1": 0.2},
                        },
                    }
                },
                "tables": {"line_items": {"row_count_extracted": 1}},
            }
        ],
        "conflicts": ["invoice.currency"],
    }


def test_origins_denominators_confidence_intervals_and_reports(tmp_path):
    report = evaluate([job(), job("real", "real_1")], tmp_path, prediction)
    real = report["origins"]["real"]["types"]["commercial_invoice"]
    synthetic = report["origins"]["synthetic"]["types"]["commercial_invoice"]
    assert real["documents"] == synthetic["documents"] == 1
    assert synthetic["sample_sufficient"] is False and synthetic["ranking"].startswith("refused")
    assert synthetic["row_completeness"]["denominator"] == 2
    assert synthetic["row_completeness"]["hits"] == 1
    assert synthetic["fields"]["invoice.number"]["provenance_hit"]["rate"] == 1
    absent = synthetic["fields"]["invoice.due_date"]
    assert absent["exact_match"]["rate"] == 1 and absent["recall"]["rate"] is None
    assert metric(1, 1)["ci95"][0] < 1
    paths = write_reports(report, tmp_path / "report")
    assert json.loads(paths[0].read_text()) == report
    assert "synthetic" in paths[1].read_text() and "denominators" in paths[1].read_text()
    assert evaluate([job()], tmp_path, prediction)["real_corpus_status"].startswith("absent")


def test_corpus_intake_rejects_unsafe_and_malformed_labels(tmp_path):
    source = tmp_path / "invoice.txt"
    source.write_text("Synthetic invoice evidence")
    manifest = tmp_path / "one.labels.json"
    raw = job().model_dump(mode="json")
    manifest.write_text(json.dumps(raw))
    assert len(load_corpus(tmp_path)) == 1
    for unsafe in ("../secret", "C:/secret", "/secret", "..\\secret"):
        broken = deepcopy(raw)
        broken["documents"][0]["file"] = unsafe
        manifest.write_text(json.dumps(broken))
        with pytest.raises((ValueError, FileNotFoundError)):
            load_corpus(tmp_path)
    raw["origin"] = "invented"
    with pytest.raises(ValidationError):
        LabelJob.model_validate(raw)
    raw = job().model_dump(mode="json")
    raw["documents"][0]["fields"]["invoice.number"]["bbox"] = None
    with pytest.raises(ValidationError):
        LabelJob.model_validate(raw)


def test_missing_predictions_and_incomplete_labels_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="identities"):
        evaluate([job()], tmp_path, lambda *_: {"documents": []})

    def extras(label, root):
        output = prediction(label, root)
        output["documents"][0]["fields"]["invoice.currency"] = {"value": "USD"}
        return output

    report = evaluate([job()], tmp_path, extras)
    metric = report["origins"]["synthetic"]["types"]["commercial_invoice"]["fields"][
        "invoice.currency"
    ]["precision"]
    assert metric["denominator"] == 1 and metric["hits"] == 0


def test_complete_truth_is_validated_without_predictions():
    raw = job().model_dump(mode="json")
    document = raw["documents"][0]
    for key in ("invoice.currency", "line_items[1].quantity"):
        bad = deepcopy(raw)
        del bad["documents"][0]["fields"][key]
        with pytest.raises(ValidationError, match="complete scalar"):
            LabelJob.model_validate(bad)
    for metadata in ({}, None, {"line_items": -1}, {"line_items": 1.5}):
        bad = deepcopy(raw)
        bad["documents"][0]["table_rows"] = metadata
        with pytest.raises(ValidationError):
            LabelJob.model_validate(bad)
    del document["table_rows"]
    with pytest.raises(ValidationError):
        LabelJob.model_validate(raw)


def test_classification_unreadable_localization_and_excess_rows(tmp_path):
    truth = job()
    truth = truth.model_copy(
        update={"documents": [truth.documents[0].model_copy(update={"unreadable": True})]}
    )

    def wrong(label, root):
        output = prediction(label, root)
        doc = output["documents"][0]
        doc["document_type"] = "bill_of_lading"
        doc["fields"]["invoice.number"]["provenance"]["bbox"] = dict(x0=0, y0=0, x1=1, y1=1)
        doc["tables"]["line_items"]["row_count_extracted"] = 3
        return output

    report = evaluate([truth], tmp_path, wrong)
    result = report["origins"]["synthetic"]["types"]["commercial_invoice"]
    assert result["classification_accuracy"]["hits"] == 0
    assert result["unreadable_recall"] == metric(0, 1)
    assert result["fields"]["invoice.number"]["provenance_hit"]["rate"] == 0
    assert result["row_completeness"]["rate"] == 1
    assert result["excess_rows"] == 1 and result["row_precision"] == metric(2, 3)
    assert result["row_count_exact_match"] == metric(0, 1)
    assert set(report["origins"]["real"]["types"]) == set(DOCUMENT_SCHEMAS)
    assert all(
        r["documents"] == 0 and not r["sample_sufficient"]
        for r in report["origins"]["real"]["types"].values()
    )
    for count in (-1, 1.1, True, "2"):

        def invalid(label, root, count=count):
            output = prediction(label, root)
            output["documents"][0]["tables"]["line_items"]["row_count_extracted"] = count
            return output

        with pytest.raises(ValueError, match="nonnegative integer"):
            evaluate([job()], tmp_path, invalid)

    def invalid_type(label, root):
        output = prediction(label, root)
        output["documents"][0]["document_type"] = "invented"
        return output

    with pytest.raises(ValueError, match="predicted document type"):
        evaluate([job()], tmp_path, invalid_type)


@pytest.mark.parametrize("origin", ["real", "synthetic"])
def test_conflict_precision_and_recall_with_false_positives_and_negatives(tmp_path, origin):
    truth = job(origin)
    truth = truth.model_copy(update={"conflicts": ["invoice.currency", "bill_of_lading.number"]})

    def pipeline(label, root):
        output = prediction(label, root)
        output["conflicts"] = ["invoice.currency", "shipment.vessel"]
        return output

    report = evaluate([truth], tmp_path, pipeline)
    assert report["origins"][origin]["conflict_precision"] == metric(1, 2)
    assert report["origins"][origin]["conflict_recall"] == metric(1, 2)


def test_unreadable_detection_precision_and_recall_use_label_truth(tmp_path):
    truths = []
    for i, unreadable in enumerate((True, False, True)):
        label = job(identifier=f"j{i}")
        truths.append(
            label.model_copy(
                update={
                    "documents": [label.documents[0].model_copy(update={"unreadable": unreadable})]
                }
            )
        )

    def pipeline(label, root):
        output = prediction(label, root)
        output["documents"][0]["status"] = (
            "unreadable" if label.job_id in ("j0", "j1") else "extracted"
        )
        return output

    result = evaluate(truths, tmp_path, pipeline)["origins"]["synthetic"]["types"][
        "commercial_invoice"
    ]
    assert result["classification_accuracy"] == metric(3, 3)
    assert result["unreadable_precision"] == metric(1, 2)
    assert result["unreadable_recall"] == metric(1, 2)
