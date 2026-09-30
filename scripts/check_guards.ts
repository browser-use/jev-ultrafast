/** Local-browser freshness/execution regressions. No model calls or external websites. */

import { assert, assertEquals, AssertionError } from "@std/assert";
import { type Action, Browser, JevError, type PageState } from "../src/mod.ts";

const HTML = `<!doctype html><title>Guard checks</title>
<style>body{margin:30px}button{width:180px;height:50px}#outside{position:absolute;top:3000px}</style>
<p id="context">Cart total: $10</p>
<button id="target" onclick="window.clicks=(window.clicks||0)+1">Continue</button>
<label>City<input id="field" value="Zurich"></label>
<label><input id="toggle" type="checkbox">Refundable</label>
<select aria-label="Category"><option>All</option><option>Design</option></select>
<p id="outside">Unrelated offscreen text</p>`;

type Loose = { kind: string; label: string; role?: string; value?: unknown };
const loose = (a: Action) => a as unknown as Loose;

function find(page: PageState, predicate: (a: Loose) => boolean): Action {
  const action = page.actions.find((a) => predicate(loose(a)));
  if (!action) throw new Error("No matching observed action");
  return action;
}

async function main(): Promise<void> {
  const browser = await Browser.open("data:text/html," + encodeURIComponent(HTML));
  const passed: string[] = [];
  try {
    let page = await browser.observe({ screenshot: false });
    let action = find(page, (a) => a.label === "Continue");
    await browser.evaluate("document.querySelector('#target').style.transform='translateX(200px)'");
    assert(await browser.fresh(page), "Movement should use fresh geometry, not another model call");
    await browser.act(action, page);
    assertEquals(await browser.evaluate("window.clicks"), 1);
    passed.push("moving target clicked at its current location");

    await browser.evaluate("document.querySelector('#outside').textContent='Updated outside the viewport'");
    assert(await browser.fresh(page));
    passed.push("unrelated offscreen text does not invalidate");

    const mutations: Record<string, string> = {
      "visible context": "document.querySelector('#context').textContent='Cart total: $100'",
      "accessible label": "document.querySelector('#target').setAttribute('aria-label','Delete account')",
      "field property": "document.querySelector('#field').value='London'",
      "checkbox property": "document.querySelector('#toggle').checked=true",
      "disabled target": "document.querySelector('#target').disabled=true",
      "read-only field": "document.querySelector('#field').readOnly=true",
      "hidden target": "document.querySelector('#target').style.display='none'",
      "replaced node": "document.querySelector('#target').outerHTML=document.querySelector('#target').outerHTML",
      "dropdown option": "document.querySelector('select').options[1].text='Coastal'",
    };
    for (const [label, expression] of Object.entries(mutations)) {
      await browser.evaluate(
        "document.querySelector('#target').style.display='block'; " +
          "document.querySelector('#target').disabled=false",
      );
      page = await browser.observe({ screenshot: false });
      await browser.evaluate(expression);
      assert(!(await browser.fresh(page)), label);
      passed.push(label + " invalidates");
    }

    await browser.evaluate(
      "document.querySelector('#target').disabled=false; " +
        "document.querySelector('#target').style.display='block'",
    );
    page = await browser.observe({ screenshot: false });
    action = find(page, (a) => a.label === "Delete account");
    // A textless overlay does not alter the model's semantic state, but must block a click.
    await browser.evaluate(
      "const cover=document.createElement('div'); " +
        "cover.style.cssText='position:fixed;inset:0;z-index:9999;background:white'; " +
        "document.body.append(cover)",
    );
    assert(await browser.fresh(page));
    let clicked = false;
    try {
      await browser.act(action, page);
      clicked = true;
    } catch (error) {
      // StalePage extends JevError; anything else is a real failure.
      if (!(error instanceof JevError)) throw error;
    }
    if (clicked) throw new AssertionError("Covered target was clicked");
    assertEquals(await browser.evaluate("window.clicks"), 1);
    passed.push("overlay blocked before input");

    await browser.evaluate(
      "document.body.innerHTML=" + JSON.stringify(`
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
        `),
    );
    page = await browser.observe({ screenshot: false });
    let buy = find(page, (a) => a.label === "Buy");
    await browser.evaluate("document.querySelector('#unrelated').textContent='New unrelated news'");
    assert(await browser.fresh(page, buy));
    assert(!(await browser.fresh(page)));
    passed.push("click guard accepts unrelated visible updates; terminal guard rejects them");
    const guarded: Record<string, string> = {
      "nearby price": "document.querySelector('#price').textContent='Total $100'",
      "form value": "document.querySelector('#query').value='changed'",
      "form toggle": "document.querySelector('#check').checked=true",
      "target replacement": "document.querySelector('#buy').outerHTML=document.querySelector('#buy').outerHTML",
    };
    for (const [label, expression] of Object.entries(guarded)) {
      page = await browser.observe({ screenshot: false });
      buy = find(page, (a) => a.label === "Buy");
      await browser.evaluate(expression);
      assert(!(await browser.fresh(page, buy)), label);
      passed.push(label + " invalidates action-specific guard");
    }

    page = await browser.observe({ screenshot: false });
    const actions = page.actions.map(loose);
    const kinds = (items: Loose[]) => [...new Set(items.map((a) => a.kind))];
    for (const role of ["checkbox", "radio"]) {
      assertEquals(kinds(actions.filter((a) => a.role === role)), ["click"]);
    }
    assertEquals(kinds(actions.filter((a) => a.label === "Read only")), ["click"]);
    assert(!actions.some((a) => a.label === "Disabled" || a.value === "never expose this"));
    assertEquals(actions.filter((a) => a.kind === "select").map((a) => a.value), ["Design"]);
    passed.push("native controls expose only supported operations and safe values");

    const select = find(page, (a) => a.kind === "select");
    await browser.act(select, page);
    assertEquals(await browser.evaluate("document.querySelector('#category').value"), "Design");
    passed.push("native dropdown selects an observed option");

    await browser.evaluate(
      "document.querySelector('#query').addEventListener('input',()=>setTimeout(()=>{" +
        "document.querySelector('#suggestions').innerHTML='<div role=option>Generated</div>'" +
        "},60))",
    );
    page = await browser.observe({ screenshot: false });
    const field = find(page, (a) => a.kind === "fill");
    await browser.act(field, page, "Generated");
    page = await browser.observe({ screenshot: false });
    const value = await browser.evaluate("document.querySelector('#query').value");
    assert(value === "Generated", JSON.stringify(value));
    assert(page.actions.some((a) => loose(a).role === "option"));
    passed.push("real text input waits for asynchronous combobox suggestions");
    await browser.call("Page.navigate", { url: "about:blank" });
    assert(!(await browser.fresh(page, field)));
    passed.push("navigation invalidates the old document");
  } finally {
    await browser.close();
  }
  console.log(passed.join("\n"));
  console.log(`PASS: ${passed.length} browser guard checks; no model calls`);
}

if (import.meta.main) await main();
