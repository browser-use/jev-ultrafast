"""Exercise offscreen hints against real layout in a local tab. No model calls or external websites."""

from urllib.parse import quote

from jev_ultrafast.browser import Browser

HTML = """<!doctype html><title>Offscreen checks</title>
<style>
  body{margin:0;min-height:6000px}
  .above{position:absolute;top:100px;left:30px}
  .below{position:absolute;top:5500px;left:30px}
</style>
<button class="above" id="save" onclick="window.saved=(window.saved||0)+1">Save changes</button>
<button class="above">Save changes</button>
<button class="below" id="next">Next section</button>
<label class="above" style="top:180px">Email address<input value="private-field-value"></label>
<button style="position:absolute;top:2500px;left:30px">Visible help</button>
<div class="above" hidden><button>Hidden control</button></div>
<div class="below" aria-hidden="true"><button>Aria hidden control</button></div>
<div class="above" inert><button>Inert control</button></div>
<button class="above" disabled>Disabled control</button>
<button class="below" aria-disabled="true">Aria disabled control</button>
<input class="above" type="password" aria-label="Password control" value="secret">
<input class="below" type="file" aria-label="File control">
<button class="above" style="left:2000px">Horizontal control</button>
<button style="position:fixed;top:-100px">Fixed control</button>
<div style="position:absolute;top:2800px;width:200px;height:100px;overflow:auto">
  <button style="position:absolute;top:1500px">Nested clipped control</button>
</div>
<div class="below" style="overflow:hidden;width:200px;height:100px">
  <button style="position:absolute;left:400px">Clipped carousel control</button>
</div>"""


def scroll_to(browser, y):
    browser.call(
        "Runtime.evaluate",
        expression=f"new Promise(resolve=>{{scrollTo(0,{y});requestAnimationFrame(()=>resolve(scrollY))}})",
        awaitPromise=True,
        returnByValue=True,
    )


def main():
    browser = Browser("data:text/html," + quote(HTML))
    passed = []
    try:
        scroll_to(browser, 2400)
        page = browser.observe(screenshot=False)
        hints = page["offscreen_controls"]
        assert hints == [
            {"role": "button", "label": "Save changes", "direction": "above"},
            {"role": "textbox", "label": "Email address", "direction": "above"},
            {"role": "button", "label": "Next section", "direction": "below"},
        ], hints
        assert all(set(hint) == {"role", "label", "direction"} for hint in hints)
        assert all("private-field-value" not in str(hint) for hint in hints)
        assert not any(a["label"] in {"Save changes", "Next section", "Email address"} for a in page["actions"])
        passed.append("above/below hints are deduplicated, filtered, and contain no targets or field values")

        browser.evaluate("document.querySelector('#next').textContent='Review changes'")
        assert not browser.fresh(page)
        assert browser.observe(screenshot=False)["fingerprint"] != page["fingerprint"]
        passed.append("changed offscreen controls invalidate terminal decisions and update the observation")

        # Link-heavy pages must retain ordinary buttons/fields and evidence in both directions.
        browser.evaluate("""(() => {
          for (const direction of ['above','below']) for (let i=0;i<45;i++) {
            const a=document.createElement('a');a.href='#'+i;
            a.className=direction;a.textContent='Navigation '+i;document.body.prepend(a);
          }
        })()""")
        page = browser.observe(screenshot=False)
        hints = page["offscreen_controls"]
        assert len(hints) == 80
        assert sum(h["direction"] == "above" for h in hints) == 40
        assert sum(h["direction"] == "below" for h in hints) == 40
        assert {h["label"] for h in hints if h["role"] != "link"} == {
            "Save changes", "Email address", "Review changes",
        }
        assert page["omitted_offscreen_controls"] == {"above": 7, "below": 6}
        passed.append("bounded lists retain controls in both directions despite many earlier links")

        browser.evaluate("document.querySelector('#next').style.width='200px';"
                         "document.querySelector('#next').textContent='x'.repeat(500)")
        page = browser.observe(screenshot=False)
        assert max(len(h["label"]) for h in page["offscreen_controls"]) == 160
        passed.append("long accessible names are bounded")

        browser.evaluate("document.querySelectorAll('a').forEach(e=>e.remove());"
                         "document.querySelectorAll('button')[1].remove();"
                         "document.querySelector('#save').style.top='400px'")
        scroll_to(browser, 0)
        page = browser.observe(screenshot=False)
        assert not any(h["label"] == "Save changes" for h in page["offscreen_controls"])
        assert not any(h["direction"] == "above" for h in page["offscreen_controls"])
        save = next(a for a in page["actions"] if a["label"] == "Save changes")
        browser.act(save, page)
        assert browser.evaluate("window.saved") == 1
        passed.append("scrolling and reobserving makes the control a real target; one input executes once")

        scroll_to(browser, 10000)
        page = browser.observe(screenshot=False)
        assert not any(h["direction"] == "below" for h in page["offscreen_controls"])
        passed.append("bottom-of-page hints only point in an available scroll direction")
    finally:
        browser.close()
    print("\n".join(passed))
    print(f"PASS: {len(passed)} offscreen checks; no model calls")


if __name__ == "__main__":
    main()
