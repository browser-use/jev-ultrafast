"""Local-browser freshness/execution regressions. No model calls or external websites."""

import threading
import time
from urllib.parse import quote

from browser_harness.helpers import drain_events

from jev_ultrafast.browser import Browser, StalePage, wait_limits

HTML = """<!doctype html><title>Guard checks</title>
<style>body{margin:30px}button{width:180px;height:50px}#outside{position:absolute;top:3000px}</style>
<p id="context">Cart total: $10</p>
<button id="target" onclick="window.clicks=(window.clicks||0)+1">Continue</button>
<label>City<input id="field" value="Zurich"></label>
<label><input id="toggle" type="checkbox">Refundable</label>
<select aria-label="Category"><option>All</option><option>Design</option></select>
<p id="outside">Unrelated offscreen text</p>"""


def main():
    browser = Browser("data:text/html," + quote(HTML))
    passed = []
    try:
        page = browser.observe(screenshot=False)
        action = next(a for a in page["actions"] if a["label"] == "Continue")
        browser.evaluate("document.querySelector('#target').style.transform='translateX(200px)'")
        assert browser.fresh(page), "Movement should use fresh geometry, not another model call"
        browser.act(action, page)
        assert browser.evaluate("window.clicks") == 1
        passed.append("moving target clicked at its current location")

        browser.evaluate("document.querySelector('#outside').textContent='Updated outside the viewport'")
        assert browser.fresh(page)
        passed.append("unrelated offscreen text does not invalidate")

        mutations = {
            "visible context": "document.querySelector('#context').textContent='Cart total: $100'",
            "accessible label": "document.querySelector('#target').setAttribute('aria-label','Delete account')",
            "field property": "document.querySelector('#field').value='London'",
            "checkbox property": "document.querySelector('#toggle').checked=true",
            "disabled target": "document.querySelector('#target').disabled=true",
            "read-only field": "document.querySelector('#field').readOnly=true",
            "hidden target": "document.querySelector('#target').style.display='none'",
            "replaced node": "document.querySelector('#target').outerHTML=document.querySelector('#target').outerHTML",
            "dropdown option": "document.querySelector('select').options[1].text='Coastal'",
        }
        for label, expression in mutations.items():
            browser.evaluate("document.querySelector('#target').style.display='block'; "
                             "document.querySelector('#target').disabled=false")
            page = browser.observe(screenshot=False)
            browser.evaluate(expression)
            assert not browser.fresh(page), label
            passed.append(label + " invalidates")

        browser.evaluate("document.querySelector('#target').disabled=false; "
                         "document.querySelector('#target').style.display='block'")
        page = browser.observe(screenshot=False)
        action = next(a for a in page["actions"] if a["label"] == "Delete account")
        # A textless overlay does not alter the model's semantic state, but must block a click.
        browser.evaluate("const cover=document.createElement('div'); "
                         "cover.style.cssText='position:fixed;inset:0;z-index:9999;background:white'; "
                         "document.body.append(cover)")
        assert browser.fresh(page)
        try:
            browser.act(action, page)
        except (RuntimeError, StalePage):
            pass
        else:
            raise AssertionError("Covered target was clicked")
        assert browser.evaluate("window.clicks") == 1
        passed.append("overlay blocked before input")

        browser.evaluate("document.body.innerHTML=" + repr("""
          <form><p id="price">Total $10</p>
          <button type="button" id="buy">Buy</button>
          <label>Search <input id="query" role="combobox" aria-controls="suggestions"></label>
          <div role="listbox" id="suggestions"></div>
          <label><input id="check" type="checkbox">Enabled</label>
          <label><input id="radio" type="radio">Choice</label>
          <input id="readonly" aria-label="Read only" readonly>
          <input id="secret" type="password" value="never expose this">
          <button id="off" disabled>Disabled</button>
          <select id="category" aria-label="Category">
            <option>All</option><option>Design</option><option disabled>Unavailable</option>
          </select></form><aside id="unrelated">News</aside>
        """))
        page = browser.observe(screenshot=False)
        buy = next(a for a in page["actions"] if a["label"] == "Buy")
        browser.evaluate("document.querySelector('#unrelated').textContent='New unrelated news'")
        assert browser.fresh(page, buy)
        assert not browser.fresh(page)
        passed.append("click guard accepts unrelated visible updates; terminal guard rejects them")
        for label, expression in {
            "nearby price": "document.querySelector('#price').textContent='Total $100'",
            "form value": "document.querySelector('#query').value='changed'",
            "form toggle": "document.querySelector('#check').checked=true",
            "target replacement": "document.querySelector('#buy').outerHTML=document.querySelector('#buy').outerHTML",
        }.items():
            page = browser.observe(screenshot=False)
            buy = next(a for a in page["actions"] if a["label"] == "Buy")
            browser.evaluate(expression)
            assert not browser.fresh(page, buy), label
            passed.append(label + " invalidates action-specific guard")

        page = browser.observe(screenshot=False)
        actions = page["actions"]
        for role in ("checkbox", "radio"):
            assert {a["kind"] for a in actions if a.get("role") == role} == {"click"}
        assert {a["kind"] for a in actions if a["label"] == "Read only"} == {"click"}
        assert not any(a["label"] == "Disabled" or a.get("value") == "never expose this" for a in actions)
        assert [a["value"] for a in actions if a["kind"] == "select"] == ["Design"]
        passed.append("native controls expose only supported operations and safe values")

        select = next(a for a in actions if a["kind"] == "select")
        browser.act(select, page)
        assert browser.evaluate("document.querySelector('#category').value") == "Design"
        passed.append("native dropdown selects an observed option")

        browser.evaluate("document.querySelector('#query').addEventListener('input',()=>setTimeout(()=>{"
                         "document.querySelector('#suggestions').innerHTML='<div role=option>Generated</div>'"
                         "},60))")
        page = browser.observe(screenshot=False)
        field = next(a for a in page["actions"] if a["kind"] == "fill")
        browser.act(field, page, text="Generated")
        page = browser.observe(screenshot=False)
        value = browser.evaluate("document.querySelector('#query').value")
        assert value == "Generated", repr(value)
        assert any(a.get("role") == "option" for a in page["actions"])
        passed.append("real text input waits for asynchronous combobox suggestions")
        # A real request, held by the browser for 1.2 s (no server needed): WAIT must outlast it, then see the
        # change that follows it. Fetch interception pauses the request inside Chrome's network stack.
        browser.call("Fetch.enable", patterns=[{"urlPattern": "*jev-guard.invalid*", "requestStage": "Request"}])
        drain_events()
        page = browser.observe(screenshot=False)
        # The page changes at once ("Loading...") while the request is still in flight, as real pages do: only the
        # in-flight counter keeps WAIT from returning on that first change.
        browser.evaluate("const p=document.createElement('p'); p.textContent='Loading...'; document.body.prepend(p);"
                         "fetch('https://jev-guard.invalid/slow', {mode: 'no-cors'})"
                         ".finally(() => { p.textContent='Loaded' })")

        def release():
            request, deadline = None, time.monotonic() + 5
            while request is None and time.monotonic() < deadline:
                request = next((e["params"]["requestId"] for e in drain_events()
                                if e["method"] == "Fetch.requestPaused"), None)
                time.sleep(0.02)
            time.sleep(1.2)
            browser.call("Fetch.fulfillRequest", requestId=request, responseCode=200, body="b2s=")

        releaser = threading.Thread(target=release)
        releaser.start()
        started = time.monotonic()
        browser.wait_for_change(page)
        elapsed = time.monotonic() - started
        releaser.join()
        browser.call("Fetch.disable")
        quiet_timeout, _ = wait_limits()
        # The upper bound matters: with a dead counter WAIT would still return, but only at the quiet timeout.
        assert 1.2 <= elapsed < min(quiet_timeout, 3), elapsed
        assert "Loaded" in browser.observe(screenshot=False)["text"]
        passed.append("WAIT outlasts a real in-flight request and returns as soon as the page settles")
        browser.evaluate("try { new XMLHttpRequest().send() } catch (_) {}")
        assert browser.evaluate("window.__jevInflight") == 0
        passed.append("a request that throws before dispatch does not leave WAIT believing the page is busy")
        # A page quiet for a second that then changes: the change itself must settle before WAIT returns (quiet
        # time measured from the start of the WAIT would let it return on the very first read of the change).
        page = browser.observe(screenshot=False)
        browser.evaluate("setTimeout(() => { document.body.prepend('Late change') }, 1000)")
        started = time.monotonic()
        browser.wait_for_change(page)
        elapsed = time.monotonic() - started
        assert elapsed >= 1.0 + 0.3 and "Late change" in browser.observe(screenshot=False)["text"], elapsed
        passed.append("a late change still settles before WAIT returns")
        browser.evaluate("const f=document.createElement('iframe'); f.id='jevframe'; f.srcdoc='<p>frame</p>';"
                         "document.body.append(f)")
        time.sleep(0.5)
        browser.evaluate("document.getElementById('jevframe').contentWindow.__jevInflight=1")
        assert browser.busy()
        browser.evaluate("document.getElementById('jevframe').contentWindow.__jevInflight=0")
        assert not browser.busy()
        passed.append("requests in a same-origin frame count as busy")
        browser.evaluate("dispatchEvent(new Event('beforeunload'))")
        assert browser.busy()
        passed.append("a navigation under way counts as busy")
        browser.call("Page.navigate", url="about:blank")
        assert not browser.fresh(page, field)
        passed.append("navigation invalidates the old document")
    finally:
        browser.close()
    print("\n".join(passed))
    print(f"PASS: {len(passed)} browser guard checks; no model calls")


if __name__ == "__main__":
    main()
