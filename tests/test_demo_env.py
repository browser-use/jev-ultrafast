"""Cover .env decoding: plain utf-8, a BOM, and a locale-encoded fallback."""

import locale
from pathlib import Path

import pytest

from jev_ultrafast.demo import _read_env_text


def test_read_env_text_plain_utf8(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_bytes(b"PORT=8766\n")
    assert _read_env_text(path) == "PORT=8766\n"


def test_read_env_text_strips_utf8_bom(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_bytes("PORT=8766\n".encode("utf-8-sig"))
    assert _read_env_text(path) == "PORT=8766\n"


def test_read_env_text_falls_back_to_locale_codec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / ".env"
    text = "note=\u4e2d\u6587\n"
    path.write_bytes(text.encode("gbk"))
    monkeypatch.setattr(locale, "getpreferredencoding", lambda do_setlocale=False: "gbk")
    assert _read_env_text(path) == text
