"""Real-DOM inspector media-state regressions; no model calls."""
import json
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from jev_ultrafast.browser import Browser


def main():
    static = Path(__file__).resolve().parents[1] / "jev_ultrafast" / "static"
    idle = {"page": None, "status": "idle", "history": [], "decision": None}

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(static), **kwargs)

        def do_GET(self):
            if self.path == "/app.js":
                body = ((static / "app.js").read_text() +
                        "\nwindow.renderTest=s=>{state=s;render()};").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/javascript")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/api/state":
                body = json.dumps(idle).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                super().do_GET()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    browser = None
    try:
        browser = Browser(f"http://127.0.0.1:{server.server_port}/")
        deadline = time.monotonic() + 5
        while not browser.evaluate("typeof window.renderTest === 'function'"):
            assert time.monotonic() < deadline, "Inspector module did not load"
            time.sleep(0.02)
        original = browser.evaluate("document.getElementById('empty').innerHTML")
        page = {"url": "https://example.test", "title": "Example", "actions": [], "w": 100, "h": 100,
                "screenshot_error": "Screenshot timed out; page data is available."}
        state = {**idle, "status": "ready", "page": page, "elements": [], "elapsed_ms": 0}
        browser.evaluate("window.renderTest(" + json.dumps(state) + ")")
        assert browser.evaluate("document.getElementById('empty').innerHTML") == original
        assert browser.evaluate("!document.getElementById('screenshot-notice').hidden")
        assert browser.evaluate("document.getElementById('screenshot').hidden")
        # The media payload is irrelevant to visibility; this checks restoration of UI state.
        state["page"] = {**page, "screenshot": "test-frame"}
        browser.evaluate("window.renderTest(" + json.dumps(state) + ")")
        assert browser.evaluate("document.getElementById('screenshot-notice').hidden")
        assert browser.evaluate("!document.getElementById('screenshot').hidden")
        browser.evaluate("window.renderTest(" + json.dumps(idle) + ")")
        assert browser.evaluate("!document.getElementById('empty').hidden")
        assert browser.evaluate("document.getElementById('screenshot').hidden")
        assert browser.evaluate("document.getElementById('screenshot-notice').hidden")
        assert browser.evaluate("document.getElementById('targets').hidden")
        assert browser.evaluate("document.getElementById('empty').innerHTML") == original
        print("PASS: placeholder retained across screenshot timeout, recovery, and idle reset")
    finally:
        if browser:
            browser.close()
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
