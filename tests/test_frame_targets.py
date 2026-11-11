"""Frame target selection.

A cross-origin iframe is a separate CDP target, so a snapshot of the top
document cannot see inside it. These cover the selection rules that decide when
to observe a child frame instead of the top document.
"""

from jev_ultrafast.browser import pick_frame_target

PAGE = "page-1"


def frame(target_id, url, parent=PAGE, kind="iframe"):
    return {"targetId": target_id, "url": url, "type": kind, "openerId": parent}


def test_single_child_frame_is_chosen():
    targets = [
        {"targetId": PAGE, "url": "https://app.example.invalid/", "type": "page"},
        frame("f1", "https://embedded.example.invalid/workflows"),
    ]
    assert pick_frame_target(targets, PAGE)["targetId"] == "f1"


def test_no_frames_returns_none():
    targets = [{"targetId": PAGE, "url": "https://app.example.invalid/", "type": "page"}]
    assert pick_frame_target(targets, PAGE) is None


def test_ambiguous_frames_are_refused_rather_than_guessed():
    """Two candidate frames must not be resolved by picking the first."""
    targets = [
        {"targetId": PAGE, "url": "https://app.example.invalid/", "type": "page"},
        frame("f1", "https://embedded.example.invalid/workflows"),
        frame("f2", "https://ads.example.invalid/pixel"),
    ]
    assert pick_frame_target(targets, PAGE) is None


def test_url_match_disambiguates():
    targets = [
        {"targetId": PAGE, "url": "https://app.example.invalid/", "type": "page"},
        frame("f1", "https://embedded.example.invalid/workflows"),
        frame("f2", "https://ads.example.invalid/pixel"),
    ]
    chosen = pick_frame_target(targets, PAGE, "embedded.example.invalid")
    assert chosen["targetId"] == "f1"


def test_url_match_with_no_hit_returns_none():
    targets = [
        {"targetId": PAGE, "url": "https://app.example.invalid/", "type": "page"},
        frame("f1", "https://embedded.example.invalid/workflows"),
    ]
    assert pick_frame_target(targets, PAGE, "nope.example.invalid") is None


def test_frames_from_another_page_are_ignored():
    targets = [
        {"targetId": PAGE, "url": "https://app.example.invalid/", "type": "page"},
        frame("f1", "https://embedded.example.invalid/a", parent="other-page"),
    ]
    assert pick_frame_target(targets, PAGE) is None


def test_non_iframe_targets_are_ignored():
    targets = [
        {"targetId": PAGE, "url": "https://app.example.invalid/", "type": "page"},
        frame("sw", "https://app.example.invalid/sw.js", kind="service_worker"),
        frame("other", "https://other.example.invalid/", kind="page"),
    ]
    assert pick_frame_target(targets, PAGE) is None


def test_targets_without_an_id_are_skipped():
    targets = [
        {"targetId": PAGE, "url": "https://app.example.invalid/", "type": "page"},
        {"url": "https://embedded.example.invalid/", "type": "iframe", "openerId": PAGE},
    ]
    assert pick_frame_target(targets, PAGE) is None


def test_match_is_applied_even_when_only_one_frame_exists():
    """A named frame must still match; otherwise stay on the top document."""
    targets = [
        {"targetId": PAGE, "url": "https://app.example.invalid/", "type": "page"},
        frame("f1", "https://embedded.example.invalid/workflows"),
    ]
    assert pick_frame_target(targets, PAGE, "workflows")["targetId"] == "f1"
    assert pick_frame_target(targets, PAGE, "contacts") is None
