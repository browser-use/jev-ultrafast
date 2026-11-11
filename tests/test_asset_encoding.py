"""Static assets must load on hosts whose default codepage is not UTF-8.

On a Turkish Windows install the default is cp1254, and `Path.read_text()` with no
encoding raised UnicodeDecodeError for app.js and fixture.html, so the inspector
served nothing. Every asset read in the package must pin encoding="utf-8".
"""

import re
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "jev_ultrafast"
TEXT_ASSETS = [*(PACKAGE / "static").iterdir(), PACKAGE / "snapshot.js"]


def test_assets_are_utf8():
    for path in TEXT_ASSETS:
        if path.is_file():
            path.read_text(encoding="utf-8")  # raises if an asset is not UTF-8


def test_no_unencoded_text_reads_in_package():
    offenders = []
    for source in PACKAGE.rglob("*.py"):
        for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\.(read|write)_text\(", line) and "encoding=" not in line:
                offenders.append(f"{source.name}:{number}")
    assert not offenders, f"text I/O without an explicit encoding: {offenders}"
