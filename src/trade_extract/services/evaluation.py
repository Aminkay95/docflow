"""Complete-job evaluation with explicit origins, denominators and Wilson intervals."""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from trade_extract.domain.provenance import BBox
from trade_extract.schemas.registry import DOCUMENT_SCHEMAS, get_schema
from trade_extract.services.checks import decimal_value


class LabelField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str | None
    value_type: Literal["string", "decimal", "integer", "date", "datetime", "boolean", "enum"] = (
        "string"
    )
    page_index: int | None = Field(default=None, ge=0)
    bbox: BBox | None = None

    @model_validator(mode="after")
    def evidence(self) -> LabelField:
        if self.value is not None and (self.page_index is None or self.bbox is None):
            raise ValueError("present labels require page and box")
        if self.value is None and (self.page_index is not None or self.bbox is not None):
            raise ValueError("absent labels cannot carry evidence")
        if self.value is not None:
            normalize(self.value, self.value_type)
        return self


class LabelDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str = Field(min_length=1)
    file: str = Field(min_length=1)
    document_type: str
    fields: dict[str, LabelField]
    table_rows: dict[str, StrictInt]
    unreadable: bool = False

    @model_validator(mode="after")
    def valid_schema(self) -> LabelDocument:
        schema = get_schema(self.document_type)
        for path in self.fields:
            if schema.model.field_set.resolve(path) is None:
                raise ValueError(f"unknown field label: {path}")
        if any(n < 0 for n in self.table_rows.values()):
            raise ValueError("negative row label")
        if any(name not in schema.tables for name in self.table_rows):
            raise ValueError("unknown table label")
        required_paths = {spec.path for spec in schema.model.field_set.specs if not spec.indexed}
        if set(self.table_rows) != set(schema.tables):
            raise ValueError(
                "complete labels require every declared table row count, including zero"
            )
        for spec in schema.model.field_set.specs:
            table = spec.table
            if (
                spec.indexed
                and table is not None
                and table in self.table_rows
                and spec.path.count("[]") == 1
            ):
                required_paths.update(
                    spec.path.replace("[]", f"[{row}]") for row in range(self.table_rows[table])
                )
        if required_paths - set(self.fields):
            raise ValueError(
                "complete scalar and table-row labels required; mark absence explicitly"
            )
        for path in self.fields:
            match = re.match(r"([a-z_]+)\[(\d+)\]", path)
            if match and match[1] in self.table_rows and int(match[2]) >= self.table_rows[match[1]]:
                raise ValueError("label row exceeds declared table cardinality")
        return self


class LabelJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(min_length=1)
    origin: Literal["real", "synthetic"]
    complete: Literal[True]
    documents: list[LabelDocument] = Field(min_length=1)
    conflicts: list[str]

    @model_validator(mode="after")
    def unique(self) -> LabelJob:
        ids = [d.document_id for d in self.documents]
        if len(ids) != len(set(ids)) or len(self.conflicts) != len(set(self.conflicts)):
            raise ValueError("duplicate document or conflict label")
        return self


def safe_path(root: Path, relative: str) -> Path:
    # Reject Windows drive/UNC and traversal even when evaluating on Unix.
    if not relative or "\\" in relative or ":" in relative:
        raise ValueError("unsafe corpus path")
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("unsafe corpus path")
    resolved = (root / path).resolve(strict=True)
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise ValueError("unsafe corpus path")
    return resolved


def load_corpus(root: Path) -> list[LabelJob]:
    root = root.resolve(strict=True)
    jobs = [
        LabelJob.model_validate(json.loads(p.read_text(encoding="utf-8")))
        for p in sorted(root.glob("*.labels.json"))
    ]
    if not jobs:
        raise ValueError("no complete-job labels")
    ids = [j.job_id for j in jobs]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate job label")
    for job in jobs:
        for doc in job.documents:
            safe_path(root, doc.file)
    return jobs


def normalize(value: str, value_type: str) -> object:
    if value_type in ("decimal", "integer"):
        parsed = decimal_value(value)
        if value_type == "integer" and parsed != parsed.to_integral_value():
            raise ValueError("noninteger label")
        return parsed
    if value_type == "date":
        return date.fromisoformat(value)
    return " ".join(value.split()).casefold() if value_type == "enum" else value


def metric(hits: int, denominator: int) -> dict[str, Any]:
    if not denominator:
        return {"hits": hits, "denominator": 0, "rate": None, "ci95": None}
    p = hits / denominator
    z = 1.959963984540054
    divisor = 1 + z * z / denominator
    center = (p + z * z / (2 * denominator)) / divisor
    delta = z * math.sqrt((p * (1 - p) + z * z / (4 * denominator)) / denominator) / divisor
    return {
        "hits": hits,
        "denominator": denominator,
        "rate": p,
        "ci95": [max(0, center - delta), min(1, center + delta)],
    }


PROVENANCE_IOU_THRESHOLD = 0.5


def type_counts() -> dict[str, Any]:
    return dict(
        documents=0,
        review=0,
        **{"pass": 0},
        unreadable=0,
        unreadable_truth=0,
        unreadable_tp=0,
        classification_hits=0,
        rows_hit=0,
        rows_labelled=0,
        rows_predicted=0,
        rows_excess=0,
        row_exact=0,
        row_tables=0,
        fields={},
    )


def localized_enclosure(actual: BBox, expected: BBox) -> bool:
    enclosure = (
        actual.x0 <= expected.x0
        and actual.y0 <= expected.y0
        and actual.x1 >= expected.x1
        and actual.y1 >= expected.y1
    )
    expected_area = expected.width * expected.height
    actual_area = actual.width * actual.height
    return (
        enclosure
        and expected_area > 0
        and actual_area > 0
        and expected_area / actual_area >= PROVENANCE_IOU_THRESHOLD
    )


Pipeline = Callable[[LabelJob, Path], dict[str, Any]]


def evaluate(jobs: list[LabelJob], root: Path, pipeline: Pipeline) -> dict[str, Any]:
    """Pipeline receives whole jobs; labels are never used as predictions here."""
    buckets: dict[str, dict[str, Any]] = {
        origin: {
            "jobs": 0,
            "types": {name: type_counts() for name in DOCUMENT_SCHEMAS},
            "conflict_tp": 0,
            "conflict_predicted": 0,
            "conflict_labelled": 0,
        }
        for origin in ("real", "synthetic")
    }
    fields: dict[tuple[str, str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for job in jobs:
        predicted = pipeline(job, root)
        docs = predicted.get("documents", [])
        by_id = {d["document_id"]: d for d in docs}
        if len(by_id) != len(docs) or set(by_id) != {d.document_id for d in job.documents}:
            raise ValueError("pipeline document identities must match the complete job")
        bucket = buckets[job.origin]
        bucket["jobs"] += 1
        expected_conflicts = set(job.conflicts)
        actual_conflicts = set(predicted.get("conflicts", []))
        bucket["conflict_tp"] += len(actual_conflicts & expected_conflicts)
        bucket["conflict_predicted"] += len(actual_conflicts)
        bucket["conflict_labelled"] += len(expected_conflicts)
        for label in job.documents:
            doc = by_id[label.document_id]
            predicted_type = doc.get("document_type")
            if predicted_type not in (*DOCUMENT_SCHEMAS, "unknown", "packing_list"):
                raise ValueError("invalid predicted document type")
            type_bucket = bucket["types"][label.document_type]
            type_bucket["classification_hits"] += predicted_type == label.document_type
            type_bucket["unreadable_truth"] += label.unreadable
            type_bucket["unreadable_tp"] += label.unreadable and doc.get("status") == "unreadable"
            type_bucket["documents"] += 1
            decision = doc.get("decision", {}).get("value")
            type_bucket["review"] += decision in ("review", "failed", "unreadable")
            type_bucket["pass"] += decision == "pass"
            type_bucket["unreadable"] += doc.get("status") == "unreadable"
            actual_fields = doc.get("fields") or {}
            # Every emitted field must be labelled, explicitly absent if appropriate.
            if set(actual_fields) - set(label.fields):
                raise ValueError("incomplete field labels for emitted predictions")
            for path, truth in label.fields.items():
                count = fields[(job.origin, label.document_type, path)]
                count["documents"] += 1
                count["labelled_present"] += truth.value is not None
                actual = actual_fields.get(path)
                present = actual is not None
                count["predicted_present"] += present
                match = truth.value is None and not present
                if truth.value is not None and actual is not None:
                    try:
                        match = normalize(actual["value"], truth.value_type) == normalize(
                            truth.value, truth.value_type
                        )
                    except (ValueError, KeyError, TypeError):
                        match = False
                    count["true_positive"] += match
                    provenance = actual.get("provenance", {})
                    box = provenance.get("bbox")
                    count["provenance_labelled"] += 1
                    if box is not None and provenance.get("page_index") == truth.page_index:
                        try:
                            actual_box = BBox.model_validate(box)
                            expected_box = truth.bbox
                            count["provenance_hit"] += bool(
                                expected_box and localized_enclosure(actual_box, expected_box)
                            )
                        except ValueError:
                            pass
                elif truth.value is not None:
                    count["provenance_labelled"] += 1
                count["exact"] += match
            for name, rows in label.table_rows.items():
                actual_rows = doc.get("tables", {}).get(name, {}).get("row_count_extracted", 0)
                if type(actual_rows) is not int or actual_rows < 0:
                    raise ValueError("predicted row count must be a nonnegative integer")
                type_bucket["rows_hit"] += min(rows, actual_rows)
                type_bucket["rows_labelled"] += rows
                type_bucket["rows_predicted"] += actual_rows
                type_bucket["rows_excess"] += max(0, actual_rows - rows)
                type_bucket["row_exact"] += rows == actual_rows
                type_bucket["row_tables"] += 1
    for (origin, doc_type, path), counts in fields.items():
        buckets[origin]["types"][doc_type]["fields"][path] = {
            "documents": counts["documents"],
            "sample_sufficient": counts["documents"] >= 20,
            "exact_match": metric(counts["exact"], counts["documents"]),
            "precision": metric(counts["true_positive"], counts["predicted_present"]),
            "recall": metric(counts["true_positive"], counts["labelled_present"]),
            "provenance_hit": metric(counts["provenance_hit"], counts["provenance_labelled"]),
        }
    for bucket in buckets.values():
        bucket["conflict_precision"] = metric(
            bucket.pop("conflict_tp"), bucket.pop("conflict_predicted")
        )
        bucket["conflict_recall"] = metric(
            bucket["conflict_precision"]["hits"], bucket.pop("conflict_labelled")
        )
        for result in bucket["types"].values():
            n = result["documents"]
            result["sample_sufficient"] = n >= 20
            result["ranking"] = "eligible" if n >= 20 else "refused: fewer than 20 documents"
            result["classification_accuracy"] = metric(result.pop("classification_hits"), n)
            result["unreadable_precision"] = metric(result["unreadable_tp"], result["unreadable"])
            result["unreadable_recall"] = metric(
                result.pop("unreadable_tp"), result.pop("unreadable_truth")
            )
            for name in ("review", "pass", "unreadable"):
                result[f"{name}_rate"] = metric(result.pop(name), n)
            result["row_precision"] = metric(result["rows_hit"], result.pop("rows_predicted"))
            result["row_count_exact_match"] = metric(
                result.pop("row_exact"), result.pop("row_tables")
            )
            result["excess_rows"] = result.pop("rows_excess")
            result["row_completeness"] = metric(result.pop("rows_hit"), result.pop("rows_labelled"))
    return {
        "evaluation_version": "complete-job-v2",
        "provenance_iou_threshold": PROVENANCE_IOU_THRESHOLD,
        "origins": buckets,
        "real_corpus_status": "supplied"
        if buckets["real"]["jobs"]
        else "absent; real accuracy and calibration unmeasured",
        "calibration": "Synthetic scores must never tune downstream thresholds.",
    }


def write_reports(report: dict[str, Any], output: Path) -> tuple[Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    json_path, markdown_path = output / "evaluation.json", output / "evaluation.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = ["# Evaluation", "", report["real_corpus_status"], "", report["calibration"]]
    for origin, bucket in report["origins"].items():
        lines += [
            "",
            f"## {origin} ({bucket['jobs']} complete jobs)",
            "",
            "| Type | Documents | Ranking |",
            "| --- | ---: | --- |",
        ]
        for doc_type, result in bucket["types"].items():
            lines.append(f"| {doc_type} | {result['documents']} | {result['ranking']} |")
        lines += [
            "",
            "All rates below include hits, denominators and 95% Wilson intervals.",
            "",
            "```json",
            json.dumps(bucket, indent=2, sort_keys=True),
            "```",
        ]
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path
