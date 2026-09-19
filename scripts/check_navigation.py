"""Real-browser link settling regressions. Local fixtures only; no model calls."""

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from jev_ultrafast.browser import Browser, StalePage


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if urlsplit(self.path).path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/destination?via=redirect")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if urlsplit(self.path).path == "/destination":
            body = ("<!doctype html><title>Destination</title><p>Destination ready</p>"
                    "<script>window.clicks=Number(window.name);window.clickedAt=0</script>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        mode = parse_qs(urlsplit(self.path).query).get("case", ["delayed"])[0]
        target = ' target="_blank"' if mode == "new-tab" else ""
        if mode == "upper-self":
            target = ' target="_SELF"'
        if mode in {"named-self", "name-changed", "named-other"}:
            target = ' target="ReportPane"'
        base = '<base target="_blank">' if mode == "base-target" else ""
        if mode == "base-named-self":
            base = '<base target="ReportPane">'
        download = ' download="sample.txt"' if mode == "download" else ""
        tag = "button" if mode == "button" else "a"
        href = "#current" if mode == "same-url" else "#destination"
        if mode in {"document", "http-redirect"}:
            href = "/redirect" if mode == "http-redirect" else "/destination"
        delay = "0" if mode == "immediate" else "650"
        navigate = "" if mode in {"cancelled", "new-tab", "base-target", "download", "button", "named-other"} else (
            "setTimeout(()=>{history.pushState({},'',"
            + json.dumps("#redirected" if mode == "spa-redirect" else href)
            + ");document.getElementById('result').textContent='Destination ready'}," + delay + ")"
        )
        if mode in {"document", "http-redirect"}:
            navigate = "window.name=String(window.clicks);setTimeout(()=>location.assign(" + json.dumps(href) + "),650)"
        setup = ""
        if mode in {"named-self", "base-named-self", "name-changed", "named-other"}:
            setup = "window.name=" + json.dumps("reportpane" if mode == "named-other" else "ReportPane") + ";"
        body = (f"<!doctype html><title>Navigation fixture</title>{base}"
                f'<style>body{{margin:40px;font:18px sans-serif}}{tag}{{display:inline-block;padding:20px}}</style>'
                f'<{tag} id="link" href="{href}"{target}{download}>Open destination</{tag}>'
                '<p id="result">Source page</p><script>window.clicks=0;window.clickedAt=0;'
                + setup +
                "document.getElementById('link').onclick=e=>{e.preventDefault();window.clicks++;"
                "window.clickedAt=performance.now();" + navigate + "};</script>").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def check(url, mode):
    browser = Browser(url)
    try:
        page = browser.observe(screenshot=False)
        action = next(a for a in page["actions"] if a["label"] == "Open destination")
        if mode in {"target-changed", "name-changed"}:
            browser.evaluate("window.name='OtherPane'" if mode == "name-changed" else
                             "document.getElementById('link').target='_blank'")
            try:
                browser.act(action, page)
            except StalePage:
                assert browser.evaluate("window.clicks") == 0
                return
            raise AssertionError("Changed navigation target was not rejected")
        browser.evaluate("""window.optionScans=0;
          for(const proto of [Document.prototype,Element.prototype]) {
            const query=proto.querySelectorAll;
            proto.querySelectorAll=function(selector) {
              if(selector==='[role="option"]') window.optionScans++;
              return query.call(this,selector);
            };
          }""")
        source_document = browser.evaluate("performance.timeOrigin")
        browser.act(action, page)
        # This is the same post-action observer used by Agent, after execution is logged.
        current = browser.observe(screenshot=False)
        assert browser.evaluate("window.clicks") == 1, "Input must execute exactly once"
        elapsed = browser.evaluate("performance.now()-window.clickedAt")
        if mode in {"delayed", "spa-redirect", "immediate", "document", "upper-self", "named-self", "base-named-self"}:
            expected = {"document": "/destination", "spa-redirect": "#redirected"}.get(mode, "#destination")
            assert current["url"].endswith(expected), f"Observed the old document after {elapsed:.0f} ms"
            assert "Destination ready" in current["text"]
        elif mode == "http-redirect":
            assert current["url"].endswith("/destination?via=redirect"), current["url"]
            assert "Destination ready" in current["text"] and current["title"] == "Destination"
            assert browser.evaluate("performance.timeOrigin") != source_document, "Expected a new document"
        elif mode == "cancelled":
            assert current["url"] == page["url"]
            assert 1400 <= elapsed < 5000, f"Navigation wait was not bounded: {elapsed:.0f} ms"
        if mode in {"delayed", "cancelled"}:
            assert browser.evaluate("window.optionScans") == 0, "Link wait scanned autocomplete options"
        if mode in {"immediate", "new-tab", "base-target", "download", "button", "same-url", "named-other"}:
            assert elapsed < 1000, f"Fast-path observation waited unnecessarily: {elapsed:.0f} ms"
    finally:
        browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    cases = ["delayed", "spa-redirect", "immediate", "document", "upper-self", "cancelled", "new-tab", "base-target",
             "download", "button", "same-url", "target-changed", "http-redirect", "named-self",
             "base-named-self", "named-other", "name-changed"]
    parser.add_argument("--case", choices=cases)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for mode in [args.case] if args.case else cases:
            check(f"http://127.0.0.1:{server.server_port}/?case={mode}#current", mode)
            print("PASS:", mode, flush=True)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
