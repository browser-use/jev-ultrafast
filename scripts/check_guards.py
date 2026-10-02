"""Local-browser freshness/execution regressions. No model calls or external websites."""

import json
import time
from urllib.parse import quote

from jev_ultrafast.browser import Browser, StalePage

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
        browser.call("Page.navigate", url="about:blank")
        assert not browser.fresh(page, field)
        passed.append("navigation invalidates the old document")

        def page_scroll_case(html, scroll_to=0, viewport=None):
            if viewport:
                browser.call("Emulation.setDeviceMetricsOverride", width=viewport[0], height=viewport[1],
                             deviceScaleFactor=1, mobile=False)
            browser.evaluate("document.documentElement.removeAttribute('style');document.body.removeAttribute('style');"
                             "document.body.innerHTML=" + json.dumps(html) + ";"
                             "for (const s of document.querySelectorAll('script[data-run]')) eval(s.textContent);"
                             f"scrollTo(0,{scroll_to})")
            time.sleep(0.1)
            page = browser.observe(screenshot=False)
            offered = {a["id"]: a for a in page["actions"] if a["id"] in ("scroll_down", "scroll_up")}

            def moves(direction):
                point = offered.get(direction) or {"x": min(550, page["w"] - 1), "y": min(650, page["h"] - 1)}
                assert 0 <= point["x"] < page["w"] and 0 <= point["y"] < page["h"], point
                before = browser.evaluate("scrollY")
                browser.call("Input.dispatchMouseEvent", type="mouseWheel", x=point["x"], y=point["y"],
                             deltaX=0, deltaY=560 if direction == "scroll_down" else -560)
                time.sleep(0.4)
                moved = browser.evaluate("scrollY") != before
                browser.evaluate(f"scrollTo(0,{scroll_to})")
                for e in ("#inner",):
                    browser.evaluate(f"document.querySelector('{e}')?.dispatchEvent(new Event('reset'))")
                return moved

            result = {d: (d in offered, moves(d)) for d in ("scroll_down", "scroll_up")}
            if viewport:
                browser.call("Emulation.setDeviceMetricsOverride", width=1120, height=780, deviceScaleFactor=1,
                             mobile=False)
            return result

        tall = '<p>Top</p><div style="height:3000px"></div><p>Bottom</p>'
        inner = ('<div id="inner" style="position:fixed;left:0;top:0;width:100%;height:100%;overflow-y:auto;'
                 'overscroll-behavior-y:{ob}"><div style="height:2000px">Inner</div></div>'
                 '<script data-run>(()=>{{const i=document.querySelector("#inner");i.scrollTop={top};'
                 'i.addEventListener("reset",()=>{{i.scrollTop={top}}})}})()</script>')
        cases = {
            "tall page": (tall, 0, None),
            "body overflow hidden": (tall + "<style>body{overflow:hidden}</style>", 0, None),
            "aria-modal dialog over a scrollable page": (
                tall + '<div role="dialog" aria-modal="true" style="position:fixed;inset:0;background:#fff">'
                + 'Dialog <button>Close</button></div>', 0, None),
            "inner region that can still scroll down": (tall + inner.format(ob="auto", top=0), 0, None),
            "inner region at its bottom, overscroll auto": (tall + inner.format(ob="auto", top=5000), 0, None),
            "inner region at its bottom, overscroll contain": (tall + inner.format(ob="contain", top=5000), 0, None),
            "page midway, inner region at its top, overscroll auto": (tall + inner.format(ob="auto", top=0), 800, None),
            "page midway, inner region that can still scroll up": (tall + inner.format(ob="auto", top=300), 800, None),
            "viewport smaller than the wheel point": (tall, 0, (480, 360)),
        }
        for label, (html, scroll_to, viewport) in cases.items():
            result = page_scroll_case(html, scroll_to, viewport)
            for direction, (offered, moved) in result.items():
                assert offered == moved, (label, direction, offered, moved)
            passed.append(f"page scroll offered exactly when it moves the document: {label} "
                          f"(down {'yes' if result['scroll_down'][0] else 'no'}, "
                          f"up {'yes' if result['scroll_up'][0] else 'no'})")
    finally:
        browser.close()
    print("\n".join(passed))
    print(f"PASS: {len(passed)} browser guard checks; no model calls")


if __name__ == "__main__":
    main()
