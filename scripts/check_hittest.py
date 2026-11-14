"""Real-browser checks that the snapshot offers only controls the act guard can hit.

Local fixture only; no model calls or external websites.
"""

from urllib.parse import quote

from jev_ultrafast.browser import Browser

# Four shapes, all inside the viewport:
#  - "Pricing" sits in a sidebar whose overflow-y clips it; its centre is on the main column.
#  - "Accept all" is under a fixed banner, like a control behind a cookie or consent layer.
#  - the wrapped link spans two lines, so its bounding-box centre lands on the text between
#    its fragments while each fragment is visibly clickable.
#  - "Continue" is an ordinary button and must keep working.
HTML = """<!doctype html><title>Hit-test checks</title>
<style>
body{margin:0;font:20px/1.6 serif}
#side{position:absolute;left:0;top:0;width:220px;height:200px;overflow-y:auto;background:#eee}
#side a{display:block;height:60px}
#main{position:absolute;left:0;top:210px;width:100%}
#banner{position:fixed;left:0;right:0;top:520px;height:120px;background:#333;color:#fff}
#hidden{position:absolute;left:40px;top:560px}
#para{width:300px;margin:0 0 0 400px}
</style>
<nav id="side"><a href="#a">Overview</a><a href="#b">Models</a><a href="#c">Limits</a>
<a href="#p" id="pricing" onclick="window.clickedPricing=1;return false">Pricing</a></nav>
<div id="main">
<p id="para">Python is a widely used, very popular
<a href="#gp" id="wrapped" onclick="window.clickedWrapped=(window.clickedWrapped||0)+1;return false">general-purpose
programming language</a> with a large standard library and many users.</p>
<button id="go" onclick="window.clickedGo=(window.clickedGo||0)+1">Continue</button>
</div>
<button id="hidden" onclick="window.clickedHidden=1">Accept all</button>
<div id="banner">This banner covers the page</div>
"""


def main():
    browser = Browser("data:text/html," + quote(HTML))
    passed = []
    try:
        # Preconditions: each shape is the one described above, so a pass cannot be vacuous.
        geometry = browser.evaluate("""(() => {
          const at = (e, r = e.getBoundingClientRect()) =>
            document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
          const p = document.getElementById('pricing'), w = document.getElementById('wrapped');
          const h = document.getElementById('hidden'), pr = p.getBoundingClientRect();
          return {pricing_in_viewport: pr.y + pr.height / 2 < innerHeight, pricing_hit: p.contains(at(p)),
                  hidden_hit: h.contains(at(h)), wrapped_lines: w.getClientRects().length,
                  wrapped_centre_hit: w.contains(at(w))};
        })()""")
        assert geometry == {"pricing_in_viewport": True, "pricing_hit": False, "hidden_hit": False,
                            "wrapped_lines": 2, "wrapped_centre_hit": False}, f"Fixture layout changed: {geometry}"
        passed.append("fixture geometry is as described")

        page = browser.observe(screenshot=False)
        labels = [a["label"] for a in page["actions"]]
        assert "Pricing" not in labels, f"A control clipped by its scroll container must not be offered: {labels}"
        passed.append("control clipped by a scroll container not offered")
        assert "Accept all" not in labels, f"A control under a fixed banner must not be offered: {labels}"
        passed.append("control under a fixed banner not offered")
        assert {"Overview", "Models", "Continue"} <= set(labels), f"Hittable controls must stay offered: {labels}"
        passed.append("hittable controls still offered")

        # Occlusion is geometry: covering a control after the observation must not make the
        # page read as changed (the act guard refuses the covered click instead).
        browser.evaluate("document.getElementById('banner').style.top='200px'")
        assert browser.fresh(page), "Covering a control must not invalidate the observation"
        browser.evaluate("document.getElementById('banner').style.top='520px'")
        passed.append("covering a control does not invalidate the marker")

        wrapped = next((a for a in page["actions"] if a["label"].startswith("general-purpose")), None)
        assert wrapped is not None, f"A link wrapped over two lines must be offered: {labels}"
        browser.act(wrapped, page)
        assert browser.evaluate("window.clickedWrapped") == 1, "The wrapped link must be clicked once, on a fragment"
        passed.append("wrapped link offered and clicked on a visible fragment")

        page = browser.observe(screenshot=False)
        go = next(a for a in page["actions"] if a["label"] == "Continue")
        browser.act(go, page)
        assert browser.evaluate("window.clickedGo") == 1
        passed.append("ordinary button clicked")
    finally:
        browser.close()
    for item in passed:
        print("PASS:", item)


if __name__ == "__main__":
    main()
