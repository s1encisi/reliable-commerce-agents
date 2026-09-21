# Jev comparison harness

Measures two decisions against the rule-based implementations a team would
ship without a decision model:

| Decision | Jev primitive | Baseline |
|---|---|---|
| Which specialist owns a message | `choice` | weighted keyword matching |
| Refuse before any tool runs | `noul` | regex denylist |

**Read `FINDINGS.zh-CN.md` for the analysis and conclusions.**
Raw per-sample results are in `results/`.

## Run

From `agents/python`:

```bash
export TYPESAFE_API_KEY=...
python -m evals.jev_comparison.runner
```

Options:

| Flag | Default | Purpose |
|---|---|---|
| `--threshold` | `0.5` | noul refusal threshold |
| `--model` | `jev-latest` | pin a Jev version |
| `--limit N` | none | cap samples per set (smoke test) |
| `--out-dir` | `./results` | where to write JSON + Markdown |

A full run makes 75 API calls and takes about three minutes — the client is
sequential on purpose, so latency samples are not contaminated by
self-inflicted queueing.

## Layout

| File | Contents |
|---|---|
| `datasets.py` | Builds both sample sets from the repository's own labelled data |
| `baselines.py` | The two rule-based baselines |
| `runner.py` | Experiment driver, metrics, report rendering |
| `results/` | Timestamped JSON (per-sample) and Markdown (summary) |

## Where the labels come from

No labels were invented for the headline numbers.

* **Routing** — `evals/datasets/orchestrator_routing.json` carries an explicit
  `expected_route`; the five per-specialist datasets name their owning
  specialist in the filename.
* **Safety gate** — `red_team.json` carries `refusal_expected`. Its three
  `refusal_expected: false` samples are *legitimate requests carrying an
  embedded payload*, which is the case a denylist structurally cannot handle.
  Negative samples are topped up from ordinary shopping requests.

Every sample records `origin` (`repository` / `synthetic`) and results are
reported split by origin, so a synthetic sample cannot quietly inflate a
headline number.

## Honesty rules the harness enforces on itself

* One sample = one independent measurement. No caching, no retries counted as
  fresh samples.
* Per-sample predictions are written to the JSON. Every summary is checkable
  against the individual rows.
* LLM cost/latency figures are labelled `estimated`. No frontier model was
  called in this run.
* The `not_verified` list in the JSON is part of the output, not an
  afterthought — it is written by the harness, not by hand.

## Scope

Two decision points only. The end-to-end agent pipeline was **not** run, so
nothing here supports a claim about the multi-agent system as a whole.
See section 6 of `FINDINGS.zh-CN.md`.
