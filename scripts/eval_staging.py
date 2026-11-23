"""Pipeline adapter for scoring labelled documents against an isolated release stack."""

import hashlib
import os
from contextlib import ExitStack
from pathlib import Path
from time import monotonic, sleep
from typing import Any
from uuid import uuid4

import httpx

from trade_extract.services.evaluation import LabelJob, safe_path


def run_job(job: LabelJob, root: Path) -> dict[str, Any]:
    base_url = os.environ["EXTRACTION_EVALUATION_URL"]
    key = os.environ["EXTRACTION_EVALUATION_API_KEY"]
    if job.origin == "real" and os.environ.get("ALLOW_REAL_EVALUATION") != "1":
        raise ValueError("Real evaluation requires ALLOW_REAL_EVALUATION=1")
    url = httpx.URL(base_url)
    if url.scheme != "https" and url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Use HTTPS for the evaluation stack")

    files = {doc.file: safe_path(root, doc.file) for doc in job.documents}
    if any(path.suffix.lower() != ".pdf" for path in files.values()):
        raise ValueError("Stage evaluation uses source PDFs")
    hashes = {}
    for name, path in files.items():
        with path.open("rb") as source:
            hashes[name] = "sha256:" + hashlib.file_digest(source, "sha256").hexdigest()
    if len(set(hashes.values())) != len(hashes):
        raise ValueError(
            "A labelled job cannot include identical source files under different names"
        )

    with httpx.Client(base_url=url, headers={"Authorization": f"Bearer {key}"}, timeout=30) as api:
        with ExitStack() as sources:
            response = api.post(
                "/v1/extract",
                data={"job_ref": f"evaluation-{uuid4().hex}"},
                files=[
                    (
                        "documents",
                        (path.name, sources.enter_context(path.open("rb")), "application/pdf"),
                    )
                    for path in files.values()
                ],
            )
        response.raise_for_status()
        result_id = response.json()["result_id"]
        deadline = monotonic() + 300
        while monotonic() < deadline:
            response = api.get(
                f"/v1/results/{result_id}",
                params={"include": "documents,decision,provenance,conflicts"},
            )
            response.raise_for_status()
            result = response.json()
            if result["status"] in {"completed", "failed"}:
                break
            sleep(1)
        else:
            raise TimeoutError("Evaluation did not reach a terminal extraction result")

    remaining = list(result["documents"])
    mapped = []
    for label in job.documents:
        matching = [
            doc
            for doc in remaining
            if doc["source"]["file_hash"] == hashes[label.file]
            and (
                label.page_start is None
                or (doc["source"]["page_start"], doc["source"]["page_end"])
                == (label.page_start, label.page_end)
            )
        ]
        if len(matching) != 1:
            raise ValueError(f"Extraction segment does not match label {label.document_id!r}")
        doc = matching[0]
        remaining.remove(doc)
        mapped.append(doc | {"document_id": label.document_id, "decision": doc["decision"] or {}})
    if remaining:
        raise ValueError("Unlabelled extraction segments require investigation")
    return {
        "documents": mapped,
        "conflicts": [conflict["canonical_key"] for conflict in result["conflicts"]],
    }
