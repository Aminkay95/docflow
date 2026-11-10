"""Head-to-head classification evaluation: lexicon vs stage-01 LLM classifier.

Compares ``services.segmentation.classify_page`` (regex lexicon) against
``services.stages.classify_segment`` (LLM reading ``01-classify.md``) on a
labelled text corpus.

Offline mode (the default) replays scripted ``MockProvider`` verdicts: no
credentials, no network, deterministic. It measures harness agreement and the
scoring logic, NOT live model accuracy. Live mode (``--allow-live``) calls
DeepSeek with real credentials against a separately supplied corpus; see
``docs/evaluation.md`` for corpus requirements.

Reports are descriptive evidence, never automatic policy promotion.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trade_extract.config import Settings
from trade_extract.domain.inference import ProviderAttempt, ProviderResult
from trade_extract.providers.mock import MockProvider
from trade_extract.services.segmentation import RECOGNISED_TYPES, UNKNOWN, classify_page
from trade_extract.services.stages import CLASSIFICATION_DOCUMENT_TYPE, classify_segment

VALID_EXPECTED = frozenset(RECOGNISED_TYPES) | {UNKNOWN}


@dataclass(frozen=True)
class Sample:
    id: str
    text: str
    expected: str


def _fail(message: str) -> SystemExit:
    return SystemExit(f"eval_classify:{message}")


def load_corpus(path: Path) -> tuple[str, list[Sample]]:
    """Validate and load the labelled corpus. Offline runs need synthetic origin."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _fail(f"corpus_unreadable:{exc}") from exc
    origin = raw.get("origin")
    if origin not in ("synthetic", "real"):
        raise _fail("corpus_origin_must_be_synthetic_or_real")
    rows = raw.get("samples")
    if not isinstance(rows, list) or not rows:
        raise _fail("corpus_samples_empty")
    samples: list[Sample] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        sample_id = str(row.get("id", "")).strip()
        if not sample_id or sample_id in seen:
            raise _fail(f"corpus_sample_id_invalid:{index}")
        seen.add(sample_id)
        expected = str(row.get("expected", "")).strip()
        if expected not in VALID_EXPECTED:
            raise _fail(f"corpus_expected_type_invalid:{sample_id}")
        text = row.get("text")
        if not isinstance(text, str):
            raise _fail(f"corpus_text_missing:{sample_id}")
        samples.append(Sample(sample_id, text, expected))
    return origin, samples


def load_verdicts(path: Path) -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _fail(f"verdicts_unreadable:{exc}") from exc
    if not isinstance(raw, dict) or not raw:
        raise _fail("verdicts_empty")
    verdicts: dict[str, dict[str, Any]] = {}
    for sample_id, verdict in raw.items():
        if not isinstance(verdict, dict):
            raise _fail(f"verdict_not_object:{sample_id}")
        if "document_type" not in verdict or "confidence" not in verdict:
            raise _fail(f"verdict_fields_missing:{sample_id}")
        verdicts[str(sample_id)] = verdict
    return verdicts


def source_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def wilson_interval(hits: int, total: int) -> list[float] | None:
    """95% Wilson interval for a proportion; null when there is no denominator."""
    if total == 0:
        return None
    z = 1.96
    p = hits / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def llm_target(expected: str) -> str:
    """packing_list is segmented but not registered: the LLM contract maps it
    to ``unknown``, so that is the LLM's correct answer for it."""
    return UNKNOWN if expected == "packing_list" else expected


def run(
    origin: str,
    samples: list[Sample],
    verdicts: dict[str, dict[str, Any]] | None,
    *,
    settings: Settings,
    allow_live: bool,
) -> dict[str, Any]:
    if allow_live:
        if settings.provider != "deepseek":
            raise _fail("live_mode_requires_provider_deepseek")
        from trade_extract.providers import make_provider

        attempts: list[ProviderAttempt] = []
        provider = make_provider(settings, recorder=attempts.append)
        mode = "live"
    else:
        if origin != "synthetic":
            raise _fail("offline_mode_refuses_real_corpus")
        if not verdicts:
            raise _fail("offline_mode_requires_verdicts")
        missing = [s.id for s in samples if s.id not in verdicts]
        if missing:
            raise _fail(f"verdicts_missing_samples:{','.join(missing)}")
        attempts = []
        fixtures: dict[tuple[str, str], list[ProviderResult]] = {}
        for sample in samples:
            verdict = verdicts[sample.id]
            fixtures[(CLASSIFICATION_DOCUMENT_TYPE, source_hash(sample.text))] = [
                ProviderResult(
                    raw_json={
                        "document_type": verdict["document_type"],
                        "confidence": verdict["confidence"],
                        "reason": verdict.get("reason", ""),
                    },
                    model=str(verdict.get("model", "scripted")),
                    input_tokens=int(verdict.get("input_tokens", 0)),
                    output_tokens=int(verdict.get("output_tokens", 0)),
                    cached_tokens=int(verdict.get("cached_tokens", 0)),
                )
            ]
        provider = MockProvider(fixtures, recorder=attempts.append)
        mode = "mock"

    min_confidence = settings.segmentation_min_confidence
    rows: list[dict[str, Any]] = []
    lexicon_hits = llm_hits = llm_errors = 0
    per_type: dict[str, dict[str, int]] = {}
    disagreements: list[dict[str, str]] = []

    for sample in samples:
        lexicon = classify_page(sample.text, min_confidence=min_confidence)
        llm_error: str | None = None
        try:
            llm_verdict = classify_segment(
                provider,
                evidence=sample.text,
                source_hash=source_hash(sample.text),
                segment_id=sample.id,
                settings=settings,
            )
            llm_prediction = llm_verdict.document_type
            llm_confidence = llm_verdict.confidence
            llm_reason = llm_verdict.reason
        except Exception as exc:  # ProviderError, JSON and contract violations
            llm_error = type(exc).__name__
            llm_prediction = UNKNOWN
            llm_confidence = None
            llm_reason = ""
            llm_errors += 1

        lexicon_correct = lexicon.document_type == sample.expected
        llm_correct = llm_prediction == llm_target(sample.expected)
        lexicon_hits += lexicon_correct
        llm_hits += llm_correct
        bucket = per_type.setdefault(sample.expected, {"n": 0, "lexicon": 0, "llm": 0})
        bucket["n"] += 1
        bucket["lexicon"] += lexicon_correct
        bucket["llm"] += llm_correct
        # Compare routing outcome: packing_list (segmentation-only) and unknown
        # both end in review, so a raw-label difference there is not a conflict.
        if llm_target(lexicon.document_type) != llm_prediction:
            disagreements.append(
                {
                    "id": sample.id,
                    "expected": sample.expected,
                    "lexicon": lexicon.document_type,
                    "llm": llm_prediction,
                }
            )
        rows.append(
            {
                "id": sample.id,
                "expected": sample.expected,
                "lexicon": lexicon.document_type,
                "lexicon_confidence": lexicon.confidence,
                "llm": llm_prediction,
                "llm_confidence": llm_confidence,
                "llm_reason": llm_reason,
                "llm_error": llm_error,
                "lexicon_correct": lexicon_correct,
                "llm_correct": llm_correct,
            }
        )

    total = len(samples)
    lexicon_reviews = sum(1 for row in rows if row["lexicon"] == UNKNOWN)
    llm_reviews = sum(1 for row in rows if row["llm"] == UNKNOWN)
    report: dict[str, Any] = {
        "mode": mode,
        "origin": origin,
        "min_confidence": min_confidence,
        "samples": total,
        "lexicon": {
            "accuracy": round(lexicon_hits / total, 4),
            "accuracy_wilson95": wilson_interval(lexicon_hits, total),
            "hits": lexicon_hits,
            "review_rate": round(lexicon_reviews / total, 4),
            "reviews": lexicon_reviews,
        },
        "llm": {
            "accuracy": round(llm_hits / total, 4),
            "accuracy_wilson95": wilson_interval(llm_hits, total),
            "hits": llm_hits,
            "review_rate": round(llm_reviews / total, 4),
            "reviews": llm_reviews,
            "errors": llm_errors,
        },
        "agreement": {
            "rate": round((total - len(disagreements)) / total, 4),
            "count": total - len(disagreements),
            "disagreements": disagreements,
        },
        "per_type": per_type,
        "tokens": {
            "input": sum(a.input_tokens for a in attempts),
            "output": sum(a.output_tokens for a in attempts),
            "cached": sum(a.cached_tokens for a in attempts),
            "calls": len(attempts),
        },
        "rows": rows,
    }
    return report


def print_summary(report: dict[str, Any]) -> None:
    lexicon = report["lexicon"]
    llm = report["llm"]
    agreement = report["agreement"]
    tokens = report["tokens"]
    print(f"mode: {report['mode']}  origin: {report['origin']}  samples: {report['samples']}")
    print(
        f"lexicon accuracy: {lexicon['hits']}/{report['samples']} = {lexicon['accuracy']} "
        f"review rate: {lexicon['reviews']}/{report['samples']}"
    )
    print(
        f"llm accuracy:     {llm['hits']}/{report['samples']} = {llm['accuracy']} "
        f"review rate: {llm['reviews']}/{report['samples']} errors: {llm['errors']}"
    )
    print(f"agreement: {agreement['count']}/{report['samples']} = {agreement['rate']}")
    for row in agreement["disagreements"]:
        print(
            f"  disagreement {row['id']}: expected={row['expected']} "
            f"lexicon={row['lexicon']} llm={row['llm']}"
        )
    print(
        f"tokens: input={tokens['input']} output={tokens['output']} "
        f"cached={tokens['cached']} calls={tokens['calls']}"
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path, help="labelled corpus JSON (see docs/evaluation.md)")
    parser.add_argument(
        "--verdicts",
        type=Path,
        help="scripted LLM verdicts for offline mode (required unless --allow-live)",
    )
    parser.add_argument("--output", type=Path, help="write the full JSON report here")
    parser.add_argument(
        "--allow-live",
        action="store_true",
        help="authorize live DeepSeek calls (requires PROVIDER=deepseek and credentials)",
    )
    args = parser.parse_args(argv)

    origin, samples = load_corpus(args.corpus)
    verdicts = load_verdicts(args.verdicts) if args.verdicts else None
    report = run(
        origin,
        samples,
        verdicts,
        settings=Settings(),
        allow_live=args.allow_live,
    )
    print_summary(report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print(args.output)


if __name__ == "__main__":
    try:
        main()
    except SystemExit as exc:
        if isinstance(exc.code, str):
            print(exc.code, file=sys.stderr)
            raise SystemExit(2) from None
        raise
