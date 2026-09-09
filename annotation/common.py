"""Shared helpers for the human-annotation pipeline (triage / sample / aggregate)."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "version_4"

# run_metadata.benchmark values shared by BOTH mutation models — the only ones
# usable for a model-vs-model comparison.
SHARED_BENCHMARKS = ["GSM8K", "OpenAI HumanEval", "MBPP", "TruthfulQA", "LatestEval BBC-latest"]

_CODE_BENCH = {"OpenAI HumanEval", "MBPP", "OpenCodeInstruct"}
_MATH_BENCH = {"GSM8K", "AIME 2024", "AIME 2026"}


def benchmark_kind(name: str) -> str:
    if name in _CODE_BENCH:
        return "code"
    if name in _MATH_BENCH:
        return "math"
    return "text"


def model_slug(path: Path) -> str:
    """Mutation model = the parent directory name (data/version_4/<model>/...)."""
    return path.parent.name


def iter_run_files() -> Iterator[Path]:
    yield from sorted(DATA_DIR.glob("*/*.json"))


def load_run(path: Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    d = json.loads(path.read_text())
    return d.get("run_metadata", {}), d.get("samples", [])


def item_id(model: str, benchmark: str, run_id: Any, index: int, original_input: str) -> str:
    h = hashlib.sha1(
        f"{model}|{benchmark}|{run_id}|{index}|{original_input}".encode()
    ).hexdigest()
    return h[:12]


def expected_answer(sample: Dict[str, Any]) -> str:
    upd = sample.get("updated_output")
    if upd is not None and str(upd).strip() not in ("", "None"):
        return str(upd).strip()
    return str(sample.get("original_output", "")).strip()


_WS = re.compile(r"\s+")


def norm(s: str) -> str:
    return _WS.sub(" ", str(s or "").strip().lower())


def records() -> Iterator[Dict[str, Any]]:
    """One dict per mutated sample across every run file, with a stable id."""
    for path in iter_run_files():
        meta, samples = load_run(path)
        model = model_slug(path)
        benchmark = meta.get("benchmark", "?")
        run_id = meta.get("run_id", "?")
        for i, s in enumerate(samples):
            rule = (s.get("metadata") or {}).get("rule") or {}
            yield {
                "id": item_id(model, benchmark, run_id, i, s.get("original_input", "")),
                "model": model,
                "benchmark": benchmark,
                "kind": benchmark_kind(benchmark),
                "run_id": run_id,
                "index": i,
                "file": str(path.relative_to(ROOT)),
                "original_input": s.get("original_input", ""),
                "original_output": s.get("original_output", ""),
                "modified_input": s.get("modified_input", ""),
                "updated_output": s.get("updated_output"),
                "expected_answer": expected_answer(s),
                "updated_tests": s.get("updated_tests"),
                "original_tests": (s.get("metadata") or {}).get("original_tests"),
                "rule_name": s.get("transformation_applied") or rule.get("name", ""),
                "rule_description": rule.get("description", ""),
                "rule_function": rule.get("transformation_function", ""),
                "output_change_description": rule.get("output_change_description", ""),
                "notes": (s.get("metadata") or {}).get("notes", ""),
            }
