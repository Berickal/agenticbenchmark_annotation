#!/usr/bin/env python3
"""Aggregate the multi-pool annotation (sample_pools.py + docs/pools/).

    python annotation/aggregate_pools.py annotation/pool_annotations/*.json

Each export carries {annotator, pool, items}. This reports:
  * rater QC on the gold (known-reject) items — flags raters who accept them
  * pooled acceptance rate (core items) + Wilson 95% CI, overall + per task-family
  * per-pool acceptance (sanity: pools should agree)
  * per (model x benchmark) with a low-n warning
  * Fleiss' kappa on the verdict (per pool + overall)
  * cross-pool calibration on the anchor items
  * every reject with its note
Full JSON -> annotation/pools_results.json
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from annotation.common import ROOT, benchmark_kind  # noqa: E402

ANN = ROOT / "annotation"
_DIFF = {1: "much_easier", 2: "easier", 3: "same", 4: "harder", 5: "much_harder"}


def wilson(k, n, z=1.96):
    if not n:
        return (float("nan"),) * 3
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(p, 3), round(max(0, c - h), 3), round(min(1, c + h), 3)


def fleiss_kappa(rating_rows: list[Counter], cats: list[str]) -> float:
    rows = [r for r in rating_rows if sum(r.values()) >= 2]
    if len(rows) < 2:
        return float("nan")
    P_i = []
    tot = Counter()
    N = 0
    for r in rows:
        ni = sum(r.values())
        N += ni
        tot.update(r)
        P_i.append((sum(v * v for v in r.values()) - ni) / (ni * (ni - 1)))
    Pbar = statistics.mean(P_i)
    Pe = sum((tot[c] / N) ** 2 for c in cats)
    return round((Pbar - Pe) / (1 - Pe), 3) if Pe != 1 else 1.0


def majority(vs: list[str]):
    vs = [v for v in vs if v]
    if not vs:
        return None
    return "accept" if vs.count("accept") * 2 > len(vs) else "reject"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*", default=[str(ANN / "pool_annotations" / "*.json")])
    ap.add_argument("--drop-failed-gold", action="store_true",
                    help="exclude raters who accept a known-reject gold item from the consensus")
    ap.add_argument("--out", default=str(ANN / "pools_results.json"))
    args = ap.parse_args(argv)

    paths = [p for pat in args.files for p in glob.glob(pat)]
    if not paths:
        print("no annotation files matched", file=sys.stderr)
        return 1

    key = json.loads((ANN / "pools_key.json").read_text())
    gold = json.loads((ANN / "pools_gold.json").read_text())
    manifest = json.loads((ANN / "pools_manifest.json").read_text()) if (ANN / "pools_manifest.json").exists() else {}
    anchors = set(manifest.get("anchors", []))

    # annotator -> {pool, items}
    raters: dict[str, dict] = {}
    for p in paths:
        d = json.loads(Path(p).read_text())
        who = d.get("annotator") or Path(p).stem
        raters[who] = {"pool": d.get("pool"), "items": d.get("items", {})}

    # ── rater QC on gold ──
    qc = {}
    for who, r in raters.items():
        got = {gid: r["items"].get(gid, {}).get("verdict") for gid in gold}
        wrong = [gid for gid, v in got.items() if v == "accept"]          # accepted a known-reject
        answered = [gid for gid, v in got.items() if v in ("accept", "reject")]
        qc[who] = {"pool": r["pool"], "gold_answered": len(answered),
                   "gold_wrong": len(wrong), "passed": len(wrong) == 0}
    trusted = {w for w, q in qc.items() if q["passed"]} if args.drop_failed_gold else set(raters)

    # ── labels per item (trusted raters only) ──
    labels: dict[str, list[dict]] = defaultdict(list)
    for who, r in raters.items():
        if who not in trusted:
            continue
        for iid, rec in r["items"].items():
            labels[iid].append({**rec, "_by": who, "_pool": r["pool"]})

    def cell_of(iid):
        k = key.get(iid, {})
        return k.get("model"), k.get("benchmark")

    def role_of(iid):
        return key.get(iid, {}).get("role", "core")

    # ── acceptance: overall / family / pool / cell (core items only) ──
    def acc_block(ids):
        verdicts = [majority([l.get("verdict") for l in labels.get(i, [])]) for i in ids]
        verdicts = [v for v in verdicts if v]
        k = sum(v == "accept" for v in verdicts)
        p, lo, hi = wilson(k, len(verdicts))
        alll = [l for i in ids for l in labels.get(i, [])]
        wf = [l.get("wellformed") for l in alll]
        ac = [l.get("answer_correct") for l in alll]
        nl = [l.get("no_leak") for l in alll]
        diff = [int(l["difficulty"]) for l in alll if str(l.get("difficulty", "")).isdigit()]
        dist = Counter(_DIFF.get(d) for d in diff)
        return {"n_items": len(verdicts), "acceptance": p, "ci95": [lo, hi],
                "pct_wellformed": _pct(wf), "pct_answer_correct": _pct(ac, {"yes", "no"}),
                "pct_no_leak": _pct(nl),
                "pct_same_difficulty": round(100 * dist["same"] / len(diff), 1) if diff else None,
                "mean_difficulty_shift": round(statistics.mean([d - 3 for d in diff]), 2) if diff else None,
                "difficulty_dist": {k: dist.get(k, 0) for k in _DIFF.values()}}

    core = [i for i in key if role_of(i) == "core"]
    by_fam = defaultdict(list)
    by_pool = defaultdict(list)
    by_cell = defaultdict(list)
    for i in core:
        m, b = cell_of(i)
        by_fam[benchmark_kind(b)].append(i)
        by_pool[key[i]["pools"][0]].append(i)
        by_cell[(m, b)].append(i)

    results = {
        "raters": len(raters), "trusted_raters": len(trusted),
        "labels_total": sum(len(v) for v in raters.values() for v in [v["items"]]),
        "qc": qc,
        "overall": acc_block(core),
        "by_task_family": {f: acc_block(ids) for f, ids in sorted(by_fam.items())},
        "by_pool": {p: acc_block(ids) for p, ids in sorted(by_pool.items())},
        "by_model_benchmark": {f"{m}/{b}": {**acc_block(ids), "low_n": len(ids) < 10}
                               for (m, b), ids in sorted(by_cell.items())},
    }

    # ── Fleiss kappa on verdict ──
    def kappa_for(ids):
        rows = []
        for i in ids:
            c = Counter(l.get("verdict") for l in labels.get(i, []) if l.get("verdict") in ("accept", "reject"))
            rows.append(c)
        return fleiss_kappa(rows, ["accept", "reject"])
    results["fleiss_kappa"] = {
        "overall": kappa_for(core),
        "by_pool": {p: kappa_for(ids) for p, ids in sorted(by_pool.items())},
    }

    # ── cross-pool calibration on anchors ──
    anch = []
    for i in sorted(anchors):
        per_pool = defaultdict(list)
        for l in labels.get(i, []):
            if l.get("verdict"):
                per_pool[l["_pool"]].append(l["verdict"])
        cons = {p: majority(v) for p, v in per_pool.items()}
        anch.append({"id": i, "benchmark": key.get(i, {}).get("benchmark"),
                     "consensus_by_pool": cons,
                     "agree": len(set(v for v in cons.values() if v)) <= 1})
    results["anchors"] = {"items": anch,
                          "n_agree": sum(a["agree"] for a in anch), "n": len(anch)}

    # ── rejects with notes ──
    results["rejects"] = [
        {"id": i, "benchmark": key.get(i, {}).get("benchmark"), "role": role_of(i),
         "by": l["_by"], "notes": l.get("notes", "")}
        for i, ls in labels.items() for l in ls if l.get("verdict") == "reject"
    ]

    Path(args.out).write_text(json.dumps(results, indent=2))

    # ── console ──
    print(f"raters: {len(raters)}   trusted (gold-passed): {len(trusted)}")
    bad = [w for w, q in qc.items() if not q["passed"]]
    if bad:
        print(f"  ⚠ failed gold check: {bad}" + ("  (excluded)" if args.drop_failed_gold else "  (kept — use --drop-failed-gold to exclude)"))
    o = results["overall"]
    print(f"\noverall acceptance: {o['acceptance']:.0%}  95% CI [{o['ci95'][0]:.0%}, {o['ci95'][1]:.0%}]  "
          f"(n={o['n_items']} core items)   Fleiss κ = {results['fleiss_kappa']['overall']}")
    print(f"\n{'group':26s} {'n':>4} {'accept':>7} {'95% CI':>13} {'=diff%':>7} {'shift':>6} {'wf%':>5} {'ans%':>5} {'leak%':>6}")
    for label, blk in [("family:"+f, b) for f, b in results["by_task_family"].items()] \
                    + [("pool:"+p, b) for p, b in results["by_pool"].items()]:
        ci = f"[{blk['ci95'][0]:.0%},{blk['ci95'][1]:.0%}]"
        print(f"{label:26s} {blk['n_items']:>4} {blk['acceptance']*100:>6.0f}% {ci:>13} "
              f"{str(blk['pct_same_difficulty']):>7} {str(blk['mean_difficulty_shift']):>6} "
              f"{str(blk['pct_wellformed']):>5} {str(blk['pct_answer_correct']):>5} {str(blk['pct_no_leak']):>6}")
    od = results["overall"]["difficulty_dist"]
    print(f"\ndifficulty vs original (overall): " + "  ".join(f"{k}={v}" for k, v in od.items())
          + f"   mean shift {results['overall']['mean_difficulty_shift']} (– easier / + harder)")
    print(f"\nper model/benchmark (n<10 flagged):")
    for k, b in results["by_model_benchmark"].items():
        print(f"  {k:34s} n={b['n_items']:>2} accept={b['acceptance']*100:>3.0f}%" + ("  ⚠low-n" if b["low_n"] else ""))
    a = results["anchors"]
    print(f"\nanchors: {a['n_agree']}/{a['n']} reach the same verdict across all pools")
    print(f"rejects: {len(results['rejects'])}   full report -> {args.out}")
    return 0


def _pct(vals, among=None):
    vals = [v for v in vals if v not in (None, "")]
    if among:
        vals = [v for v in vals if v in among]
    return round(100 * sum(v == "yes" for v in vals) / len(vals), 1) if vals else None


if __name__ == "__main__":
    raise SystemExit(main())
