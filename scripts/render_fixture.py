"""Render recorded fixture frames at their original timestamps. No acceleration."""

import json
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / "artifacts/final"
state = json.loads((source / "travel-1.json").read_text(encoding="utf-8"))
frames = sorted((int(p.stem), Image.open(p).convert("RGB")) for p in (source / "frames").glob("*.jpg"))
end_ms = state["elapsed_ms"]
folder = ROOT / "artifacts/video-frames"
folder.mkdir(parents=True, exist_ok=True)
font_path = "/System/Library/Fonts/Menlo.ttc"
font = ImageFont.truetype(font_path, 23)
small = ImageFont.truetype(font_path, 17)
images = []
# 750 ms lead-in and 1,250 ms endpoint hold; action time itself is unmodified.
for index in range(round((end_ms + 2000) * 30 / 1000)):
    t = min(end_ms, max(0, round(index * 1000 / 30) - 750))
    frame = next(image for timestamp, image in reversed(frames) if timestamp <= t)
    canvas = Image.new("RGB", (1240, 960), "#f5f5ed")
    canvas.paste(frame, (60, 105))
    draw = ImageDraw.Draw(canvas)
    draw.text((60, 34), "BROWSER USE × TYPESAFE", font=font, fill="#283c2c")
    draw.text((945, 38), f"{t / 1000:0.2f}s / 1×", font=small, fill="#487645")
    step = sum(h["elapsed_ms"] <= t for h in state["history"])
    draw.text((60, 909), f"Jev Ultrafast     {step}/5 browser actions     Live API calls", font=small, fill="#64745c")
    canvas.save(folder / f"{index:04d}.png")
    images.append(canvas.resize((930, 720)))
images[0].save(
    ROOT / "docs/fixture-demo.gif", save_all=True, append_images=images[1:], duration=1000 / 30, loop=0, optimize=True
)
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
        str(ROOT / "docs/fixture-demo.mp4"),
    ],
    check=True,
)
print("Rendered 1× footage from", len(frames), "recorded observations; action time:", end_ms, "ms")
