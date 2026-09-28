"""Offline transport retries for model HTTP calls. No paid APIs."""

from unittest.mock import Mock

import httpx
import pytest

from jev_ultrafast import model


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("temporary"),
        httpx.ReadTimeout("slow"),
        httpx.RemoteProtocolError("Server disconnected without sending a response."),
    ],
)
def test_transient_connection_errors_are_retried(monkeypatch, error):
    success = Mock(status_code=200, is_error=False)
    success.json.return_value = {"ok": True}
    post = Mock(side_effect=[error, success])
    sleep = Mock()
    monkeypatch.setattr(model.CLIENT, "post", post)
    monkeypatch.setattr(model.time, "sleep", sleep)
    assert model.post_json("https://example.test/v1", "k", {}) == {"ok": True}
    assert post.call_count == 2
    assert [c.args for c in sleep.call_args_list] == [(0.5,)]


def test_exhausted_connection_errors_still_fail(monkeypatch):
    post = Mock(side_effect=httpx.ConnectError("down"))
    sleep = Mock()
    monkeypatch.setattr(model.CLIENT, "post", post)
    monkeypatch.setattr(model.time, "sleep", sleep)
    with pytest.raises(RuntimeError, match="Model connection failed"):
        model.post_json("https://example.test/v1", "k", {})
    assert post.call_count == 3
    assert [c.args for c in sleep.call_args_list] == [(0.5,), (1.0,)]


@pytest.mark.parametrize(
    "error",
    [httpx.UnsupportedProtocol("ftp"), httpx.TooManyRedirects("loop"), httpx.DecodingError("body")],
)
def test_permanent_errors_are_not_retried(monkeypatch, error):
    post = Mock(side_effect=error)
    sleep = Mock()
    monkeypatch.setattr(model.CLIENT, "post", post)
    monkeypatch.setattr(model.time, "sleep", sleep)
    with pytest.raises(RuntimeError, match="Model connection failed"):
        model.post_json("https://example.test/v1", "k", {})
    assert post.call_count == 1
    sleep.assert_not_called()
