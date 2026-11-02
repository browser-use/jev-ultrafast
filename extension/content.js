/* Tab-side bridge: settles after input, validates observed targets, executes actions.
   Element identity comes from snapshot.js (window.__jevFast), injected by the background
   script. Ported from jev_ultrafast/browser.py; the model only ever picks observed nodes. */

(() => {
  if (window.__jevBridge) return;
  window.__jevBridge = true;

  let afterInput = null;

  const visible = (e) =>
    e.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });

  // Wait for useful state after an interaction: visible autocomplete options or two frames.
  function settle(action) {
    const field = window.__jevFast ? window.__jevFast.nodes.get(action.node) : null;
    const autocomplete = action.kind === "fill" && field && field.getAttribute("role") === "combobox";
    return new Promise((resolve) => {
      let frames = 0;
      let stopped = false;
      const finish = () => {
        stopped = true;
        resolve();
      };
      setTimeout(finish, autocomplete ? 200 : 50);
      const ready = () => {
        if (stopped) return;
        const ids = (field && (field.getAttribute("aria-controls") || field.getAttribute("aria-owns")) || "")
          .split(/\s+/)
          .filter(Boolean);
        const roots = ids.length ? ids.map((id) => document.getElementById(id)).filter(Boolean) : [document];
        const options = roots.flatMap((root) => [...root.querySelectorAll('[role="option"]')]);
        if (
          ++frames >= 2 &&
          (!autocomplete ||
            options.some((e) => {
              const r = e.getBoundingClientRect();
              return r.width && r.height && r.bottom > 0 && r.top < innerHeight && visible(e);
            }))
        ) {
          finish();
        } else {
          requestAnimationFrame(ready);
        }
      };
      requestAnimationFrame(ready);
    });
  }

  function dispatchClick(e) {
    const options = { bubbles: true, cancelable: true, composed: true, view: window };
    const point = { pointerId: 1, pointerType: "mouse", isPrimary: true, width: 1, height: 1, pressure: 0.5 };
    const rect = e.getBoundingClientRect();
    const at = { clientX: rect.x + rect.width / 2, clientY: rect.y + rect.height / 2, button: 0, buttons: 1 };
    for (const type of ["pointerdown", "mousedown"]) {
      e.dispatchEvent(new PointerEvent(type, { ...options, ...point, ...at }));
    }
    for (const type of ["pointerup", "mouseup"]) {
      e.dispatchEvent(new PointerEvent(type, { ...options, ...point, ...at, buttons: 0 }));
    }
    e.click();
  }

  function typeText(e, text) {
    e.focus();
    if (e.select) {
      e.select();
    } else if (e.setSelectionRange && typeof e.value === "string") {
      e.setSelectionRange(0, e.value.length);
    } else if (e.isContentEditable) {
      const range = document.createRange();
      range.selectNodeContents(e);
      const selection = getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    }
    const inserted = document.execCommand("insertText", false, text);
    if (!inserted) {
      // Fallback for pages where execCommand is unavailable: native setter + events.
      const proto = e.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const descriptor = Object.getOwnPropertyDescriptor(proto, "value");
      if (descriptor && descriptor.set) descriptor.set.call(e, text);
      e.dispatchEvent(new Event("input", { bubbles: true }));
      e.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

  async function act(message) {
    const action = message.action;
    if (action.kind === "wait") {
      await new Promise((resolve) => setTimeout(resolve, 100));
      return { executed: action.id };
    }
    if (action.kind === "scroll") {
      window.scrollBy(0, action.delta);
      afterInput = null;
      return { executed: action.id };
    }
    // Code-owned node IDs refer to actually observed elements, never model-generated selectors.
    const e = window.__jevFast ? window.__jevFast.nodes.get(action.node) : null;
    const stale = () => ({ stale: true });
    if (!e || !e.isConnected || e.matches(":disabled") || e.closest('[aria-disabled="true"],[inert]') || !visible(e)) {
      return stale();
    }
    if (action.kind === "fill" && (e.readOnly || e.getAttribute("aria-readonly") === "true")) return stale();
    const r = e.getBoundingClientRect();
    const x = r.x + r.width / 2;
    const y = r.y + r.height / 2;
    if (!r.width || !r.height || x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) return stale();
    if (!e.contains(document.elementFromPoint(x, y))) return stale();
    if (action.kind === "select") {
      if (e.tagName !== "SELECT" || ![...e.options].some((o) => o.value === action.value && !o.disabled && !o.closest("optgroup[disabled]"))) {
        return stale();
      }
      e.value = action.value;
      e.dispatchEvent(new Event("input", { bubbles: true }));
      e.dispatchEvent(new Event("change", { bubbles: true }));
      afterInput = action;
      return { executed: action.id };
    }
    if (action.kind === "click") {
      dispatchClick(e);
      afterInput = action;
      return { executed: action.id };
    }
    if (action.kind === "fill") {
      typeText(e, message.text == null ? "" : message.text);
      afterInput = action;
      return { executed: action.id };
    }
    return { stale: true };
  }

  browser.runtime.onMessage.addListener((message) => {
    if (!message || !message.cmd) return undefined;
    if (message.cmd === "ping") return Promise.resolve(true);
    if (message.cmd === "settle") {
      const action = afterInput;
      afterInput = null;
      return action ? settle(action).then(() => true) : Promise.resolve(true);
    }
    if (message.cmd === "freshNode") {
      const cache = window.__jevFast;
      if (!cache) return Promise.resolve(null);
      return Promise.resolve([cache.pageKey(), cache.guard(cache.nodes.get(message.node))]);
    }
    if (message.cmd === "act") return act(message);
    return undefined;
  });
})();
