"""End to end through BulkSession and the CLI, with the fake model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from bulk_bg_remove.bulk.events import Event
from bulk_bg_remove.bulk.session import BulkOptions, BulkSession
from bulk_bg_remove.cli import main, run
from conftest import MakeImage
from fakes import FAKE_FACTORY, NO_SUBJECT_MARKER, NOISY_FACTORY

OPTIONS = BulkOptions(model="fake", model_factory=FAKE_FACTORY, min_free_ram_gb=0.1)


def run_session(path: Path, options: BulkOptions = OPTIONS) -> tuple[BulkSession, list[Event]]:
    events: list[Event] = []
    session = BulkSession(path, options, events.append)
    assert session.prepare().ok
    session.execute()
    return session, events


def test_folder_goes_to_a_sibling_no_bg_folder_with_subfolders(
    make_image: MakeImage, tmp_path: Path
) -> None:
    make_image("products/a.jpg")
    make_image("products/shoes/b.png", fmt="PNG")
    make_image("products/hats/c.heic", fmt="HEIF")
    (tmp_path / "products" / "notes.txt").write_text("not an image")

    session, events = run_session(tmp_path / "products")
    out = tmp_path / "products-no-bg"
    assert session.output_root == out.resolve()
    assert sorted(p.relative_to(out).as_posix() for p in out.rglob("*.png")) == [
        "a.png",
        "hats/c.png",
        "shoes/b.png",
    ]
    finished = next(e for e in events if e.type == "finished").data
    assert finished["processed"] == 3
    assert finished["not_processed"] == [
        {"path": str((tmp_path / "products" / "notes.txt").resolve()), "reason": "not an image"}
    ]
    assert not list((tmp_path / "products").rglob("*-no-bg"))  # originals folder untouched


def test_single_photo_gets_a_folder_named_after_it(make_image: MakeImage, tmp_path: Path) -> None:
    photo = make_image("shoe.jpg")
    run_session(photo)
    assert (tmp_path / "shoe-no-bg" / "shoe.png").is_file()


def test_second_run_resumes_and_force_redoes(make_image: MakeImage, tmp_path: Path) -> None:
    for i in range(3):
        make_image(f"in/{i}.jpg")
    run_session(tmp_path / "in")

    _, events = run_session(tmp_path / "in")
    plan = next(e for e in events if e.type == "plan").data
    assert plan["already_done"] == 3
    assert plan["images"] == 0
    assert next(e for e in events if e.type == "finished").data["already_done"] == 3

    forced = BulkOptions(model="fake", model_factory=FAKE_FACTORY, min_free_ram_gb=0.1, force=True)
    _, events = run_session(tmp_path / "in", forced)
    assert next(e for e in events if e.type == "finished").data["processed"] == 3


def test_empty_cutout_is_reported_as_a_warning(make_image: MakeImage, tmp_path: Path) -> None:
    make_image("in/ghost.jpg", color=NO_SUBJECT_MARKER, fmt="PNG")
    _, events = run_session(tmp_path / "in")
    warnings = next(e for e in events if e.type == "finished").data["warnings"]
    assert warnings and "no subject detected" in json.dumps(warnings)


def test_originals_are_never_modified(make_image: MakeImage, tmp_path: Path) -> None:
    photo = make_image("in/a.jpg")
    before = photo.read_bytes()
    run_session(tmp_path / "in")
    assert photo.read_bytes() == before


def cli_json(argv: list[str], capfd: pytest.CaptureFixture[str]) -> tuple[int, list[dict]]:
    code = run(argv, model_factory=FAKE_FACTORY)
    lines = [json.loads(line) for line in capfd.readouterr().out.splitlines() if line]
    return code, lines


def test_cli_json_mode_end_to_end(
    make_image: MakeImage, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    make_image("in/a.jpg")
    make_image("in/b.jpg")
    code, events = cli_json(
        [str(tmp_path / "in"), "--json", "--yes", "--min-free-ram", "0.1"], capfd
    )
    assert code == 0
    kinds = [e["type"] for e in events]
    assert kinds[0] == "scan"
    assert kinds[1] == "plan"
    assert kinds[-1] == "finished"
    assert events[-1]["processed"] == 2
    with Image.open(tmp_path / "in-no-bg" / "a.png") as image:
        assert image.mode == "RGBA"


def test_cli_requires_yes_when_not_interactive(
    make_image: MakeImage, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    make_image("in/a.jpg")
    code, events = cli_json([str(tmp_path / "in"), "--json", "--min-free-ram", "0.1"], capfd)
    assert code == 2
    assert events[-1]["type"] == "error"
    assert not (tmp_path / "in-no-bg").exists()


def test_cli_dry_run_writes_nothing(
    make_image: MakeImage, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    make_image("in/a.jpg")
    code, events = cli_json([str(tmp_path / "in"), "--json", "--dry-run"], capfd)
    assert code == 0
    assert [e["type"] for e in events] == ["scan", "plan"]
    assert not (tmp_path / "in-no-bg").exists()


def test_cli_missing_path(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    code, events = cli_json([str(tmp_path / "nope"), "--json", "--yes"], capfd)
    assert code == 2
    assert events == [{"type": "error", "at": events[0]["at"], "message": events[0]["message"]}]


def test_cli_exit_code_1_when_an_image_fails(
    make_image: MakeImage, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    make_image("in/good.jpg")
    bad = make_image("in/bad.jpg", size=(400, 300))
    data = bad.read_bytes()
    bad.write_bytes(data[: len(data) * 6 // 10])  # header intact, pixel data truncated
    code, events = cli_json(
        [str(tmp_path / "in"), "--json", "--yes", "--min-free-ram", "0.1"], capfd
    )
    assert code == 1
    assert len(events[-1]["failed"]) == 1


def test_doctor_json(capfd: pytest.CaptureFixture[str]) -> None:
    main(["doctor", "--json"])
    report = json.loads(capfd.readouterr().out)
    assert report["checks"]["heic"] is True
    assert report["checks"]["avif"] is True


def test_json_stdout_stays_clean_when_libraries_print(
    make_image: MakeImage, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    make_image("in/a.jpg")
    argv = [str(tmp_path / "in"), "--json", "--yes", "--min-free-ram", "0.1"]
    assert run(argv, model_factory=NOISY_FACTORY) == 0
    out, err = capfd.readouterr()
    for line in out.splitlines():
        json.loads(line)  # every stdout line is an event
    assert "PERFORMANCE WARNING" in err
