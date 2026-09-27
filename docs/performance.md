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

Three further live runs, same goal and models, all seven independent checks passing:

| Run | Steps | Predictions paid | Wall | Input tokens | Cost | The `Done` click | Paid calls inside it |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| `after 5` (before the rule) | 17 | 107 | 79.9 s | 1,276,151 | $0.0536 | 46.9 s of churn | 86 |
| replay 1 | 35 | 49 | 46.1 s | 221,071 | $0.0093 | 1.8 s, one table | 1 |
| replay 2 | 20 | 26 | 50.1 s | 143,545 | $0.0060 | 11.2 s, one table | 2 |
| replay 3 | 30 | 41 | 62.3 s | 213,366 | $0.0090 | 12.0 s, one table | 2 |

Two of the three hit the same date dialog and held one byte-identical table for 11-12 s of churn; each paid two calls and 27,259 tokens for it, where the earlier run paid 86 calls and 1,176,738. The longest run of consecutive predictions against a single action table was 86 before the rule (next worst: 12) and is 2-3 after it, and those few remaining repeats are executed actions that left the page unchanged -- the no-op rule's business, not this one's.

Two things this does not claim. A byte-identical table is the same *question*, not a provably identical *answer*: in the original trace the 86th question flipped to a different node, and that flip is what got executed, which is why spending the cap hands the control to the exclusion list rather than ending the run. And the wandering this exposed is unchanged -- the runs above still click the ticket-type trigger 22, 7 and 17 times, inside the 1-17 range of the five verified runs before the rule, because each of those clicks does change the page and so spends none of the no-progress budget.

## Development attempts retained

Before freezing the candidate, the original runtime passed once in 9.302 s. Two accessibility-tree/semantic-guard candidates took 9.395 s and 10.157 s. The first direct-DOM candidate took 8.697 s but failed independent verification because name/value extraction was incomplete. Recursive labels and combobox values fixed that failure; subsequent verified diagnostics took 8.051, 8.631, 8.395, 8.385, and 7.741 s. A Mercury diagnostic passed in 7.559 s. These are changed-code development attempts, not the matched comparison above.

A six-call helper probe used the two real flight-field contexts with Gemini 2.5 Flash Lite, Gemini 3.1 Flash Lite, and Mercury 2.5. All returned the correct values in this tiny probe. Mercury then passed the live Flights, Wikipedia, and local filter checks. This does not establish general semantic accuracy. Earlier probes had rejected a model that swapped origin/destination and another that emitted commentary instead of valid JSON.

The previous 11.387-second recording and post-recording 12.898-second policy regression are described in the [original performance report](https://github.com/browser-use/jev-ultrafast/blob/68c077bf79caca4e817b8e8a5854b2efa0c81ff6/docs/performance.md). The older prepared-step prototype remains in [performance-prepared.md](performance-prepared.md). Raw attempts and original-timestamp frames remain in ignored local artifacts.

## Limits

This DOM reader supports common HTML and ARIA controls; it does not implement the full accessible-name algorithm or traverse shadow roots/frames. Scoped click guards deliberately allow unrelated visible updates. Canvas, uploads, new tabs, nested scrolling, and arbitrary keyboard widgets remain unsupported. A valid operation can still be wrong, and DONE is never independent evidence of success.
