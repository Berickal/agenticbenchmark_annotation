#!/usr/bin/env python3
"""Draw N disjoint annotation POOLS + a shared anchor set + gold checks.

Each new annotator is randomly routed to one pool and labels everything in it:
    pool = <core, stratified by model x shared benchmark>
         + <anchors: same items in every pool -> cross-pool calibration>
         + <gold: known-reject items (blank input) -> rater attention check>

Coverage = pools x per-pool unique core items; every core item gets labelled by
everyone in its pool; anchors + gold get labelled by everyone.

Outputs (consumed by docs/pools/index.html except the *_key / *_gold files):
    docs/pools/pools.json          {"A":[item...], "B":[...], ...}   blind, no model field
    annotation/pools_key.json      id -> {model, benchmark, role, pools:[...]}   (unblinding)
    annotation/pools_gold.json     id -> expected verdict   (gold items only)
    annotation/pools_manifest.json audit trail

Usage
    python annotation/sample_pools.py --pools 3 --per-pool 30 --anchors 5 --gold 3 --seed 42
"""
from __future__ import annotations

import argparse
import json
import string
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from annotation.common import ROOT, SHARED_BENCHMARKS, records  # noqa: E402
from annotation.sample import _POOL_FIELDS, _cochran_hint  # reuse  # noqa: E402
import random  # noqa: E402

DOCS = ROOT / "docs" / "pools"
ANN = ROOT / "annotation"


def _item(rec: dict, triage: dict) -> dict:
    it = {k: rec.get(k) for k in _POOL_FIELDS}
    it["triage_flags"] = triage.get(rec["id"], {}).get("flags", [])
    return it


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pools", type=int, default=3)
    ap.add_argument("--per-pool", type=int, default=30, help="unique core items per pool")
    ap.add_argument("--anchors", type=int, default=5, help="items shared by ALL pools")
    ap.add_argument("--gold", type=int, default=3, help="known-reject items (blank input) shared by ALL pools")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--triage", default=str(ANN / "triage.json"))
    args = ap.parse_args(argv)

    rng = random.Random(args.seed)
    triage = json.loads(Path(args.triage).read_text()) if Path(args.triage).exists() else {}
    pool_names = list(string.ascii_uppercase[: args.pools])

    # ── bucket clean+soft records by cell, and collect hard-fails for gold ──
    by_cell: dict[tuple, list] = defaultdict(list)
    hard_fail: list[dict] = []
    for rec in records():
        if rec["benchmark"] not in SHARED_BENCHMARKS:
            continue
        t = triage.get(rec["id"], {})
        if t.get("hard_fail"):
            hard_fail.append(rec)
        else:
            by_cell[(rec["model"], rec["benchmark"])].append(rec)

    cells = sorted(by_cell)
    n_cells = len(cells)
    if not n_cells:
        print("no eligible items", file=sys.stderr)
        return 1

    # per-cell core quota: spread per_pool*pools items over the cells
    total_core = args.per_pool * args.pools
    base, rem = divmod(total_core, n_cells)
    quota = {c: base + (1 if i < rem else 0) for i, c in enumerate(cells)}

    # ── draw disjoint core items, round-robin them into pools ──
    pools: dict[str, list[str]] = {p: [] for p in pool_names}
    key: dict[str, dict] = {}
    manifest_cells = []
    core_ids_by_cell: dict[tuple, list[str]] = {}

    for (model, bench) in cells:
        recs = by_cell[(model, bench)]
        best_run = max({r["run_id"] for r in recs},
                       key=lambda rid: sum(1 for r in recs
                                           if r["run_id"] == rid and not triage.get(r["id"], {}).get("flags")))
        elig = [r for r in recs if r["run_id"] == best_run]
        take = min(quota[(model, bench)], len(elig))
        chosen = rng.sample(elig, take)
        rng.shuffle(chosen)
        core_ids_by_cell[(model, bench)] = [r["id"] for r in chosen]
        for i, r in enumerate(chosen):
            p = pool_names[i % args.pools]
            pools[p].append(r["id"])
            key[r["id"]] = {"model": model, "benchmark": bench, "run_id": best_run,
                            "index": r["index"], "file": r["file"], "role": "core", "pools": [p]}
        manifest_cells.append({"model": model, "benchmark": bench, "run_id": best_run,
                               "eligible": len(elig), "core_sampled": take,
                               "per_pool": [sum(1 for j in range(take) if j % args.pools == k)
                                            for k in range(args.pools)],
                               "cochran_hint_95_10": _cochran_hint(len(elig))})

    used = set(key)

    # ── anchors: 1 per cell round-robin until we have `--anchors`, into every pool ──
    anchors: list[str] = []
    ci = 0
    while len(anchors) < args.anchors and ci < n_cells * 3:
        model, bench = cells[ci % n_cells]
        pool_recs = [r for r in by_cell[(model, bench)] if r["id"] not in used]
        if pool_recs:
            r = rng.choice(pool_recs)
            used.add(r["id"]); anchors.append(r["id"])
            key[r["id"]] = {"model": model, "benchmark": bench, "run_id": r["run_id"],
                            "index": r["index"], "file": r["file"],
                            "role": "anchor", "pools": list(pool_names)}
        ci += 1

    # ── gold: known-reject (blank modified_input), into every pool ──
    gold: dict[str, str] = {}
    rng.shuffle(hard_fail)
    for r in hard_fail:
        if len(gold) >= args.gold:
            break
        if r["id"] in used:
            continue
        used.add(r["id"]); gold[r["id"]] = "reject"
        key[r["id"]] = {"model": r["model"], "benchmark": r["benchmark"], "run_id": r["run_id"],
                        "index": r["index"], "file": r["file"],
                        "role": "gold", "pools": list(pool_names), "flags": triage.get(r["id"], {}).get("flags", [])}

    # ── assemble each pool: core + anchors + gold, shuffled ──
    rec_by_id = {}
    for rec in records():
        if rec["id"] in key:
            rec_by_id[rec["id"]] = rec

    out: dict[str, list[dict]] = {}
    for p in pool_names:
        ids = list(pools[p]) + anchors + list(gold)
        prng = random.Random(args.seed + pool_names.index(p) + 1)
        prng.shuffle(ids)
        out[p] = [_item(rec_by_id[i], triage) for i in ids]

    DOCS.mkdir(parents=True, exist_ok=True)
    (DOCS / "pools.json").write_text(json.dumps(out, indent=1))
    (ANN / "pools_key.json").write_text(json.dumps(key, indent=1))
    (ANN / "pools_gold.json").write_text(json.dumps(gold, indent=1))
    (ANN / "pools_manifest.json").write_text(json.dumps({
        "seed": args.seed, "pools": pool_names, "per_pool_core": args.per_pool,
        "anchors": anchors, "gold": list(gold), "n_cells": n_cells,
        "items_per_pool": {p: len(v) for p, v in out.items()},
        "unique_core_items": sum(len(v) for v in pools.values()),
        "cells": manifest_cells,
    }, indent=2))

    print(f"{args.pools} pools -> docs/pools/pools.json")
    print(f"  core: {sum(len(v) for v in pools.values())} unique items  "
          f"(~{args.per_pool}/pool)  +  {len(anchors)} anchors  +  {len(gold)} gold  in every pool")
    print(f"  each annotator labels {len(next(iter(out.values())))} items")
    print(f"\n  {'model / benchmark':38s} {'elig':>5} {'core':>5}  per-pool")
    for c in manifest_cells:
        print(f"  {c['model'] + ' / ' + c['benchmark']:38s} {c['eligible']:>5} {c['core_sampled']:>5}  {c['per_pool']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
