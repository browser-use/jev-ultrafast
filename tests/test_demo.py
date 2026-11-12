"""Local inspector resource routes; no browser or model calls."""

import threading
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer

import pytest

from jev_ultrafast import demo


@pytest.fixture
def inspector(tmp_path, monkeypatch):
    root = tmp_path / "jev_ultrafast"
    root.mkdir()
    monkeypatch.setattr(demo, "ROOT", root)
    server = ThreadingHTTPServer(("127.0.0.1", 0), demo.Handler)
    monkeypatch.setattr(demo, "PORT", server.server_port)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    worker.start()
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=2)
    try:
        yield root, connection
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


@pytest.mark.parametrize("location", ["static/demo.mp4", "../docs/demo.mp4"])
def test_demo_video_is_served_from_installed_and_source_layouts(inspector, location):
    root, connection = inspector
    video = root / location
    video.parent.mkdir(parents=True)
    video.write_bytes(b"local demo recording")

    connection.request("GET", "/demo.mp4")
    response = connection.getresponse()
    assert response.status == 200
    assert response.getheader("Content-Type") == "video/mp4"
    assert response.read() == video.read_bytes()


@pytest.mark.parametrize("path,host,status", [
    ("/demo.mp4", None, 404),
    ("/missing", None, 404),
    ("/demo.mp4", "example.test", 403),
])
def test_missing_resources_and_invalid_hosts_still_fail(inspector, path, host, status):
    _, connection = inspector
    connection.request("GET", path, headers={"Host": host} if host else {})
    response = connection.getresponse()
    assert response.status == status
    response.read()
