# Read-only observation runs

The DOM reader sends visible text only: offscreen content never reaches the model. That is the right
design for a chooser, and it means catalogue results that render below the initial viewport are
invisible to the agent. A run that has not seen a price correctly answers `BLOCKED` instead of
inventing one.

`examples/observe.py` composes the agent with two code-owned steps so those results still become
auditable evidence:

```text
Agent search and route selection        model decisions over visible controls
        ↓
Controlled settle and scroll            code-owned scroll, one observed page per step
        ↓
Rendered-card extraction                read-only DOM read of visible anchors
        ↓
Raw observations                        price evidence with review_status "pending"
        ↓
Human review                            people accept, correct, or reject
```

## Why the added steps are code-owned

- The scroll is the same `{kind: "scroll"}` action the snapshot already offers, dispatched through
  the normal executor after a freshness check. The model never emits input events.
- The extraction is one `Runtime.evaluate` read. It returns visible anchors and their nearest
  listing-like surrounding text; it never mutates the page.
- The agent loop, questions, and action space are untouched. No site-specific plan or prepared
  strings enter the policy.

## The observation contract

```json
{
  "source_url": "https://catalog.example.com/items/123",
  "observed_at": "2026-01-02T03:04:05+00:00",
  "description_raw": "visible card text, trimmed",
  "price_raw": "25.50 USD",
  "evidence_text": "the visible line the price was read from",
  "agent_status": "done",
  "match_confidence": "candidate",
  "review_status": "pending"
}
```

Every record is a `candidate` marked `review_status: "pending"`. A visible price is evidence about a
page at a timestamp — not a verified fact about the product, its availability, or anything a person
should act on. Accepting a record is a human decision that happens outside the run.

## Guardrails

1. **One bounded goal.** Name the item, the fields to record, and the refusal rule.
   Good: `Search for "classic widget 120 x 240" and stop when result listings are visible. Read
   only: no sign-in, cart, enquiry, or contact actions.`
   Bad: `Find the best price for a widget.` The second goal invites incomparable items and unstated
   commercial terms, and it asks for a conclusion instead of an observation.
2. **Approved hosts.** Keep the allowlist in version control. The example refuses a start URL
   outside it, and verification re-checks the final URL, including subdomains.
3. **Prohibited controls.** A deny pattern runs over every executed action label — sign-in and
   sign-up, account creation, cart, checkout, order, book-now, enquiry and quote requests,
   contact, chat, subscribe. One hit fails the run. The list denies common commerce and contact
   labels, not all of them; an unrecognized label is a reviewer question.
4. **Deterministic verification.** Independent of the model's `DONE`: the agent finished, the final
   host is approved, at least one priced card was captured, and the action history stays read-only.
5. **Human review.** Nothing is auto-promoted. A run that produces zero candidates is a result to
   inspect, not a failure to hide.

`agent_finished` accepts both `done` and `blocked`. With extraction as a separate step, a `BLOCKED`
agent that still surfaced the right search results can be a usable run, and the terminal status is
recorded on every observation.

## Run it

```bash
uv run --env-file .env python examples/observe.py \
  --url https://catalog.example.com/ \
  --allow catalog.example.com \
  --goal 'Search for "classic widget 120 x 240" and stop when result listings are visible.
           Read only: no sign-in, cart, enquiry, or contact actions.'
```

Writes `artifacts/observations/latest/observations.json` and `state.json`, and exits non-zero when
the checks fail. `--price` replaces the default price regex, `--max-scrolls` bounds the walk, and
`--settle` tunes the render wait after each scroll.

## Limits

- Card detection is a heuristic — an anchor, its nearest listing-like container, and a price match.
  Expect misses and false positives; review exists for exactly that reason.
- One scroll container, like the MVP itself. Nested scrolling and lazy loaders that never render
  stay out of scope; `--max-scrolls` caps the walk either way.
- A `--price` match is not price verification. Taxes, units, packs, and commercial terms are
  reviewer questions, not regex questions.
- Like `DONE`, a `BLOCKED` answer is something to inspect, not a fact.
