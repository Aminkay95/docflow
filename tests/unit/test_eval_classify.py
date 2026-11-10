"""Head-to-head classification eval: harness correctness on scripted fixtures.

The fixture corpus is synthetic; offline runs replay scripted verdicts through
MockProvider. These tests pin the scoring logic (accuracy, review rate,
routing agreement, Wilson intervals) and the fail-closed guards — they do not
measure live model accuracy.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import eval_classify
from trade_extract.config import Settings

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "classification"


def eval_settings() -> Settings:
    return Settings(provider="mock", segmentation_min_confidence=0.55)


def load_fixtures():
    origin, samples = eval_classify.load_corpus(FIXTURES / "corpus.json")
    verdicts = eval_classify.load_verdicts(FIXTURES / "verdicts.json")
    return origin, samples, verdicts


def test_fixture_corpus_is_valid_and_covered():
    origin, samples, verdicts = load_fixtures()
    assert origin == "synthetic"
    assert len(samples) == 16
    # Every registered type plus packing_list and unknown is represented.
    expected_types = {s.expected for s in samples}
    assert expected_types >= {
        "commercial_invoice",
        "proforma_invoice",
        "bill_of_lading",
        "delivery_order",
        "port_pass",
        "custom_entry",
        "verification_memo",
        "packing_list",
        "unknown",
    }
    assert set(verdicts) == {s.id for s in samples}


def test_offline_head_to_head_report_metrics():
    origin, samples, verdicts = load_fixtures()
    report = eval_classify.run(
        origin, samples, verdicts, settings=eval_settings(), allow_live=False
    )
    assert report["mode"] == "mock"
    assert report["samples"] == 16
    # Scripted verdicts are correct by construction; the lexicon misses do-01
    # (delivery order citing its B/L number near-ties the title) and wins the rest.
    assert report["lexicon"]["hits"] == 15
    assert report["lexicon"]["accuracy"] == 0.9375
    assert report["llm"]["hits"] == 16
    assert report["llm"]["accuracy"] == 1.0
    assert report["llm"]["errors"] == 0
    # do-01, unk-01, unk-02, edge-tie-01 route to review under both systems.
    assert report["lexicon"]["reviews"] == 4
    assert report["llm"]["reviews"] == 4


def test_disagreement_is_one_real_routing_conflict():
    _, samples, verdicts = load_fixtures()
    report = eval_classify.run(
        "synthetic", samples, verdicts, settings=eval_settings(), allow_live=False
    )
    disagreements = report["agreement"]["disagreements"]
    assert [row["id"] for row in disagreements] == ["do-01"]
    assert disagreements[0]["expected"] == "delivery_order"
    assert disagreements[0]["lexicon"] == "unknown"
    assert disagreements[0]["llm"] == "delivery_order"
    assert report["agreement"]["count"] == 15
    # pack-01 (packing_list vs unknown) is not a conflict: both route to review.
    assert all(row["id"] != "pack-01" for row in disagreements)


def test_packing_list_is_correct_for_both_systems_in_its_own_terms():
    _, samples, verdicts = load_fixtures()
    report = eval_classify.run(
        "synthetic", samples, verdicts, settings=eval_settings(), allow_live=False
    )
    packing = next(row for row in report["rows"] if row["id"] == "pack-01")
    assert packing["lexicon"] == "packing_list"
    assert packing["lexicon_correct"] is True
    assert packing["llm"] == "unknown"
    assert packing["llm_correct"] is True
    assert report["per_type"]["packing_list"] == {"n": 1, "lexicon": 1, "llm": 1}


def test_report_is_json_serializable_and_counts_tokens():
    origin, samples, verdicts = load_fixtures()
    report = eval_classify.run(
        origin, samples, verdicts, settings=eval_settings(), allow_live=False
    )
    encoded = json.dumps(report)
    assert "eval_classify" not in encoded
    assert report["tokens"]["calls"] == 16
    assert report["tokens"]["input"] > 0
    assert report["tokens"]["output"] > 0


def test_per_type_bucket_counts_match_samples():
    _, samples, verdicts = load_fixtures()
    report = eval_classify.run(
        "synthetic", samples, verdicts, settings=eval_settings(), allow_live=False
    )
    assert sum(bucket["n"] for bucket in report["per_type"].values()) == len(samples)
    for bucket in report["per_type"].values():
        assert bucket["lexicon"] <= bucket["n"]
        assert bucket["llm"] <= bucket["n"]


def test_wilson_interval_is_null_without_denominator_and_bounded():
    assert eval_classify.wilson_interval(0, 0) is None
    low, high = eval_classify.wilson_interval(10, 10)
    assert 0.0 <= low < high == 1.0 or high <= 1.0
    low, high = eval_classify.wilson_interval(0, 10)
    assert low == 0.0
    assert 0.0 < high < 1.0


def test_llm_target_maps_packing_list_to_unknown_only():
    assert eval_classify.llm_target("packing_list") == "unknown"
    assert eval_classify.llm_target("commercial_invoice") == "commercial_invoice"
    assert eval_classify.llm_target("unknown") == "unknown"


def test_offline_mode_refuses_real_corpus(tmp_path: Path):
    corpus = tmp_path / "real.json"
    corpus.write_text(
        json.dumps(
            {
                "origin": "real",
                "samples": [
                    {"id": "r1", "text": "COMMERCIAL INVOICE", "expected": "commercial_invoice"}
                ],
            }
        ),
        encoding="utf-8",
    )
    origin, samples = eval_classify.load_corpus(corpus)
    with pytest.raises(SystemExit, match="offline_mode_refuses_real_corpus"):
        eval_classify.run(origin, samples, {}, settings=eval_settings(), allow_live=False)


def test_offline_mode_requires_verdicts():
    _, samples, _ = load_fixtures()
    with pytest.raises(SystemExit, match="offline_mode_requires_verdicts"):
        eval_classify.run("synthetic", samples, None, settings=eval_settings(), allow_live=False)


def test_offline_mode_requires_every_sample_verdict():
    _, samples, verdicts = load_fixtures()
    verdicts.pop(samples[0].id)
    with pytest.raises(SystemExit, match="verdicts_missing_samples"):
        eval_classify.run(
            "synthetic", samples, verdicts, settings=eval_settings(), allow_live=False
        )


def test_live_mode_refuses_non_deepseek_provider():
    origin, samples, verdicts = load_fixtures()
    with pytest.raises(SystemExit, match="live_mode_requires_provider_deepseek"):
        eval_classify.run(origin, samples, verdicts, settings=eval_settings(), allow_live=True)


def test_corpus_validation_rejects_duplicate_ids_and_bad_types(tmp_path: Path):
    duplicate = tmp_path / "dup.json"
    duplicate.write_text(
        json.dumps(
            {
                "origin": "synthetic",
                "samples": [
                    {"id": "a", "text": "x", "expected": "unknown"},
                    {"id": "a", "text": "y", "expected": "unknown"},
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="corpus_sample_id_invalid"):
        eval_classify.load_corpus(duplicate)

    bad_type = tmp_path / "bad.json"
    bad_type.write_text(
        json.dumps(
            {
                "origin": "synthetic",
                "samples": [{"id": "a", "text": "x", "expected": "packing_list_extra"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="corpus_expected_type_invalid"):
        eval_classify.load_corpus(bad_type)


def test_cli_writes_json_report(tmp_path: Path):
    output = tmp_path / "report.json"
    eval_classify.main(
        [
            str(FIXTURES / "corpus.json"),
            "--verdicts",
            str(FIXTURES / "verdicts.json"),
            "--output",
            str(output),
        ]
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["samples"] == 16
    assert report["mode"] == "mock"
