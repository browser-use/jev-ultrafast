"""Offline contracts for .env loading. No paid APIs."""

import os

import pytest

from jev_ultrafast.demo import load_environment


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Write a .env in an isolated working directory and load it into a clean environment."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(os, "environ", {})

    def load(text):
        data = text if isinstance(text, bytes) else text.encode("utf-8")
        (tmp_path / ".env").write_bytes(data)
        load_environment()
        return os.environ

    return load


def test_a_plain_entry_loads(env):
    assert env("TYPESAFE_API_KEY=abc123")["TYPESAFE_API_KEY"] == "abc123"


@pytest.mark.parametrize("quote", ['"', "'"])
def test_quotes_delimit_the_value_and_are_not_part_of_it(env, quote):
    """A quoted model name was reaching the provider with its quotes attached."""
    loaded = env(f"TEXT_MODEL={quote}inception/mercury-2.5{quote}")
    assert loaded["TEXT_MODEL"] == "inception/mercury-2.5"


def test_trailing_whitespace_is_not_part_of_the_value(env):
    """A trailing space turned the base URL into one the provider never matches."""
    loaded = env("TEXT_MODEL_BASE_URL=https://openrouter.ai/api/v1   ")
    assert loaded["TEXT_MODEL_BASE_URL"] == "https://openrouter.ai/api/v1"


def test_whitespace_inside_quotes_survives(env):
    assert env('GREETING="  padded  "')["GREETING"] == "  padded  "


def test_an_indented_key_still_loads(env):
    assert env("   TEXT_MODEL=deepseek-chat")["TEXT_MODEL"] == "deepseek-chat"


def test_an_exported_entry_still_loads(env):
    """`export KEY=value` used to register a key literally named 'export KEY'."""
    loaded = env("export TEXT_MODEL_REASONING=none")
    assert loaded["TEXT_MODEL_REASONING"] == "none"
    assert "export TEXT_MODEL_REASONING" not in loaded


def test_comments_and_blank_lines_are_skipped(env):
    loaded = env("# Required for TYPE_TEXT.\n\n   \nTEXT_MODEL_API_KEY=key\n")
    assert loaded["TEXT_MODEL_API_KEY"] == "key"
    assert [key for key in loaded if key.startswith("#") or not key.strip()] == []


def test_a_value_may_contain_separators(env):
    """Only the first = splits, and # never starts a comment inside a value."""
    loaded = env("TOKEN=abc=def#ghi")
    assert loaded["TOKEN"] == "abc=def#ghi"


def test_an_empty_value_is_kept(env):
    assert env("TEXT_MODEL_API_KEY=")["TEXT_MODEL_API_KEY"] == ""


def test_a_real_environment_variable_wins(env, monkeypatch):
    monkeypatch.setattr(os, "environ", {"TYPESAFE_API_KEY": "from-the-shell"})
    assert env("TYPESAFE_API_KEY=from-the-file")["TYPESAFE_API_KEY"] == "from-the-shell"


def test_the_file_is_read_as_utf8(env):
    """Without an explicit encoding this raised on any Windows locale that is not UTF-8."""
    assert env("GOAL=Zürich → London")["GOAL"] == "Zürich → London"


def test_a_missing_file_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(os, "environ", {})
    load_environment()
    assert "TYPESAFE_API_KEY" not in os.environ


def test_a_byte_order_mark_is_not_part_of_the_first_key(env):
    """Editors on Windows save UTF-8 with a BOM by default, which hid the first credential."""
    loaded = env("TYPESAFE_API_KEY=abc123".encode("utf-8-sig"))
    assert loaded["TYPESAFE_API_KEY"] == "abc123"
    assert all(not key.startswith(chr(0xFEFF)) for key in loaded)

