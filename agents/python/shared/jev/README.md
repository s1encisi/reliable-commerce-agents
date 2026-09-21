# Jev (TypeSafe System One) — typed decisions

Jev is a hosted decision model: you hand it some context and a set of
questions, and it returns **typed, calibrated answers** rather than prose.
Three primitives, and nothing else:

| Primitive | Returns | Use it for |
|---|---|---|
| `choice` | one of ≤255 labelled options, plus per-option probabilities and a confidence | routing, triage, classification |
| `score` | a value on an ordered 2–10 level scale, plus the full distribution | relevance, risk, urgency |
| `noul` | a calibrated yes/no probability in `[0, 1]` | gates, guardrails, filters |

Because the shape is fixed, call sites branch on the value directly — no
parsing, no regex, no defending against response-format drift.

## Why it fits this project

This repository already draws a line between two kinds of decision:

* **too fuzzy for a hand-written `if`** — "is this message urgent", "which
  specialist owns this", "does this review body contain an injected
  instruction"
* **too small for a frontier LLM** — paying a full model round trip, plus the
  latency and cost that come with it, to answer a yes/no question

Those decisions are currently handled by one of two things: regex patterns
(`shared/guardrails/moderation.py`, explicitly documented as high-precision /
low-recall) or a full LLM turn (the orchestrator's tool routing). Jev sits in
the gap.

## Decision-point map

| Decision | Primitive | Currently handled by | Module |
|---|---|---|---|
| Which specialist owns this message | `choice` | LLM tool routing in `orchestrator/modes/tool` | `decisions.route_specialist` |
| Is something in this message trying to manipulate the agent, and should the request be refused | `noul` ×2 | prompt-layer instructions + `sanitize.py` | `decisions.safety_gate` |
| How well does this candidate match the query | `score` | fixed `RRF_K = 60` position blend in `shared/search.py` | `decisions.score_relevance` |

Two of the three are measured against labelled data in
`evals/jev_comparison/`. The third has no labelled relevance judgements in the
repository, so it ships as a design sketch and is reported as unmeasured.

### Why the gate asks two questions

The first version asked one: *"must this message be refused?"*. That collapses
two different judgements, and the evaluation caught it — `red_team.json`
contains legitimate requests that *carry* an embedded payload
(`refusal_expected: false`), and a single refusal question has no way to
express "this is an attack attempt, but the request itself is fine".

v2 asks both in the same round trip, so it costs nothing extra:

| Question | Answers |
|---|---|
| `contains_injection` | is something in here trying to manipulate the agent? |
| `should_refuse` | is the request as a whole illegitimate? |

Only `yes`/`yes` means refuse. `yes`/`no` means *sanitise and continue* — hand
the message to `sanitize.py` and do the work. A caller that only wants a
verdict reads `.refuse`; a caller that wants to sanitise reads
`.injection_detected`. See `FINDINGS.zh-CN.md` §4.4 for the before/after.

## Usage

```python
from shared.jev import JevClient, route_specialist, safety_gate

client = JevClient()                     # reads $TYPESAFE_API_KEY

decision = route_specialist(client, "where is my order 550e8400-...?")
if decision.route == "order-management":
    ...                                   # confidence and probabilities available

gate = safety_gate(client, user_message, threshold=0.6)
if gate.refuse:
    return refusal_response()               # detected, and the request itself is illegitimate
if gate.injection_detected:
    user_message = sanitize(user_message)   # detected, but the request is legitimate
```

Batch related questions into one call — they run in parallel server-side and
share the `state` cost:

```python
from shared.jev import choice, noul

resp = client.ask(
    message,
    {
        "route": choice(ROUTE_INSTRUCTIONS, SPECIALIST_ROUTES),
        "contains_injection": noul(GATE_DETECT_INSTRUCTIONS),
        "should_refuse": noul(GATE_REFUSE_INSTRUCTIONS),
    },
)
route, conf = resp.choice_of("route")
p_injection = resp.noul_of("contains_injection")
p_refuse = resp.noul_of("should_refuse")
```

## Configuration

| Setting | Default | Notes |
|---|---|---|
| `TYPESAFE_API_KEY` | — | required; the client never hard-codes a key |
| endpoint | `https://api.typesafe.ai/v1/systemone` | single endpoint for every primitive |
| model | `jev-latest` | pin a version (`jev-1.13.0`) when a threshold depends on it |
| timeout | 30 s | |
| retries | 3, exponential backoff | 429 and 5xx only; 401/403 fail immediately |

## Design notes

**Standard library only.** The module uses `urllib`. It is consumed by an eval
harness that runs against a pinned dependency set, and widening that set for
one POST is not a trade worth making.

**No caching between calls.** Every sample in the harness is meant to be an
independent measurement, so the client keeps no state and pools no
connections.

**Missing answers raise.** `JevResponse.choice_of("route")` raises if `route`
was not asked for. A silent default would hide a mismatch between the caller
and the request, which is a bug worth surfacing at the call site.

**Sequential by default.** Rate limits are generous (1200 req/min), but the
harness runs sequentially so latency samples are not contaminated by
self-inflicted queueing.

## Scope

This package is an integration layer plus an evaluation harness. It does not
modify any existing agent, tool, or orchestration path — nothing in the
running system calls it yet. See `evals/jev_comparison/results/` for measured
numbers and the list of things that were **not** verified.
