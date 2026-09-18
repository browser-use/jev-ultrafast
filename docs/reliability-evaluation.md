# Nested DOM and text-recovery evaluation

This note records an exploratory, paired comparison against commit
`1231850a0bf1a0c0341fe408ef1668dbbfdfac46`. It is evidence for this change, not a
claim of general browser-agent superiority. The production files in this PR are
byte-identical to the evaluated candidate; their SHA-256 hashes and sanitized
per-trial outcomes are in [reliability-evaluation.json](reliability-evaluation.json).

## Changes being evaluated

- Observe and operate controls in nested open shadow roots and accessible
  same-origin frame documents, retaining actual node identities.
- Resolve root-local labels, frame coordinates, and shadow/frame hit tests;
  include nested values/documents in freshness checks.
- Reject unsupported frame geometry (including padding) before input rather than
  guessing coordinates. Native select `input` events cross shadow boundaries;
  `change` remains non-composed.
- Retry a malformed text-helper reply once with identical input, before any
  browser mutation. The valid-first-response path still makes one generation
  request. A second invalid reply fails closed.

There are no site-specific policy plans, prepared field strings, model-generated
selectors/scripts, new dependencies, or automatic browser-mutation retries.

## Challenge comparison

A protocol fixed six equally weighted families before comparative live runs.
Two development variants per family were separate from ten held-out variants.
The held-out evaluation ran 60 paired tasks (120 attempts), balanced 30 AB / 30 BA,
in fresh owned tabs. The same Jev and text models were used for both versions:
`jev-latest` and `inception/mercury-2.5`, reasoning disabled for the text helper.
The browser was isolated HeadlessChrome 149 with browser-harness 0.1.13.

Each synthetic registration task required text entry, native selection, consent,
and one submission. A fresh DOM oracle checked actual field values and the
submitted payload, not just the agent's `DONE` answer.

| Family | Baseline | Candidate |
|---|---:|---:|
| Standard form | 10/10 | 10/10 |
| Delayed control exposure | 0/10 | 0/10 |
| Control replacement | 10/10 | 10/10 |
| Open shadow root | 0/10 | 9/10 |
| Same-origin iframe | 0/10 | 10/10 |
| Malformed first text-helper reply | 0/10 | 9/10 |
| Total | 20/60 | 48/60 |

This is 2.4x verified completion (+140% relative, +46.67 percentage points) **on
this intentionally enriched challenge distribution**. There were 28
candidate-only successes and no baseline-only successes. No false `DONE`,
duplicate submissions, or wrong submitted payloads were observed. Both versions
failed all delayed-control cases; the candidate's other two failures stopped
before submission with `ValueError`.

The malformed family replaces exactly the first helper response with synthetic
invalid JSON, without a provider call for that replacement; subsequent calls are
real. Its frequency is artificial and does not estimate provider reliability.
Two of the other families exercise features explicitly unsupported upstream.
Those limitations are why the aggregate is not a broad-web success estimate.

### Timing and uncertainty

Task wall time includes initial navigation, model calls, observation, and final
verification; process startup and tab cleanup are excluded. Challenge event
logging adds synchronous instrumentation overhead equally to both versions.

- Standard-form common-success pairs: median candidate/baseline wall ratio
  **0.9756**, n=10; p90 ratio **1.1372**.
- All common-success challenge pairs: median ratio **0.9991**, n=20.
- Stratified paired percentile bootstrap (10,000 draws, seed 732019): success
  ratio interval **[2.25, 2.50]**; standard paired-time interval
  **[0.9045, 1.0690]**.

The bootstrap resamples within the fixed families. Several families have
boundary outcomes, so the baseline count stays 20 in every resample. These
intervals describe suite-resampling uncertainty, **not general-web uncertainty
or statistical latency noninferiority**. Fast `BLOCKED` failures are not counted
as fast completions. Some individual candidate runs were slower.

## Separate sanity/regression checks

Three paired repeats per task, separately from the challenge score:

| Task | Baseline | Candidate | Median paired wall ratio on common successes |
|---|---:|---:|---:|
| Repository's hotel fixture | 2/3 | 3/3 | 0.8834 |
| Wikipedia article search | 3/3 | 3/3 | 1.0830 |
| Google Flights search | 3/3 | 3/3 | 0.8991 |

Pooled common-success median ratio: **0.9307** (eight pairs); p90 **1.1023**.
The hotel task is synthetic, not a public hotel website. Its baseline failure
was an invalid text-helper reply. Flights checks covered route/date/year,
one-way, visible results, one passenger and Economy. Passenger age composition
was not independently expanded/verified. No flight was selected or booked.
This small sanity set is not a new reliability benchmark.

## Regression checks available in this PR

```sh
uv sync
uv run pytest -q
uv run ruff check .
node --check jev_ultrafast/snapshot.js
node --check jev_ultrafast/static/app.js
uv build
# Real local browser, no model/provider calls:
uv run python scripts/check_guards.py
uv run python scripts/check_dom_roots.py
```

The focused contribution has 106 offline tests: the original 31 plus 75
text-recovery cases, including valid-path request count, malformed shapes,
exhaustion, exact-input reuse, transport/auth propagation, and mutation ordering.
The separate browser script has 13 tests covering nested roots, actual input,
navigation/replacement, overlays, unsupported frames, padded-frame decoy clicks,
shadow event counts, and clipped frame text. The original 21 browser guard
checks also pass. Test-first and independent review caught the padded-frame
wrong-click and shadow event-propagation defects before final evaluation.

The JSON is a compact outcome export, not a self-contained replay of the live
experiment: the original experiment harness, detailed traces, and generated
fixtures are deliberately not bundled into this focused runtime PR. The tests
and browser regression script are runnable, but the aggregate experiment needs
that separate harness and paid API calls to replicate. Raw evidence is retained
by the contributor; no credentials, browser profile, or local paths are included
here.

## Remaining boundaries

Closed shadow roots, cross-origin/opaque frames, padded/transformed/zoomed frame
chains, nested scrolling, canvas, uploads, pop-up tabs and arbitrary keyboard
widgets remain unsupported. Full accessible-name/slot semantics are not claimed.
The page-world node cache is not a security boundary against hostile page code.
No approval policy, general PII redaction, or durable mutation reconciliation is
added. Additional DOM traversal has overhead; DOM-heavy broad-site performance
has not been established. Browser Harness emitted an existing unclosed-socket
`ResourceWarning` during some passing local browser tests.
