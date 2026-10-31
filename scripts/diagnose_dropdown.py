from jev_ultrafast.browser import Browser
import time

b = Browser("https://www.google.com/travel/flights?hl=en")
p = b.observe(screenshot=False)
action = next(a for a in p["actions"] if "ticket type" in a.get("label", "").lower())
print("Initial action:", action)
b.act(action, p)
time.sleep(1)

js = """
(() => {
    const elms = Array.from(document.querySelectorAll("*")).filter(e => 
        (e.innerText || "").includes("One way") && e.children.length === 0
    );
    return elms.map(e => ({
        tag: e.tagName,
        role: e.getAttribute("role"),
        text: e.innerText,
        vis: e.checkVisibility({checkOpacity: true, checkVisibilityCSS: true}),
        bounds: e.getBoundingClientRect(),
        closestAriaHidden: !!e.closest('[aria-hidden="true"]'),
        closestInert: !!e.closest('[inert]'),
        parentRole: e.parentElement ? e.parentElement.getAttribute("role") : null
    }));
})()
"""
res = b.evaluate(js)
print("One way matches:", res)

# Also check what element is at the click coordinates of action
act_x = action["rect"]["x"] + action["rect"]["w"] / 2
act_y = action["rect"]["y"] + action["rect"]["h"] / 2
top_elm = b.evaluate(f"(() => {{ const e = document.elementFromPoint({act_x}, {act_y}); return e ? {{tag: e.tagName, role: e.getAttribute('role'), class: e.className, ariaLabel: e.getAttribute('aria-label')}} : null; }})()")
print("Element at click point:", top_elm)

b.close()
