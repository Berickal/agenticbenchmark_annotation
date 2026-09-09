#!/usr/bin/env python3
"""Draw a stratified sample of mutated items for human annotation.

Strata = mutation model x shared benchmark (2 x 5 = 10 cells).
For each cell we pick the single most triage-clean run and randomly sample
`--per-cell` items from its non-hard-fail pool.

Outputs (all consumed by docs/annotate/index.html except key.json):
    docs/annotate/pool.json            blind items to annotate (NO model field)
    docs/annotate/assignments.json     annotator -> [item id, ...]  (disjoint + overlap)
    annotation/key.json       id -> {model, benchmark, run_id, ...}  (unblinding)
    annotation/sample_manifest.json   full audit trail (seed, per-cell picks)

Usage
    python annotation/sample.py --annotators alice bob carol dave
    python annotation/sample.py --per-cell 30 --overlap 0.25 --seed 42
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from annotation.common import ROOT, SHARED_BENCHMARKS, records  # noqa: E402

DOCS = ROOT / "docs" / "annotate"
ANN = ROOT / "annotation"

_POOL_FIELDS = (
    "id", "benchmark", "kind", "original_input", "original_output",
    "modified_input", "updated_output", "expected_answer", "updated_tests",
    "original_tests", "rule_name", "rule_description", "rule_function",
    "output_change_description", "notes",
)


def _cochran_hint(n_pop: int, conf: float = 0.95, p: float = 0.5, e: float = 0.1) -> int:
    from statistics import NormalDist
    z = NormalDist().inv_cdf((1 + conf) / 2)
    n0 = (z * z * p * (1 - p)) / (e * e)
    return max(1, min(n_pop, math.ceil(n0 / (1 + n0 / n_pop))))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--annotators", nargs="+", default=["a1", "a2", "a3", "a4"])
    ap.add_argument("--per-cell", type=int, default=30)
    ap.add_argument("--overlap", type=float, default=0.25, help="fraction reviewed by ALL annotators")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--triage", default=str(ANN / "triage.json"))
    args = ap.parse_args(argv)

    rng = random.Random(args.seed)
    triage = json.loads(Path(args.triage).read_text()) if Path(args.triage).exists() else {}

    # bucket every record by (model, benchmark, run_id)
    by_cell_run: dict[tuple, list] = defaultdict(list)
    for rec in records():
        if rec["benchmark"] not in SHARED_BENCHMARKS:
            continue
        by_cell_run[(rec["model"], rec["benchmark"], rec["run_id"])].append(rec)

    models = sorted({m for (m, _, _) in by_cell_run})
    pool, key, assignments_src, manifest_cells = [], {}, [], []

    for model in models:
        for bench in SHARED_BENCHMARKS:
            runs = {r: recs for (m, b, r), recs in by_cell_run.items() if m == model and b == bench}
            if not runs:
                continue
            # pick the run with the most triage-clean (no-flag) items
            def clean_count(recs):
                return sum(1 for r in recs if not triage.get(r["id"], {}).get("flags"))
            run_id = max(runs, key=lambda r: clean_count(runs[r]))
            recs = runs[run_id]
            eligible = [r for r in recs if not triage.get(r["id"], {}).get("hard_fail")]
            take = min(args.per_cell, len(eligible))
            chosen = rng.sample(eligible, take)

            n_flagged = sum(1 for r in chosen if triage.get(r["id"], {}).get("flags"))
            manifest_cells.append({
                "model": model, "benchmark": bench, "run_id": run_id,
                "run_size": len(recs), "eligible": len(eligible),
                "sampled": take, "flagged_in_sample": n_flagged,
                "cochran_hint_95_10": _cochran_hint(len(eligible)),
                "ids": [r["id"] for r in chosen],
            })
            for r in chosen:
                item = {k: r.get(k) for k in _POOL_FIELDS}
                item["triage_flags"] = triage.get(r["id"], {}).get("flags", [])
                pool.append(item)
                key[r["id"]] = {
                    "model": model, "benchmark": bench, "run_id": run_id,
                    "index": r["index"], "file": r["file"],
                    "checker_verdict": "pass",  # only gate-passing items are saved
                }

    # ── assignments: overlap subset for all, remainder round-robin ──
    ids = [it["id"] for it in pool]
    rng.shuffle(ids)
    n_overlap = round(args.overlap * len(ids))
    overlap_ids, solo_ids = ids[:n_overlap], ids[n_overlap:]
    assignments = {a: list(overlap_ids) for a in args.annotators}
    for i, iid in enumerate(solo_ids):
        assignments[args.annotators[i % len(args.annotators)]].append(iid)
    for a in assignments:
        rng.shuffle(assignments[a])

    DOCS.mkdir(exist_ok=True)
    (DOCS / "pool.json").write_text(json.dumps(pool, indent=1))
    (DOCS / "assignments.json").write_text(json.dumps(assignments, indent=1))
    (ANN / "key.json").write_text(json.dumps(key, indent=1))
    (ANN / "sample_manifest.json").write_text(json.dumps({
        "seed": args.seed, "per_cell": args.per_cell, "overlap": args.overlap,
        "annotators": args.annotators, "n_items": len(pool),
        "n_overlap": n_overlap, "labels_total": n_overlap * len(args.annotators) + len(solo_ids),
        "cells": manifest_cells,
    }, indent=2))

    print(f"pool: {len(pool)} items across {len(manifest_cells)} cells -> docs/annotate/pool.json")
    print(f"overlap: {n_overlap} items reviewed by all {len(args.annotators)} annotators")
    print(f"labels total: {n_overlap * len(args.annotators) + len(solo_ids)} "
          f"(~{(n_overlap * len(args.annotators) + len(solo_ids)) // len(args.annotators)} per annotator)")
    print(f"{'model/benchmark':40s} {'run':>4} {'samp':>5} {'flagged':>8}")
    for c in manifest_cells:
        print(f"{c['model'] + '/' + c['benchmark']:40s} {c['run_id']:>4} {c['sampled']:>5} {c['flagged_in_sample']:>8}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
