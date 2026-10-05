"""Evaluate RAG retrieval accuracy with a labeled CSV test set.

This script does not print environment variables or API keys. It only prints
retrieval results and aggregate metrics.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from app.services.vector_store_manager import vector_store_manager


@dataclass
class EvalCase:
    case_id: str
    question: str
    expected_file: str


@dataclass
class EvalResult:
    case_id: str
    question: str
    expected_file: str
    retrieved_files: list[str]
    hit: bool
    rank: int | None


def load_cases(path: Path) -> list[EvalCase]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    cases: list[EvalCase] = []
    for row in rows:
        case_id = (row.get("id") or "").strip()
        question = (row.get("question") or "").strip()
        expected_file = (row.get("expected_file") or "").strip()
        if not case_id or not question or not expected_file:
            continue
        cases.append(EvalCase(case_id, question, expected_file))
    return cases


def metadata_file_name(metadata: dict) -> str:
    nested = metadata.get("metadata")
    if isinstance(nested, dict):
        metadata = {**nested, **metadata}

    file_name = metadata.get("_file_name")
    if file_name:
        return str(file_name).replace("\\", "/")

    source = metadata.get("_source") or metadata.get("source")
    if source:
        return Path(str(source).replace("\\", "/")).name

    return ""


def is_expected_file(retrieved_file: str, expected_file: str) -> bool:
    expected_name = Path(expected_file.replace("\\", "/")).name.lower()
    return retrieved_file.lower() == expected_name


def evaluate_case(case: EvalCase, top_k: int) -> EvalResult:
    docs = vector_store_manager.similarity_search(case.question, k=top_k)
    retrieved_files = [metadata_file_name(doc.metadata or {}) for doc in docs]

    rank = None
    for idx, file_name in enumerate(retrieved_files, start=1):
        if is_expected_file(file_name, case.expected_file):
            rank = idx
            break

    return EvalResult(
        case_id=case.case_id,
        question=case.question,
        expected_file=case.expected_file,
        retrieved_files=retrieved_files,
        hit=rank is not None,
        rank=rank,
    )


def write_results(path: Path, results: Iterable[EvalResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "id",
                "question",
                "expected_file",
                "retrieved_files",
                "hit",
                "rank",
            ],
        )
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "id": result.case_id,
                    "question": result.question,
                    "expected_file": result.expected_file,
                    "retrieved_files": " | ".join(result.retrieved_files),
                    "hit": "1" if result.hit else "0",
                    "rank": result.rank or "",
                }
            )


def print_summary(results: list[EvalResult], top_k: int) -> None:
    total = len(results)
    hits = sum(1 for item in results if item.hit)
    hit_at_k = hits / total if total else 0.0
    mrr = sum(1 / item.rank for item in results if item.rank) / total if total else 0.0

    print(f"Total cases: {total}")
    print(f"Hits@{top_k}: {hits}")
    print(f"Hit@{top_k}: {hit_at_k:.4f}")
    print(f"MRR@{top_k}: {mrr:.4f}")
    print()
    print("Missed cases:")
    misses = [item for item in results if not item.hit]
    if not misses:
        print("  None")
        return

    for item in misses:
        retrieved = ", ".join(item.retrieved_files) or "no result"
        print(f"  {item.case_id}: expected={item.expected_file}; retrieved={retrieved}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate RAG retrieval Hit@K.")
    parser.add_argument(
        "--dataset",
        default="eval/retrieval_test_set.csv",
        help="CSV file with id, question, and expected_file columns.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="Number of retrieved chunks to evaluate.",
    )
    parser.add_argument(
        "--output",
        default="eval/retrieval_eval_results.csv",
        help="Where to write detailed evaluation results.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dataset_path = Path(args.dataset)
    output_path = Path(args.output)

    cases = load_cases(dataset_path)
    results = [evaluate_case(case, args.top_k) for case in cases]

    write_results(output_path, results)
    print_summary(results, args.top_k)
    print(f"Detailed results: {output_path}")


if __name__ == "__main__":
    main()
