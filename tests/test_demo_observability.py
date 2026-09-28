"""Failures in the demo server must be visible, never silent. No paid APIs."""

import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

import jev_ultrafast.demo as demo


@pytest.fixture
def server(capsys):
    """A real demo server on an ephemeral port with a patched Host check."""
    saved = (demo.AGENT, demo.PORT, demo.ORIGIN)
    demo.AGENT = None
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    demo.PORT = port
    demo.ORIGIN = f"http://127.0.0.1:{port}"
    httpd = demo.ThreadingHTTPServer(("127.0.0.1", port), demo.Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()
    httpd.server_close()
    demo.AGENT, demo.PORT, demo.ORIGIN = saved


def call(base, name, body=None):
    request = urllib.request.Request(
        base + "/api/" + name,
        data=json.dumps(body or {}).encode(),
        headers={"Content-Type": "application/json", "X-Demo-Token": demo.TOKEN},
        method="POST",
    )
    try:
        response = urllib.request.urlopen(request, timeout=5)
        return response.status, response.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()


def test_requests_are_logged(server, capsys):
    """Every request leaves an access line on stderr; nothing is silent."""
    urllib.request.urlopen(server + "/api/state", timeout=5).read()
    captured = capsys.readouterr()
    assert "/api/state" in captured.err, "request was served but not logged"


def test_handler_errors_are_logged(server, capsys):
    """A rejected command logs the failure, not just the HTTP status."""
    code, _body = call(server, "predict", {})
    assert code == 400
    captured = capsys.readouterr()
    assert "predict" in captured.err, "handler error was converted to 400 but never logged"


def test_aborted_post_does_not_poison_server(server):
    """A client vanishing mid-request must not hold the server's LOCK forever."""
    sock = socket.create_connection(("127.0.0.1", demo.PORT), timeout=5)
    try:
        # Headers promise a body that never arrives (a browser tab just died).
        sock.sendall(
            b"POST /api/tick HTTP/1.1\r\n"
            + f"Host: 127.0.0.1:{demo.PORT}\r\n".encode()
            + b"Content-Type: application/json\r\n"
            + f"X-Demo-Token: {demo.TOKEN}\r\n".encode()
            + b"Content-Length: 64\r\n\r\n"
            + b'{"par'
        )
        # Keep the socket open, like a discarded page would: the server is now
        # stuck reading a body that will never complete.
        threading.Event().wait(0.5)
        # The stuck read must time out (Handler.timeout) and release the LOCK,
        # so the server recovers within seconds instead of staying poisoned.
        deadline = time.monotonic() + demo.Handler.timeout + 3
        code = 409
        while time.monotonic() < deadline:
            code, _body = call(server, "predict", {})
            if code != 409:
                break
            threading.Event().wait(0.5)
        assert code == 400, f"server did not recover from the aborted request: last {code}"
    finally:
        sock.close()
