"""`--model auto` end to end: the session picks, explains or blocks; the sample sheet."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

import bulk_bg_remove.bulk.session as session_module
from bulk_bg_remove.bulk.events import Event
from bulk_bg_remove.bulk.models import AUTO_MODEL, Profile, choose_model
from bulk_bg_remove.bulk.sample import pick_sample
from bulk_bg_remove.bulk.scan import Hardware
from bulk_bg_remove.bulk.session import BulkOptions, BulkSession
from bulk_bg_remove.cli import run
from bulk_bg_remove.core.matting import EdgeMode
from conftest import MakeImage
from fakes import FAKE_FACTORY


def machine(free_gb: float) -> Hardware:
    return Hardware("Test", "CPU", 8, 4, 16.0, free_gb, "cpu", False)


def plan_event(
    path: Path, monkeypatch: pytest.MonkeyPatch, free_gb: float, **options: object
) -> tuple[BulkSession, dict[str, object]]:
    monkeypatch.setattr(session_module, "probe_hardware", lambda: machine(free_gb))
    events: list[Event] = []
    opts = BulkOptions(model=AUTO_MODEL, model_factory=FAKE_FACTORY, **options)  # type: ignore[arg-type]
    session = BulkSession(path, opts, events.append)
    session.prepare()
    plan = next(e for e in events if e.type == "plan")
    return session, plan.data


def test_products_on_a_4_gb_laptop_run_with_a_light_model(
    make_image: MakeImage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_image("in/a.jpg")
    session, plan = plan_event(tmp_path / "in", monkeypatch, 4.0)
    assert session.model == "silueta"
    assert plan["model"] == "silueta"
    assert plan["blockers"] == []
    choice = plan["model_choice"]
    assert isinstance(choice, dict) and choice["best_model"] == "birefnet-general-lite"


def test_hair_on_a_small_laptop_needs_the_users_ok(
    make_image: MakeImage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_image("in/a.jpg")
    _, plan = plan_event(tmp_path / "in", monkeypatch, 4.0, profile=Profile.COMPLEX)
    blockers = plan["blockers"]
    assert isinstance(blockers, list) and len(blockers) == 1
    assert "--allow-fallback" in str(blockers[0])
    assert "birefnet-general" in str(blockers[0])

    _, allowed = plan_event(
        tmp_path / "in", monkeypatch, 4.0, profile=Profile.COMPLEX, allow_fallback=True
    )
    assert allowed["blockers"] == []
    assert allowed["model"] == "silueta"


def test_plenty_of_ram_needs_no_explanation(
    make_image: MakeImage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_image("in/a.jpg")
    _, plan = plan_event(tmp_path / "in", monkeypatch, 30.0, profile=Profile.COMPLEX)
    assert plan["model"] == "birefnet-general"
    choice = plan["model_choice"]
    assert isinstance(choice, dict) and choice["gap"] == 0


def test_too_little_ram_for_any_model_reports_the_ram_blocker_only(
    make_image: MakeImage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_image("in/a.jpg")
    _, plan = plan_event(tmp_path / "in", monkeypatch, 1.0, profile=Profile.COMPLEX)
    blockers = plan["blockers"]
    assert isinstance(blockers, list) and len(blockers) == 1
    assert "free RAM 1.0 GB is below" in str(blockers[0])


def test_sample_spreads_over_the_folder() -> None:
    paths = [Path(f"{i:03d}.jpg") for i in range(100)]
    sample = pick_sample(paths, 12)
    assert len(sample) == 12
    assert sample[0] == paths[0] and sample[-1] == paths[-1]
    assert pick_sample(paths[:5], 12) == paths[:5]


def test_cli_writes_a_sample_sheet_outside_the_photos(
    make_image: MakeImage, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    for i in range(3):
        make_image(f"in/{i}.jpg")
    code = run(
        [str(tmp_path / "in"), "--json", "--dry-run", "--sample-sheet"], model_factory=FAKE_FACTORY
    )
    events = [json.loads(line) for line in capfd.readouterr().out.splitlines() if line]
    sheet = next(e for e in events if e["type"] == "sample_sheet")
    assert code in (0, 2)  # 2 only if this machine lacks free RAM
    assert sheet["images"] == ["0.jpg", "1.jpg", "2.jpg"]
    path = Path(sheet["path"])
    assert path.is_file() and tmp_path not in path.parents
    with Image.open(path) as image:
        assert image.width > 0


def test_doctor_is_ready_on_a_4_gb_laptop_with_a_light_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import bulk_bg_remove.doctor as doctor_module

    monkeypatch.setattr(doctor_module, "probe_hardware", lambda: machine(4.0))
    report = doctor_module.collect("auto")
    assert report["model"] == "silueta"
    assert report["ready"] is True
    auto = report["auto"]
    assert isinstance(auto, dict)
    complex_pick = auto["complex"]
    assert isinstance(complex_pick, dict) and complex_pick["model"] == "silueta"
    assert complex_pick["gap"] > 0.5  # a run would ask the user first


def test_an_empty_folder_only_says_there_are_no_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "in").mkdir()
    _, plan = plan_event(tmp_path / "in", monkeypatch, 4.0, profile=Profile.COMPLEX)
    assert plan["blockers"] == ["no supported images found"]


def test_auto_counts_the_shop_finish_in_the_ram_it_needs() -> None:
    from bulk_bg_remove.core.finish import Finish

    shop = Finish(background=(255, 255, 255), square=True, pad=7.5, size=2000)
    plain = choose_model(Profile.UNIFORM, 14.0, EdgeMode.MATTE, 45.0)
    shopped = choose_model(Profile.UNIFORM, 14.0, EdgeMode.MATTE, 45.0, shop)
    assert plain.model == "birefnet-general-lite" and plain.fits
    assert shopped.best_needs_gb > plain.best_needs_gb  # the finish needs more RAM
    assert shopped.fits and shopped.model != plain.model  # a lighter model that fits
