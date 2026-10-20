"""Local-browser freshness/execution regressions. No model calls or external websites."""

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
        assert browser.fresh(page, action), "Movement should use fresh geometry, not another model call"
        browser.act(action, page)
        assert browser.evaluate("window.clicks") == 1
        passed.append("moving target clicked at its current location")

        page = browser.observe(screenshot=False)
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
        assert not browser.fresh(page)
        try:
            browser.act(action, page)
        except (RuntimeError, StalePage):
            pass
        else:
            raise AssertionError("Covered target was clicked")
        assert browser.evaluate("window.clicks") == 1
        passed.append("overlay blocked before input")

        browser.evaluate("document.body.innerHTML=" + repr("""
          <style>
            #card {position:absolute;left:40px;top:40px;width:240px;height:180px;background:#eee}
            #cover {position:absolute;left:110px;top:100px;width:100px;height:60px;z-index:2}
            #child {display:block;width:100%;height:100%}
          </style>
          <a id="card" href="#product" aria-label="Product"
            onclick="event.preventDefault();window.productClicks++;window.lastPoint=[event.clientX,event.clientY]">
            <span id="child">Product image</span>
          </a>
          <button id="cover" onclick="window.coverClicks++">Add to cart</button>
        """) + ";window.productClicks=0;window.coverClicks=0")
        page = browser.observe(screenshot=False)
        card = next(a for a in page["actions"] if a["label"] == "Product")
        browser.act(card, page)
        assert browser.evaluate("window.productClicks") == 1
        assert browser.evaluate("window.coverClicks") == 0
        passed.append("partially covered link clicks its exposed area, not the overlay button")

        browser.evaluate("document.querySelector('#cover').style.display='none'")
        page = browser.observe(screenshot=False)
        card = next(a for a in page["actions"] if a["label"] == "Product")
        browser.act(card, page)
        assert browser.evaluate("window.lastPoint") == [160, 130]
        assert browser.evaluate("window.productClicks") == 2
        passed.append("uncovered target still clicks its center through a non-interactive child")

        browser.evaluate("const nested=document.createElement('button');nested.id='nested';"
                         "nested.textContent='Separate action';"
                         "nested.style.cssText='position:absolute;left:70px;top:60px;width:100px;height:60px';"
                         "nested.onclick=event=>{event.preventDefault();event.stopPropagation();window.coverClicks++};"
                         "document.querySelector('#card').append(nested)")
        page = browser.observe(screenshot=False)
        card = next(a for a in page["actions"] if a["label"] == "Product")
        browser.act(card, page)
        assert browser.evaluate("window.productClicks") == 3
        assert browser.evaluate("window.coverClicks") == 0
        passed.append("nested independent button is not clicked instead of its parent link")

        browser.evaluate("document.querySelector('#nested').style.cssText="
                         "'position:absolute;inset:0;width:100%;height:100%'")
        covered = browser.observe(screenshot=False)
        assert not any(a["label"] == "Product" for a in covered["actions"])
        try:
            browser.act(card, page)
        except StalePage:
            pass
        else:
            raise AssertionError("Fully covering nested button was clicked")
        assert browser.evaluate("[window.productClicks,window.coverClicks]") == [3, 0]
        passed.append("all candidate points blocked means no input is dispatched")

        browser.evaluate("document.querySelector('#nested').remove()")
        page = browser.observe(screenshot=False)
        card = next(a for a in page["actions"] if a["label"] == "Product")
        # Reuse the observed node after a geometry-only move takes its center outside the viewport.
        browser.evaluate("document.querySelector('#card').style.left='-150px'")
        browser.act(card, page)
        assert browser.evaluate("window.productClicks") == 4
        assert browser.evaluate("window.lastPoint[0]>=0") is True
        passed.append("candidate points stay inside the viewport for a partially visible target")

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
    finally:
        browser.close()
    print("\n".join(passed))
    print(f"PASS: {len(passed)} browser guard checks; no model calls")


if __name__ == "__main__":
    main()
