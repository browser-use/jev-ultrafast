"""Verify covered targets and custom controls in a local modal; no model calls or external sites."""

from urllib.parse import quote

from jev_ultrafast.browser import Browser

HTML = """<!doctype html><title>Overlay checks</title>
<style>
body{margin:30px}
#cart{position:fixed;right:40px;top:40px}
#modal{display:none;position:fixed;inset:0;background:white;padding:60px}
.buttons{cursor:pointer;width:300px}
.buttons>div{height:50px;margin:12px;background:#ddd}
</style>
<a id="cart" href="#cart">Header cart</a>
<button id="add" onclick="window.additions++;document.querySelector('#modal').style.display='block'">
  <span style="cursor:pointer">Add item</span>
</button>
<div id="modal">
  <h1>Item added</h1>
  <div class="buttons">
    <div id="view"><span>View cart</span></div>
    <div id="continue">Continue shopping</div>
    <div aria-disabled="true">Unavailable action</div>
  </div>
  <div>Plain text is not a button</div>
</div>
<script>
window.additions=0;window.cartViews=0;
document.addEventListener('click',event=>{
  if(event.target.closest('#view')){window.cartViews++;document.querySelector('h1').textContent='Cart opened'}
});
</script>"""


def main():
    browser = Browser("data:text/html," + quote(HTML))
    try:
        page = browser.observe(screenshot=False)
        assert any(a["label"] == "Header cart" for a in page["actions"])
        assert sum(a["label"] == "Add item" for a in page["actions"]) == 1
        add = next(a for a in page["actions"] if a["label"] == "Add item")
        browser.act(add, page)
        page = browser.observe(screenshot=False)
        labels = [a["label"] for a in page["actions"]]
        assert "Header cart" not in labels
        assert "Add item" not in labels
        assert "Plain text is not a button" not in labels
        assert "Unavailable action" not in labels
        assert labels.count("View cart") == 1
        assert labels.count("Continue shopping") == 1
        assert "View cart Continue shopping Unavailable action" not in labels
        view = next(a for a in page["actions"] if a["label"] == "View cart")
        assert view["role"] == "button" and view["kind"] == "click"
        browser.act(view, page)
        result = browser.observe(screenshot=False)
        assert "Cart opened" in result["text"]
        assert browser.evaluate("[window.additions,window.cartViews]") == [1, 1]
        print("PASS: covered controls excluded; custom modal controls deduplicated; each input executes once")
    finally:
        browser.close()


if __name__ == "__main__":
    main()
