"""A real navigation with a delayed response body, using a local server and no model calls."""

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from jev_ultrafast.browser import Browser


class Handler(BaseHTTPRequestHandler):
    visits = 0

    def do_GET(self):
        if self.path == "/slow":
            Handler.visits += 1
            head = b"<!doctype html><html><head><title>Arrived</title>"
            body = b"</head><body><p>Destination loaded</p><input aria-label='New field'></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(head) + len(body)))
            self.end_headers()
            self.wfile.write(head)
            self.wfile.flush()
            time.sleep(0.7)
            self.wfile.write(body)
        else:
            body = b"<!doctype html><title>Start</title><a href='/slow'>Continue</a>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *_args):
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    browser = None
    try:
        browser = Browser(f"http://127.0.0.1:{server.server_port}/")
        page = browser.observe(screenshot=False)
        action = next(a for a in page["actions"] if a["label"] == "Continue")
        browser.act(action, page)
        arrived = browser.observe(screenshot=False)
        assert arrived["url"].endswith("/slow")
        assert "Destination loaded" in arrived["text"]
        assert any(a["kind"] == "fill" and a["label"] == "New field" for a in arrived["actions"])
        assert Handler.visits == 1
        assert not browser.fresh(page, action)
        print("PASS: delayed document body becomes observable after one navigation; no input replay")
    finally:
        if browser:
            browser.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


if __name__ == "__main__":
    main()
