"""Verify and resume with finishing: mode, size and the stored finish must match."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from bulk_bg_remove.bulk.plan import PlanSettings, make_plan
from bulk_bg_remove.bulk.scan import ImageSource
from bulk_bg_remove.bulk.verify import is_complete, verify_output
from bulk_bg_remove.core.finish import Finish
from bulk_bg_remove.core.io import save_image
from bulk_bg_remove.domain import OutputFormat
from test_plan import hardware, inventory

SOURCE = ImageSource(Path("/in/a.jpg"), "JPEG", 300, 200, 10_000)
WHITE = Finish(background=(255, 255, 255))
MINT = Finish(background=(230, 246, 234))


def finished(tmp_path: Path, finish: Finish, fmt: OutputFormat, size=(300, 200)) -> Path:
    path = tmp_path / f"a{fmt.extension}"
    image = Image.new("RGB" if finish.opaque else "RGBA", size, (255, 255, 255))
    save_image(image, path, fmt, tag=finish.signature if finish.active else None)
    return path


def test_rgb_outputs_verify_in_every_format(tmp_path: Path) -> None:
    for fmt in OutputFormat:
        path = finished(tmp_path, WHITE, fmt)
        assert verify_output(path, SOURCE, WHITE).ok, fmt
        assert is_complete(path, SOURCE, WHITE), fmt


def test_another_background_colour_is_redone(tmp_path: Path) -> None:
    path = finished(tmp_path, WHITE, OutputFormat.JPG)
    assert not is_complete(path, SOURCE, MINT)


def test_dropping_the_finish_is_redone(tmp_path: Path) -> None:
    square = Finish(square=True)
    path = finished(tmp_path, square, OutputFormat.PNG, size=(300, 200))
    assert not is_complete(path, SOURCE, Finish())  # same size, but it was finished
    plain = finished(tmp_path, Finish(), OutputFormat.PNG)
    assert not is_complete(plain, SOURCE, square)


def test_a_wrong_finished_size_fails_verification(tmp_path: Path) -> None:
    shop = Finish(background=(255, 255, 255), square=True, size=400)
    path = finished(tmp_path, shop, OutputFormat.JPG, size=(400, 300))
    check = verify_output(path, SOURCE, shop)
    assert not check.ok and check.problem == "size (400, 300) is not square"


def test_framing_raises_the_ram_floor_and_the_disk_estimate() -> None:
    big = inventory(count=10, megapixels=45)
    base = {"model": "silueta", "output_format": OutputFormat.PNG}
    plain = make_plan(hardware(free_gb=64), big, 500.0, PlanSettings(**base))
    framed = make_plan(
        hardware(free_gb=64), big, 500.0, PlanSettings(**base, finish=Finish(square=True, pad=25))
    )
    assert framed.min_free_ram_gb > plain.min_free_ram_gb + 4  # 45 MP x8 canvas
    assert framed.estimated_output_gb > plain.estimated_output_gb * 7
    sized = make_plan(
        hardware(free_gb=64),
        big,
        500.0,
        PlanSettings(**base, finish=Finish(square=True, size=1000)),
    )
    assert sized.estimated_output_gb < plain.estimated_output_gb
