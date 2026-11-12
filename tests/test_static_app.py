"""Run the browser inspector's deterministic network-state contracts."""

import subprocess
from pathlib import Path


def test_static_app_connection_recovery():
    test_file = Path(__file__).with_name("static_app.test.mjs")
    subprocess.run(["node", "--test", str(test_file)], check=True)
