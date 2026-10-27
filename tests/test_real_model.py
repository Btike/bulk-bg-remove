"""Smoke test with a real ONNX model, end to end through the CLI.

Every other test uses a fake model; this one checks that the real runtime and a
real model load and cut out a subject on this platform. It downloads `silueta`
(43 MB) on first use, so it only runs when BULK_BG_REMOVE_REAL_MODEL=1 (set in
the Docker step of scripts/ci.sh and in hosted CI on Linux, macOS and Windows).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from bulk_bg_remove.cli import run

pytestmark = pytest.mark.skipif(
    os.environ.get("BULK_BG_REMOVE_REAL_MODEL") != "1",
    reason="set BULK_BG_REMOVE_REAL_MODEL=1 to run the real-model smoke test",
)

ARGS = ["--model", "silueta", "--json", "--yes", "--min-free-ram", "0.5"]


def product_photo(path: Path) -> None:
    """A dark red ball on a plain white backdrop: an easy, unambiguous subject."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (640, 480), (250, 250, 250))
    ImageDraw.Draw(image).ellipse((200, 120, 440, 360), fill=(150, 20, 30))
    image.save(path, quality=95)


def cli(argv: list[str], capfd: pytest.CaptureFixture[str]) -> tuple[int, list[dict]]:
    code = run(argv)
    return code, [json.loads(line) for line in capfd.readouterr().out.splitlines() if line]


def test_real_model_cuts_out_the_subject(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    product_photo(tmp_path / "in" / "ball.jpg")
    code, events = cli([str(tmp_path / "in"), *ARGS], capfd)
    assert code == 0, events[-1]
    assert events[-1]["type"] == "finished" and events[-1]["processed"] == 1
    with Image.open(tmp_path / "in-no-bg" / "ball.png") as image:
        assert image.mode == "RGBA" and image.size == (640, 480)
        alpha = image.getchannel("A")
        assert alpha.getpixel((320, 240)) > 200  # the ball is kept
        assert alpha.getpixel((20, 20)) < 50  # the backdrop is removed


def test_real_model_with_shop_finishing(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    product_photo(tmp_path / "in" / "ball.jpg")
    code, events = cli([str(tmp_path / "in"), *ARGS, "--shop", "--size", "800"], capfd)
    assert code == 0, events[-1]
    with Image.open(tmp_path / "in-no-bg" / "ball.jpg") as image:
        assert image.format == "JPEG" and image.size == (800, 800)
        assert image.getpixel((5, 5)) == (255, 255, 255)
