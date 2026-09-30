# A real flight search, at real speed

> [!NOTE]
> Historical record from the earlier Python prototype. Nothing on this page was re-measured for the TypeScript port; [measurement.json](measurement.json) and [flights-prepared-measurement.json](flights-prepared-measurement.json) are unchanged.

**12.884 seconds on Google Flights.** Zürich → London, one way, Sunday 20 September 2026, one adult, economy. Historical prepared recording (the primary demo has since been replaced) · [Machine-readable evidence](flights-prepared-measurement.json).

| Recorded run | Measurement |
| --- | ---: |
| Agent wall time | 12,884 ms |
| Jev requests | 17 |
| Browser actions | 11: 10 interactions + 1 wait |
| Time inside model requests | 3,050 ms |
| Text-generation calls | 0 |
| Captured screencast frames | 81 |
| Playback speed | 1× |

The clock starts with the first prediction, after the initial Google Flights homepage observation. It ends at the final DONE choice. It includes model calls, browser execution, observation, screenshots, asynchronous Google loading, and decisions discarded when the page changes. Browser launch, initial navigation, and setup are outside that clock. The video adds a 750 ms opening hold and a 2-second final hold; the timed run is uncut and unaccelerated.

The agent starts at the generic Google Flights homepage, with no route/date query prefilled by code. Jev selects every target. Five explicit ordered goals specify the trip; this is not autonomous trip planning. Zurich and London are copied from quoted goal values, so these timings do not include the optional text model. The optional GLM helper was separately exercised on the local fixture.

## What was checked

An independent predicate checked the resulting search page, one-way setting, origin Zürich, destination London, departure display, year from the page's price-tracking text, and actual flight-result labels for September 20. The captured page included:

- easyJet: ZRH → LGW, 16:45–17:35, nonstop, $216.
- British Airways / BA Cityflyer: ZRH → LCY, 20:25–21:00, nonstop, $265.
- SWISS: ZRH → LGW, 17:10–17:50, nonstop, $370.

Prices were observed during this run and can change. The search returned other results; this is not a claim that these are the cheapest available fares. No flight was selected or booked.

## Development attempts

| Attempt | Outcome | Agent time | Change |
| --- | --- | ---: | --- |
| 1 | Route/date/results checked | 14.118 s | Per-node DOM reads |
| 2 | Route/date/results checked | 14.162 s | Batched layout extraction |
| 3 | Failed | 1.585 s | Relaxed freshness let a menu-animation state reach the model; it chose BLOCKED |
| 4 | Route/date/results checked; recorded | 12.884 s | Restored strict freshness; explicit animation/wait guidance |

These are iterative development attempts with changed code, browser caches, and live site responses. They are not matched performance comparisons or a reliability estimate. The losing freshness change was removed. One successful public workflow is not a general browser benchmark.

## Earlier fixture baseline

The authored hotel fixture completed five actions in **1,086 / 1,242 / 1,311 ms** across three runs, with six Jev calls per run. That smaller state and local page omit real-site loading costs. Those numbers are retained in [measurement.json](measurement.json), and are not the Google Flights result. A separate fixture run using GLM for text took 4,650 ms.
