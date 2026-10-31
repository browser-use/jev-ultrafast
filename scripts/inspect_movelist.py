import json
from jev_ultrafast.browser import Browser

b = Browser("https://www.chess.com/play/computer")

info = b.evaluate("""(() => {
  const moveNodes = [...document.querySelectorAll('.vertical-move-list-component .node, div.node, [data-whole-move-number]')];
  return moveNodes.map(n => ({
    tag: n.tagName,
    className: n.className,
    text: n.innerText.trim(),
    html: n.outerHTML.slice(0, 150)
  }));
})()""")

print(json.dumps(info[:10], indent=2))
b.close()
