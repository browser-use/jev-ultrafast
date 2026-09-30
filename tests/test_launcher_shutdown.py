import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest
import websockets.sync.client as ws_client

LAUNCHER = Path(__file__).resolve().parents[1] / "jev_browser.py"

INFO = {"webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/browser/abc"}


class Proc:
    def __init__(self):
        self.terminated = 0

    def terminate(self):
        self.terminated += 1


def launcher(monkeypatch, popen=None, run=None):
    """Load the root launcher and swap the two modules it reaches outside itself."""
    spec = importlib.util.spec_from_file_location("jev_browser", LAUNCHER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "time", types.SimpleNamespace(sleep=lambda *_a: None))
    monkeypatch.setattr(
        mod,
        "subprocess",
        types.SimpleNamespace(DEVNULL=None, Popen=popen or (lambda *a, **k: Proc()), run=run),
    )
    return mod


def test_an_instance_that_was_already_up_comes_back_without_a_handle(monkeypatch):
    started = []
    mod = launcher(
        monkeypatch, popen=lambda *a, **k: started.append(a) or Proc()
    )
    monkeypatch.setattr(mod, "version_info", lambda: INFO)
    assert mod.ensure_browser() == (INFO, None)
    assert started == []


def test_starting_one_hands_back_the_process_so_later_we_can_close_it(monkeypatch):
    proc = Proc()
    mod = launcher(monkeypatch, popen=lambda *a, **k: proc)
    answers = [None, INFO]
    monkeypatch.setattr(mod, "version_info", lambda: answers.pop(0))
    assert mod.ensure_browser() == (INFO, proc)


def test_leaving_does_not_touch_an_instance_this_launcher_did_not_start(monkeypatch):
    mod = launcher(monkeypatch)
    asked = []
    monkeypatch.setattr(mod, "close_instance", lambda url: asked.append(url))
    mod.leave(INFO, None)
    assert asked == []


def test_the_close_request_asks_the_browser_itself_to_exit(monkeypatch):
    mod = launcher(monkeypatch)
    sent = []

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def send(self, frame):
            sent.append(json.loads(frame))

    monkeypatch.setattr(ws_client, "connect", lambda url, **k: Socket())
    mod.close_instance(INFO["webSocketDebuggerUrl"])
    assert sent == [{"id": 1, "method": "Browser.close"}]


def test_a_socket_that_dies_with_the_browser_counts_as_closed(monkeypatch):
    mod = launcher(monkeypatch)

    def refuse(url, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(ws_client, "connect", refuse)
    mod.close_instance(INFO["webSocketDebuggerUrl"])


def test_a_port_that_frees_is_reported_and_nobody_is_terminated(monkeypatch, capsys):
    mod = launcher(monkeypatch)
    proc = Proc()
    monkeypatch.setattr(mod, "close_instance", lambda url: None)
    busy = [True, False]
    monkeypatch.setattr(mod, "port_busy", lambda port: busy.pop(0))
    mod.leave(INFO, proc)
    assert proc.terminated == 0
    assert f"port {mod.PORT} closed" in capsys.readouterr().out


def test_a_port_that_stays_busy_means_the_request_never_landed(monkeypatch):
    mod = launcher(monkeypatch)
    proc = Proc()
    monkeypatch.setattr(mod, "close_instance", lambda url: None)
    monkeypatch.setattr(mod, "port_busy", lambda port: True)
    mod.leave(INFO, proc)
    assert proc.terminated == 1


def test_leaving_the_panel_runs_the_teardown_even_when_the_server_raises(monkeypatch):
    mod = launcher(monkeypatch)
    proc = Proc()
    left = []
    monkeypatch.setattr(mod, "ensure_browser", lambda: (INFO, proc))
    monkeypatch.setattr(mod, "leave", lambda info, handle: left.append((info, handle)))
    monkeypatch.setattr(mod, "port_busy", lambda port: False)

    def leaving():
        raise KeyboardInterrupt

    fake = types.ModuleType("jev_ultrafast.demo")
    fake.main = leaving
    monkeypatch.setitem(sys.modules, "jev_ultrafast.demo", fake)
    monkeypatch.setattr(sys, "argv", ["jev_browser.py"])
    with pytest.raises(KeyboardInterrupt):
        mod.main()
    assert left == [(INFO, proc)], "the panel leaving must reach the browser teardown"


def test_exec_leaves_the_instance_up(monkeypatch):
    left = []
    mod = launcher(monkeypatch, run=lambda *a, **k: types.SimpleNamespace(returncode=0))
    monkeypatch.setattr(mod, "ensure_browser", lambda: (INFO, Proc()))
    monkeypatch.setattr(mod, "leave", lambda *a: left.append(a))
    monkeypatch.setattr(sys, "argv", ["jev_browser.py", "exec", "pytest"])
    with pytest.raises(SystemExit) as exit_info:
        mod.main()
    assert exit_info.value.code == 0
    assert left == [], "a one-off command is not leaving the panel"
