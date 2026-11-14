from pathlib import Path

import pytest

from jev_ultrafast.demo import read_static_asset

ROOT = Path(__file__).parents[1]
TEXT_ASSETS = (
    ROOT / "jev_ultrafast" / "snapshot.js",
    ROOT / "jev_ultrafast" / "static" / "app.js",
    ROOT / "jev_ultrafast" / "static" / "fixture.html",
    ROOT / "jev_ultrafast" / "static" / "index.html",
    ROOT / "jev_ultrafast" / "static" / "style.css",
)


@pytest.mark.parametrize("path", TEXT_ASSETS, ids=lambda path: path.name)
def test_bundled_text_assets_are_utf8(path):
    """Keep browser and inspector assets readable on every supported platform."""
    content = path.read_bytes().decode("utf-8")
    assert content.strip(), f"{path} has no readable content"


@pytest.mark.parametrize("name", ("app.js", "fixture.html", "index.html", "style.css"))
def test_demo_serves_static_assets_as_utf8(name):
    content = read_static_asset(name)
    assert content.strip(), f"{name} has no readable content"
