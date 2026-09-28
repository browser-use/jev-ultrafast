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

        for attribute in ('contenteditable="true"', 'contenteditable', 'contenteditable=""',
                          'contenteditable="plaintext-only"', 'contenteditable="TRUE"',
                          'contenteditable="PLAINTEXT-ONLY"'):
            browser.evaluate("document.body.innerHTML=" + repr(f"""
              <div id="editor" {attribute} aria-label="Draft"
                   style="width:400px;min-height:60px;border:1px solid">Old draft</div>
              <button onclick="document.querySelector('output').textContent=
                  document.querySelector('#editor').innerText">Save draft</button><output></output>
              <div contenteditable="false" aria-label="Locked">Locked text</div>
              <div contenteditable="invalid" aria-label="Invalid">Plain text</div>
              <div aria-label="Static">Static text</div>
              <div contenteditable="" hidden aria-label="Hidden">Hidden text</div>
              <div contenteditable="plaintext-only" inert aria-label="Inert">Inert text</div>
              <div contenteditable="true" aria-readonly="true" aria-label="Read only">Read only text</div>
              <div id="other-editor" contenteditable="true" aria-label="Other draft">Keep this
                <span contenteditable="inherit" aria-label="Inherited">paragraph</span></div>
            """))
            page = browser.observe(screenshot=False)
            draft = [a for a in page["actions"] if a["label"] in {"Draft", "Open Draft"}]
            assert {a["kind"] for a in draft} == {"fill", "click"}, attribute
            assert all(a["role"] == "textbox" and a["value"] == "Old draft" for a in draft)
            assert not any(a["label"] in {"Locked", "Invalid", "Static", "Hidden", "Inert", "Inherited"}
                           for a in page["actions"])
            assert {a["kind"] for a in page["actions"] if a["label"] == "Read only"} == {"click"}
            field = next(a for a in draft if a["kind"] == "fill")
            browser.act(field, page, text="Revised draft")
            assert browser.evaluate("document.querySelector('#editor').innerText") == "Revised draft"
            page = browser.observe(screenshot=False)
            save = next(a for a in page["actions"] if a["label"] == "Save draft")
            browser.act(save, page)
            assert browser.evaluate("document.querySelector('output').textContent") == "Revised draft"
            assert browser.evaluate("document.querySelector('#other-editor').innerText") == "Keep this paragraph"
        passed.append("contenteditable variants replace and save text while respecting non-editable controls")

        page = browser.observe(screenshot=False)
        field = next(a for a in page["actions"] if a["label"] == "Draft" and a["kind"] == "fill")
        browser.evaluate("document.querySelector('#editor').contentEditable='false'")
        assert not browser.fresh(page)
        try:
            browser.act(field, page, text="Must not be inserted")
        except StalePage:
            pass
        else:
            raise AssertionError("Editor disabled after observation was typed into")
        assert browser.evaluate("document.querySelector('#editor').innerText") == "Revised draft"
        passed.append("removing editability invalidates the observed text action")
    finally:
        browser.close()
    print("\n".join(passed))
    print(f"PASS: {len(passed)} browser guard checks; no model calls")


if __name__ == "__main__":
    main()
