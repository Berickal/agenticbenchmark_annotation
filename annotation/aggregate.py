#!/usr/bin/env python3
"""Merge annotator exports and report mutation-quality metrics.

    python annotation/aggregate.py annotation/annotations/*.json

Reads the exported annotations_<name>.json files plus docs/pool.json,
annotation/key.json (unblinding) and annotation/triage_summary.json.

Reports, per (mutation model x benchmark):
  * acceptance rate (per-item majority verdict) + Wilson 95% CI
  * mean faithfulness / transformed-enough (1-4)
  * % well-formed, % answer-correct, % no-leak
  * triage hard-fail rate (auto-rejects that never reached annotators)
Plus inter-annotator agreement (Cohen's kappa) on the overlap subset.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import statistics
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from annotation.common import ROOT  # noqa: E402

ANN = ROOT / "annotation"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float, float]:
    if n == 0:
        return (float("nan"),) * 3
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0.0, c - h), min(1.0, c + h)


def cohen_kappa(a: list, b: list) -> float:
    labels = sorted(set(a) | set(b))
    if len(labels) < 2:
        return float("nan")
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    pe = sum((a.count(l) / n) * (b.count(l) / n) for l in labels)
    return (po - pe) / (1 - pe) if pe != 1 else 1.0


def majority(vals: list[str]) -> str | None:
    vals = [v for v in vals if v]
    if not vals:
        return None
    acc = vals.count("accept")
    return "accept" if acc * 2 > len(vals) else "reject"  # tie -> reject (conservative)


def _mean(xs):
    xs = [float(x) for x in xs if x not in (None, "")]
    return round(statistics.mean(xs), 2) if xs else None


def _pct(vals, yes="yes", among=None):
    vals = [v for v in vals if v not in (None, "")]
    if among:
        vals = [v for v in vals if v in among]
    return round(100 * sum(v == yes for v in vals) / len(vals), 1) if vals else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", default=[str(ANN / "annotations" / "*.json")])
    ap.add_argument("--out", default=str(ANN / "results.json"))
    args = ap.parse_args(argv)

    paths = [p for pat in args.files for p in glob.glob(pat)]
    if not paths:
        print("no annotation files matched", file=sys.stderr)
        return 1

    key = json.loads((ANN / "key.json").read_text())
    triage_sum = json.loads((ANN / "triage_summary.json").read_text()) if (ANN / "triage_summary.json").exists() else {}
    manifest = json.loads((ANN / "sample_manifest.json").read_text()) if (ANN / "sample_manifest.json").exists() else {}
    # (model, benchmark) -> the single run id that was actually sampled
    chosen_run = {(c["model"], c["benchmark"]): str(c["run_id"]) for c in manifest.get("cells", [])}

    # id -> annotator -> record
    labels: dict[str, dict[str, dict]] = defaultdict(dict)
    annotators = set()
    for p in paths:
        d = json.loads(Path(p).read_text())
        who = d.get("annotator") or Path(p).stem.replace("annotations_", "")
        annotators.add(who)
        for iid, r in d.get("items", {}).items():
            labels[iid][who] = r

    # ── per cell ──
    cells: dict[tuple, dict] = {}
    per_cell_ids = defaultdict(list)
    for iid, k in key.items():
        per_cell_ids[(k["model"], k["benchmark"])].append(iid)

    for (model, bench), ids in sorted(per_cell_ids.items()):
        item_verdicts, faith, transf, wf, ac, nl = [], [], [], [], [], []
        for iid in ids:
            recs = list(labels.get(iid, {}).values())
            if not recs:
                continue
            mv = majority([r.get("verdict") for r in recs])
            if mv:
                item_verdicts.append(mv)
            for r in recs:
                faith.append(r.get("faithful"))
                transf.append(r.get("transformed_enough"))
                wf.append(r.get("wellformed"))
                ac.append(r.get("answer_correct"))
                nl.append(r.get("no_leak"))
        n = len(item_verdicts)
        acc = sum(v == "accept" for v in item_verdicts)
        p, lo, hi = wilson(acc, n)
        runs = triage_sum.get(model, {}).get(bench, {})
        rid = chosen_run.get((model, bench))
        tri = runs.get(rid, {}) if rid else {}
        hard_rate = round(100 * tri.get("hard_fail", 0) / tri.get("total", 1), 1) if tri.get("total") else None
        cells[(model, bench)] = {
            "n_items_labeled": n,
            "acceptance_rate": round(p, 3) if n else None,
            "ci95": [round(lo, 3), round(hi, 3)] if n else None,
            "mean_faithful": _mean(faith),
            "mean_transformed_enough": _mean(transf),
            "pct_wellformed": _pct(wf),
            "pct_answer_correct": _pct(ac, among={"yes", "no"}),
            "pct_no_leak": _pct(nl),
            "triage_hard_fail_pct": hard_rate,
        }

    # ── inter-annotator agreement on the overlap subset ──
    overlap = {iid: v for iid, v in labels.items() if len(v) >= 2}
    kappas, agrees = [], []
    for iid, v in overlap.items():
        for a, b in combinations(sorted(v), 2):
            va, vb = v[a].get("verdict"), v[b].get("verdict")
            if va and vb:
                agrees.append(va == vb)
    pair_lists = defaultdict(lambda: ([], []))
    for iid, v in overlap.items():
        for a, b in combinations(sorted(v), 2):
            va, vb = v[a].get("verdict"), v[b].get("verdict")
            if va and vb:
                pair_lists[(a, b)][0].append(va)
                pair_lists[(a, b)][1].append(vb)
    for (a, b), (la, lb) in pair_lists.items():
        kappas.append(cohen_kappa(la, lb))

    agreement = {
        "overlap_items": len(overlap),
        "pct_pairwise_agreement": round(100 * sum(agrees) / len(agrees), 1) if agrees else None,
        "mean_cohen_kappa": round(statistics.mean([k for k in kappas if k == k]), 3) if kappas else None,
    }

    # ── rejects with reasons ──
    rejects = []
    for iid, v in labels.items():
        k = key.get(iid, {})
        for who, r in v.items():
            if r.get("verdict") == "reject":
                rejects.append({"id": iid, "model": k.get("model"), "benchmark": k.get("benchmark"),
                                "annotator": who, "notes": r.get("notes", "")})

    results = {
        "annotators": sorted(annotators),
        "n_items_with_labels": len(labels),
        "cells": {f"{m}/{b}": c for (m, b), c in cells.items()},
        "agreement": agreement,
        "rejects": rejects,
        "manifest": {"seed": manifest.get("seed"), "per_cell": manifest.get("per_cell")},
    }
    Path(args.out).write_text(json.dumps(results, indent=2))

    # ── console ──
    print(f"annotators: {sorted(annotators)}   items with >=1 label: {len(labels)}")
    print(f"\n{'model / benchmark':38s} {'n':>4} {'accept':>7} {'95% CI':>15} {'faith':>6} {'xform':>6} {'wf%':>5} {'ans%':>5} {'leak-ok%':>8} {'hardfail%':>9}")
    for (m, b), c in sorted(cells.items()):
        ci = f"[{c['ci95'][0]:.2f},{c['ci95'][1]:.2f}]" if c["ci95"] else "—"
        print(f"{m + ' / ' + b:38s} {c['n_items_labeled']:>4} "
              f"{('%.0f%%'%(100*c['acceptance_rate'])) if c['acceptance_rate'] is not None else '—':>7} {ci:>15} "
              f"{str(c['mean_faithful']):>6} {str(c['mean_transformed_enough']):>6} "
              f"{str(c['pct_wellformed']):>5} {str(c['pct_answer_correct']):>5} "
              f"{str(c['pct_no_leak']):>8} {str(c['triage_hard_fail_pct']):>9}")
    print(f"\nagreement: {agreement}")
    print(f"rejects: {len(rejects)}   full report -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
