"""Offline contracts for the read-only observation example. No paid APIs."""

from datetime import datetime

from examples.observe import PRICE, card_candidates, observation, on_approved_host, verify

ALLOWED = ["catalog.example.com"]


def finished_state(url="https://catalog.example.com/search?q=widget", history=("Search", "Go", "Scroll down")):
    return {
        "status": "done",
        "page": {"url": url},
        "history": [{"action": label} for label in history],
    }


def priced_card():
    card = {"href": "https://catalog.example.com/a", "text": "Classic widget, 120 x 240 mm\n25.50 USD per pack"}
    return card_candidates([card], PRICE)


def test_cards_need_a_visible_price_and_some_product_text():
    cards = [
        {"href": "https://catalog.example.com/a", "text": "Classic widget\n120 x 240 mm\n25.50 USD per pack\nIn stock"},
        {"href": "https://catalog.example.com/b", "text": "About us\nWe sell widgets and parts to everyone"},
        {"href": "https://catalog.example.com/c", "text": "$ 19.00"},
    ]
    found = card_candidates(cards, PRICE)
    assert [c["href"] for c in found] == ["https://catalog.example.com/a"]
    assert found[0]["price_raw"] == "25.50 USD"
    assert "25.50 USD" in found[0]["evidence_text"]


def test_every_observation_stays_a_pending_candidate():
    card = priced_card()[0]
    card["observed_at"] = "2026-01-02T03:04:05+00:00"
    record = observation(card, "done")
    assert record["source_url"] == "https://catalog.example.com/a"
    assert record["match_confidence"] == "candidate"
    assert record["review_status"] == "pending"
    assert record["price_raw"] == "25.50 USD"
    datetime.fromisoformat(record["observed_at"])


def test_a_read_only_run_on_an_approved_host_passes():
    assert verify(finished_state(), priced_card(), ALLOWED)["passed"]


def test_a_blocked_run_with_priced_cards_still_passes():
    state = finished_state()
    state["status"] = "blocked"
    assert verify(state, priced_card(), ALLOWED)["passed"]


def test_commerce_and_contact_label_variants_fail_the_run():
    for label in ("Sign up", "Create account", "Get a quote", "Book now", "Live chat"):
        state = finished_state(history=("Search", "Go", label))
        assert not verify(state, priced_card(), ALLOWED)["passed"], label


def test_subdomains_of_an_approved_host_are_accepted():
    assert on_approved_host("https://shop.catalog.example.com/x", ALLOWED)


def test_a_host_outside_the_allowlist_fails():
    state = finished_state(url="https://other.example.net/search")
    assert not verify(state, priced_card(), ALLOWED)["passed"]


def test_a_prohibited_control_fails_the_run():
    state = finished_state(history=("Search", "Go", "Add to cart"))
    assert not verify(state, priced_card(), ALLOWED)["passed"]


def test_no_priced_cards_fails_the_run():
    assert not verify(finished_state(), [], ALLOWED)["passed"]


def test_an_unfinished_agent_fails_the_run():
    state = finished_state()
    state["status"] = "ready"
    assert not verify(state, priced_card(), ALLOWED)["passed"]
