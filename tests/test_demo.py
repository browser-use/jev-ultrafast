"""Offline contracts for demo configuration. No paid APIs."""

from jev_ultrafast import demo


def configure(tmp_path, monkeypatch, dotenv, shell_port=None):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TYPESAFE_DEMO_PORT", raising=False)
    monkeypatch.setattr(demo, "PORT", demo.DEFAULT_PORT)
    monkeypatch.setattr(demo, "ORIGIN", f"http://127.0.0.1:{demo.DEFAULT_PORT}")
    (tmp_path / ".env").write_text(dotenv)
    if shell_port is not None:
        monkeypatch.setenv("TYPESAFE_DEMO_PORT", shell_port)
    demo.configure_environment()


def test_main_binds_and_reports_the_dotenv_port(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TYPESAFE_DEMO_PORT", raising=False)
    monkeypatch.setattr(demo, "PORT", demo.DEFAULT_PORT)
    monkeypatch.setattr(demo, "ORIGIN", f"http://127.0.0.1:{demo.DEFAULT_PORT}")
    monkeypatch.setattr(demo.atexit, "register", lambda _callback: None)
    (tmp_path / ".env").write_text("TYPESAFE_DEMO_PORT=9000")
    addresses = []

    class Server:
        def __init__(self, address, _handler):
            addresses.append(address)

        def serve_forever(self):
            pass

        def server_close(self):
            pass

    monkeypatch.setattr(demo, "ThreadingHTTPServer", Server)
    demo.main()

    assert addresses == [("127.0.0.1", 9000)]
    assert capsys.readouterr().out == "Jev Ultrafast: http://127.0.0.1:9000\n"


def test_demo_port_keeps_its_default_when_unconfigured(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch, "")
    assert demo.PORT == demo.DEFAULT_PORT
    assert demo.ORIGIN == f"http://127.0.0.1:{demo.DEFAULT_PORT}"


def test_shell_port_takes_precedence_over_dotenv(tmp_path, monkeypatch):
    configure(tmp_path, monkeypatch, "TYPESAFE_DEMO_PORT=9000", shell_port="9100")
    assert demo.PORT == 9100
    assert demo.ORIGIN == "http://127.0.0.1:9100"
