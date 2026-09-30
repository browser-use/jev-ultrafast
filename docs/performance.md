# Faster on the real web

The current video completes the Google Flights task in **7.073 seconds at 1×**. It starts with one natural-language goal and uses dynamic controls throughout. Jev selects operation + target in one request; Mercury generates the city strings when TYPE_TEXT is selected.

[Video](demo.mp4) · [Recording measurements](flights-measurement.json) · [Matched run measurements](full-speed-measurement.json)

## Matched runtime comparison

Six alternating runs, one task, one existing Chrome profile. Both arms used the same natural-language goal, independent result checker, 1120×780 viewport, TypeSafe `jev-1.13.0`, `inception/mercury-2.5`, disabled text reasoning, and action/request budgets. Initial navigation is excluded in both arms. Each run creates and closes its own tab. All six attempts are included; no provider or verification failures occurred.

| Pair | Original runtime | Optimized runtime | Verified |
| --- | ---: | ---: | --- |
| 1 | 11.214 s | 6.964 s | Both |
| 2 | 8.984 s | 7.913 s | Both |
| 3 | 9.450 s | 7.092 s | Both |
| **Median** | **9.450 s** | **7.092 s** | **3/3 each** |

The optimized runtime was faster in all three pairs. Median task time was **25.0% lower**, median TypeSafe requests fell **22 → 17**, and median browser protocol calls fell **1,092 → 101**. Three pairs are too few for a strong statistical claim (two-sided sign-test p = 0.25). This is a small controlled-input comparison, not a broad agent benchmark; Google, network responses, routing, and browser caches remain live.

The original arm is the frozen source from `68c077bf79caca4e817b8e8a5854b2efa0c81ff6`. Both arms use Mercury so the runtime comparison does not conflate a helper-model change with code changes. Per-run source hashes, model settings, token counts, helper costs, browser version, protocol counts, and verification results are in the measurement JSON.

## Where the time went

The original loop invalidated decisions on every DOM mutation, including animations. It also read the accessibility tree repeatedly and resolved hundreds of DOM nodes. The new snapshot reads common HTML/ARIA controls in one browser call. Click guards compare the selected target and nearby context, plus document/form state. Current geometry and hit-testing still run before input.

A brief event-based combobox wait lets suggestions arrive before asking Jev to choose from an incomplete popup. Text comes from an actual LLM: the recorded run generated **Zurich in 581 ms** and **London in 346 ms**. Native text replacement was also fixed to issue the browser's select-all command explicitly.

The recording contains **17 Jev requests**, **10 interactions plus one explicit WAIT**, and **two helper calls**. Median Jev latency was **178 ms**. Search executed at **5.217 s**; final verified completion was **7.073 s**. That final interval includes Google's results loading, state changes, and the completion decision. It stays in the video.

Timing begins at the first prediction after initial homepage observation and ends at the accepted DONE choice. It includes text generation, model requests, browser work, stale decisions, and loading. Browser setup, initial navigation, and fresh independent post-run verification are outside the clock. The video contains 186 continuous screencast frames plus the initial screenshot, uses original timestamps, has no opening hold, and adds a 0.5-second final hold. Only the top account/navigation strip is cropped.

The recording reports 90,558 TypeSafe input tokens and 6,325 output tokens across all requests. OpenRouter reported **$0.00006272** for the two text calls. That is the text-helper charge, not total task cost: the TypeSafe responses contain token counts without a billed dollar amount, and browser costs are excluded.

## Other checks

| Task | Time | Independent result |
| --- | ---: | --- |
| Wikipedia: open Gödel’s incompleteness theorems | 2.798 s | Exact article URL |
| Local hotel fixture: search Lisbon, Design, Free cancellation, open Casa Flora | 1.896 s | Property plus all three applied filters |

These are separate smoke checks, not matched speed comparisons. Local browser checks cover moved/replaced/hidden/disabled controls, field and checkbox properties, changed nearby context, overlay blocking, native-select execution, real text replacement, autocomplete arrival, and navigation. Offline tests cover the model contract, stale retries, interrupted mutations, helper validation, and independent trip verification.

After the timed runs, native-select interruption handling was tightened: uncertain mutation results stop instead of being treated as retryable stale reads. Flights does not exercise native SELECT. Its timing and recording hashes are retained unchanged; the final failure path is covered by offline fault injection and local browser checks.

## Overlay menus, 2026-09-27

Six runs of `examples/flights.py`, identical goal (`October 18, 2026`), models, and settings. The first is the state before the hidden-page re-read and the `aria-expanded` rule; the other five have both. Wall time is the loop's own clock (first prediction after the initial observation to the accepted `DONE`/`BLOCKED`); model time is the sum of recorded decision latencies; browser time is the remainder, so it includes page rendering and navigation. `Option row seen` counts that run's decision contexts whose elements contained the ticket menu's own `One way` row -- not the trigger's own label after it had been set to one way, which is a different string. `Input tokens` is the TypeSafe leg only: the text helper bills separately and its usage keys do not add.

| Run | Verified | Steps | Decisions | Option row seen | Wall | Model | Browser | `ESCAPE` chosen | Input tokens |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| before | no, `blocked` | 55 | 61 | 0 | 35.0 s | 20.5 s | 14.5 s | 0 | 306,711 |
| after 1 | 7/7 | 19 | 25 | 7 | 40.7 s | 9.0 s | 31.7 s | 1 | 157,509 |
| after 2 | 7/7 | 29 | 36 | 6 | 53.3 s | 11.9 s | 41.4 s | 2 | 211,478 |
| after 3 | 7/7 | 15 | 23 | 6 | 16.2 s | 8.3 s | 7.9 s | 1 | 106,089 |
| after 4 | 7/7 | 22 | 39 | 15 | 61.6 s | 22.3 s | 39.3 s | 1 | 294,533 |
| after 5 | 7/7 | 17 | 107 | 89 | 79.9 s | 40.7 s | 39.2 s | 1 | 1,276,151 |

That is $0.0045 to $0.0536 of the policy leg at $42 per billion input tokens. Five verified runs against one blocked run: this is a before/after of a correctness regression and its fix, not a speed comparison, and the wall numbers are not paired against the 7.073 s recording above. `after 5` is the cautionary one -- 17 executed steps bought with 107 predictions, 91 of which re-carried a fingerprint already predicted, so the page moved between predicting and acting and each rejection re-sent the whole table. Its cost is 12x the cheapest run's for the same seven verified checks.

A zero-model-call probe (`0` decisions, `0` text calls) polled the raw reader every 0.2 s for 6 s after each input, to size the re-read budget rather than guess it:

| Input | Hidden-page window after the input | Menu's own row in the table |
| --- | --- | --- |
| Click ticket type | 0.6 s - 2.2 s | 2.4 s |
| Type a city (both fields) | none | 0.2 s |
| Click Departure | none | 0.4 s |

So `OVERLAY_SETTLE = 3.0` covers the one control that hides its page, and autocomplete and date-picker steps cannot be charged to it. What the verified runs actually spend on the browser side ranges from 7.9 s to 41.4 s, and the bounded wait accounts for at most a few seconds of it: it can only fire on an input that leaves the page hidden, which on this site means the ticket-type menu. The rest is Google's own rendering plus discarded predictions -- the `after 5` row above is 90 rejected predictions re-sending the table, not 90 pieces of work. See the next section for what actually moved inside those 90 rejections.

## The same question twice, 2026-09-27

`after 5` paid for 107 predictions to execute 17 steps. 86 of those predictions answered one action table (`e9f08de6`), 85 of them for a click the page then refused before any input went out: 43.2 s of wall time, 1,176,738 input tokens, $0.0494, and the answer only changed on the 86th question -- which chose the *other* copy of the same button and dispatched.

A zero-model-call replay drove the same scripted route and then ran the loop's own timing at that exact point (observe, wait the measured 384 ms decision latency, then perform `Browser.act`'s freshness check) for 16 s. The only field that moved was `guard.scope.innerText`: the date dialog's own text went 1368 → 1523 → 1501 characters as Google re-priced it, 2 of 38 rounds rejected, both on that field. The action table held byte-identical the whole time, because the reader's page text is viewport-filtered and a scope's `innerText` is not. So the loop was not discovering a changed page; it was paying to re-read a page whose churn lives below the line where the model can see it.

The rule now: a rejection raised before input is re-offered against the re-observation, and the model is asked again only when the action table has actually changed. One table can refuse the same control `MAX_STALE_REPLAY = 96` times -- above the worst measured streak of 85 -- after which that control leaves the table and the runner-up is offered, so a page that never settles ends through the no-progress cap instead of the call budget.

Three further live runs, same goal and models, all seven independent checks passing, measured on the same rulers as the table above:

| Run | Steps | Predictions paid | Wall | Model | Browser | Input tokens | Cost | The `Done` click | Paid calls inside it |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| `after 5` (before the rule) | 17 | 107 | 79.9 s | 40.7 s | 39.2 s | 1,276,151 | $0.0536 | 46.9 s of churn | 86 |
| replay 1 | 35 | 49 | 44.6 s | 21.1 s | 23.4 s | 221,071 | $0.0093 | 1.8 s, one table | 1 |
| replay 2 | 20 | 26 | 49.0 s | 12.0 s | 37.0 s | 143,545 | $0.0060 | 11.2 s, one table | 2 |
| replay 3 | 30 | 41 | 60.7 s | 19.0 s | 41.7 s | 213,366 | $0.0090 | 12.0 s, one table | 2 |

Wall time did not improve and should not have: the page still churns, it just churns on the browser side of the split now, where it costs a re-read instead of a request.

Two of the three hit the same date dialog and held one byte-identical table for 11-12 s of churn; each paid two calls and 27,259 tokens for it, where the earlier run paid 86 calls and 1,176,738. The longest run of consecutive predictions against a single action table was 86 before the rule (next worst: 12) and is 2-3 after it, and those few remaining repeats are executed actions that left the page unchanged -- the no-op rule's business, not this one's.

Two things this does not claim. A byte-identical table is the same *question*, not a provably identical *answer*: in the original trace the 86th question flipped to a different node, and that flip is what got executed, which is why spending the cap hands the control to the exclusion list rather than ending the run. And the wandering this exposed is unchanged -- the runs above still click the ticket-type trigger 22, 7 and 17 times, inside the 1-17 range of the five verified runs before the rule, because each of those clicks does change the page and so spends none of the no-progress budget. The next section is what resolved that one.

## The menu that opens without hiding the page, 2026-09-28

Yesterday's two defences both miss Google Flights' ticket-type menu. The instruction not to re-click an expanded control cannot help a table that has no options in it, and the re-read only fires when the whole page has gone dark. The saved traces say how often that state was paid for -- across the 407 predictions the nine 2026-09-27 runs paid, grouped by whether the trigger reported itself expanded and whether the menu's own option row sat in the same table:

| Trigger | Option rows in that table | Paid predictions |
| --- | --- | ---: |
| closed | none | 314 |
| closed | present | 17 |
| expanded | none | 76 |
| expanded | present | **0** |

No prediction in any saved run ever saw the menu open *and* saw its options. A zero-model-call probe (two trials, the engine's own snapshot read every 0.2 s after one click) shows the order of events:

| After the click | Addressable actions | `aria_hidden_fallback` | The trigger | Option rows |
| --- | ---: | --- | --- | --- |
| 0.1 s | 32 | false | in the table, `expanded=true` | none |
| 2.4-2.5 s | 6 | false | gone from the table | Round trip / One way / Multi-city |

The page is never hidden on the way, so a gate that asks only "did the page go dark" never opens. The loop is then asked about the 32-row table, where the only thing that has changed is the control that just reported itself expanded -- so the move on offer is to click it again, and that click closes the menu. 76 of 407 predictions were spent that way, and all 129 executed trigger clicks in those runs were made against a table with no option row in it.

The rule now: the bounded post-input re-read also fires when the control that input opened reports itself expanded and the action table has not moved. A table that moves ends the wait, so an autocomplete that keeps its field expanded after its suggestions land pays no extra second, and the wait is still tied to an input -- a cold observation never waits.

Measured the same morning, same goal, same models, both arms live. The `b` arm is the widened gate edited back to yesterday's shipped condition for the duration of the two runs and restored byte-exact afterwards (`sha256 2542c4fa` before and after):

| Run | Steps | Paid | Wall | Model | Browser | Input tokens | Cost | Trigger clicks | Expanded-no-options predictions | `ESCAPE` |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `b1` yesterday's gate | 22 | 28 | 48.9 s | 9.8 s | 39.0 s | 152,956 | $0.0064 | 11 | 6 | 1 |
| `b2` yesterday's gate | 21 | 27 | 46.7 s | 10.0 s | 36.6 s | 148,261 | $0.0062 | 10 | 5 | 1 |
| `w1` with the new gate | 14 | 21 | 14.6 s | 7.4 s | 7.2 s | 92,686 | $0.0039 | 1 | 0 | 0 |
| `w2` | 14 | 23 | 14.6 s | 7.6 s | 7.0 s | 104,447 | $0.0044 | 1 | 0 | 0 |
| `w3` | 13 | 19 | 14.3 s | 6.4 s | 7.9 s | 85,744 | $0.0036 | 1 | 0 | 0 |
| `w4` | 12 | 21 | 15.1 s | 7.1 s | 8.0 s | 89,360 | $0.0038 | 1 | 0 | 0 |
| `w5` | 11 | 19 | 12.0 s | 6.2 s | 5.9 s | 82,642 | $0.0035 | 1 | 0 | 0 |

All seven independent route/date checks passed in every run of both arms, so the shorter run is not a shorter attempt. The saved seconds are browser-side: `b1` spends 39.0 s of its 48.9 s waiting on the page, and the new gate converts most of that into one bounded wait instead of ten round trips of click-render-reclick. `ESCAPE` stops being chosen because the state it was escaping from is no longer put in front of the model.

Two things this does not claim. The control arm is two runs: the direction reproduces in five of five and the gap is larger than anything in the spread, but a two-run arm is a two-run arm. And the menu's options still only reach the table *after* the trigger has left it -- the grid above reads 0 for "expanded and options together" in the new runs as well, because on this site those two never coexist. What changed is that the loop no longer pays to sit in that moment and click through it; it is not that the moment became readable.

## Saying what an observation cost, 2026-09-29

Every number above was reconstructed after the fact by a dedicated probe, because a saved page said nothing about how it was obtained. The reader now reports its own cost: `observe()` counts the re-reads it performed inside the bounded wait and attaches them as `overlay_reads`, the panel's element line renders that as `2 re-reads for the overlay` beside the existing hidden-page note, and every executed step's history row carries the count of the observation that followed it -- so the question "which step sat in a menu" is answered by the trace rather than by an experiment.

Two live episodes of `examples/flights.py`, same goal, same models, after the change:

| Run | Steps | Paid | Wall | Checks passed | Steps that paid a re-read | Max on one step |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `n1` | 11 | 19 | 14.1 s | 7 / 7 | not recorded (predates the row) | -- |
| `n2` | 11 | 19 | 14.1 s | 7 / 7 | 1 | 13 |

`n2` spent 13 re-reads on its first executed step, the ticket-type trigger: 13 passes at a 0.2 s floor is 2.6 s of the 3.0 s `OVERLAY_SETTLE` budget, against 0 on the other ten steps. That is the shape the field was added to show -- one step pays, and the seconds it pays are no longer mixed into browser time with no label.

What the field does not say: *why* the wait stopped. `n2`'s 13 reads are consistent with running out the deadline, but an exit on a table that kept moving while its opened control stayed expanded would count the same, and the row does not record which. Both exits have their own offline test.

Gates: 96 passed with the debug browser up (the 13 live-snapshot tests skip rather than pass without it, and a skipped gate is not a green one), `ruff` clean, `node --check` clean, `uv build` clean. The promise is pinned by 5 offline assertions on the counter and its boundaries, 1 on the payload reaching the panel, 1 on the history row, and a node harness (`scripts/panel_note_check.js`) that loads the shipped `static/app.js` into a fake DOM and compares 5 rendered strings. Since `c619861` that harness is no longer outside `pytest`: `tests/test_panel_note.py` runs it, and its second test mutates a throwaway copy of `app.js` in a temporary directory and requires the harness to go red -- so the comparison announces whether it can fail at all, without anyone patching the file another change may be sitting on. The mutation battery is a scratch harness outside this repository (`D:/ML_Projects/_scratch/jev_mutation_battery.py`); 25 of its entries stood a single round on 2026-09-29, red sets compared by exact set equality. That number is now stated against the dictionary rather than against the round, because the two disagreed: the version committed that afternoon had 33 entries and the harness holds 39 (`len(MUTATIONS)`) -- six added since, five for the launcher's shutdown and one that neuters this harness's own exit code.

The sweep no longer needs a quiet working tree. `JEV_BATTERY_REPO` points the harness at a `git clone` of the committed state and `BU_CDP_PORT` stops the live-snapshot tests from skipping (it does not by itself connect them to that browser -- see the 2026-10-01 note below, which is where that cost me 13 errors), so no mutation lands inside a directory another change is editing; in that mode `python -m pytest` replaces `uv run pytest` (a clone carries no environment of its own, and the shared one is not to be re-solved), the harness prints the `jev_ultrafast/__init__.py` the interpreter actually resolved and refuses to sweep if it is not the copy's. Measured that way at `0636587` on 2026-09-30 10:29 (`_scratch/jev_battery_headcopy_0930.txt`; private browser on 9224, the shared debug instance on 9223 never touched): **31 of 39 entries went red against exactly their expected sets**, the restore round read `81 passed` (that `81` and the `96` above differ by collection, not by code path: the copy's own run collects and passes 81, `--collect-only` in this working tree counts 96, and the gap is that same uncommitted `tests/test_agent.py` -- re-measured on 2026-10-01 in a fresh clone, where the same collection claim held but the number had moved to 84 because of the panel mutants below), and the slowest single round took 18.89 s (32 rounds, 154.5 s of suite time). The clone was disposable and has been deleted; the log is the retained artefact, kept in the scratch directory that holds the harness itself (`D:/ML_Projects/_scratch/`), not in this repository.

The other eight do not sweep at the committed state, and the reason is not concurrency: every anchor string appears **zero times in the history of the file it patches** (`git log -S`, per file, all eight), while each appears exactly once in a working tree another change is holding open -- 5 in `agent.py`, 1 each in `model.py`, `questions.py`, `demo.py` -- and all 11 tests those eight expect to turn red live in that same uncommitted `tests/test_agent.py` (68 test definitions at the committed state, 79 in that tree, none unique to the committed side). They are mutants of code that has never been in this repository's chain, not promises this repository left unverified; they become sweepable the day that work lands, and until then the harness reports `ANCHOR 0 occurrences` for them rather than a pass. The count that was wrong before is wrong no longer: `len(MUTATIONS)` = 39 = 31 swept-at-HEAD-plus-red + 8 not-in-the-chain.

One correction about the battery itself, found while adding those five: its `pytest` invocation passed `-rfE` and then `-rs`. `-r` takes a single value, so the later flag silently replaced the earlier one, no `FAILED`/`ERROR` names were ever printed, and the parsed red set came back empty for every mutant -- while the counts line still read `5 failed`. Ten rows of the 2026-09-28 round in `battery_expand3.log` were produced that way, so the verdicts in that round are void; the flag is now one `-rfEs`, and `run_suite` aborts when the counts line reports more failures than it parsed names for, on either channel. A sweep whose parser is blind must fail, not report that the rules came unpinned.

## Rendering the rest of the panel, 2026-10-01

Until today the node harness pinned one line. Everything else `render()` wrote -- the trace rows, the cost line -- was still judged only by whether a word survived somewhere in the file, which is exactly the gap `c619861` closed for the element counter and left standing beside it. The harness now renders three groups and prints a tally per group, so a red run names the promise that moved: `notes` (5 strings, the re-read and hidden-page counters as before), `trace` (3: the empty-state sentence; a row credited to a named text helper, including its HTML-escaped quotes and the two-branch percentage format; and a two-row join that must carry no separator between them), `cost` (2, counting actions, seconds, decision/text calls, input tokens and dollars). `TOTAL 10/10 rendered strings match` is the green line.

The cost group compares a **prefix**, not the whole line, and says so in the file: another change is holding an uncommitted edit that appends a verdict-audit count to that same string, so pinning the tail would make this gate collide with work it has no opinion about. The limit is stated rather than hidden -- a dropped or reordered field inside the prefix goes red, a longer tail does not.

Every group gets its own single-place mutant, run against a throwaway copy of `app.js` in a temporary directory, never against the file another change is editing. Four of them, each asserting not only that its own tally drops but that the other two stay full: `page.overlay_reads ?` -> `false ?` (notes 2/5), `<small>${escape(h.text_helper)}</small>` -> nothing (trace 1/3), the `page_changed` ternary collapsed to one answer (trace 2/3), `state.cost.input_tokens.toLocaleString()` -> `0` (cost 0/2). A mutant that reddened everything would prove only that the harness stops, not that it looks. Each test also re-reads its anchor with `count(anchor) == 1`, so an anchor that moves or duplicates fails with a name instead of passing quietly.

Both `app.js` revisions in this repository right now -- the committed one and the one carrying the in-flight audit tail -- render 10/10 through the same harness: measured in a clone at `6ec837f` and again against this working tree on 2026-10-01 07:3x. (That clone's contents are deleted; the empty directory at `D:/ML_Projects/_scratch/jev_gate_clone/` still refuses `rm -rf` because a handle from my own earlier commands holds it -- unlike the 2026-09-30 sweep clone above, which is gone entirely.) Gates with the change in place: **99 passed** in this working tree, **84 passed** in the HEAD clone holding the same two files, `ruff` clean, `node --check` clean on `app.js` and on the harness, `uv build` clean.

The venue cost a measurement to learn: `BU_CDP_PORT` only stops the live-snapshot tests from skipping. Pointed at a hand-launched headless Edge with nothing but that variable, the suite came back `71 passed, 13 errors`, each error reading `daemon default didn't come up` from browser-harness' own log, which was looking for a browser it recognises rather than the port I set. Passing `BU_CDP_WS` the `webSocketDebuggerUrl` from `/json/version` on top of it turned those 13 into passes. A private browser is therefore two variables, not one, and a run where the live group errors is not a run where the page reader broke.

That is not the whole of it, and the second half came from re-running the same wiring against a clone of the commit that carries this change. With **both** variables set and a cold daemon, the full suite returned `71 passed, 13 errors` again; one single-file run warmed the daemon (its log then read `connecting to ws://127.0.0.1:9224` / `listening on 127.0.0.1:11324`) and the identical full command after it returned **84 passed**. So the two variables are necessary but not sufficient on a cold start: a red or erroring live group gets re-run once before it is read as anything about the page reader, and the honest form of the recipe is "set both, expect the first round to error, re-run".

## Development attempts retained

Before freezing the candidate, the original runtime passed once in 9.302 s. Two accessibility-tree/semantic-guard candidates took 9.395 s and 10.157 s. The first direct-DOM candidate took 8.697 s but failed independent verification because name/value extraction was incomplete. Recursive labels and combobox values fixed that failure; subsequent verified diagnostics took 8.051, 8.631, 8.395, 8.385, and 7.741 s. A Mercury diagnostic passed in 7.559 s. These are changed-code development attempts, not the matched comparison above.

A six-call helper probe used the two real flight-field contexts with Gemini 2.5 Flash Lite, Gemini 3.1 Flash Lite, and Mercury 2.5. All returned the correct values in this tiny probe. Mercury then passed the live Flights, Wikipedia, and local filter checks. This does not establish general semantic accuracy. Earlier probes had rejected a model that swapped origin/destination and another that emitted commentary instead of valid JSON.

The previous 11.387-second recording and post-recording 12.898-second policy regression are described in the [original performance report](https://github.com/browser-use/jev-ultrafast/blob/68c077bf79caca4e817b8e8a5854b2efa0c81ff6/docs/performance.md). The older prepared-step prototype remains in [performance-prepared.md](performance-prepared.md). Raw attempts and original-timestamp frames remain in ignored local artifacts.

## Limits

This DOM reader supports common HTML and ARIA controls; it does not implement the full accessible-name algorithm or traverse shadow roots/frames. Scoped click guards deliberately allow unrelated visible updates. Canvas, uploads, new tabs, nested scrolling, and arbitrary keyboard widgets remain unsupported. A valid operation can still be wrong, and DONE is never independent evidence of success.
