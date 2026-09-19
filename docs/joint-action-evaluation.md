# Experimental joint action choice: results and limitations

**Draft research contribution, not a validated production upgrade.** This branch builds on PR #33 (`91d8fc5f0b9010823b81cffa01227f7450285564`). That PR remains separate and unchanged. While #33 is open, this PR's main-base diff includes its prerequisite changes; review the follow-up commit separately for this experiment.

## Implementation

`Agent` calls `model.choose_joint` instead of independently selecting operation and target. One TypeSafe choice question offers complete observed action choices, including observed SELECT options, plus terminal/wait choices. The original full goal and policy rules remain. Responses must select an offered action. Text entry retains the existing strictly validated, bounded-retry text helper.

At more than 64 offered choices (including terminals), the original `model.choose` is called unchanged, once. Direct callers of `model.choose` continue to use the original behavior. Joint confidence and derived operation marginals are explicitly distinguished from independently predicted target confidence.

Separately, a freshness-rejected BLOCKED stops with `blocked_freshness_unconfirmed`: feasibility is unknown, not proven impossible. It performs no mutation or further model call and cannot reopen on a later tick. Action freshness and stale-DONE rejection remain unchanged. This avoids request loops on changing irrelevant text; it can also refuse a task that became feasible, so it is not a general reliability guarantee.

There are no extra speculative recovery requests, forced-progress actions, fixture-specific values, or model-selected JavaScript. **This branch changes the Agent's default policy for experimentation; it is not gated behind an opt-in flag.** Maintainers should not merge it as a default-policy improvement based on these results.

## Paired evaluation against PR #33

120 held-out matched pairs, 20 cases per family, 60 AB / 60 BA, isolated sequential browser trials. Both arms remeasured; prior campaign scores were not reused. Independent oracles check actual fields, exact submitted payload and exactly one submission. Six equally weighted synthetic families are not representative web traffic.

| Family | PR #33 | Joint choice |
|---|---:|---:|
| Standard | 20/20 | 19/20 |
| Delayed controls | 0/20 | 4/20 |
| Control replacement | 19/20 | 20/20 |
| Open shadow roots | 20/20 | 20/20 |
| Same-origin iframe | 20/20 | 20/20 |
| Malformed helper | 19/20 | 19/20 |
| Total | 98/120 | 102/120 |

Failures decreased from 22 to 18: **18.2% observed failure reduction**, not the targeted 50%. Failure ratio 0.8182; stratified paired percentile bootstrap 95% interval [0.5926, 1.05], 10,000 draws, seed 941827. Exact McNemar equality p=0.2891. These data do not establish a statistically significant improvement, let alone a 50% population effect.

96 pairs succeeded in both arms. Median paired task-wall ratio 0.9885 (~1.1% lower observed), p90 1.1558. Standard common-success median ratio 0.9428, but standard success nonregression failed (20→19). Conditional medians do not prove universal speed preservation.

All 240 held-out attempts completed. Zero synthetic wrong/duplicate submissions or false DONE. All 551 recorded candidate held-out decisions used joint choice; zero fallback, so this does not establish large-page fallback performance.

## Negative and public controls

Development negative controls: candidate 8/8, baseline 6/8. Two candidate outcomes were explicit safe uncertainty refusals, not improved impossibility detection. Fresh independent checks confirmed unchanged fields and zero input/change/autosave/submission events. Cases requiring DONE could not pass with BLOCKED.

Public controls: 9 pairs / 18 attempts, both arms 8/9. Synthetic hotel fixture 3/3 each, Wikipedia 3/3 each, Google Flights 2/3 each. Flights successes were on different repeats: one baseline-only pair and only one common success, failing the registered public gate. The candidate declared DONE once while the independent `results` check was false. No specific underlying cause is claimed.

**Evidence limitation:** the public harness retained durable check results and timing, but not fresh raw DOM observations or chooser metadata. Full independent public-oracle replay, public fallback counts and public payload-safety verification are unavailable. Synthetic raw observations were retained and independently replayed locally.

## Scientific record

Five approaches were explored: progress-first instructions, same-request dissent, selective recovery, bounded temporal observation, and joint choice. Temporal observation was rejected for lifecycle/freshness defects. Selective recovery's small-screen gain did not reproduce in full development validation. Joint choice was an explicitly registered adaptive development hypothesis; its final development result was 16/18 versus 15/18, with 8/8 negative controls. It was frozen before the once-used final holdout. No post-holdout source tuning occurred.

Versioned harness corrections preserved all manifest/fixture bytes: nested decision-budget integration, forwarding retry keywords through instrumentation, and distinguishing per-task failures from shared-budget campaign aborts. Earlier failed/incomplete runs were retained. The continuation rule was clarified after development, not misrepresented as wholly preregistered.

Recorded provider dispatch attempts: 774 development + 1,285 held-out + 193 public = 2,252. These are instrumented attempts, not independently audited billing.

## Verification and contribution scope

The focused contribution has 143 offline tests (106 inherited, 37 new). Existing 21 browser guard checks and 13 nested-DOM checks pass; Ruff, JavaScript syntax and package build are run before publishing. The full local experimental checkout passed 228 tests including benchmark machinery.

[`joint-action-evidence.json`](joint-action-evidence.json) contains compact summaries and the measured production hashes. It is not a self-contained reproduction bundle: local benchmark infrastructure, raw traces, credentials and machine configuration are excluded.

**Overall experimental acceptance: FAIL.** The 50% endpoint, standard success nonregression and public regression gates failed. This draft preserves a tested implementation and negative findings for maintainer review; it does not supersede PR #33's validated contribution or claim a safe production upgrade.
