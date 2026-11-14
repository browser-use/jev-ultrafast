# Ego reliability closure and the Ego vs Chrome backend benchmark

This report closes three questions in one pass:

- **Part A** — is the Ego backend reliable enough to keep, and is its freshness
  contract correct rather than merely permissive?
- **Part B** — do `SELECT`, `SCROLL` and `WAIT` actually work in a real browser,
  or were they only ever exercised by unit tests?
- **Part C** — how do Ego and Chrome (Browser Harness) compare on the same
  tasks, measured on identical code with an independent validator?

Everything below comes from recorded runs under `recordings/`. A failure is kept,
never overwritten or retried away: the runs that exposed the two Ego adapter bugs
described in Part A live in `recordings/final/ab-before-ref-fix/` and
`recordings/pre-final/`, the pass that exposed the Chrome `WAIT` defect lives in
`recordings/final/probes-before-wait-fix/`, and every superseded pass of the final
A/B is kept under its own name (`ab-blocks-two-windows`,
`ab-interleaved-before-wait-fix`, `ab-old-truth`) rather than deleted.

The final evidence — the A/B in Part C, the five-run regression in A.3 and the six
probe runs in Part B — is one implementation hash,
`41a9eed3bafea3c3dc764e210602d4667fe8f2605732ab0da657ff69b1f5d71d`, and is
reproducible from the recordings by re-running the scorer and the table
derivations.

## How the runs are produced

| Piece | Where |
| --- | --- |
| Runner | `scripts/run_backend_benchmark.py` (`--suite {reliability,probes,ab}`) |
| Task definitions and validators | `scripts/benchmark_suite.py` |
| Scoring | `scripts/score_backend_benchmark.py` |
| Local deterministic fixture | `tests/fixtures/jev_complex_benchmark.html` |
| Per-run artifacts | `summary.json`, `steps.json`, `validation.json`, `events.jsonl`, `metrics.json`, `process_evidence.json`, `tab_evidence.json`, `version_manifest.json` |

Every run records the implementation hash it ran against
(`version_manifest.json` → `implementation_hash`), so a number can always be
traced back to the policy code state it ran on (the hash covers `jev_ultrafast/**`
and `snapshot.js`, not the benchmark scripts — see C.8). The raw runs stay on disk like the earlier rounds'
(`recordings/final/`, 19 MB of JSON, no images, `.gitignore`d): the report cites
them by path, and `scripts/run_backend_benchmark.py` re-records any suite.
Isolation is measured, not assumed: for Chrome the
runner records the target list before and after the run (a background target is
created and closed; the user's tabs must be identical), and for Ego it records
the runtime's child pids, whether they survive `close()`, and which
`ego-browser nodejs` processes appeared **during** the run rather than before it.

Success is never the model's own `DONE`. Each task has a validator that is a
code-owned, read-only DOM/protobuf expression evaluated after the run while the
page is still open, and again at the moment a `DONE`/`BLOCKED` decision is made
(so a premature `DONE` is measured, not guessed).

---

# Part A — Ego reliability final closure

## A.1 The freshness contract

A decision is bound to the observation it was made on. Before any mutation both
backends must answer the same question: *is the page still the page I decided
about, and is the decided target still the same live, usable element?*

| Check | Chrome (Browser Harness) | Ego |
| --- | --- | --- |
| Same document | the page marker produced by the atomic snapshot | backend generation counter + document probe |
| Same target | the target node's full guard, including the nearby visible text it was decided on | the semantic node's guard from a read-only `fresh` probe |
| Actionability | the snapshot's actionable flag | `actionable`, plus `writable` for `fill` |
| Document-level decisions (`DONE`/`WAIT`/`SCROLL`) | the page marker (visible change invalidates) | identity only |

The two contracts are not identical, and this report states the difference instead
of flattening it: Chrome's document-level check is stricter (a `DONE` decided on
text that has since changed is stale), while Ego's is deliberately narrow
(unrelated text, layout movement, or scrolling does not invalidate a decision).
Both are pinned by their own code and checks — Chrome's by
`scripts/check_guards.py`, which asserts that nearby text churn *does* invalidate a
click decision, and Ego's by the adapter's own probe. An earlier draft of this work
re-implemented Chrome's probe with Ego's contract; that was rejected by the
independent review and reverted, because it silently changed a documented
behaviour instead of fixing a bug.

Both are read-only: a freshness check never re-snapshots the page, never rebuilds
the action list, and never replays the mutation it is guarding. When the answer is
"no", the loop re-observes and decides again. `MAX_STALE_RETRIES` is a
**no-progress fuse** — it counts consecutive ticks in which nothing was executed —
not a retry budget for mutations: a mutation is attempted at most once, and a
retry after a stale decision re-decides from a fresh observation first.

Ego's target check is deliberately narrow. Unrelated page text, layout movement,
or scrolling does not invalidate a decision; only the decided node changing,
detaching, or becoming unusable does. On a real site that still leaves a control
that briefly animates or re-renders, so the target probe is re-read a few times
(bounded, read-only) before a decision is called stale.

## A.2 Definite bugs found and fixed

The audit was not a refactor: the loop's decision mechanism, the backend
factory, Browser Harness, metrics, cleanup, the MCP surface, `probe.js` and
`timeline.py` are byte-identical to the commit this work started from. Four
definite bugs were found and fixed in the Ego adapter, plus one configuration bug
in `Agent` (Bug 5) and one in the shared `WAIT` action (Bug 6). Earlier drafts of
this report also carried two further changes to `browser.py` and `snapshot.js`;
they were reverted when the independent review showed they changed the documented
Chrome freshness contract instead of fixing a bug, and every run here was recorded
again afterwards. `browser.py` and `snapshot.js` therefore differ from that commit
by the two lines of Bug 6 and nothing else.

### Bug 1 — every ARIA listbox option after the first was unclickable

Ego's semantic snapshot names a listbox option `listboxoption`, while the state
actions describe the same element as `role="option"`. The adapter's role map had
no entry for `listboxoption`, so no semantic node was extracted for options 2..n,
no ref could be attached, and `act` refused the target with `missing_ref`. The
model chose the right element and the run still died.

Recorded proof, `recordings/final/ab-before-ref-fix/flights-ego-2/`: two
consecutive decisions on the same offered target (`CLICK`, decision index 2 both
times, confidences 0.74 and 0.79) were refused by the adapter with
`stale reason missing_ref source act`, and the run then hit the no-progress fuse
at three ticks. The recorded step list shows the target it kept choosing was
`One way` — the right element, on the right page.

Fix: map `listboxoption → option` (and `treeitem → treeitem`), so the option
nodes exist for the matcher to find. Refs are assigned per observation, so they
are not stable between runs, and the report quotes them only where a recording
contains them: in `flights-ego-3` the decided `One way` option is `ref @31`, the
click is recorded `result: success`, and the URL it returns already carries the
committed one-way search (`tfs=CBwQARoQ…`, field 19 = 2). What the fix
guarantees is that every option in the menu has a ref at all.

Ref matching is a preference, not a gate. Ego's semantic name for a control is
often the field's own placeholder — the fixture input the page labels
*"Destination city"* is `textbox [ref=6] text "City or route"`, and the category
select is `combobox [ref=7] text "Choose a category"` — so an unrelated name
usually still means the same live element. A candidate whose name relates to the
control is ranked first; when nothing relates, the previous best-effort ranking
still applies. Rejecting unrelated names outright was measured and rejected: it
cut fixture target coverage from 16/16 to 11/16 and made the fixture's own
`SELECT` step impossible. The residual risk is stated in Part C: a best-effort
ref could in principle address a neighbouring element, which the post-run
validator would catch as a wrong target rather than as success.

### Bug 2 — a target the adapter cannot act on was still offered as a choice

`observe()` counted `unmapped_targets` and then handed those targets to the
decision anyway. Choosing one could only burn a stale retry, and three of them
fused the run. Fix: a target with no ref at all is no longer offered as a choice,
and the count is kept on the page (`unmapped_targets`) and reported in every
`observe` event of a recorded run (5 per observation on the Flights suggestion
list). The labels themselves live only in memory during the run — the recorded
page keeps the count, not the list — and the review flagged that; C.6 therefore
marks the label quote as a live probe rather than a recording. This is the belt
to Bug 1's braces: a control Ego's tree never names cannot be clicked, so the
model must not be invited to try.

### Bug 3 — `observe()` reported refs it had not verified

`_ref_match` could harvest `@word` out of an accessible name (for example
`button "Email us @support"`), turning a label into a ref. The name is now read
before the attribute block, and only the bracketed/attribute ref forms are
accepted.

### Bug 4 — a click's next observation was a transitional frame

After a click that dismisses a popup, Ego's observation kept returning the popup
that the click had just closed. The URL had already moved on, the action list had
not. A policy shown only three spent menu options can do nothing but stop, and
that is what happened: on Google Flights the one-way search *was* committed
correctly (`tfs=…`, field 19 = 2) and the next decision was `BLOCKED` at
0.41 confidence, because the page it was shown contained no way forward.

Measured directly, same code, before and after the fix (a read-only live probe,
no model calls; it is not part of `recordings/`, and the durable evidence for
this loop is the deterministic settle test in A.5):

```
before   after clicking "One way":  5 actions  = Round trip / One way / Multi-city
after    after clicking "One way": 29 actions  = the settled search form
```

The first fix — accept a reading once three consecutive reads agree — was measured
and was not enough, because these transitional frames are *stable*: the dismissed
popup stays in the tree for 2.2–2.7 s, and the menu that opens animates for about
2 s through frames of 31 → 34 → partial → 5 actions. Each frame is stable for
hundreds of milliseconds, so "stable" accepts it.

The settle now waits for the action set to be **unchanged for a 1.2 s quiet
period** (200 ms probe delay, 200 ms interval, 4 s budget), and a click whose URL
changed additionally waits, bounded at 2.5 s, until the observation reflects the
navigation it just caused. Both numbers come from the measured curves above.
Verified on the live page with the same read-only probe:

```
click "Change ticket type. Round trip"  ->  5 actions, menu open   (4.3 s)
click "One way"                         -> 29 actions, the form    (3.5 s)
```

A last comment on Bug 4, because the report has to be straight about its own
scope: this settle is what removed the Ego arm's *need* for an explicit `WAIT` on
the Part B fixture. The first version of that fixture finished its asynchronous
job in 650 ms — less than the 1.2 s quiet period — so after the fix Ego's next
observation already contained the finished state and the run never chose `WAIT`.
The fixture's delay is now 3.5 s, longer than either backend's own post-action
settle, and Part B's Ego `WAIT` row is a run on the final code that really issues
the operation.

The cost is real and shows up in Part C's performance subscore; leaving the race
in place was not an option, because it turns a correct action into a spurious
`BLOCKED`.

### Bug 5 — `screenshots=False` could not be turned off while recording

`Agent.__init__` computed `self.screenshots = screenshots or bool(record_dir)`, so
a caller that passed `screenshots=False` together with a record directory — which
is exactly what a measured run does — silently got screenshots anyway. The model
never consumes them (AGENTS.md: "Screenshots are optional"), and a measured run
must not pay a capture cost that differs per backend, so the flag now means what
it says: `None` keeps the old default (on while recording), `False` turns them
off. Two functional lines, in `agent.py`; every benchmark run passes
`screenshots=False`, so the wall times below measure the loop and not the
capture.

Two smaller edits belong in the same list, since the report claims to account for
every change in the tree: the goal text in `examples/flights.py`, `README.md`, and
the demo page (`static/index.html`, `static/app.js`) no longer asks for a fixed
"September 20, 2026" and no longer waits for visible flight options — it asks for
the first date the calendar offers and stops when the search is committed, which
is what the example actually verifies. The Ego adapter, the loop, the benchmark
scripts and the tests are the rest of the diff.

### Bug 6 — the Chrome `WAIT` action did not actually wait

`snapshot.js` offered the model a `wait` action whose implementation was
`time.sleep(0.1)`: 100 ms, well under the loop's own decision latency. The Ego
bridge, written independently, reads a wait duration from the action and sleeps it
(clamped to 50–3000 ms, default 500). On an asynchronous page the same model made
the same correct choice on both backends and got opposite outcomes:

```
Chrome  CLICK(0.99) → WAIT(0.81) → WAIT(0.54) → WAIT(0.46) → WAIT(0.39) → BLOCKED(0.44)
        validator: started=true  ready=false  result_visible=false  downloaded=false
Ego     CLICK(0.99) → WAIT(0.80) → WAIT(0.55) → CLICK(0.94, "Download report") → DONE(0.97)
        validator: started=true  ready=true  result_visible=true  downloaded=true   → pass
```

Both traces are recorded (`recordings/final/probes-before-wait-fix/`). Four
consecutive `WAIT` decisions — all executable, all executed, none of them a retry
— moved the Chrome page forward by about 0.4 s in total, not enough to cross a
3.5 s job, and the model's confidence decayed 0.81 → 0.39 until it declared the
page `BLOCKED`. The operation existed but could not do its job. Fix: the `wait`
action carries `wait_ms: 800` and `Browser.act` sleeps exactly that field — the
same field the Ego bridge already honored — so one `WAIT` decision means the same
thing on either backend. Both Part B `WAIT` runs are on the fixed code.

This is the one place where the anti-refactor rule's "unless a definite bug is
found" clause was used for a file outside the Ego adapter, and it was used only
after the bug was recorded: an earlier draft changed this as a fairness
preference, with no failing evidence, and the independent review correctly
rejected that. The two other frozen-surface changes from that draft (a
re-implementation of Chrome's freshness probe and a different wait without a
recorded failure behind it) were reverted rather than defended.

## A.3 Live regression — Ego on Wikipedia, five consecutive runs

Same goal, same final code, five runs back to back:
*"Find and open the Wikipedia article about Gödel's incompleteness theorems."*

| Run | Status | Validator | Wall | Jev calls | Orphan process |
| --- | --- | --- | ---: | ---: | --- |
| `wikipedia-ego-1` | done | pass | 12.2 s | 3 | no |
| `wikipedia-ego-2` | done | pass | 14.1 s | 3 | no |
| `wikipedia-ego-3` | done | pass | 10.6 s | 3 | no |
| `wikipedia-ego-4` | done | pass | 11.8 s | 4 | no |
| `wikipedia-ego-5` | done | pass | 12.7 s | 4 | no |

5/5 consecutive passes on the final code
(`implementation_hash 41a9eed3…`, `recordings/final/reliability-2/`), 10.6–14.1 s,
three to four Jev calls each (runs 4 and 5 record one stale tick after the first
step, so the loop re-decided once — the fuse, not a replayed mutation), and no
surviving runtime process after `close()` in any run. The validator is not the
model's `DONE`: it re-reads the live page and requires the article heading and its
URL, so a `DONE` on the wrong page fails the run.

The neighbouring pass is reported too, because leaving it out would be the kind of
selection this report is supposed to avoid: `recordings/final/reliability/` ran the
same five-run suite 74 seconds after the first pass finished and scored **4/5** —
`wikipedia-ego-4` died with
`RuntimeError: Model connection failed; no action executed.` after two correct
steps (typed the query, clicked the suggestion). Across both passes the record is
**9/10 on this implementation**, with the single failure an upstream model-provider
connection error rather than a browser, adapter, or loop failure. Every earlier
pass is archived next to these and is not mixed in:
`reliability-before-ref-fix`, `reliability-ego-ref-fix`, `reliability-ego-settle`,
`reliability-ego-quiet` and `reliability-old-truth`; the last of those was also 5/5
on this hash and differs only in the step-scoring ground truth fixed in A.5, which
does not touch the validator outcome.

## A.4 What the audit confirmed

- Ego error taxonomy is intact and used: `StalePage` for a decision the page has
  moved past, `EgoActionError` for a rejected/failed input, `EgoTransportError` /
  `EgoRemoteError` for a dead or malformed runtime. A transport failure is never
  silently reported as a stale page, and a stale page is never reported as a
  successful action.
- `TYPE_TEXT` binds to the decided target's ref, never to a CSS selector, and the
  cached text value is reused only while the whole helper input is identical.
  `fill` is issued once, with `clearFirst`, and never replayed.
- `close()` finishes the Ego task space exactly once and is safe on a partially
  constructed backend (the double-spawn path on a failed construction is fixed).

## A.5 Measurement fixes

The benchmark's own instrumentation was audited with the same standard, because a
wrong measurement is as damaging as a wrong adapter:

- **A step the task does not pin down was scored as a wrong target.** The target
  head is now scored only when the predicted operation is one that carries a
  target *and* the task's expectations name one for that state; the rest are left
  unscored. The rule is applied uniformly, old runs included, by the scorer, so no
  recorded evidence had to be rewritten.
- **The runner recorded `ego_task_name: null` for every Ego run.** It read the
  environment variable back *after* restoring it, so the isolation check that each
  run gets a fresh TaskSpace could never pass. The runner now records the space
  name it set, and the check compares it against the name derived from the run id.
- **Isolation scoring counted third-party activity.** A changed *user* tab set is
  now reported separately instead of scored: Chrome target ids also change when
  the browser discards a tab, and a person browsing the same profile during a run
  is not this benchmark's leak. Scored failures are only what the benchmark did —
  a tab it opened and did not close, a process that outlived `close()`, a second
  runtime, a run without its own space. Unrecorded fields are unknown, never
  failures.
- **The settle fix got a deterministic test.** The transitional-frame loop is
  exercised with a fake clock and a fake page, so the quiet-period behaviour is
  pinned without a browser: a churning frame must run to the budget instead of
  ending early.
- **Two of the four tasks scored the target head against a ground truth that was
  itself wrong**, and the final pass was recorded again after fixing it. On the
  Example task the expectation still named the historical `More information...`
  link; the live page has said `Learn more` for years, so a correct click was
  scored as a wrong target on both arms (all six runs). On Flights the expectation
  re-demanded the trip-type control on the step *after* the model used it, so the
  correct `One way` click that opens the menu was scored as a wrong target; the
  menu-open state is now its own claimed state. These are two different situations
  and the report states both: the Example defect penalised both arms equally, while
  the Flights defect penalised Chrome on three of the four steps the fix re-credits
  (three Chrome runs clicked `One way`, one Ego run did), so the correction widened
  Chrome's target lead slightly — about 0.07 of the final 14.1 points — instead of
  favouring Ego. Neither defect was left in place: a measurement that is wrong for
  everyone still measures nothing, and one that is wrong for one arm measures the
  report.
- **Label matching is tolerant of a control being named at different granularity,
  and that tolerance is inert in this pass.** The comparison accepts normalized
  equality or whole-token containment (`labels_match` in
  `scripts/run_backend_benchmark.py`, pinned by tests). Its original justification
  was wrong and a review caught it: no recording shows Chrome naming the Wikipedia
  input `Search` — both arms predict `Search Wikipedia` in every Wikipedia run,
  including the 74 runs archived under `recordings/final/` (15 Chrome, 59 Ego)
  whose expectation was the bare `Search`.
  What the recordings do show is Ego clicking a suggestion named `Search` on the
  next step, in an unconstrained state. After the ground-truth corrections, all 80
  target-scored comparisons in the final A/B are exact matches, so the tolerance
  changes no number in C.9 either way; it stays as a naming-difference guard, not
  as credit for either arm.
- **The review's anti-refactor finding was accepted.** Two of the three changes
  an earlier draft had made outside the Ego adapter did not fix a bug: Chrome's
  freshness probe had been re-implemented with the Ego contract, which
  contradicted the repo's own `scripts/check_guards.py` (it asserts that nearby
  text churn invalidates a click decision), and the wait duration had been
  changed as a preference. Both were reverted to the frozen files; only the
  recorded `WAIT` defect in Bug 6 remained, and only after it had produced a
  failing run. `scripts/check_guards.py` now passes again on the live browser:
  `PASS: 21 browser guard checks; no model calls` (a live check that
needs a browser, so it is not part of `recordings/`).

---

# Part B — live coverage for SELECT, SCROLL and WAIT

Each operation gets its own purpose-built fixture, its own real run in a real
browser, and a validator that checks the operation, the target, executability,
the page change, and the classification. The two backends run the identical goal.
All six runs below are from one pass on the final code
(`implementation_hash 41a9eed3…`), recorded in `recordings/final/probes/`.

## B.1 SELECT

Fixture: a carrier list with two unrelated selects (`packaging` is a deliberate
distractor) and a confirmation region that only renders for the right choice.

| Backend | Steps | Validator checks | Jev calls | Wall |
| --- | --- | --- | ---: | ---: |
| `browser_harness` | `SELECT(0.98) → DONE(0.97)` | `carrier="overnight"`, `confirmed`, `confirmation_visible`, `packaging=""` | 2 | 1.6 s |
| `ego` | `SELECT(0.99) → DONE(0.99)` | identical | 2 | 3.7 s |

`packaging=""` is the part that matters: the distractor select was never touched,
so the right target was chosen for the right operation, and the classification is
`SELECT` rather than a click on the option. Two Jev calls per run is the whole
cost: one decision for the select, one for `DONE` — no retry storm.

## B.2 SCROLL

Fixture: a 900 px filler, a no-op `#changelog` link, and a `#reveal` panel whose
code only appears after it is clicked — so scrolling is necessary, and scrolling
alone is not sufficient.

| Backend | Steps | Validator checks | Jev calls | Wall |
| --- | --- | --- | ---: | ---: |
| `browser_harness` | `SCROLL_DOWN(0.86) → SCROLL_DOWN(0.55) → CLICK(0.99, "Reveal release code") → DONE(1.00)` | `scrolled` (max 696), `revealed`, `code_visible`, `code_text="CODE-4.2-READY"`, `changelog_clicks=0` | 4 | 2.0 s |
| `ego` | `SCROLL_DOWN(0.86) → SCROLL_DOWN(0.48) → CLICK(0.99) → DONE(0.99)` | identical (max 674) | 4 | 6.5 s |

Both runs scroll with the `scroll` action (`scroll_down`, delta 560), not with a
key press or a synthetic wheel on a random element, and both leave the distractor
link untouched. Note the operation confidence: the second scroll is a lower-
confidence repeat of the first (0.55 / 0.48) because the panel is still off-screen
— the model continues an operation it already started rather than inventing one.

The **first-pass Ego run of this probe failed**, and it is kept:
`recordings/pre-final/probes-first-pass/probe-scroll-ego-1` ended `DONE` at
confidence 0.43 while the page still said *"Reveal the code to finish."* The
scroll itself was correct (`max_scroll 560`, `scrolled=true`); the goal-state
judgment was wrong. Chrome's identical run passed. That is a real Ego failure mode
— a premature `DONE` — and it is exactly what the goal-state column in Part C
measures rather than hides.

## B.3 WAIT

Fixture: a **Generate report** button starts an asynchronous job that takes 3.5 s;
the result only appears after the job finishes, so clicking immediately is too
early. The delay is deliberately longer than either backend's own post-action
settle, so the run cannot pass by accident.

| Backend | Steps | Validator checks | Jev calls | Wall |
| --- | --- | --- | ---: | ---: |
| `browser_harness` | `CLICK(0.99) → WAIT(0.78) → WAIT(0.57) → WAIT(0.44) → CLICK(0.95, "Download report") → DONE(0.97)` | `started`, `ready`, `result_visible`, `downloaded`, `distractor_clicks=0` | 7 | 5.1 s |
| `ego` | `CLICK(0.99) → WAIT(0.82) → WAIT(0.51) → CLICK(0.94, "Download report") → DONE(0.96)` | identical | 5 | 9.1 s |

Chrome's seven calls for six steps are one extra *read-only* re-decision: the run
recorded exactly one stale tick, and no mutation was ever replayed. That is the
no-progress fuse behaving as designed, and it is the only retry seen in the six
probe runs.

Fairness note: the `WAIT` action now carries `wait_ms: 800` in the observation and
both backends sleep exactly that field, so one `WAIT` decision means the same
thing on either arm. That was not true when this report was first written, and the
difference is Bug 6 above — Chrome's five-step probe row is the fixed behaviour,
and the failing row that motivated it is preserved in
`recordings/final/probes-before-wait-fix/probe-wait-browser_harness-1`.

`unnecessary_wait` is 0 for both arms in Part C: the model does not reach for
`WAIT` when the page is already settled.

---

# Part C — Ego vs Chrome, final A/B

## C.1 Design, arms, and controls

Four tasks, one goal each, both backends, interleaved so neither arm runs in a
block and machine conditions are shared:

| Task | Page | Goal (verbatim) | Runs per arm |
|---|---|---|---:|
| A simple | `https://example.com/` | Click the link on this page that explains the example domain, so the IANA page about example domains is open. | 3 |
| B medium | Wikipedia main page | Find and open the Wikipedia article about Gödel's incompleteness theorems. Stop when the requested article is visibly open. | 3 |
| C complex, real site | Google Flights | Use Google Flights to set up a one-way flight search from Zurich to London for one adult in economy, and pick the first departure date the calendar offers. Stop once the search is committed for that route and date. | 3 |
| D deterministic, complex, local | [jev_complex_benchmark.html](../tests/fixtures/jev_complex_benchmark.html) | Create a travel research request: enter "Zurich to London" as the destination, set the category to "Flights", then choose "High" priority, then wait until the Continue control appears, continue, scroll down to the review section, open the final review, and submit only when the summary shows the requested destination, category, and priority. Stop when the success panel is visible. | 5 |

Controls held constant:

- **Same policy, same model, same instructions.** Only `ULTRAFAST_BROWSER_BACKEND`
  differs. The decision mechanism, the operation/target heads, and the text
  helper are untouched by the backend choice.
- **One interleaved pass.** Both arms were recorded by a single runner invocation
  that alternates the backends inside every repetition — 27 arm switches across
  the 28 runs, Chrome 22:31:15–22:36:00Z and Ego 22:31:20–22:36:23Z, so the two
  arms share one machine window instead of one block each (the recordings store
  the runner's single alternating plan order and `suite: "ab"`, not the command
  line that produced it). This is a correction:
  the first version of this report compared two passes recorded two hours apart
  and described them as interleaved, which the independent review caught. The
  earlier passes are kept in `recordings/final/ab-blocks-two-windows/` and
  `recordings/final/ab-interleaved-before-wait-fix/`.
- **The Chrome arm ran against a dedicated automation instance.** Chrome was
  started with its own profile and `--remote-debugging-port`, and the harness was
  pointed at it with `BU_CDP_URL`; the user's own Chrome profile and its tabs were
  never part of the measurement. This is also why no run in this pass reports a
  changed user tab set. It is a deliberate deviation from "the user's Chrome",
  and it makes the Chrome arm's wall times incomparable with the earlier passes'
  (an empty profile with no extensions is faster). The recordings do not store the
  launch command; the indirect evidence they do store is that all 14 Chrome runs
  see the same four-id target list before and after the run.
- **Isolation.** Ego gets a fresh, uniquely named TaskSpace per run
  (`ultrafast-benchmark-ego-<run_id>`, recorded in the summary) and exactly one
  runtime, closed at the end. Chrome gets one background target per run, created
  and closed by the harness.
- **Independent validation.** Every run ends with a code-owned, read-only
  expression written before the run. For Google Flights that is two different
  sources and the report now names them separately: the trip type and the
  departure date are decoded from Google's base64 `tfs` protobuf (field 19 = 2 is
  one way, and the ISO date is searched in the decoded bytes), while origin and
  destination are read from the page text. Wikipedia is a URL/title check, and the
  local fixture is DOM state. A `DONE` choice is never proof of success; the
  validator also runs live at the `DONE`/`BLOCKED` decision, so a premature stop
  is visible.
- **Scoring.** The fixed formula from the task specification, applied by
  [score_backend_benchmark.py](../scripts/score_backend_benchmark.py) to the
  recorded runs. Nothing in this section is hand-tallied, and the score file is
  regenerated from the recordings by that script.

## C.2 Per-task results

Per-task raw results (validator pass, median wall time, Jev calls, stale ticks,
recorded steps):

| Task | Arm | Runs | Validator | Median wall | Jev calls | Stale ticks | Steps |
|---|---|---:|---:|---:|---:|---:|---:|
| example | Chrome | 3 | 3/3 | 1.1 s | 6 | 0 | 6 |
| example | Ego | 3 | 3/3 | 4.0 s | 6 | 0 | 6 |
| wikipedia | Chrome | 3 | 3/3 | 5.0 s | 15 | 6 | 9 |
| wikipedia | Ego | 3 | 3/3 | 12.4 s | 9 | 0 | 9 |
| flights | Chrome | 3 | 3/3 | 12.4 s | 50 | 21 | 30 |
| flights | Ego | 3 | 0/3 | 16.7 s | 14 | 0 | 14 |
| complex | Chrome | 5 | 5/5 | 5.2 s | 45 | 0 | 45 |
| complex | Ego | 5 | 5/5 | 22.2 s | 40 | 0 | 40 |

The Ego flights median is *faster* than Chrome's because those runs end early with
the run status `blocked` — the no-progress fuse, not the model choosing `BLOCKED`,
which is why C.5 shows zero `BLOCKED` decisions for the same runs; see C.6.

## C.3 Operation and target judgment, step by step

Every recorded step carries the top-1 operation and target, their probabilities,
the runner's expectation for that state, and whether the choice was correct. A
step is scored only when the task pins the answer down: a state the expectations
do not constrain has no ground truth, and is left unscored rather than counted as
a wrong target. Target labels are compared on normalized accessible names that
match exactly or as a whole-token part of one another, so that a control named at
a different granularity is scored as a naming difference rather than a wrong
target (A.5 records when that tolerance actually fires, which is not in this
pass).

| Task | Arm | Op steps | Op top-1 | Correct-op p | Target steps | Target top-1 | Correct-target p |
|---|---|---:|---:|---:|---:|---:|---:|
| example | Chrome | 6 | 1.00 | 0.72 | 3 | 1.00 | 1.00 |
| example | Ego | 6 | 1.00 | 0.73 | 3 | 1.00 | 1.00 |
| wikipedia | Chrome | 9 | 1.00 | 0.96 | 3 | 1.00 | 1.00 |
| wikipedia | Ego | 9 | 1.00 | 0.96 | 3 | 1.00 | 1.00 |
| flights | Chrome | 13 | 0.69 | 0.63 | 10 | 0.60 | 0.52 |
| flights | Ego | 4 | 0.50 | 0.53 | 4 | 0.50 | 0.44 |
| complex | Chrome | 45 | 0.89 | 0.75 | 30 | 1.00 | 0.91 |
| complex | Ego | 40 | 1.00 | 0.83 | 30 | 1.00 | 0.85 |

Two things are worth reading carefully here. On the two deterministic tasks
(example, and the local fixture) the target head is perfect for both arms, and the
operation misses are one per Chrome fixture run plus the real-site ones described
below. On Google Flights both arms score 0.60 / 0.50 on targets over small
samples, and the recorded misses are *order* deviations — the expectation claims
"while the ticket type is still round trip, switch it first", and the model
sometimes types the origin field first. Both are valid paths to the goal; the
metric encodes one of them, and it does so for both arms equally. The claim is
stated rather than hidden: this column measures "follows the canonical order where
the task pins one down", not "reaches the goal".

Chrome's five operation misses on the fixture deserve their own note, because they
are the whole of Chrome's 0.89: in all five `complex` runs the policy clicked the
fixture's deliberate distractor **"Save and continue"** as its fourth step
(`distractor_clicks: 1` in every Chrome run), then clicked the real "Continue" and
finished the task in nine steps. Ego took the canonical eight-step path in all
five runs and never touched the distractor. The distraction costs Chrome operation
accuracy and wall time but not success — the fixture's validator requires the
destination, category, priority, review, submit and success panel, and records
distractor clicks without failing on them. An earlier draft of this section
summarised this as "the final five runs all take the canonical eight-step path",
which is true for Ego only; the Chrome arm is the reason the fixture has a
distractor at all.

The earlier version of this table showed 0.00 target accuracy for example and
Wikipedia on both arms. That was a ground-truth defect in this benchmark — the
expectation named `More information...` and `Search`, the live pages say
`Learn more` and `Search Wikipedia` — and it was fixed before this pass was
recorded, not explained away (A.5).

## C.4 Probability calibration

Reported separately from success, as required. Predicted confidence is bucketed;
"correct" is the observed outcome in that bucket.

| Predicted | Operation steps | Operation correct | Target steps | Target correct |
|---|---:|---:|---:|---:|
| 0.0–0.4 | 4 | 1.00 | 0 | — |
| 0.4–0.6 | 29 | 0.86 | 0 | — |
| 0.6–0.8 | 4 | 0.50 | 2 | 1.00 |
| 0.8–0.9 | 6 | 0.50 | 8 | 0.62 |
| 0.9–1.0 | 89 | 0.98 | 76 | 0.96 |

The operation head is well calibrated at the top: 89 steps predicted above 0.9
were right 98% of the time. Its low-confidence middle is where the real-site
errors live — of the four scored steps in the 0.6–0.8 bucket, two are correct
`DONE` calls on Flights (0.61 and 0.63) and two are the misses that cost Chrome
its operation accuracy: `TYPE_TEXT` on `Where to?` at 0.72 and 0.71, typed before
the trip type was switched. All four steps below 0.4 were still right. The target
head is honest at the top (0.96 over 76 steps) and overconfident in the 0.8–0.9
bucket (0.62 over 8 steps), which is the honest summary of where this system's
judgment is thinnest. Margins say the same thing in one number, over the steps the
task actually pins down (73 / 59 operations, 46 / 40 targets — the same
populations as C.3 and C.9):

| Arm | Op steps | Mean op top-1 p | Mean op second best | Mean op margin | Target steps | Mean target top-1 p | Mean target second best | Mean target margin |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Chrome | 73 | 0.827 | 0.108 | 0.7192 | 46 | 0.954 | 0.040 | 0.9063 |
| Ego | 59 | 0.827 | 0.108 | 0.7188 | 40 | 0.965 | 0.037 | 0.9215 |

The second-best columns average over the steps that actually had a second
candidate: every scored operation step does, and 40 of Chrome's 46 scored target
steps do (34 of Ego's 40) — the other six are single-candidate states where the
runner records no margin at all, and they are excluded rather than counted as a
margin of zero.

The two arms' operation margins are indistinguishable (0.7192 vs 0.7188, and the
same mean top-1 probability to four decimals); Ego's target margin is the wider of
the two (0.9215 vs 0.9063). An earlier version of this table averaged over *every*
recorded step instead of the scored population, which mixed wrong answers into the
top-1 mean and produced a margin gap that the scorer's own `score.json` does not
show; the step counts and margins above are read from that file.

## C.5 Goal-state judgment

| Task | Arm | DONE decisions | Verified at the decision | Premature DONE | BLOCKED decisions |
|---|---|---:|---:|---:|---:|
| example | Chrome | 3 | 3 | 0 | 0 |
| example | Ego | 3 | 3 | 0 | 0 |
| wikipedia | Chrome | 3 | 3 | 0 | 0 |
| wikipedia | Ego | 3 | 3 | 0 | 0 |
| flights | Chrome | 3 | 3 | 0 | 0 |
| flights | Ego | 0 | 0 | 0 | 0 |
| complex | Chrome | 5 | 5 | 0 | 0 |
| complex | Ego | 5 | 5 | 0 | 0 |

Every `DONE` in the A/B was independently verified at the moment it was chosen:
**no premature completion in either arm**, in either the Chrome or the Ego arm, on
any task. The Ego flights runs never claimed `DONE`; they were stopped by the
no-progress fuse, which is why their `BLOCKED` decision count is zero — the fuse
stopped the loop, not the model.

## C.6 Every failed run, and why

| Run | Status | Validator | Recorded cause |
|---|---|---|---|
| `flights-ego-1` | blocked | origin ✓, no one-way, no destination, no date | origin suggestion rows carry no ref |
| `flights-ego-2` | blocked | origin ✓, no one-way, no destination, no date | same |
| `flights-ego-3` | blocked | one-way ✓, origin ✓, no destination, no date | same |

All three have the same signature, and it is an Ego capability gap rather than a
decision error. After the origin is typed, Ego's snapshot lists the suggestions
as

```
listboxoption "Zürich, Switzerland"
listboxoption "Zurich Airport (ZRH)"
listboxoption "Zürich HB"
```

with **no ref**, while the same rows in Chrome's snapshot are addressable
actions (Chrome clicks *"Zürich, Switzerland"* and continues). Without a ref the
adapter cannot offer them, so they are counted as `unmapped_targets` (5 per
observation, recorded in the run's events) and dropped. The policy is left with
the origin field itself, clicks *"Open Where else?"* repeatedly, and the
no-progress fuse stops the run. The validator confirms the honest outcome: the
one-way setting and the origin are real partial progress, the destination and
date are missing, so the run does not pass.

Two honesty notes the review asked for. First, the label list above is a
**live read-only probe**, not something the recording stores: `events.jsonl` keeps
`unmapped_targets: 5` per observation, and the labels exist only in the adapter's
memory during the run. The count and the page text corroborate it, but the
three-line quote itself is not in `recordings/`. Second, the upstream model
provider killed one Ego run in a superseded pass, and the pass is named correctly
here after a review correction: `recordings/final/ab-old-truth/complex-ego-4`
ended `status=error` after four successful steps because TypeSafe rejected an
upstream response (`Invalid TypeSafe response; no action executed.`). The same
upstream rejection also ended a **Chrome** run —
`ab-interleaved-before-wait-fix/flights-browser_harness-3` — so it is a provider
failure that hits either arm, not an Ego-specific one, and it counts as a
validator failure in whichever pass it lands.

The two `complex` runs that failed in earlier passes are gone: the fixture's
distractor trap ("Review draft") caught Ego twice before the settle fix, and the
final five Ego runs all take the canonical eight-step path (the Chrome runs take
nine, because of the fixture distractor described in C.3).

## C.7 Isolation and cleanup

Scored failures only count what this benchmark did. A changed *user* tab set is
reported, not scored: Chrome target ids also change when the browser discards a
tab, and a person using the same profile during a run is not this benchmark's
leak. Raw observations are shown either way.

| Arm | Runs | Own tab/space | Leak recorded | User tab set changed | Orphan process | Score |
|---|---:|---|---|---:|---|---:|
| Chrome | 14 | 14 created, 14 closed | 0 | 0 | — | 10/10 |
| Ego | 14 | 14 fresh TaskSpaces, 1 runtime each | 0 | — | 0 surviving, 0 alive after close | 10/10 |

Two limits of this table, stated because the review found them: the Ego
"fresh TaskSpace" check compares the name the runner set with the name derived
from the run id, so it proves the runner *asked* for a fresh space rather than
that the runtime honoured it (the runtime's own name is not echoed back); and the
Chrome check observes the target list before and after, so it detects a tab that
was lost, not one that appeared. Both readings — `post_targets_immediate` and the
settled `post_targets` — are kept in each run's `tab_evidence.json`. In this pass
the question is moot for the appeared-tab direction: the Chrome arm ran against a
dedicated automation instance with no user tabs in it at all, and every run
reports the same four-id target list before and after (`pre_targets` and
`post_targets`).

## C.8 Deviations and limits

- **The Chrome arm is a dedicated instance, not the user's profile.** See C.1.
  Its wall times are therefore not comparable with the earlier archived passes,
  and the whole A/B below is one internally consistent pass.
- **Ego pays for its settle.** After a mutation Ego waits for the action set to
  be quiet (1.2 s) and, for a click that navigated, for the observation to
  reflect the navigation (up to 2.5 s). That is a correctness fix for the
  transitional-frame bug in Part A, and it costs wall time; Chrome needs no
  equivalent wait because it never exhibited the bug. Some of the performance gap
  in C.9 is that price, and it is the deliberate trade.
- **Ego's Task C gap is not isolated to one cause.** Ego's runtime opens its own
  viewport and Chrome's automation window is a different size; this benchmark does
  not isolate whether the missing refs on the suggestion rows depend on that
  geometry, and it does not publish viewport numbers it did not record. It reports
  the observed difference and its consequence.
- **Sample size.** Three runs per arm for tasks A–C and five for task D. Per-task
  pass rates are noisy at that size; the operation and target accuracies, which
  aggregate over every recorded step, are the more stable comparison.
- **Transient model-provider failures are visible, not smoothed.** Across the
  archived passes, three runs died on upstream errors and are kept in their pass
  directories. Two are on the final hash, both Ego: `Invalid TypeSafe response` in
  `ab-old-truth/complex-ego-4`, `Model connection failed` in
  `reliability/wikipedia-ego-4`. The third is Chrome, on the previous hash
  `8cea501c…`: the same `Invalid TypeSafe response` in
  `ab-interleaved-before-wait-fix/flights-browser_harness-3`. None of them is
  smoothed out of the pass it belongs to.
- **`success_panel_seen` is recorded but unused.** It is computed from the full
  snapshot text the runner captured (capped at 6000 characters upstream) and is 0
  for both arms; it is published in `score.json` for completeness and is not part
  of any subscore. The fixture's independent DOM validator is what decides
  success.
- **The implementation hash covers the policy code, not the harness.** It is taken
  over `jev_ultrafast/**` and `snapshot.js`, so two passes can share a hash while
  the benchmark scripts or ground truth differ — `probes-old-truth` is exactly
  that case. Where the distinction matters, this report names the pass and the
  script state rather than the hash alone.
- **Two files outside the deliverable changed and are disclosed here.** `.gitignore`
  now keeps this round's recordings on disk (see the top of this report), and
  `scripts/live_runs.py` records a process baseline before a run so that
  `ego-browser` processes already running on the machine are not attributed to
  this run as orphans. Neither flatters the result: all 14 Ego runs in the final
  A/B record an empty baseline, an empty attributed set, no surviving process, and
  `pid_alive_after_close: false`.
- **Measurement rules fixed while scoring.** A step whose state the task does not
  pin down previously counted as a wrong target, and the example/Wikipedia target
  labels were wrong (C.3). Both are fixed in the runner and pinned by tests, and
  the final pass was recorded after the fix rather than patched in the report.

## C.9 Final score

| 指标 | Chrome / Browser Harness | Ego Browser |
|---|---:|---:|
| Validator pass rate | 1.000 | 0.786 |
| Operation top-1 accuracy | 0.877 | 0.966 |
| Target top-1 accuracy | 0.913 | 0.950 |
| Correct operation probability | 0.756 | 0.821 |
| Correct target probability | 0.834 | 0.829 |
| Median wall time (ms, all tasks) | 5111 | 16248 |
| Jev calls | 116 | 69 |
| Action success rate | 0.817 | 1.000 |
| Stale count | 27 | 0 |
| Backend operation time (ms) | 11182 | 165119 |
| Isolation / cleanup | 10/10 | 10/10 |
| Reliability score /35 | 35.00 | 27.50 |
| Operation score /15 | 13.15 | 14.49 |
| Target score /15 | 13.70 | 14.25 |
| Probability score /10 | 7.95 | 8.25 |
| Performance score /15 | 15.00 | 6.20 |
| Isolation score /10 | 10 | 10 |
| **TOTAL /100** | **94.8** | **80.7** |

最终成绩对比

Chrome / Browser Harness:
- Reliability: 35.0/35 (validator 14/14 runs)
- Jev operation judgment: 13.2/15 (top-1 0.877 over 73 steps)
- Jev target selection: 13.7/15 (top-1 0.913 over 46 steps)
- Probability/confidence: 7.9/10 (correct operation 0.756, correct target 0.834)
- Performance: 15.0/15
- Isolation/cleanup: 10/10

Ego Browser:
- Reliability: 27.5/35 (validator 11/14 runs)
- Jev operation judgment: 14.5/15 (top-1 0.966 over 59 steps)
- Jev target selection: 14.2/15 (top-1 0.950 over 40 steps)
- Probability/confidence: 8.2/10 (correct operation 0.821, correct target 0.829)
- Performance: 6.2/15
- Isolation/cleanup: 10/10

本轮原始结果：
- Chrome / Browser Harness: 14/14 runs passed
- Ego Browser: 11/14 runs passed
- complex median wall: Chrome / Browser Harness 5237 ms; Ego Browser 22201 ms
- example median wall: Chrome / Browser Harness 1086 ms; Ego Browser 3962 ms
- flights median wall: Chrome / Browser Harness 12416 ms; Ego Browser 16739 ms
- wikipedia median wall: Chrome / Browser Harness 4985 ms; Ego Browser 12395 ms

最终成绩对比

Chrome / Browser Harness: 94.8 / 100
Ego Browser: 80.7 / 100
分差: 14.1

