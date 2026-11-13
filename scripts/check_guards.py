"""Local-browser freshness/execution regressions. No model calls or external websites."""

from urllib.parse import quote

from jev_ultrafast.browser import Browser, StalePage
from jev_ultrafast.model import action_space

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

        browser.evaluate("document.body.innerHTML=" + repr("""
          <form>
            <label><input id="preference" type="checkbox">Announcements</label>
            <button id="inherit" type="button">Use workspace default</button>
          </form>
          <label><input id="partial" type="checkbox" checked>Partial selection</label>
          <label><input id="choice" type="radio" checked>Radio choice</label>
          <div role="checkbox" aria-checked="mixed">ARIA partial selection</div>
        """))
        browser.evaluate("""(() => {
          const preference=document.querySelector('#preference');
          window.preferenceValue=false; window.preferenceInputs=0;
          preference.addEventListener('change',()=>{
            window.preferenceValue=preference.checked; window.preferenceInputs++;
          });
          document.querySelector('#inherit').addEventListener('click',()=>{
            window.preferenceValue=null; preference.checked=false; preference.indeterminate=true;
          });
          document.querySelector('#partial').indeterminate=true;
          document.querySelector('#choice').indeterminate=true;
        })()""")
        before = browser.observe(screenshot=False)
        preference = next(a for a in before["actions"] if a["label"] == "Announcements")
        assert preference["checked"] == "false"
        inherit = next(a for a in before["actions"] if a["label"] == "Use workspace default")
        browser.act(inherit, before)
        page = browser.observe(screenshot=False)
        elements, _, _ = action_space(page["actions"])
        checked = {e["label"]: e["checked"] for e in elements if "checked" in e}
        assert checked == {
            "Announcements": "mixed", "Partial selection": "mixed",
            "Radio choice": "true", "ARIA partial selection": "mixed",
        }, checked
        assert page["fingerprint"] != before["fingerprint"]
        assert browser.evaluate("window.preferenceValue") is None
        passed.append("native mixed checkbox state reaches the element table without changing radio or ARIA state")

        assert not browser.fresh(before)
        assert not browser.fresh(before, preference)
        assert not browser.fresh(before, inherit)
        try:
            browser.act(preference, before)
        except StalePage:
            pass
        else:
            raise AssertionError("A click based on the old checkbox state was executed")
        assert browser.evaluate("window.preferenceInputs") == 0
        assert browser.evaluate("window.preferenceValue") is None
        preference = next(a for a in page["actions"] if a["label"] == "Announcements")
        browser.act(preference, page)
        assert browser.evaluate("window.preferenceInputs") == 1
        assert browser.evaluate("window.preferenceValue") is True
        assert browser.evaluate("document.querySelector('#preference').indeterminate") is False

        page = browser.observe(screenshot=False)
        partial = next(a for a in page["actions"] if a["label"] == "Partial selection")
        browser.evaluate("document.querySelector('#partial').checked=false")
        assert not browser.fresh(page)
        assert not browser.fresh(page, partial)
        passed.append("mixed-state changes reject stale clicks and preserve underlying checked-state guards")
    finally:
        browser.close()
    print("\n".join(passed))
    print(f"PASS: {len(passed)} browser guard checks; no model calls")


if __name__ == "__main__":
    main()
