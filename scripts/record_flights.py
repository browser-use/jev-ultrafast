"""A measured live run with continuous CDP screencast; original timestamps retained."""

import base64
import hashlib
import json
import sys
import threading
import time
from pathlib import Path


def main(argv=None):
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from browser_harness.helpers import drain_events

    from examples.flights import GOALS, URL, verify
    from jev_ultrafast import Agent

    args = sys.argv[1:] if argv is None else argv
    folder = Path(args[0] if args else "artifacts/flights/recorded")
    folder.mkdir(parents=True, exist_ok=False)
    source_hashes = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (root / "jev_ultrafast").iterdir()
        if p.suffix in {".py", ".js"}
    }
    (folder / "frames").mkdir(exist_ok=True)
    frames = folder / "screencast"
    frames.mkdir(exist_ok=True)
    stop = threading.Event()
    errors = []
    agent = Agent(URL, GOALS)
    try:
        (folder / "frames" / "000000.jpg").write_bytes(
            base64.b64decode(agent.browser.call("Page.captureScreenshot", format="jpeg", quality=85)["data"])
        )
        epoch = time.time()

        def capture():
            try:
                while not stop.is_set():
                    for event in drain_events():
                        if (
                            event["method"] != "Page.screencastFrame"
                            or event.get("session_id") != agent.browser.session
                        ):
                            continue
                        p = event["params"]
                        timestamp = max(0, round((p["metadata"]["timestamp"] - epoch) * 1000))
                        (frames / f"{timestamp:06d}.jpg").write_bytes(base64.b64decode(p["data"]))
                        agent.browser.call("Page.screencastFrameAck", sessionId=p["sessionId"])
                    stop.wait(0.015)
            except Exception as e:
                errors.append(str(e))

        agent.browser.call(
            "Page.startScreencast", format="jpeg", quality=80, maxWidth=1120, maxHeight=780, everyNthFrame=2
        )
        worker = threading.Thread(target=capture, daemon=True)
        worker.start()
        # The first prediction starts the run timer; this anchors video timestamps to it.
        epoch = time.time()
        try:
            for state in agent.run():
                action = state["history"][-1]["action"] if state["history"] else ""
                print(state["elapsed_ms"], state["status"], action, flush=True)
        finally:
            time.sleep(0.08)  # Drain the last frame, outside the reported agent time.
            stop.set()
            worker.join(timeout=3)
            agent.browser.call("Page.stopScreencast")
            state = agent.snapshot()
            state["final_page"] = agent.browser.observe(screenshot=False)
            state["verification"] = verify(state["final_page"])
            state["source_hashes"] = source_hashes
            state["recording_errors"] = errors
            (folder / "state.json").write_text(json.dumps(state, indent=2))
            (folder / "session.json").write_text(
                json.dumps({"target": agent.browser.target, "session": agent.browser.session})
            )
        print(json.dumps(state["verification"], indent=2))
        print("Screencast frames", len(list(frames.glob("*.jpg"))), "errors", errors)
        if not state["verification"]["passed"]:
            raise SystemExit("Final-page verification failed")
    finally:
        agent.close()


if __name__ == "__main__":
    main()
