"""Offline contract for the .env loader in demo.py. Runs the real parsing code; no browser or model APIs."""

import ast
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "jev_ultrafast" / "demo.py").read_text()


def loader():
    """load_environment and its helpers, compiled verbatim out of demo.py."""
    namespace = {"os": os, "Path": Path}
    for node in ast.parse(SOURCE).body:
        if isinstance(node, ast.FunctionDef):
            exec(compile(ast.Module(body=[node], type_ignores=[]), "demo.py", "exec"), namespace)
    return namespace["load_environment"]


@pytest.fixture
def load_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def run(env_text, *keys):
        for key in keys:
            monkeypatch.delenv(key, raising=False)
        (tmp_path / ".env").write_text(env_text)
        loader()()
        loaded = {key: os.environ.get(key) for key in keys}
        for key in keys:
            os.environ.pop(key, None)
        return loaded

    return run


def test_issue_examples(load_env):
    loaded = load_env(
        "\n".join(
            [
                "# demo keys",
                'TYPESAFE_API_KEY="tsk-abc123"',
                "TEXT_MODEL_API_KEY=sk-xyz # my key",
                "TEXT_MODEL=  mercury-2.5  ",
                "export TYPESAFE_MODEL=jev-latest",
            ]
        ),
        "TYPESAFE_API_KEY",
        "TEXT_MODEL_API_KEY",
        "TEXT_MODEL",
        "TYPESAFE_MODEL",
    )
    assert loaded == {
        "TYPESAFE_API_KEY": "tsk-abc123",
        "TEXT_MODEL_API_KEY": "sk-xyz",
        "TEXT_MODEL": "mercury-2.5",
        "TYPESAFE_MODEL": "jev-latest",
    }


def test_export_prefix_creates_no_export_variable(load_env):
    loaded = load_env("export TYPESAFE_MODEL=jev-latest\n", "export TYPESAFE_MODEL", "TYPESAFE_MODEL")
    assert loaded == {"export TYPESAFE_MODEL": None, "TYPESAFE_MODEL": "jev-latest"}


def test_export_prefix_accepts_any_whitespace(load_env):
    loaded = load_env(
        "export\tTYPESAFE_MODEL=jev-latest\nexport  TEXT_MODEL=mercury-2.5\n",
        "TYPESAFE_MODEL",
        "TEXT_MODEL",
    )
    assert loaded == {"TYPESAFE_MODEL": "jev-latest", "TEXT_MODEL": "mercury-2.5"}


def test_escaped_quote_does_not_close_the_string(load_env):
    loaded = load_env('SECRET="hello \\"# world"\n', "SECRET")
    assert loaded == {"SECRET": 'hello \\"# world'}


def test_quoted_value_keeps_inner_spaces_and_hashes(load_env):
    loaded = load_env('SECRET="hello # world"\n', "SECRET")
    assert loaded == {"SECRET": "hello # world"}


def test_comment_after_quoted_value(load_env):
    loaded = load_env('export KEY="hello world"  # comment\n', "KEY")
    assert loaded == {"KEY": "hello world"}


def test_single_quoted_value(load_env):
    loaded = load_env("KEY='sk-abc'\n", "KEY")
    assert loaded == {"KEY": "sk-abc"}


def test_unquoted_values_are_trimmed_only(load_env):
    loaded = load_env("PLAIN=abc\nOTHER = spaced\n", "PLAIN", "OTHER")
    assert loaded == {"PLAIN": "abc", "OTHER": "spaced"}


def test_comment_and_blank_lines_are_skipped(load_env):
    loaded = load_env("# a comment\n\n   \nKEY=abc\n", "KEY")
    assert loaded == {"KEY": "abc"}


def test_hash_without_leading_space_is_kept_in_unquoted_values(load_env):
    loaded = load_env("TOKEN=abc#def\nKEY=#tag\n", "TOKEN", "KEY")
    assert loaded == {"TOKEN": "abc#def", "KEY": "#tag"}


def test_comment_only_value_is_empty(load_env):
    loaded = load_env("EMPTY=  # note\n", "EMPTY")
    assert loaded == {"EMPTY": ""}


def test_existing_environment_wins(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TYPESAFE_API_KEY", "original")
    (tmp_path / ".env").write_text('TYPESAFE_API_KEY="new"\n')
    loader()()
    assert os.environ["TYPESAFE_API_KEY"] == "original"
