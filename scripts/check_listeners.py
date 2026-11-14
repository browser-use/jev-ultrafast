"""Real-browser checks for controls whose only clickability is a script-attached listener.

Local fixture only; no model calls or external websites.
"""

from urllib.parse import quote

from jev_ultrafast.browser import Browser

# The table header mirrors jQuery tablesorter: a bare <th> with a class, its click bound by
# script, and nothing in the markup (role, tabindex, onclick, href, cursor) that says it is
# clickable. The other elements are what the reader must NOT offer.
HTML = """<!doctype html><title>Listener checks</title>
<style>body{margin:30px;font:16px sans-serif}th{padding:8px}#wrapper{height:700px}</style>
<table><thead><tr><th class="header" id="last">Last Name</th><th>Email</th></tr></thead>
<tbody><tr><td>Smith</td><td>js@example.com</td></tr><tr><td>Bach</td><td>fb@example.com</td></tr></tbody></table>
<div id="card" class="card"><h3>Card title</h3><a href="#details">Details</a></div>
<div id="outer" class="chip"><span id="inner">Remove filter</span></div>
<div id="blank" style="width:40px;height:40px"></div>
<div id="wrapper"><p>Large region with a click listener</p></div>
<script>
document.getElementById('last').addEventListener('click', () => {
  const body = document.querySelector('tbody');
  [...body.rows].sort((a, b) => a.cells[0].textContent.localeCompare(b.cells[0].textContent))
    .forEach(row => body.appendChild(row));
  window.sorted = (window.sorted || 0) + 1;
});
document.getElementById('card').addEventListener('click', () => { window.card = 1; });
document.getElementById('outer').addEventListener('click', () => { window.outer = 1; });
document.getElementById('inner').addEventListener('pointerdown', () => { window.inner = 1; });
document.getElementById('blank').addEventListener('click', () => { window.blank = 1; });
document.getElementById('wrapper').addEventListener('click', () => { window.wrapper = 1; });
</script>"""


def main():
    browser = Browser("data:text/html," + quote(HTML))
    passed = []
    try:
        page = browser.observe(screenshot=False)
        labels = [a["label"] for a in page["actions"]]
        header = next((a for a in page["actions"] if a["label"] == "Last Name"), None)
        assert header is not None, f"Script-bound header must be offered; offered: {labels}"
        assert header["role"] == "button" and header["kind"] == "click" and header.get("listener") is True
        passed.append("script-bound table header offered as a button")

        assert browser.fresh(page), "The freshness marker must agree with the observe read"
        passed.append("freshness marker reuses the observed set")

        browser.act(header, page)
        assert browser.evaluate("window.sorted") == 1, "Clicking the offered header must run its handler"
        assert browser.evaluate("document.querySelector('tbody td').textContent") == "Bach"
        passed.append("offered header clicked and its handler ran")

        page = browser.observe(screenshot=False)
        labels = [a["label"] for a in page["actions"]]
        assert "Details" in labels, "An ordinary link must still be offered"
        assert not any("Card title" in label for label in labels), "A card wrapping an offered link is a wrapper"
        passed.append("listener wrapper around an indexed link not offered")
        assert "Remove filter" in labels and sum(label == "Remove filter" for label in labels) == 1, \
            f"Nested listeners must yield the inner element once; offered: {labels}"
        passed.append("nested listeners offer the innermost element only")
        assert not any("Large region" in label for label in labels), \
            "Regions over a quarter of the viewport are not controls"
        passed.append("large listener region not offered")
        assert len([a for a in page["actions"] if a.get("listener")]) == 2, \
            "Only the header and the inner chip may be offered from listeners (an unnamed box is skipped)"
        passed.append("unnamed listener box not offered")
    finally:
        browser.close()
    for item in passed:
        print("PASS:", item)


if __name__ == "__main__":
    main()
