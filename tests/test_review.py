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


def product_shot(path: Path, background: tuple[int, int, int] = (250, 250, 250)) -> None:
    """A subject exactly where the fake model looks (the centred half) on a backdrop.
    Odd sides keep that rectangle symmetric, so the mirror check agrees."""
    from PIL import ImageDraw

    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (161, 121), background)
    ImageDraw.Draw(image).rectangle((40, 30, 120, 90), fill=(60, 140, 40))
    image.save(path)


def test_certified_cut_outs_skip_review_but_for_an_audit(tmp_path: Path) -> None:
    for i in range(3):
        product_shot(tmp_path / "in" / f"bottle-{i}.png")
    make_busy = Image.effect_noise((161, 121), 90).convert("RGB")
    make_busy.save(tmp_path / "in" / "scene.png")
    events = run_session(tmp_path / "in", replace(OPTIONS, review_sheets=True))
    done = {Path(e.data["source"]).name: e.data for e in events if e.type == "job_done"}
    assert all(done[f"bottle-{i}.png"]["certified"] for i in range(3))
    assert done["scene.png"]["certified"] is False
    sheets = next(e for e in events if e.type == "review_sheets").data
    images = [img for sheet in sheets["sheets"] for img in sheet["images"]]  # type: ignore[index]
    assert sheets["certified"] == 3 and sheets["audit"] == 1
    assert sorted((img["label"], img["audit"]) for img in images) == [  # type: ignore[index]
        ("bottle-0.png", True),
        ("scene.png", False),
    ]
    assert next(e for e in events if e.type == "finished").data["certified"] == 3


def test_review_all_sheets_every_cut_out(tmp_path: Path) -> None:
    for i in range(3):
        product_shot(tmp_path / "in" / f"bottle-{i}.png")
    events = run_session(tmp_path / "in", replace(OPTIONS, review_sheets=True, review_all=True))
    sheets = next(e for e in events if e.type == "review_sheets").data
    assert len(sheets["sheets"][0]["images"]) == 3  # type: ignore[index]
    assert all(e.data["certified"] is None for e in events if e.type == "job_done")


def test_audits_are_every_twentieth_certified_cut_out_by_path(tmp_path: Path) -> None:
    for i in range(41):
        product_shot(tmp_path / "in" / f"bottle-{i:02d}.png")
    events = run_session(tmp_path / "in", replace(OPTIONS, review_sheets=True))
    sheets = next(e for e in events if e.type == "review_sheets").data
    labels = [img["label"] for sheet in sheets["sheets"] for img in sheet["images"]]  # type: ignore[index]
    assert sheets["certified"] == 41 and sheets["audit"] == 3
    assert labels == ["bottle-00.png", "bottle-20.png", "bottle-40.png"]
