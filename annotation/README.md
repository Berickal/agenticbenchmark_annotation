# Human annotation of mutated benchmarks

Judge a stratified sample of the mutated items in `data/version_4/**` (two
mutation models × the 5 shared benchmarks) and turn the labels into
mutation-quality metrics + a validity caveat on the robustness gap Δ.

## Pipeline

```
data/version_4/**  ──triage.py──▶  triage.json / triage_summary.json
                   ──sample.py──▶  docs/pool.json  docs/assignments.json
                                   annotation/key.json  sample_manifest.json
                                        │
                       GitHub Pages (docs/index.html)  ◀── annotators
                                        │  each exports annotations_<name>.json
                                        ▼
              annotation/annotations/*.json ──aggregate.py──▶ results.json
```

### 1. Triage (automated pre-filter)

```bash
python annotation/triage.py --shared-only
```

Flags blank inputs and unparseable code (**hard fail** → auto-reject, excluded
from the human pool but counted in the denominator), plus answer-leak /
near-verbatim / fuzzy-leak (**soft flags** → kept, shown to the annotator).
`--run-tests` also executes `updated_tests` for code items (slow).

### 2. Sample

```bash
python annotation/sample.py --annotators alice bob carol dave \
    --per-cell 30 --overlap 0.25 --seed 42
```

Per (model × benchmark) cell: picks the single most triage-clean run, randomly
samples `--per-cell` non-hard-fail items. `--overlap` is the fraction reviewed
by **every** annotator (for agreement); the rest are split round-robin.
Writes the blind `docs/annotate/pool.json` (no model field), `docs/annotate/assignments.json`,
the unblinding `annotation/key.json`, and `annotation/sample_manifest.json`
(seed + every sampled id — the audit trail).

### 3. Annotate (GitHub Pages)

Enable Pages for this repo with **source = `/docs`  (the app lives at `/docs/annotate/`)**. Each annotator opens

```
https://<user>.github.io/<repo>/annotate/?annotator=<their-name>
```

The page shows original vs mutated side by side + an 8-point rubric, is
keyboard-driven (←/→ move, `1`–`4` / `Y`/`N`/`U` fill the next row), and
auto-saves to `localStorage`. When finished they click **Export** and send you
`annotations_<name>.json`. Drop those into `annotation/annotations/`.

Local preview: `cd docs && python -m http.server 8777` →
`http://localhost:8777/annotate/?annotator=alice`.

### 4. Aggregate

```bash
python annotation/aggregate.py annotation/annotations/*.json
```

Per cell: acceptance rate (per-item majority verdict) + Wilson 95% CI, mean
faithfulness / transformed-enough, % well-formed / answer-correct / no-leak,
and the triage hard-fail rate of the sampled run. Plus Cohen's κ and pairwise
agreement on the overlap subset, and every reject with its note. Full JSON in
`annotation/results.json`.

## Rubric

| field | scale | meaning |
|---|---|---|
| `wellformed` | yes/no | mutated problem makes sense and is answerable |
| `faithful` | 1–4 | same skill/knowledge at comparable difficulty |
| `answer_correct` | yes/no/unsure | shown expected answer is right for the mutated item |
| `tests_ok` | yes/no/na | (code) updated assertions valid & consistent |
| `no_leak` | yes/no | prompt doesn't state/hint the answer (passage-grounded RC excepted) |
| `transformed_enough` | 1–4 | a model that memorised the original wouldn't recognise it |
| `fluent` | yes/no | reads like a human-written problem |
| `verdict` | accept/reject | usable as a mutated benchmark item |

## Notes

- The two mutation models don't cover the same benchmark set — `OpenCodeInstruct`
  (mistral only) is dropped so the comparison is apples-to-apples.
- `use_per_sample_rules: true` in generation ⇒ `transformation_applied` is ~1
  unique name per item, so rule-type strata aren't possible; the manifest records
  the rule-name spread that was actually drawn.
- Only gate-passing items are in `data/version_4/**`, so the human reject rate on
  this sample **is** the checker's false-positive rate.
