"""Local-browser trace export regressions; simulated state, no model or website calls."""

import re
import time
from pathlib import Path
from urllib.parse import quote

from jev_ultrafast.browser import Browser

CHECKS = """
const page = {url:'https://example.test/', title:'Example', text:'Ready',
  actions:[], screenshot:'not exported', w:1120, h:780};
const decision = {choice:'DONE', operation:'DONE', target:null,
  target_probabilities:{}, operation_probabilities:{DONE:1}, latency_ms:5,
  request:{state:{page:{text:'Ready'}}}, raw_answers:{operation:{choice:'DONE'}}};
const ready = {page, status:'ready', history:[], decisions:[], decision:null,
  elements:[], plan:[], goal:'Inspect the page', elapsed_ms:0};
const cases = [
  ['idle', {page:null, status:'idle', history:[], decision:null}, false],
  ['observed', ready, false],
  ['predicted', {...ready, status:'predicted', decisions:[decision], decision}, true],
  ['done', {...ready, status:'done', decisions:[decision]}, true],
  ['blocked', {...ready, status:'blocked', decisions:[{...decision,
    choice:'BLOCKED', operation:'BLOCKED', operation_probabilities:{BLOCKED:1},
    raw_answers:{operation:{choice:'BLOCKED'}}}]}, true],
  ['executed', {...ready, decisions:[{...decision, choice:'wait', operation:'WAIT',
    operation_probabilities:{WAIT:1}, raw_answers:{operation:{choice:'WAIT'}}}],
    history:[{step:1, action:'Wait for the page to update', probability:1,
      latency_ms:5, page_changed:false}]}, true],
  ['reset', ready, false],
];
window.exportChecks = [];
for (const [name, sample, enabled] of cases) {
  state = sample;
  render();
  window.exportBlob = null;
  $('download').click();
  const saved = window.exportBlob ? JSON.parse(await window.exportBlob.text()) : null;
  const expected = {...sample, page:{...sample.page}};
  delete expected.page.screenshot;
  window.exportChecks.push({name, enabled, disabled:$('download').disabled, saved, expected});
}
window.exportChecksDone = true;
"""


def main():
    assets = Path(__file__).resolve().parents[1] / "jev_ultrafast" / "static"
    html = re.sub(r"<link\b[^>]*>", "", (assets / "index.html").read_text(encoding="utf-8"))
    # Supply state locally and intercept only the file-save boundary. The real
    # button, app handlers, Blob creation and JSON serialization run in Chrome.
    setup = """<script>
      window.fetch = () => new Promise(() => {});
      URL.createObjectURL = blob => {window.exportBlob = blob; return 'blob:test'};
      URL.revokeObjectURL = () => {};
      HTMLAnchorElement.prototype.click = () => {};
    </script>"""
    script = '<script type="module" src="/app.js"></script>'
    html = html.replace(script, setup + '<script type="module">' +
                        (assets / "app.js").read_text(encoding="utf-8") + CHECKS + '</script>')
    browser = Browser("data:text/html," + quote(html))
    try:
        deadline = time.monotonic() + 5
        while not browser.evaluate("window.exportChecksDone === true") and time.monotonic() < deadline:
            time.sleep(0.02)
        checks = browser.evaluate("window.exportChecks")
        assert browser.evaluate("window.exportChecksDone === true"), "Inspector checks did not finish"
        failures = [check["name"] for check in checks
                    if check["disabled"] == check["enabled"] or
                    check["saved"] != (check["expected"] if check["enabled"] else None)]
        assert not failures, f"Failed export cases: {failures}"
        print(f"PASS: {len(checks)} inspector export checks; no model calls")
    finally:
        browser.close()


if __name__ == "__main__":
    main()
