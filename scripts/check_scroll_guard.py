"""Read-only scroll guards plus one real wheel event on a local fixture. No model calls."""

from urllib.parse import quote

from jev_ultrafast.browser import Browser, StalePage

HTML = """<!doctype html><title>Scroll guard checks</title>
<style>body{margin:30px;min-height:4000px}</style>
<p id="details">Loading details</p><input aria-label="Search">
<script>window.wheels=0;addEventListener('wheel',()=>window.wheels++,{passive:true})</script>"""


def main():
    browser = Browser("data:text/html," + quote(HTML))
    passed = []
    try:
        page = browser.observe(screenshot=False)
        down = next(a for a in page["actions"] if a["id"] == "scroll_down")
        browser.evaluate("document.querySelector('#details').textContent='Details finished loading';"
                         "document.querySelector('input').value='updated';"
                         "document.body.insertAdjacentHTML('beforeend','<button>New control</button>');"
                         "document.body.style.minHeight='5000px'")
        assert not browser.fresh(page)
        assert browser.fresh(page, down)
        browser.act(down, page)
        result = browser.observe(screenshot=False)
        assert result["scroll"]["y"] > 0
        assert browser.evaluate("window.wheels") == 1
        assert not browser.fresh(page, down)
        passed.append("changed copy, fields, controls and page height allow one real document scroll")

        mutations = {
            "changed URL": (
                "history.replaceState(null,'','#changed')",
                "history.replaceState(null,'',location.href.split('#')[0])",
            ),
            "changed scroll position": ("scrollTo(0,100)", "scrollTo(0,0)"),
            "new fixed overlay": (
                "document.body.insertAdjacentHTML('beforeend',"
                "'<div id=overlay style=\"position:fixed;inset:0;background:white\"></div>')",
                "document.querySelector('#overlay').remove()",
            ),
            "locked document": ("document.body.style.overflow='hidden'", "document.body.style.overflow=''"),
            "new nested scroller": (
                "document.body.insertAdjacentHTML('beforeend',"
                "'<div id=nested style=\"position:fixed;left:500px;top:600px;"
                "width:200px;height:150px;overflow:auto\"><div style=\"height:2000px\">Nested</div></div>')",
                "document.querySelector('#nested').remove()",
            ),
        }
        for label, (mutate, restore) in mutations.items():
            page = browser.observe(screenshot=False)
            down = next(a for a in page["actions"] if a["id"] == "scroll_down")
            browser.evaluate(mutate)
            assert not browser.fresh(page, down), label
            try:
                browser.act(down, page)
            except StalePage:
                pass
            else:
                raise AssertionError(label + " allowed unexpected wheel input")
            assert browser.evaluate("window.wheels") == 1
            if label in {"locked document", "new nested scroller"}:
                observed = browser.observe(screenshot=False)
                assert not any(a["kind"] == "scroll" for a in observed["actions"])
            browser.evaluate(restore)
            passed.append(label + " invalidates before wheel input")

        page = browser.observe(screenshot=False)
        down = next(a for a in page["actions"] if a["id"] == "scroll_down")
        browser.call("Emulation.setDeviceMetricsOverride", width=900, height=650, deviceScaleFactor=1, mobile=False)
        assert not browser.fresh(page, down)
        passed.append("resized viewport invalidates")
        browser.call("Emulation.setDeviceMetricsOverride", width=1120, height=780, deviceScaleFactor=1, mobile=False)

        page = browser.observe(screenshot=False)
        down = next(a for a in page["actions"] if a["id"] == "scroll_down")
        browser.evaluate("document.body.style.minHeight='0'")
        assert not browser.fresh(page, down)
        assert not any(a["kind"] == "scroll" for a in browser.observe(screenshot=False)["actions"])
        passed.append("a direction that can no longer scroll is not offered")

        browser.call("Page.navigate", url="about:blank")
        assert not browser.fresh(page, down)
        passed.append("new document invalidates")
    finally:
        browser.close()
    print("\n".join(passed))
    print(f"PASS: {len(passed)} scroll guard checks; no model calls")


if __name__ == "__main__":
    main()
