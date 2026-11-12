"""Offline checks for original-timing rendering with real JPEG inputs."""

import json
import runpy
import sys
import weakref
from collections import Counter
from pathlib import Path
from unittest.mock import Mock, call

import pytest
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError


@pytest.fixture
def recording(tmp_path, monkeypatch):
    script = tmp_path / "scripts/render_demo.py"
    script.parent.mkdir()
    script.write_text(
        (Path(__file__).resolve().parents[1] / "scripts/render_demo.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "docs").mkdir()
    source = tmp_path / "recording"
    (source / "frames").mkdir(parents=True)
    (source / "screencast").mkdir()
    (source / "state.json").write_text(json.dumps({
        "verification": {"passed": True},
        "recording_errors": [],
        "elapsed_ms": 100,
        "history": [{"executed_ms": 0, "action": "Search"}],
        "decisions": [],
        "text_calls": [{"model": "test/text"}],
    }))
    default_font = ImageFont.load_default()
    monkeypatch.setattr(ImageFont, "truetype", lambda *args, **kwargs: default_font)
    monkeypatch.setattr(sys, "argv", [str(script), str(source)])
    encode = Mock()
    monkeypatch.setattr("subprocess.run", encode)
    return script, source, encode


def save_frame(path, color):
    with Image.new("RGB", (1120, 780), color) as image:
        ImageDraw.Draw(image).rectangle((0, 0, 1119, 63), fill="white")
        image.save(path, quality=85)


@pytest.mark.parametrize("timestamps", [(180, 34, 100, 35, 0), (180, 34, 100, 35), ()])
def test_render_selects_frames_without_preloading(recording, monkeypatch, timestamps, capsys):
    script, source, encode = recording
    initial = source / "frames/000000.jpg"
    save_frame(initial, "gray")
    colors = {0: "red", 34: "green", 35: "blue", 100: "yellow", 180: "purple"}
    for timestamp in timestamps:
        save_frame(source / "screencast" / f"{timestamp:06d}.jpg", colors[timestamp])

    open_image, save_image, convert_image = Image.open, Image.Image.save, Image.Image.convert
    opened = []
    opened_at_first_output = []
    decoded = []
    released_before_open = []

    def released():
        for reference in decoded:
            image = reference()
            if image is not None:
                try:
                    image.getpixel((0, 0))
                except ValueError:
                    continue
                return False
        return True

    def track_open(path, *args, **kwargs):
        released_before_open.append(released())
        opened.append(Path(path))
        return open_image(path, *args, **kwargs)

    def track_convert(image, *args, **kwargs):
        result = convert_image(image, *args, **kwargs)
        if image.format == "JPEG":
            decoded.append(weakref.ref(result))
        return result

    def track_save(image, path, *args, **kwargs):
        if not opened_at_first_output:
            opened_at_first_output.extend(opened)
        return save_image(image, path, *args, **kwargs)

    monkeypatch.setattr(Image, "open", track_open)
    monkeypatch.setattr(Image.Image, "save", track_save)
    monkeypatch.setattr(Image.Image, "convert", track_convert)
    runpy.run_path(str(script), run_name="__main__")
    released_after_render = released()

    # Output samples are 0, 33, 67, then 100 ms throughout the end hold.
    first = source / "screencast/000000.jpg" if 0 in timestamps else initial
    expected = [first] * 18
    if timestamps:
        expected[2] = source / "screencast/000035.jpg"
        expected[3:] = [source / "screencast/000100.jpg"] * 15
    outputs = sorted((source / "video-frames").glob("*.png"))
    assert [path.name for path in outputs] == [f"{index:04d}.png" for index in range(18)]
    for output, selected in zip(outputs, expected, strict=True):
        with open_image(output) as rendered, open_image(selected) as original:
            assert rendered.size == (1536, 1000)
            assert rendered.crop((36, 226, 1156, 942)).tobytes() == original.crop((0, 64, 1120, 780)).tobytes()
    with open_image(script.parents[1] / "docs/flights-result.png") as final, open_image(outputs[-1]) as last:
        assert final.tobytes() == last.tobytes()

    assert opened_at_first_output == [first]
    assert Counter(opened) == Counter({path: 1 for path in expected})
    assert all(released_before_open)
    assert released_after_render
    docs = script.parents[1] / "docs"
    assert encode.call_args_list == [
        call([
            "ffmpeg", "-y", "-loglevel", "error", "-framerate", "30", "-i",
            str(source / "video-frames/%04d.png"), "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-crf", "18", "-movflags", "+faststart", str(docs / "demo.mp4"),
        ], check=True),
        call([
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(docs / "demo.mp4"), "-vf",
            "fps=12,scale=1152:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse",
            "-loop", "0", str(docs / "demo.gif"),
        ], check=True),
    ]
    assert capsys.readouterr().out == (
        f"Rendered {len(timestamps) + 1} source frames at original timing: 100 ms, plus a 500ms end hold.\n"
    )


@pytest.mark.parametrize("failure", ["corrupt", "truncated", "missing", "missing_initial"])
def test_failed_frame_read_allows_retry(recording, monkeypatch, failure):
    script, source, encode = recording
    save_frame(source / "frames/000000.jpg", "gray")
    selected = source / "screencast/000100.jpg"
    save_frame(selected, "blue")
    if failure == "missing_initial":
        selected = source / "frames/000000.jpg"
    open_image = Image.open
    partial_outputs = []

    def fail_read(path, *args, **kwargs):
        if Path(path) == selected:
            partial_outputs.append(len(list((source / "video-frames").glob("*.png"))))
            if failure.startswith("missing"):
                selected.unlink()
            elif failure == "truncated":
                selected.write_bytes(selected.read_bytes()[:-100])
                with open_image(selected) as header:
                    assert header.size == (1120, 780)  # Decoding, not opening, must fail.
            else:
                selected.write_bytes(b"invalid JPEG")
        return open_image(path, *args, **kwargs)

    with monkeypatch.context() as fault:
        fault.setattr(Image, "open", fail_read)
        error = (FileNotFoundError if failure.startswith("missing") else
                 OSError if failure == "truncated" else UnidentifiedImageError)
        with pytest.raises(error):
            runpy.run_path(str(script), run_name="__main__")
    encode.assert_not_called()
    assert partial_outputs == [0 if failure == "missing_initial" else 3]
    assert not (source / "video-frames").exists()
    assert (source / "state.json").exists()
    if failure != "missing_initial":
        assert (source / "frames/000000.jpg").exists()

    save_frame(selected, "blue")
    runpy.run_path(str(script), run_name="__main__")
    assert len(list((source / "video-frames").glob("*.png"))) == 18
    assert encode.call_count == 2


def test_existing_output_directory_is_preserved(recording):
    script, source, encode = recording
    save_frame(source / "frames/000000.jpg", "gray")
    output = source / "video-frames"
    output.mkdir()
    existing = output / "keep.txt"
    existing.write_text("previous output")
    with pytest.raises(FileExistsError):
        runpy.run_path(str(script), run_name="__main__")
    assert existing.read_text() == "previous output"
    encode.assert_not_called()
