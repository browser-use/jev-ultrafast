"""Real isolated-browser DOM-root regressions; no model calls or screenshots."""

import json
import unittest
from unittest.mock import patch
from urllib.parse import quote

from jev_ultrafast import browser as browser_module
from jev_ultrafast.browser import Browser, StalePage


class DOMRoots(unittest.TestCase):
    def setUp(self):
        self.b = Browser(
            "data:text/html,"
            + quote('<!doctype html><body style="margin:35px"><span id="label">Wrong root</span><div id="host"></div>')
        )
        result = self.b.call(
            "Runtime.evaluate",
            awaitPromise=True,
            returnByValue=True,
            expression="""
        (async()=>{
          const controls=prefix=>`<span id="label">${prefix} field</span><input aria-labelledby="label">
            <button onclick="this.dataset.clicks=+(this.dataset.clicks||0)+1">${prefix} button</button>
            <select aria-label="${prefix} choice"><option>A</option><option>B</option></select>`;
          const s=document.querySelector('#host').attachShadow({mode:'open'});
          s.innerHTML=controls('Shadow')+'<div id="nested"></div>'+
            '<iframe style="display:block;width:800px;height:380px;border:7px solid;margin:20px"></iframe>';
          s.querySelector('#nested').attachShadow({mode:'open'}).innerHTML=controls('Nested');
          const f=s.querySelector('iframe');
          await new Promise(resolve=>{f.onload=resolve;f.srcdoc='<!doctype html><body>'+controls('Frame')+
            '<div id="inner"></div><iframe style="width:700px;height:140px"></iframe>';});
          f.contentDocument.querySelector('#inner').attachShadow({mode:'open'}).innerHTML=controls('Frame shadow');
          const nested=f.contentDocument.querySelector('iframe');
          await new Promise(resolve=>{nested.onload=resolve;
            nested.srcdoc='<!doctype html><body>'+controls('Nested frame');});
          window.testRoots=[s,s.querySelector('#nested').shadowRoot,f.contentDocument,f.contentDocument.querySelector('#inner').shadowRoot,nested.contentDocument];
          return true;
        })()""",
        )
        self.assertNotIn("exceptionDetails", result)

    def tearDown(self):
        self.b.close()

    def action(self, label, kind):
        page = self.b.observe(screenshot=False)
        matches = [a for a in page["actions"] if a["label"] == label and a["kind"] == kind]
        self.assertEqual(len(matches), 1, (label, page["actions"]))
        return page, matches[0]

    def test_nested_roots_names_identity_and_real_input(self):
        nodes = []
        for i, prefix in enumerate(["Shadow", "Nested", "Frame", "Frame shadow", "Nested frame"]):
            for kind, suffix in [("fill", " field"), ("click", " button"), ("select", " choice → B")]:
                page, action = self.action(prefix + suffix, kind)
                nodes.append(action["node"])
                self.b.act(action, page, text="actual input")
                selector = {"fill": "input", "click": "button", "select": "select"}[kind]
                prop = {"fill": "value", "click": "dataset.clicks", "select": "value"}[kind]
                value = self.b.evaluate(f"testRoots[{i}].querySelector({json.dumps(selector)}).{prop}")
                self.assertEqual(value, {"fill": "actual input", "click": "1", "select": "B"}[kind])
        self.assertEqual(len(nodes), len(set(nodes)))

    def test_frame_replacement_and_navigation_invalidate(self):
        for mutation in [
            "testRoots[2].querySelector('button').outerHTML=testRoots[2].querySelector('button').outerHTML",
            "testRoots[0].querySelector('iframe').remove()",
        ]:
            page, action = self.action("Frame button", "click")
            self.b.evaluate(mutation)
            self.assertFalse(self.b.fresh(page, action))
        # Detached frame documents can retain connected nodes; they must not remain actionable.
        self.assertFalse(any(a["label"] == "Frame button" for a in self.b.observe(False)["actions"]))

    def test_nested_form_value_invalidates_topologically_shared_guard(self):
        page, action = self.action("Shadow button", "click")
        self.b.evaluate("testRoots[2].querySelector('input').value='changed'")
        self.assertFalse(self.b.fresh(page, action))

    def test_overlays_and_hidden_hosts_block_without_mutation(self):
        for expression in [
            "document.body.insertAdjacentHTML('beforeend','<div style=\"position:fixed;inset:0;z-index:9999\"></div>')",
            "testRoots[2].body.insertAdjacentHTML('beforeend',"
            "'<div style=\"position:fixed;inset:0;z-index:9999\"></div>')",
        ]:
            page, action = self.action("Frame button", "click")
            self.b.evaluate(expression)
            with self.assertRaises((StalePage, RuntimeError)):
                self.b.act(action, page)
            self.assertIsNone(self.b.evaluate("testRoots[2].querySelector('button').dataset.clicks"))
            self.b.evaluate(
                "document.body.lastElementChild.id==='host'||document.body.lastElementChild.remove();testRoots[2].body.querySelector('div[style]')?.remove()"
            )
        self.b.evaluate("document.querySelector('#host').setAttribute('inert','')")
        self.assertFalse(any(a.get("node") for a in self.b.observe(False)["actions"]))

    def test_root_text_and_nearby_shadow_context(self):
        self.b.evaluate(
            "testRoots[0].append(Object.assign(document.createElement('p'),{textContent:'Local total 123'}))"
        )
        page, action = self.action("Shadow button", "click")
        self.assertIn("Local total 123", page["text"])
        self.assertIn("Frame field", page["text"])
        self.b.evaluate("testRoots[0].querySelector('p').textContent='Local total 456'")
        self.assertFalse(self.b.fresh(page, action))

    def test_shadow_host_itself_can_be_clicked(self):
        self.b.evaluate(
            "const host=document.createElement('div');host.setAttribute('role','button');"
            "host.setAttribute('aria-label','Host button');host.style.cssText='width:100px;height:30px';"
            "host.onclick=()=>host.dataset.clicked='yes';document.body.prepend(host);"
            "host.attachShadow({mode:'open'})"
        )
        page, action = self.action("Host button", "click")
        self.b.act(action, page)
        self.assertEqual(self.b.evaluate("document.body.firstElementChild.dataset.clicked"), "yes")

    def test_actual_frame_document_navigation(self):
        page, action = self.action("Frame button", "click")
        self.b.call(
            "Runtime.evaluate",
            awaitPromise=True,
            returnByValue=True,
            expression="""
          new Promise(resolve=>{const f=testRoots[0].querySelector('iframe');
          f.onload=()=>resolve(true);f.srcdoc='<button>Replacement document</button>'})""",
        )
        self.assertFalse(self.b.fresh(page, action))
        with self.assertRaises(StalePage):
            self.b.act(action, page)

    def test_transformed_frame_fails_closed(self):
        self.b.evaluate("testRoots[0].querySelector('iframe').style.transform='scale(.8)'")
        page = self.b.observe(False)
        self.assertFalse(any(a["label"] == "Frame button" for a in page["actions"]))
        self.assertTrue(page["unsupported_frames"])

    def test_select_interruption_is_not_retryable(self):
        page, action = self.action("Frame choice → B", "select")
        self.b.evaluate(
            "testRoots[2].querySelector('select').addEventListener('input',()=>testRoots[0].querySelector('iframe').remove())"
        )
        try:
            self.b.act(action, page)
        except RuntimeError:
            pass
        except StalePage:
            self.fail("Post-mutation interruption must never be retryable")
        self.assertEqual(self.b.evaluate("testRoots[2].querySelector('select').value"), "B")

    def test_padded_frame_never_clicks_decoy(self):
        result = self.b.call(
            "Runtime.evaluate", awaitPromise=True, returnByValue=True,
            expression="""new Promise(resolve=>{
              document.body.innerHTML='';window.clicks={target:0,decoy:0};
              const f=document.createElement('iframe');window.paddedFrame=f;
              f.style.cssText='width:300px;height:180px;border:7px solid;padding:40px';
              f.onload=()=>resolve(true);
              f.srcdoc=`<body style="margin:0"><button aria-label="Padding target"
                style="position:absolute;left:60px;top:60px;width:20px;height:20px;padding:0"
                onclick="parent.clicks.target++">T</button><button aria-label="Padding decoy"
                style="position:absolute;left:20px;top:20px;width:20px;height:20px;padding:0"
                onclick="parent.clicks.decoy++">D</button>`;
              document.body.append(f);
            })""",
        )
        self.assertNotIn("exceptionDetails", result)
        page = self.b.observe(False)
        actions = [a for a in page["actions"] if a["label"] == "Padding target"]
        # On the old implementation this sends a real click to the decoy.
        if actions:
            self.b.act(actions[0], page)
        counts = self.b.evaluate("clicks")
        print("padded frame actual click counts:", counts, flush=True)
        self.assertEqual(counts, {"target": 0, "decoy": 0})
        self.assertFalse(actions)
        self.assertTrue(any(f["reason"] == "unsupported frame geometry" for f in page["unsupported_frames"]))
        # Geometry can also change after observation: reject before any input.
        self.b.evaluate("paddedFrame.style.padding='0'")
        page, action = self.action("Padding target", "click")
        self.b.evaluate("paddedFrame.style.padding='40px'")
        with patch.object(browser_module, "cdp", wraps=browser_module.cdp) as protocol:
            with self.assertRaises(StalePage):
                self.b.act(action, page)
        inputs = [c.args[0] for c in protocol.call_args_list if c.args[0].startswith("Input.")]
        print("padding added after observation, input protocol calls:", inputs, flush=True)
        self.assertEqual(inputs, [])
        self.assertEqual(self.b.evaluate("clicks"), {"target": 0, "decoy": 0})

    def test_shadow_select_input_composes_once_change_stays_local(self):
        self.b.evaluate("""window.selectEvents={hostInput:0,localInput:0,hostChange:0,localChange:0};
          const host=document.querySelector('#host'), select=testRoots[0].querySelector('select');
          host.addEventListener('input',()=>selectEvents.hostInput++);
          host.addEventListener('change',()=>selectEvents.hostChange++);
          select.addEventListener('input',()=>selectEvents.localInput++);
          select.addEventListener('change',()=>selectEvents.localChange++);""")
        page, action = self.action("Shadow choice → B", "select")
        self.b.act(action, page)
        events = self.b.evaluate("selectEvents")
        print("shadow SELECT event counts:", events, flush=True)
        self.assertEqual(self.b.evaluate("testRoots[0].querySelector('select').value"), "B")
        self.assertEqual(events, {"hostInput": 1, "localInput": 1, "hostChange": 0, "localChange": 1})

    def test_long_frame_text_uses_text_rect_not_parent_center(self):
        self.b.evaluate("""testRoots[2].body.innerHTML='Visible first frame text'+
          '<div style="height:2400px"></div>Offscreen last frame text';""")
        page = self.b.observe(False)
        print("long frame observed text:", repr(page["text"]), flush=True)
        self.assertIn("Visible first frame text", page["text"])
        self.assertNotIn("Offscreen last frame text", page["text"])
        # Local visibility alone is insufficient when the frame is offscreen upstairs.
        self.b.evaluate("testRoots[0].querySelector('iframe').style.marginTop='2000px'")
        self.assertNotIn("Visible first frame text", self.b.observe(False)["text"])

    def test_cross_origin_frame_is_safe_and_reported(self):
        result = self.b.call(
            "Runtime.evaluate",
            awaitPromise=True,
            returnByValue=True,
            expression="""
          new Promise(resolve=>{const f=document.createElement('iframe');f.sandbox='';
          f.onload=()=>resolve(true);f.srcdoc='<button>Opaque button</button>';document.body.append(f)})""",
        )
        self.assertNotIn("exceptionDetails", result)
        page = self.b.observe(False)
        self.assertFalse(any(a["label"] == "Opaque button" for a in page["actions"]))
        self.assertTrue(page.get("unsupported_frames"), "Unsupported frames must be explicitly reported")


if __name__ == "__main__":
    unittest.main(verbosity=2)
