"""Native dropdown identity/freshness regressions in a local browser. No model calls."""

import json
from urllib.parse import quote

from jev_ultrafast.browser import Browser, StalePage, browser_operation


def check(browser):
    passed = []

    def reset(options):
        browser.evaluate(
            "document.body.innerHTML=" + json.dumps(
                '<form><select id="category" aria-label="Category">'
                '<option value="">All</option>' + options + '</select></form><aside id="news">News</aside>'
            )
        )
        browser.evaluate("window.events=[]; for (const type of ['input','change']) "
                         "document.querySelector('select').addEventListener(type,e=>window.events.push(e.type))")
        page = browser.observe(screenshot=False)
        action = next(a for a in page["actions"] if a["label"] == "Category → Second")
        return page, action

    for first in (
        '<option value="same">First</option>',
        '<option value="same" disabled>First</option>',
        '<optgroup label="Unavailable" disabled><option value="same">First</option></optgroup>',
    ):
        page, action = reset(first + '<option value="same">Second</option>')
        browser.act(action, page)
        assert browser.evaluate("document.querySelector('select').selectedIndex") == 2
        assert browser.evaluate("window.events") == ["input", "change"]
        passed.append("duplicate value selects the observed option with exactly one event pair: " + first)

    options = ('<option value="first">First</option>'
               '<optgroup label="Choices"><option value="second">Second</option></optgroup>')
    mutations = {
        "value reassigned to another option":
            "s.options[2].value='changed'; s.options[1].value='second'",
        "label attribute changed": "s.options[2].label='Different'",
        "option disabled": "s.options[2].disabled=true",
        "optgroup disabled": "s.querySelector('optgroup').disabled=true",
        "option replaced with an identical node": "s.options[2].replaceWith(s.options[2].cloneNode(true))",
        "option removed": "s.options[2].remove()",
        "options reordered": "s.prepend(s.options[2])",
        "multiple mode changed": "s.multiple=true",
    }
    for label, expression in mutations.items():
        page, action = reset(options)
        browser.evaluate("(()=>{const s=document.querySelector('select');" + expression + "})()")
        assert not browser.fresh(page, action), label
        try:
            browser.act(action, page)
        except StalePage:
            pass
        else:
            raise AssertionError(label + " executed a stale selection")
        assert browser.evaluate("window.events") == [], label
        passed.append(label + " rejects before mutation")

    # Also exercise the final executor check without the earlier Browser.fresh check.
    for label, expression in {
        "detached option": "s.options[2].remove()",
        "changed value": "s.options[2].value='changed'",
        "disabled option": "s.options[2].disabled=true",
        "disabled optgroup": "s.querySelector('optgroup').disabled=true",
        "option moved to another select":
            "const other=document.createElement('select'); document.body.append(other); other.append(s.options[2])",
    }.items():
        page, action = reset(options)
        browser.evaluate("(()=>{const s=document.querySelector('select');" + expression + "})()")
        try:
            browser_operation({"operation": "act", "session": browser.session, "action": action})
        except RuntimeError:
            pass
        else:
            raise AssertionError(label + " escaped the final executor check")
        assert browser.evaluate("window.events") == [], label
        passed.append(label + " rejected by final executor check")

    page, action = reset(options)
    browser.evaluate("document.querySelector('#news').textContent='Updated unrelated news'")
    assert browser.fresh(page, action)
    browser.act(action, page)
    assert browser.evaluate("document.querySelector('select').selectedIndex") == 2
    assert browser.evaluate("window.events") == ["input", "change"]
    passed.append("unrelated updates still allow an unchanged observed option")
    return passed


def main():
    browser = Browser("data:text/html," + quote('<!doctype html><title>Select checks</title><body></body>'))
    try:
        passed = check(browser)
    finally:
        browser.close()
    print("\n".join(passed))
    print(f"PASS: {len(passed)} dropdown checks; no model calls")


if __name__ == "__main__":
    main()
