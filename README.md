# AgenticBenchmark v4 — mutation annotation (standalone)

Self-contained copy of the human-annotation study for the mutated benchmarks in
`data/version_4/`. Pure Python 3.9+ stdlib — no dependencies.

```
docs/annotate/        the GitHub Pages review app  (index.html + pool.json + assignments.json)
annotation/           triage / sample / aggregate scripts + local artifacts
  annotations/        drop the exported annotations_<name>.json files here
data/version_4/        the mutated benchmark runs the sample was drawn from
```

## Deploy the review app (GitHub Pages)

1. Push this folder to its own GitHub repo (public — Pages is free for public repos).
   ```bash
   cd agentic-annotation
   git init && git add . && git commit -m "annotation site"
   git branch -M main
   git remote add origin https://github.com/<you>/<repo>.git
   git push -u origin main
   ```
2. Repo **Settings → Pages → Deploy from a branch → `main` / `/docs`**.
3. After ~1 min the app is live at
   `https://<you>.github.io/<repo>/annotate/?annotator=<name>`

Send one link per annotator (`<name>` must match the `--annotators` values used
when `pool.json` was generated: see `annotation/sample_manifest.json`).

Local preview without deploying:
```bash
cd docs && python3 -m http.server 8777
# open http://localhost:8777/annotate/?annotator=<name>
```

## Collect & score

Each annotator clicks **Export** in the app and sends you `annotations_<name>.json`.
Put them in `annotation/annotations/`, then:

```bash
python3 annotation/aggregate.py annotation/annotations/*.json
```

→ per-(model × benchmark) acceptance rate + 95% CI, faithfulness, Cohen's κ on
the overlap subset, every reject with its note. Full JSON in `annotation/results.json`.

## Regenerate the sample (optional)

Needs `data/version_4/`:

```bash
python3 annotation/triage.py --shared-only
python3 annotation/sample.py --annotators alice bob carol dave --per-cell 30 --overlap 0.25 --seed 42
```

Item ids are content hashes, so re-sampling the same items preserves any
in-progress annotations in the browser. Commit the refreshed
`docs/annotate/pool.json` + `assignments.json` and Pages redeploys automatically.

See `annotation/README.md` for the rubric and the full method.

## Notes

- `annotation/key.json` and `sample_manifest.json` **unblind** the sample
  (they map each item to its mutation model). They're included here for the
  study owner; if you make the repo public and want to keep annotators blind,
  move them out before pushing (the app never loads them).
- `data/` is only needed to re-run triage/sample. To keep the deploy repo small,
  uncomment `data/` in `.gitignore`.
