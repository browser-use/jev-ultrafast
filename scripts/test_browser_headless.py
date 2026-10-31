import os
import subprocess
import time
from pathlib import Path

user_data = Path(os.environ.get("TEMP", ".")) / "chrome-auto"
proc = subprocess.Popen([
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "--remote-debugging-port=9333",
    f"--user-data-dir={user_data}",
    "--headless=new",
    "--no-first-run",
    "--no-default-browser-check",
])
time.sleep(2)
try:
    from jev_ultrafast.browser import Browser
    b = Browser("https://example.com")
    obs = b.observe(screenshot=False)
    print("Observed Title:", obs["title"])
    print("Actions:", len(obs["actions"]))
    b.close()
finally:
    proc.terminate()
