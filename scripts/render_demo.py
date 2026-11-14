"""Render the actual Google Flights screencast at 1x, including every loading wait."""

import argparse
import json
import statistics
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("source", type=Path, help="Exact verified recording directory")
args = parser.parse_args()
source = args.source.resolve()
state = json.loads((source / "state.json").read_text())
assert state["verification"]["passed"] and not state["recording_errors"]
assert all(h.get("execution") != "unknown" for h in state["history"]), "Cannot render unconfirmed execution timing"
frames = [(0, Image.open(source / "frames/000000.jpg").convert("RGB"))]
frames += sorted((int(p.stem), Image.open(p).convert("RGB")) for p in (source / "screencast").glob("*.jpg"))
end = state["elapsed_ms"]
folder = source / "video-frames"
folder.mkdir(parents=True, exist_ok=False)
font_path = "/System/Library/Fonts/Supplemental/Arial.ttf"
font_bold = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def font(n, bold=False):
    return ImageFont.truetype(font_bold if bold else font_path, n)


def mono(n):
    return ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", n)


ink, muted, green = "#172a20", "#6a766c", "#2a743f"
steps = [
    ("One way", "One way"),
    ("Zürich", "Zürich, Switzerland"),
    ("London", "London, United Kingdom"),
    ("20 September", "Done. Search"),
    ("Search flights", "Search"),
]
for i in range(round((end + 500) * 30 / 1000)):
    t = min(end, round(i * 1000 / 30))
    screenshot = next(im for ts, im in reversed(frames) if ts <= t)
    canvas = Image.new("RGB", (1536, 1000), "#f3f4ec")
    d = ImageDraw.Draw(canvas)
    d.text((36, 26), "browser use", font=font(23, True), fill=ink)
    d.text((186, 27), "×  TypeSafe", font=font(22), fill=muted)
    d.rounded_rectangle((1287, 24, 1499, 59), radius=17, fill="#dfebd9")
    d.text((1310, 32), "REAL WEB  ·  1× SPEED", font=font(14, True), fill=green)
    d.text((36, 80), f"Zürich → London. In {end / 1000:.1f} seconds.", font=font(43, True), fill=ink)
    d.text((38, 139), "One goal. Dynamic elements. LLM-generated text.", font=font(20), fill=muted)
    d.rounded_rectangle((35, 191, 1157, 943), radius=14, fill="#202124")
    for j, c in enumerate(["#de8278", "#d6bd6e", "#8dbd8a"]):
        d.ellipse((54 + j * 19, 205, 63 + j * 19, 214), fill=c)
    d.text((145, 201), "google.com/travel/flights", font=mono(13), fill="#d4d6d5")
    # Omit Google account controls in every frame. No content from the task area is redrawn.
    canvas.paste(screenshot.crop((0, 64, 1120, 780)), (36, 226))
    d.text((1192, 206), "JEV ULTRAFAST", font=font(16, True), fill=green)
    d.text((1189, 242), f"{t / 1000:05.2f}", font=mono(52), fill=ink)
    d.text((1193, 307), "SECONDS ELAPSED", font=font(13, True), fill=muted)
    history = [h for h in state["history"] if h["executed_ms"] <= t]
    for j, (label, key) in enumerate(steps):
        done = any(h["action"] == key or (key == "Done. Search" and h["action"].startswith(key)) for h in history)
        y = 370 + j * 54
        d.ellipse((1194, y, 1218, y + 24), fill=green if done else "#e0e4d9")
        if done:
            d.line([(1200, y + 12), (1204, y + 16), (1212, y + 8)], fill="white", width=2)
        d.text((1236, y - 1), label, font=font(21, done), fill=ink if done else muted)
    waiting = t >= next(h["executed_ms"] for h in state["history"] if h["action"] == "Search") and t < end
    final = t >= end
    d.rounded_rectangle((1189, 670, 1499, 789), radius=14, fill="#dfeeda" if final else "#e7e9df")
    title = "Flights found" if final else "Waiting for Google…" if waiting else "Choose. Act. Repeat."
    d.text((1209, 691), title, font=font(22, True), fill=green if final else ink)
    subtitle = (
        "Route + date verified"
        if final
        else "Loading stays in the video"
        if waiting
        else f"{len(history)} actions executed"
    )
    d.text((1209, 734), subtitle, font=font(16), fill=muted)
    latencies = [x["latency_ms"] for x in state["decisions"] if x["elapsed_ms"] <= t]
    latency = f"{statistics.median(latencies):.0f} ms" if latencies else "—"
    d.text((1194, 835), latency, font=mono(30), fill=ink)
    d.text((1194, 878), "median decision latency", font=font(16), fill=muted)
    d.line((37, 960, 1498, 960), fill="#d3d9cc", width=2)
    d.line((37, 960, 37 + (1498 - 37) * t / end, 960), fill=green, width=3)
    d.text(
        (37, 973),
        f"Operation + index by Jev. Text by {state['text_calls'][0]['model'].split('/')[-1]}. "
        "Original timing; waits included.",
        font=font(14),
        fill=muted,
    )
    d.text((1194, 973), "github.com/browser-use/jev-ultrafast", font=font(12), fill=muted)
    canvas.save(folder / f"{i:04d}.png")
canvas.save(ROOT / "docs/flights-result.png")
subprocess.run(
    [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-framerate",
        "30",
        "-i",
        str(folder / "%04d.png"),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-crf",
        "18",
        "-movflags",
        "+faststart",
        str(ROOT / "docs/demo.mp4"),
    ],
    check=True,
)
subprocess.run(
    [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(ROOT / "docs/demo.mp4"),
        "-vf",
        "fps=12,scale=1152:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse",
        "-loop",
        "0",
        str(ROOT / "docs/demo.gif"),
    ],
    check=True,
)
print("Rendered", len(frames), "source frames at original timing:", end, "ms, plus a 500ms end hold.")
