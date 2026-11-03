"""Evaluate complete jobs using an explicitly injected, offline or scratch pipeline."""

import argparse
import importlib
from pathlib import Path

from trade_extract.services.evaluation import evaluate, load_corpus, write_reports


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument(
        "--pipeline",
        required=True,
        help="module:function; must use mock providers or separately authorized live execution",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    module, name = args.pipeline.rsplit(":", 1)
    pipeline = getattr(importlib.import_module(module), name)
    report = evaluate(load_corpus(args.corpus), args.corpus, pipeline)
    for path in write_reports(report, args.output):
        print(path)


if __name__ == "__main__":
    main()
