"""Local-browser regression for transparent checkbox menu toggles. No model calls or external websites.

Many sites (including Wikipedia's Vector skin) open menus with a transparent checkbox laid over an icon.
The checkbox is the real hit target, so it must be offered and clickable even though its opacity is 0.
A transparent checkbox hidden underneath other content must still be rejected."""

from urllib.parse import quote

from jev_ultrafast.browser import Browser

HTML = """<!doctype html><title>Toggle menu</title>
<style>
body{margin:30px;font:16px sans-serif}
.toggle{position:relative;width:32px;height:32px}
.toggle input{position:absolute;inset:0;margin:0;width:32px;height:32px;opacity:0;z-index:1;cursor:pointer}
.toggle span{position:absolute;inset:0}
#menu{display:none;padding-top:40px}
#menu-toggle:checked ~ #menu{display:block}
#buried{position:absolute;top:200px;left:30px;width:32px;height:32px;opacity:0}
#cover{position:absolute;top:190px;left:20px;width:80px;height:60px;background:#eee;z-index:2}
</style>
<div class="toggle"><input id="menu-toggle" type="checkbox" aria-label="Main menu"><span>&#9776;</span>
<nav id="menu"><a href="#about" onclick="window.opened='about'">About this site</a></nav></div>
<input id="buried" type="checkbox" aria-label="Buried toggle"><div id="cover">cover</div>"""


def main():
    browser = Browser("data:text/html," + quote(HTML))
    passed = []
    try:
        page = browser.observe(screenshot=False)
        labels = [a["label"] for a in page["actions"]]
        toggle = next((a for a in page["actions"] if a["label"] == "Main menu"), None)
        assert toggle, f"Transparent menu toggle should be offered; got {labels}"
        passed.append("transparent hit-testable checkbox is offered")

        assert not any(a["label"] == "Buried toggle" for a in page["actions"]), "Covered transparent checkbox offered"
        passed.append("covered transparent checkbox is not offered")

        browser.act(toggle, page)
        assert browser.evaluate("document.querySelector('#menu-toggle').checked") is True
        passed.append("transparent toggle click executes")

        page = browser.observe(screenshot=False)
        about = next((a for a in page["actions"] if a["label"] == "About this site"), None)
        assert about, "Menu link should be offered after opening the menu"
        browser.act(about, page)
        assert browser.evaluate("window.opened") == "about"
        passed.append("link inside the opened menu is clickable")
    finally:
        browser.close()
    print("\n".join(f"PASS {item}" for item in passed))


if __name__ == "__main__":
    main()
