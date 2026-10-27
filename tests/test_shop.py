"""Shop-ready output end to end: --shop, --background and friends through the CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from bulk_bg_remove.cli import run
from conftest import MakeImage
from fakes import FAKE_FACTORY

BASE = ["--json", "--yes", "--min-free-ram", "0.1"]


def cli_json(argv: list[str], capfd: pytest.CaptureFixture[str]) -> tuple[int, list[dict]]:
    code = run(argv, model_factory=FAKE_FACTORY)
    lines = [json.loads(line) for line in capfd.readouterr().out.splitlines() if line]
    return code, lines


def test_shop_writes_white_square_jpegs(
    make_image: MakeImage, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    make_image("in/a.jpg", size=(320, 240))
    code, events = cli_json([str(tmp_path / "in"), "--shop", "--size", "400", *BASE], capfd)
    assert code == 0 and events[-1]["processed"] == 1
    plan = next(e for e in events if e["type"] == "plan")
    assert plan["output_format"] == "jpg"
    assert plan["finish"] == {"background": "#FFFFFF", "square": True, "pad": 7.5, "size": 400}
    with Image.open(tmp_path / "in-no-bg" / "a.jpg") as image:
        assert image.format == "JPEG" and image.mode == "RGB" and image.size == (400, 400)
        assert image.getpixel((2, 2)) == (255, 255, 255)
        centre = image.getpixel((200, 200))
        assert max(abs(a - b) for a, b in zip(centre, (40, 120, 200), strict=True)) <= 4


def test_a_second_run_resumes_unless_the_finish_changed(
    make_image: MakeImage, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    make_image("in/a.jpg", size=(320, 240))
    shop = [str(tmp_path / "in"), "--shop", "--size", "400", *BASE]
    cli_json(shop, capfd)
    _, events = cli_json(shop, capfd)
    assert events[-1]["already_done"] == 1 and events[-1]["processed"] == 0
    _, events = cli_json([*shop, "--size", "300"], capfd)  # the last --size wins
    assert events[-1]["processed"] == 1
    with Image.open(tmp_path / "in-no-bg" / "a.jpg") as image:
        assert image.size == (300, 300)


def test_a_new_background_colour_redoes_the_images(
    make_image: MakeImage, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    make_image("in/a.jpg", size=(320, 240))
    cli_json([str(tmp_path / "in"), "--background", "white", *BASE], capfd)
    _, events = cli_json([str(tmp_path / "in"), "--background", "#E6F6EA", *BASE], capfd)
    assert events[-1]["processed"] == 1
    with Image.open(tmp_path / "in-no-bg" / "a.png") as image:
        assert image.getpixel((2, 2)) == (230, 246, 234)


def test_the_ui_takes_no_finishing_flags(capfd: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exited:
        run(["--ui", "--shop"], model_factory=FAKE_FACTORY)
    assert exited.value.code == 2
    assert "not the UI" in capfd.readouterr().err


def test_background_alone_keeps_the_photo_size(
    make_image: MakeImage, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    make_image("in/a.jpg", size=(320, 240))
    code, _ = cli_json([str(tmp_path / "in"), "--background", "#FCE4EF", *BASE], capfd)
    assert code == 0
    with Image.open(tmp_path / "in-no-bg" / "a.png") as image:
        assert image.mode == "RGB" and image.size == (320, 240)
        assert image.getpixel((2, 2)) == (252, 228, 239)


def test_review_sheets_judge_the_transparent_cut_out(
    make_image: MakeImage, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    make_image("in/a.jpg", size=(320, 240))
    _, events = cli_json([str(tmp_path / "in"), "--shop", "--review-sheets", *BASE], capfd)
    image = next(e for e in events if e["type"] == "review_sheets")["sheets"][0]["images"][0]
    assert image["label"] == "a.jpg"
    with Image.open(image["zoom"]) as zoom:
        # On black, the removed background must look black, not the white --background.
        assert sum(zoom.getpixel((0, 0))) < 30


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--format", "jpg"], "needs --background"),
        (["--background", "pinkish"], "not a colour"),
        (["--pad", "60"], "--pad must be"),
    ],
)
def test_bad_finishing_options_are_usage_errors(
    make_image: MakeImage,
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
    args: list[str],
    message: str,
) -> None:
    make_image("in/a.jpg")
    with pytest.raises(SystemExit) as exited:
        run([str(tmp_path / "in"), *args, *BASE], model_factory=FAKE_FACTORY)
    assert exited.value.code == 2
    assert message in capfd.readouterr().err
    assert not (tmp_path / "in-no-bg").exists()
