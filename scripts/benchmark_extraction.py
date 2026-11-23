"""Measure upload-to-result latency against a running test stack."""

import argparse
import asyncio
import json
import math
import os
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx

TARGET_SECONDS = 120

async def measure(client: httpx.AsyncClient, path: Path, timeout: float) -> dict:
    started = perf_counter()
    result = {"file": str(path), "status": "timeout", "documents": []}
    try:
        async with asyncio.timeout(timeout):
            with path.open("rb") as source:
                response = await client.post(
                    "/v1/extract",
                    data={"job_ref": f"benchmark-{uuid4().hex}"},
                    files={"documents": (path.name, source, "application/pdf")},
                )
            response.raise_for_status()
            result_id = response.json()["result_id"]
            result["result_id"] = result_id
            while True:
                response = await client.get(
                    f"/v1/results/{result_id}", params={"include": "documents,decision"}
                )
                response.raise_for_status()
                payload = response.json()
                if payload["status"] in {"completed", "failed"}:
                    result["status"] = payload["status"]
                    result["documents"] = [
                        {
                            "type": doc["document_type"],
                            "status": doc["status"],
                            "fields": len(doc.get("fields") or {}),
                            "tables": len(doc.get("tables") or {}),
                            "diagnostics": (doc.get("decision") or {}).get("diagnostics", []),
                        }
                        for doc in payload["documents"]
                    ]
                    break
                await asyncio.sleep(1)
    except TimeoutError:
        pass
    except httpx.HTTPStatusError as exc:
        result["status"] = f"http_{exc.response.status_code}"
    except httpx.RequestError:
        result["status"] = "connection_error"
    result["seconds"] = perf_counter() - started
    return result


async def benchmark(args: argparse.Namespace, key: str) -> dict:
    results = []
    async with httpx.AsyncClient(
        base_url=args.url, headers={"Authorization": f"Bearer {key}"}, timeout=None
    ) as client:
        for offset in range(0, len(args.files), args.concurrency):
            batch = args.files[offset : offset + args.concurrency]
            results.extend(await asyncio.gather(*(measure(client, p, args.timeout) for p in batch)))
    successful = [
        result["seconds"]
        for result in results
        if result["status"] == "completed"
        and result["documents"]
        and all(
            doc["status"] in {"extracted", "review_required", "resolved"}
            and doc["type"] not in {None, "unknown"}
            and doc["fields"] > 0
            for doc in result["documents"]
        )
    ]
    successful.sort()
    p95 = successful[math.ceil(len(successful) * 0.95) - 1] if successful else None
    under_target = sum(seconds < TARGET_SECONDS for seconds in successful)
    return {
        "cohort": args.cohort,
        "concurrency": args.concurrency,
        "submitted": len(results),
        "successful": len(successful),
        "p95_seconds_successful": round(p95, 3) if p95 is not None else None,
        "under_120_seconds": under_target,
        "target_met": bool(results) and under_target == len(results),
        "results": [{**result, "seconds": round(result["seconds"], 3)} for result in results],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--cohort", choices=("digital", "scan", "mixed"), required=True)
    parser.add_argument("--concurrency", type=int, choices=(1, 10), required=True)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout < TARGET_SECONDS:
        parser.error(f"--timeout must be finite and at least {TARGET_SECONDS} seconds")
    try:
        url = httpx.URL(args.url)
    except httpx.InvalidURL as exc:
        parser.error(f"invalid benchmark URL: {exc}")
    if not url.host or not (
        url.scheme == "https"
        or (url.scheme == "http" and url.host in {"localhost", "127.0.0.1", "::1"})
    ):
        parser.error("use an HTTPS benchmark URL or a local HTTP URL")
    if len(args.files) < args.concurrency:
        parser.error("provide at least as many PDFs as concurrent uploads")
    if any(not path.is_file() or path.suffix.lower() != ".pdf" for path in args.files):
        parser.error("all inputs must be existing PDF files")
    key = os.environ.get("EXTRACTION_BENCHMARK_API_KEY")
    if not key:
        parser.error("set EXTRACTION_BENCHMARK_API_KEY to a test client key")
    report = asyncio.run(benchmark(args, key))
    print(json.dumps(report, indent=2))
    return 0 if report["target_met"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
