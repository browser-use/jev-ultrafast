"""Local-browser freshness/execution regressions. No model calls or external websites."""

from urllib.parse import quote

from jev_ultrafast.browser import Browser, StalePage, browser_operation

HTML = """<!doctype html><title>Guard checks</title>
<style>
body{margin:30px}button{width:180px;height:50px}#outside{position:absolute;top:3000px}
#feed{position:absolute;left:500px;top:140px;width:320px;height:180px;overflow-y:auto}
#feed div{height:800px}
#wheel-control{position:sticky;top:0;width:100px;height:40px}
#outer-feed{position:absolute;left:40px;top:520px;width:1000px;height:200px;overflow-y:auto}
#inner-feed{height:200px;overflow-y:auto}
#inner-feed div,#outer-tail{height:600px}
</style>
<p id="context">Cart total: $10</p>
<button id="target" onclick="window.clicks=(window.clicks||0)+1">Continue</button>
<label>City<input id="field" value="Zurich"></label>
<label><input id="toggle" type="checkbox">Refundable</label>
<select aria-label="Category"><option>All</option><option>Design</option></select>
<section id="feed" aria-label="Search results">
  <input id="wheel-control" type="number" value="5"><div>First result<br>More results below</div>
</section>
<section id="outer-feed" aria-label="Outer results">
  <section id="inner-feed" aria-label="Inner results"><div>Nested results</div></section>
  <div id="outer-tail">Outer tail</div>
</section>
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

        page = browser.observe(screenshot=False)
        scroll = next(a for a in page["actions"] if a["label"] == "Scroll down Search results")
        browser.evaluate(
            "(()=>{const e=document.querySelector('#wheel-control'); e.focus(); "
            "e.addEventListener('wheel',()=>window.controlWheels=(window.controlWheels||0)+1)})()"
        )
        page_y = browser.evaluate("scrollY")
        browser.act(scroll, page)
        browser.observe(screenshot=False)
        assert browser.evaluate("document.querySelector('#feed').scrollTop") > 0
        assert browser.evaluate("scrollY") == page_y
        assert browser.evaluate("window.controlWheels||0") == 0
        assert browser.evaluate("document.querySelector('#wheel-control').value") == "5"
        assert not browser.fresh(page, scroll)
        passed.append("nested scroll avoids form controls and moves only its observed region")

        for attribute, value in (("inert", ""), ("aria-hidden", "true")):
            browser.evaluate("document.querySelector('#feed').scrollTop=0")
            page = browser.observe(screenshot=False)
            scroll = next(a for a in page["actions"] if a["label"] == "Scroll down Search results")
            browser.evaluate(
                f"document.querySelector('#feed').setAttribute({attribute!r},{value!r})"
            )
            assert not browser.fresh(page, scroll)
            try:
                browser_operation({
                    "operation": "act", "session": browser.session, "action": scroll,
                })
            except StalePage:
                pass
            else:
                raise AssertionError(f"{attribute} scroll region received input")
            browser.evaluate(f"document.querySelector('#feed').removeAttribute({attribute!r})")
            passed.append(attribute + " scroll region rejected before input")

        browser.evaluate(
            "(()=>{const e=document.querySelector('#feed'); "
            "e.scrollTop=e.scrollHeight-e.clientHeight-13})()"
        )
        page = browser.observe(screenshot=False)
        scroll = next(a for a in page["actions"] if a["label"] == "Scroll down Search results")
        assert 0 < scroll["delta"] <= 13
        page_y = browser.evaluate("scrollY")
        browser.act(scroll, page)
        browser.observe(screenshot=False)
        assert browser.evaluate("scrollY") == page_y
        passed.append("nested scroll delta is clamped at the region boundary")

        page = browser.observe(screenshot=False)
        labels = {a["label"] for a in page["actions"]}
        assert "Scroll down Inner results" in labels
        assert "Scroll down Outer results" not in labels
        inner = next(a for a in page["actions"] if a["label"] == "Scroll down Inner results")
        outer_y = browser.evaluate("document.querySelector('#outer-feed').scrollTop")
        browser.act(inner, page)
        browser.observe(screenshot=False)
        assert browser.evaluate("document.querySelector('#inner-feed').scrollTop") > 0
        assert browser.evaluate("document.querySelector('#outer-feed').scrollTop") == outer_y
        passed.append("nearest nested scroll region receives the wheel event")

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

        # Keep all 260 buttons independently hittable when covered controls are filtered out.
        browser.evaluate("""document.body.innerHTML=
          '<section id="dense-feed" aria-label="Dense results" style="position:fixed;left:500px;top:140px;'
          +'width:320px;height:180px;overflow-y:auto"><div style="height:800px">Results</div></section>'
          +Array.from({length:260},(_,i)=>'<button style="position:fixed;left:'+(i%12)*40+'px;top:'
          +Math.floor(i/12)*26+'px;width:38px;height:24px;padding:0;overflow:hidden">Button '
          +i+'</button>').join('')""")
        page = browser.observe(screenshot=False)
        labels = {a["label"] for a in page["actions"]}
        retained_buttons = sum(a["kind"] == "click" for a in page["actions"])
        assert len(page["actions"]) == 250, (
            len(page["actions"]), retained_buttons, page["omitted_actions"], labels,
        )
        assert retained_buttons + page["omitted_actions"] == 260
        assert "Scroll down Dense results" in labels
        assert "Wait for the page to update" in labels
        passed.append("250-action cap retains bounded scroll and wait controls")

        assert browser.fresh(page)
        browser.call("Page.navigate", url="about:blank")
        new_page = browser.observe(screenshot=False)
        assert new_page["page_key"] != page["page_key"]
        assert not browser.fresh(page)
        passed.append("navigation invalidates the old document")
    finally:
        browser.close()
    print("\n".join(passed))
    print(f"PASS: {len(passed)} browser guard checks; no model calls")


if __name__ == "__main__":
    main()
