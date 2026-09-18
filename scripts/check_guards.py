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

        browser.evaluate("""(() => {
          document.body.innerHTML=`<div id="open-host"></div>
            <div id="hidden-host" aria-hidden="true"></div><div id="closed-host"></div>`;
          const host=document.querySelector('#open-host');
          const root=host.attachShadow({mode:'open'});
          root.innerHTML=`<style>:host{display:block}button,input,select{display:block;margin:12px;width:220px;height:42px}</style>
            <p>Shadow panel content</p><label id="city-label">City</label>
            <input id="city" role="combobox" aria-labelledby="city-label" aria-controls="city-options"
              aria-autocomplete="list"><div id="city-options"></div>
            <button id="save">Save shadow form</button>
            <span id="shadow-status">Ready</span>
            <select id="shadow-category" aria-label="Shadow category">
              <option>All</option><option>Design</option>
            </select>
            <button id="slotted"><slot name="label"></slot></button>
            <slot name="suppressed" aria-hidden="true"></slot>
            <fieldset disabled><button>Disabled fieldset action</button></fieldset>
            <div aria-disabled="true"><button>ARIA-disabled shadow action</button></div>
            <div inert><button>Inert shadow action</button></div>
            <div id="nested-host"></div>`;
          root.append(document.createTextNode(' Direct shadow text '));
          root.querySelector('#save').addEventListener('click',()=>window.shadowClicks=(window.shadowClicks||0)+1);
          root.querySelector('#slotted').addEventListener('click',()=>window.slottedClicks=(window.slottedClicks||0)+1);
          root.querySelector('#city').addEventListener('input',()=>requestAnimationFrame(()=>{
            root.querySelector('#city-options').innerHTML='<div role="option">Lisbon, Portugal</div>';
          }),{once:true});
          const label=document.createElement('span');label.slot='label';
          label.textContent='Slotted action';host.append(label);
          const suppressed=document.createElement('button');suppressed.slot='suppressed';
          suppressed.textContent='Suppressed slotted action';host.append(suppressed);
          const nested=root.querySelector('#nested-host').attachShadow({mode:'open'});
          nested.innerHTML=`<style>button{display:block;margin:12px;width:220px;height:42px}</style>
            <button>Nested action</button>`;
          nested.querySelector('button').addEventListener('click',()=>window.nestedClicks=(window.nestedClicks||0)+1);
          const hidden=document.querySelector('#hidden-host').attachShadow({mode:'open'});
          hidden.innerHTML='<button>Hidden shadow action</button>';
          const closed=document.querySelector('#closed-host').attachShadow({mode:'closed'});
          closed.innerHTML='<button>Closed shadow action</button>';
          window.shadowSelectEvents=[];
          for (const type of ['input','change']) document.addEventListener(type,event=>{
            if (event.composedPath()[0]?.id==='shadow-category') window.shadowSelectEvents.push(type);
          });
        })()""")
        page = browser.observe(screenshot=False)
        actions = page["actions"]
        assert "Shadow panel content" in page["text"]
        assert "Direct shadow text" in page["text"]
        assert any(a["label"] == "City" and a["kind"] == "fill" for a in actions)
        assert any(a["label"] == "Save shadow form" for a in actions)
        assert any(a["label"] == "Slotted action" for a in actions)
        assert any(a["label"] == "Nested action" for a in actions)
        assert [a["value"] for a in actions if a["label"] == "Shadow category → Design"] == ["Design"]
        assert not any(
            "Hidden shadow" in a["label"]
            or "Closed shadow" in a["label"]
            or "Suppressed slotted" in a["label"]
            or "Disabled fieldset" in a["label"]
            or "ARIA-disabled shadow" in a["label"]
            or "Inert shadow" in a["label"]
            for a in actions
        )
        passed.append("shadow text and controls observed; hidden, closed, disabled, and inert targets excluded")

        field = next(a for a in actions if a["label"] == "City" and a["kind"] == "fill")
        browser.act(field, page, text="Lisbon")
        page = browser.observe(screenshot=False)
        value = browser.evaluate(
            "document.querySelector('#open-host').shadowRoot.querySelector('#city').value"
        )
        assert value == "Lisbon"
        assert any(a["role"] == "option" and a["label"] == "Lisbon, Portugal" for a in page["actions"])
        passed.append("shadow field waits for its asynchronous combobox option")

        select = next(a for a in page["actions"] if a["label"] == "Shadow category → Design")
        browser.act(select, page)
        assert browser.evaluate(
            "document.querySelector('#open-host').shadowRoot.querySelector('#shadow-category').value"
        ) == "Design"
        assert browser.evaluate("window.shadowSelectEvents") == ["input", "change"]
        passed.append("shadow dropdown selection emits composed input and change events")

        page = browser.observe(screenshot=False)
        nested = next(a for a in page["actions"] if a["label"] == "Nested action")
        browser.act(nested, page)
        assert browser.evaluate("window.nestedClicks") == 1
        passed.append("nested shadow button clicked through recursive hit testing")

        page = browser.observe(screenshot=False)
        slotted = next(a for a in page["actions"] if a["label"] == "Slotted action")
        browser.act(slotted, page)
        assert browser.evaluate("window.slottedClicks") == 1
        passed.append("slotted content names and hit-tests its shadow control")

        page = browser.observe(screenshot=False)
        nested = next(a for a in page["actions"] if a["label"] == "Nested action")
        browser.evaluate("const shadowCover=document.createElement('div');shadowCover.id='shadow-cover';"
                         "shadowCover.style.cssText='position:fixed;inset:0;z-index:9999;background:white';"
                         "document.body.append(shadowCover)")
        assert browser.fresh(page, nested)
        try:
            browser.act(nested, page)
        except (RuntimeError, StalePage):
            pass
        else:
            raise AssertionError("Covered shadow target was clicked")
        assert browser.evaluate("window.nestedClicks") == 1
        browser.evaluate("document.querySelector('#shadow-cover').remove()")
        passed.append("overlay blocked a nested shadow target before input")

        page = browser.observe(screenshot=False)
        save = next(a for a in page["actions"] if a["label"] == "Save shadow form")
        browser.evaluate(
            "document.querySelector('#open-host').shadowRoot.querySelector('#shadow-status').textContent='Changed'"
        )
        assert not browser.fresh(page, save)
        passed.append("nearby shadow context invalidates its action guard")

        page = browser.observe(screenshot=False)
        save = next(a for a in page["actions"] if a["label"] == "Save shadow form")
        browser.evaluate(
            "document.querySelector('#open-host').shadowRoot.querySelector('#save').textContent='Delete data'"
        )
        assert not browser.fresh(page, save)
        passed.append("shadow target meaning change invalidates its guard")

        page = browser.observe(screenshot=False)
        save = next(a for a in page["actions"] if a["label"] == "Delete data")
        browser.evaluate("document.querySelector('#open-host').setAttribute('inert','')")
        assert not browser.fresh(page, save)
        try:
            browser.act(save, page)
        except StalePage:
            pass
        else:
            raise AssertionError("Inert shadow target was executed")
        browser.evaluate("document.querySelector('#open-host').removeAttribute('inert')")
        passed.append("inert shadow host invalidates and blocks its observed target")

        page = browser.observe(screenshot=False)
        save = next(a for a in page["actions"] if a["label"] == "Delete data")
        browser.evaluate("document.querySelector('#open-host').setAttribute('aria-hidden','true')")
        assert not browser.fresh(page, save)
        assert not any(a.get("node") == save["node"] for a in browser.observe(screenshot=False)["actions"])
        passed.append("composed hidden host removes shadow actions")

        browser.evaluate("""(() => {
          document.body.innerHTML='<div id="early-host"></div>';
          const root=document.querySelector('#early-host').attachShadow({mode:'open'});
          root.innerHTML='<button>Early shadow action</button>';
          for (let i=0;i<260;i++) {
            const button=document.createElement('button');button.textContent='Light action '+i;
            button.style.cssText='position:fixed;top:0;left:0;width:10px;height:10px';
            document.body.append(button);
          }
        })()""")
        page = browser.observe(screenshot=False)
        assert page["omitted_actions"] > 0
        assert page["actions"][0]["label"] == "Early shadow action"
        assert not any(a["label"] == "Light action 259" for a in page["actions"])
        passed.append("candidate cap preserves composed order around an early shadow host")

        browser.evaluate("""(() => {
          document.body.innerHTML='<div id="deep-host"></div>';
          const host=document.querySelector('#deep-host');host.style.display='none';
          const root=host.attachShadow({mode:'open'});
          let parent=root;
          for (let i=0;i<12000;i++) {
            const child=document.createElement('div');parent.append(child);parent=child;
          }
        })()""")
        page = browser.observe(screenshot=False)
        assert browser.fresh(page)
        passed.append("deep composed traversal is stack-safe during observation and freshness checks")

        browser.call("Page.navigate", url="about:blank")
        assert not browser.fresh(page, field)
        passed.append("navigation invalidates the old document")
    finally:
        browser.close()
    print("\n".join(passed))
    print(f"PASS: {len(passed)} browser guard checks; no model calls")


if __name__ == "__main__":
    main()
