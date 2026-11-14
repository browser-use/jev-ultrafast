"""Real-browser busy-control settling regressions. Local fixtures only; no model calls."""

import argparse
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from jev_ultrafast.browser import Browser


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        mode = parse_qs(urlsplit(self.path).query).get("case", ["delayed"])[0]
        # The second `id="loading"` div carries an inline style so the `#loading` stylesheet
        # rule cannot hide it: duplicate ids are common on real pages, and getElementById only
        # ever returns the first, so one indicator lingers forever after the operation ends.
        # It is visible from load in the `sloppy` and `masked` cases.
        extra = ('<div id="loading" style="display:block">Still loading</div>'
                 if mode in {"sloppy", "masked"} else "")
        busy = '<div id="busywork" aria-busy="true">Working</div>' if mode == "masked" else ""
        if mode == "masked":
            # The pre-existing strong signal clears 1.5 s after the click, while the page
            # marker never changes: the lingering weak indicator must not then be mistaken
            # for new work and hold the settle to its cap.
            restore = "setTimeout(()=>document.getElementById('busywork').removeAttribute('aria-busy'),1500);"
            disable = ""
        elif mode == "stuck":
            restore, disable = "", "b.disabled=true;"
        else:
            restore = ("setTimeout(()=>{b.disabled=false;"
                       "document.getElementById('loading').style.display='none';"
                       "document.getElementById('sentinel').disabled=false;},1500);")
            disable = "b.disabled=true;"
        show = "" if mode == "masked" else "document.getElementById('loading').style.display='block';"
        body = (f"<!doctype html><title>Busy fixture</title>"
                f'<style>body{{margin:40px;font:18px sans-serif}}button{{padding:20px}}'
                f'#loading{{display:none}}</style>'
                f'<button id="go">{"Refresh" if mode == "masked" else "Enable"}</button>'
                f'<label>Sentinel <input id="sentinel" disabled></label>'
                f'{busy}<div id="loading">Loading</div>{extra}'
                f"<script>document.getElementById('go').onclick=e=>{{"
                f"window.clicks=(window.clicks||0)+1;window.clickedAt=performance.now();"
                f"const b=e.currentTarget;{disable}{show}{restore}}};</script>").encode()
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
        action = next(a for a in page["actions"] if a["label"] == ("Refresh" if mode == "masked" else "Enable"))
        assert not any(a["label"] == "Sentinel" for a in page["actions"]), "Disabled field must not be offered"
        started = time.monotonic()
        browser.act(action, page)
        # This is the same post-action observer used by Agent, after execution is logged.
        current = browser.observe(screenshot=False)
        elapsed = time.monotonic() - started
        assert browser.evaluate("window.clicks") == 1, "Input must execute exactly once"
        if mode == "stuck":
            # A page that is busy forever must keep the wait bounded, not hang or skip it.
            assert 8.0 <= elapsed < 9.5, f"Busy-forever wait was not capped as designed: {elapsed:.1f} s"
            assert not any(a["label"] == "Sentinel" for a in current["actions"])
        elif mode == "masked":
            # The strong signal clearing is the only event; the settle must release on it.
            assert 1.5 <= elapsed < 4.0, f"Stale weak indicator held the settle: {elapsed:.1f} s"
        else:
            sentinel = next(a for a in current["actions"] if a["label"] == "Sentinel")
            assert sentinel["kind"] == "fill", "Field enabled by the handler must be offered as fillable"
            assert elapsed < 8.0, f"Settle outlived its cap: {elapsed:.1f} s"
    finally:
        browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    cases = ["delayed", "sloppy", "masked", "stuck"]
    parser.add_argument("--case", choices=cases)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        for mode in [args.case] if args.case else cases:
            check(f"http://127.0.0.1:{server.server_port}/?case={mode}", mode)
            print("PASS:", mode, flush=True)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
