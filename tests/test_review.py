"""The review loop's engine side: review sheets after a run, and redoing chosen images."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from bulk_bg_remove.bulk.events import Event
from bulk_bg_remove.bulk.session import BulkOptions, BulkSession
from bulk_bg_remove.cli import run
from conftest import MakeImage
from fakes import FAKE_FACTORY

OPTIONS = BulkOptions(model="fake", model_factory=FAKE_FACTORY, min_free_ram_gb=0.1)


def run_session(path: Path, options: BulkOptions) -> list[Event]:
    events: list[Event] = []
    session = BulkSession(path, options, events.append)
    assert session.prepare().ok
    session.execute()
    return events


def test_review_sheets_list_every_cut_out_once(make_image: MakeImage, tmp_path: Path) -> None:
    for i in range(14):
        make_image(f"in/{i:02d}.jpg")
    events = run_session(tmp_path / "in", replace(OPTIONS, review_sheets=True))
    kinds = [e.type for e in events]
    assert kinds.index("review_sheets") == len(kinds) - 2  # just before `finished`
    sheets = next(e for e in events if e.type == "review_sheets").data["sheets"]
    assert isinstance(sheets, list) and len(sheets) == 2  # 12 per sheet
    numbers = [img["n"] for sheet in sheets for img in sheet["images"]]  # type: ignore[index]
    assert numbers == list(range(1, 15))
    first = sheets[0]["images"][0]  # type: ignore[index]
    assert first["label"] == "00.png"
    with Image.open(first["zoom"]) as zoom:  # on black | on white, alpha flattened
        assert zoom.mode == "RGB" and zoom.format == "JPEG"
        left, right = zoom.getpixel((0, 0)), zoom.getpixel((zoom.width - 1, 0))
    assert sum(left) < 30 and sum(right) > 700  # background corners: black, then white
    for sheet in sheets:
        path = Path(sheet["path"])  # type: ignore[index]
        assert path.is_file() and tmp_path not in path.parents
        with Image.open(path) as image:
            assert image.width > 0


def test_only_redoes_the_named_images_with_their_usual_names(
    make_image: MakeImage, tmp_path: Path
) -> None:
    make_image("in/a.jpg")
    make_image("in/shoes/b.jpg")
    make_image("in/shoes/b.png", fmt="PNG")  # name clash: outputs b_jpg.png / b_png.png
    run_session(tmp_path / "in", OPTIONS)
    out = tmp_path / "in-no-bg"
    before = {p: p.stat().st_mtime_ns for p in out.rglob("*.png")}

    options = replace(OPTIONS, only=("shoes/b.png", "a.jpg"))
    events = run_session(tmp_path / "in", options)
    finished = events[-1].data
    assert finished["processed"] == 2 and finished["images"] == 2
    changed = {p for p in before if p.stat().st_mtime_ns != before[p]}
    assert changed == {out / "a.png", out / "shoes" / "b_png.png"}


def test_only_with_an_unknown_name_is_an_error(
    make_image: MakeImage, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    make_image("in/a.jpg")
    code = run(
        [str(tmp_path / "in"), "--json", "--yes", "--only", "nope.jpg", "--min-free-ram", "0.1"],
        model_factory=FAKE_FACTORY,
    )
    events = [json.loads(line) for line in capfd.readouterr().out.splitlines() if line]
    assert code == 2
    assert events[-1]["type"] == "error" and "nope.jpg" in events[-1]["message"]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("shoes/b.png", "shoes/b_png.png"),
        ("./shoes/B.PNG", "shoes/b_png.png"),
        ("shoes\\b.jpg", "shoes/b_jpg.png"),
        ("a.jpg", "a.png"),
    ],
)
def test_only_accepts_relative_names_in_any_case(
    make_image: MakeImage, tmp_path: Path, name: str, expected: str
) -> None:
    make_image("in/a.jpg")
    make_image("in/shoes/b.jpg")
    make_image("in/shoes/b.png", fmt="PNG")
    events = run_session(tmp_path / "in", replace(OPTIONS, only=(name,)))
    done = [e for e in events if e.type == "job_started"]
    assert len(done) == 1
    assert (
        Path(str(done[0].data["destination"])).relative_to(tmp_path / "in-no-bg").as_posix()
        == expected
    )


def test_only_accepts_the_absolute_source_from_the_review_event(
    make_image: MakeImage, tmp_path: Path
) -> None:
    source = make_image("in/shoes/b.jpg")
    make_image("in/a.jpg")
    events = run_session(tmp_path / "in", replace(OPTIONS, only=(str(source),)))
    assert events[-1].data["processed"] == 1


def test_an_ambiguous_bare_name_asks_for_the_path(make_image: MakeImage, tmp_path: Path) -> None:
    make_image("in/bags/b.jpg")
    make_image("in/shoes/b.jpg")
    session = BulkSession(tmp_path / "in", replace(OPTIONS, only=("b.jpg",)), lambda e: None)
    with pytest.raises(ValueError, match="matches several images"):
        session.prepare()


def test_output_inside_the_input_must_be_named_like_an_output(
    make_image: MakeImage, tmp_path: Path
) -> None:
    make_image("in/a.jpg")
    bad = BulkSession(
        tmp_path / "in", OPTIONS, lambda e: None, output_root=tmp_path / "in" / "retry"
    )
    with pytest.raises(ValueError, match="inside the input folder"):
        bad.prepare()
    fine = BulkSession(
        tmp_path / "in", OPTIONS, lambda e: None, output_root=tmp_path / "in" / "retry-no-bg"
    )
    assert fine.prepare().ok
