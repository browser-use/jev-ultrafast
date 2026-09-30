// Independent final-page verification for the live flights example. Pure function; no browser.
import { assert } from "@std/assert";
import { verify } from "../examples/flights.ts";

function trip() {
  return {
    url: "https://www.google.com/travel/flights/search?tfs=example",
    text: "Track prices from Zürich to London departing 2026-09-20",
    actions: [
      ["Change ticket type. One way", "One way"],
      ["Where from?", "Zürich"],
      ["Where to?", "London"],
      ["Departure", "Sun, Sep 20"],
      ["Nonstop flight on Sunday, September 20. Select flight", ""],
    ].map(([label, value]) => ({ label, value })),
  };
}

for (const changed of ["Departure", "Where from?", "Where to?", "year"]) {
  Deno.test(`flight verification rejects the wrong trip: ${changed}`, () => {
    const actual = trip();
    // deno-lint-ignore no-explicit-any
    const check = (p: typeof actual) => verify(p as any).passed;
    assert(check(actual));
    if (changed === "year") actual.text = actual.text.replace("2026", "2027");
    else actual.actions.find((a) => a.label === changed)!.value = "wrong";
    assert(!check(actual));
  });
}
