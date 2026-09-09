#!/usr/bin/env python3
"""Automated pre-filter over data/version_4/** before human annotation.

Cheap, safe checks (no code execution unless --run-tests):

  hard fails  (auto-reject; kept OUT of the human pool, counted in the denominator)
    blank_input        modified_input is empty / whitespace
    code_unparseable   updated_output is not valid Python (code benchmarks)

  soft flags  (kept in the pool; surfaced to the annotator)
    answer_leak        the reference answer appears verbatim inside the prompt
    near_verbatim      mutated input is ~identical to the original (no de-memorisation)
    fuzzy_leak         high token overlap between reference answer and prompt (text)

Usage
    python annotation/triage.py
    python annotation/triage.py --run-tests        # also execute updated_tests (code)
    python annotation/triage.py --out annotation/triage.json
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from annotation.common import ROOT, SHARED_BENCHMARKS, norm, records  # noqa: E402

HARD = {"blank_input", "code_unparseable"}


def _parses_as_python(src: str) -> bool:
    src = str(src or "")
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # regex strings in code -> SyntaxWarning, not our concern
        try:
            ast.parse(src)
            return True
        except SyntaxError:
            # bare function body (leading indent / top-level return) — wrap and retry
            try:
                ast.parse("def _f():\n" + "\n".join("    " + ln for ln in src.splitlines()))
                return True
            except SyntaxError:
                return False


def _token_overlap(a: str, b: str) -> float:
    ta, tb = set(norm(a).split()), set(norm(b).split())
    if not ta:
        return 0.0
    return len(ta & tb) / len(ta)


def triage_one(rec: dict, run_tests: bool) -> dict:
    flags: list[str] = []
    mod = str(rec["modified_input"] or "")
    exp = str(rec["expected_answer"] or "")

    if not mod.strip():
        flags.append("blank_input")

    if rec["kind"] == "code":
        if rec.get("updated_output") and not _parses_as_python(rec["updated_output"]):
            flags.append("code_unparseable")
    elif rec["benchmark"] == "LatestEval BBC-latest":
        # reading comprehension: the answer is *meant* to be in the passage, so
        # substring / token overlap is not leakage — skip those checks here.
        pass
    else:
        # answer leakage — only meaningful for short-ish reference answers
        if exp and len(exp) >= 4 and norm(exp) in norm(mod):
            flags.append("answer_leak")
        elif rec["kind"] == "text" and exp and _token_overlap(exp, mod) >= 0.8:
            flags.append("fuzzy_leak")

    if mod.strip() and rec["original_input"]:
        ratio = SequenceMatcher(None, norm(rec["original_input"]), norm(mod)).ratio()
        if ratio >= 0.95:
            flags.append("near_verbatim")

    if run_tests and rec["kind"] == "code" and rec.get("updated_tests") and rec.get("updated_output"):
        if not _run_code_tests(rec):
            flags.append("tests_fail")

    return {
        "id": rec["id"], "model": rec["model"], "benchmark": rec["benchmark"],
        "run_id": rec["run_id"], "index": rec["index"],
        "flags": flags, "hard_fail": any(f in HARD for f in flags),
    }


def _run_code_tests(rec: dict) -> bool:
    try:
        from evaluation.utils.code_runner import run_code_with_tests  # type: ignore
    except Exception:
        return True  # runner unavailable — don't penalise
    tests = rec["updated_tests"]
    if isinstance(tests, list):
        tests = "\n".join(str(t) for t in tests)
    try:
        res = run_code_with_tests(str(rec["updated_output"]), str(tests), timeout=10)
        return bool(getattr(res, "passed", res) if not isinstance(res, dict) else res.get("passed"))
    except Exception:
        return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "annotation" / "triage.json"))
    ap.add_argument("--summary", default=str(ROOT / "annotation" / "triage_summary.json"))
    ap.add_argument("--run-tests", action="store_true", help="execute updated_tests for code items (slow)")
    ap.add_argument("--shared-only", action="store_true", help="restrict to the 5 shared benchmarks")
    args = ap.parse_args(argv)

    out, summary = {}, {}
    per_cell: dict[tuple, Counter] = {}
    n = 0
    for rec in records():
        if args.shared_only and rec["benchmark"] not in SHARED_BENCHMARKS:
            continue
        t = triage_one(rec, args.run_tests)
        out[t["id"]] = t
        n += 1
        cell = (rec["model"], rec["benchmark"], rec["run_id"])
        c = per_cell.setdefault(cell, Counter())
        c["total"] += 1
        if t["hard_fail"]:
            c["hard_fail"] += 1
        if not t["flags"]:
            c["clean"] += 1
        for f in t["flags"]:
            c[f] += 1

    Path(args.out).write_text(json.dumps(out, indent=1))
    for (m, b, r), c in sorted(per_cell.items()):
        summary.setdefault(m, {}).setdefault(b, {})[str(r)] = dict(c)
    Path(args.summary).write_text(json.dumps(summary, indent=2))

    print(f"triaged {n} items -> {args.out}")
    print(f"{'model/benchmark/run':52s} {'total':>6} {'clean':>6} {'hard':>5}  flags")
    for (m, b, r), c in sorted(per_cell.items()):
        extra = ", ".join(f"{k}={v}" for k, v in c.items() if k not in ("total", "clean", "hard_fail"))
        print(f"{m + '/' + b + '/' + str(r):52s} {c['total']:>6} {c['clean']:>6} {c['hard_fail']:>5}  {extra}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
