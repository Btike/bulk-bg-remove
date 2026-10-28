"""Runner integration: real worker processes and pipeline, fake model."""

from __future__ import annotations

import json
import signal
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image

from bulk_bg_remove.bulk.events import Event
from bulk_bg_remove.bulk.runner import Job, Runner, RunSettings, crash_reason
from bulk_bg_remove.cli import run
from bulk_bg_remove.core.matting import EdgeMode
from bulk_bg_remove.domain import OutputFormat
from conftest import MakeImage
from fakes import FAILING_FACTORY, FAKE_FACTORY, ILLEGAL_FACTORY


def settings(workers: int = 2, factory: str = FAKE_FACTORY, **overrides: float) -> RunSettings:
    return RunSettings(
        model="fake",
        output_format=OutputFormat.PNG,
        edges=EdgeMode.MATTE,
        workers=workers,
        threads_per_worker=1,
        pause_below_gb=overrides.get("pause_below_gb", 0.0),
        pause_timeout_s=overrides.get("pause_timeout_s", 600.0),
        model_factory=factory,
        recycle_above_gb=overrides.get("recycle_above_gb"),
        recycle_when_free_below_gb=overrides.get("recycle_when_free_below_gb"),
    )


def jobs_for(paths: list[Path], out: Path) -> list[Job]:
    return [Job(i, p, out / f"{p.stem}.png") for i, p in enumerate(paths)]


def types(events: list[Event]) -> list[str]:
    return [e.type for e in events]


def test_processes_every_image_in_parallel(make_image: MakeImage, tmp_path: Path) -> None:
    sources = [make_image(f"in/{i}.jpg") for i in range(5)]
    events: list[Event] = []
    outcomes = Runner(jobs_for(sources, tmp_path / "out"), settings(), events.append).run()

    assert all(o.ok for o in outcomes)
    assert len(outcomes) == 5
    for outcome in outcomes:
        with Image.open(outcome.job.destination) as image:
            assert image.mode == "RGBA"
            assert image.size == (64, 48)
    assert types(events).count("worker_ready") == 2
    assert types(events).count("job_done") == 5
    assert "job_stage" in types(events)
    assert not list((tmp_path / "out").glob(".*.part"))  # no temp files left behind


def test_a_corrupt_file_fails_alone(make_image: MakeImage, tmp_path: Path) -> None:
    good = make_image("in/good.jpg")
    bad = make_image("in/bad.jpg", size=(400, 300))
    bad.write_bytes(bad.read_bytes()[:300])  # valid header, truncated data
    events: list[Event] = []
    outcomes = Runner(jobs_for([good, bad], tmp_path / "out"), settings(), events.append).run()

    by_name = {o.job.source.name: o for o in outcomes}
    assert by_name["good.jpg"].ok
    assert not by_name["bad.jpg"].ok
    assert by_name["bad.jpg"].reason
    assert not (tmp_path / "out" / "bad.png").exists()


def test_pauses_on_low_ram_and_resumes(make_image: MakeImage, tmp_path: Path) -> None:
    sources = [make_image(f"in/{i}.jpg") for i in range(2)]
    readings: Iterator[float] = iter([0.5, 0.5, 0.5] + [8.0] * 1000)
    events: list[Event] = []
    runner = Runner(
        jobs_for(sources, tmp_path / "out"),
        settings(workers=1, pause_below_gb=1.0),
        events.append,
        ram_probe=lambda: next(readings),
        poll_seconds=0.05,
    )
    outcomes = runner.run()

    assert all(o.ok for o in outcomes)
    seen = types(events)
    assert seen.index("paused") < seen.index("resumed") < seen.index("job_started")


def test_gives_up_when_ram_never_recovers(make_image: MakeImage, tmp_path: Path) -> None:
    sources = [make_image(f"in/{i}.jpg") for i in range(2)]
    runner = Runner(
        jobs_for(sources, tmp_path / "out"),
        settings(workers=1, pause_below_gb=1.0, pause_timeout_s=0.2),
        lambda event: None,
        ram_probe=lambda: 0.1,
        poll_seconds=0.05,
    )
    outcomes = runner.run()
    assert [o.ok for o in outcomes] == [False, False]
    assert all("RAM" in (o.reason or "") for o in outcomes)


def test_model_that_cannot_load_fails_every_job(make_image: MakeImage, tmp_path: Path) -> None:
    sources = [make_image(f"in/{i}.jpg") for i in range(3)]
    events: list[Event] = []
    outcomes = Runner(
        jobs_for(sources, tmp_path / "out"), settings(factory=FAILING_FACTORY), events.append
    ).run()
    assert len(outcomes) == 3
    assert not any(o.ok for o in outcomes)
    assert "worker_failed" in types(events)


def test_cancel_stops_handing_out_images(make_image: MakeImage, tmp_path: Path) -> None:
    sources = [make_image(f"in/{i}.jpg") for i in range(6)]
    events: list[Event] = []
    runner: Runner

    def emit(event: Event) -> None:
        events.append(event)
        if event.type == "job_done":
            runner.cancel()

    runner = Runner(jobs_for(sources, tmp_path / "out"), settings(workers=1), emit)
    outcomes = runner.run()
    reasons = [o.reason for o in outcomes if not o.ok]
    assert reasons and all(r == "cancelled" for r in reasons)
    assert sum(o.ok for o in outcomes) < 6


def test_a_worker_over_its_memory_limit_is_restarted_without_losing_images(
    make_image: MakeImage, tmp_path: Path
) -> None:
    sources = [make_image(f"in/{i}.jpg") for i in range(4)]
    events: list[Event] = []
    # Any real process is above 0 GB, so every worker retires after each image.
    outcomes = Runner(
        jobs_for(sources, tmp_path / "out"),
        settings(workers=2, recycle_above_gb=0.0),
        events.append,
    ).run()
    assert len(outcomes) == 4
    assert all(o.ok for o in outcomes)
    assert all((tmp_path / "out" / f"{i}.png").is_file() for i in range(4))
    assert types(events).count("worker_loading") >= 4  # 2 at start + restarts


def test_no_restart_when_nothing_is_left_to_do(make_image: MakeImage, tmp_path: Path) -> None:
    sources = [make_image("in/0.jpg")]
    events: list[Event] = []
    outcomes = Runner(
        jobs_for(sources, tmp_path / "out"),
        settings(workers=1, recycle_above_gb=0.0),
        events.append,
    ).run()
    assert [o.ok for o in outcomes] == [True]
    assert types(events).count("worker_loading") == 1  # retired after the last image, not reloaded


def test_a_worker_keeps_running_while_the_machine_has_room(
    make_image: MakeImage, tmp_path: Path
) -> None:
    sources = [make_image(f"in/{i}.jpg") for i in range(3)]
    events: list[Event] = []
    # Over its limit, but free RAM is never below 0 GB: no restart.
    outcomes = Runner(
        jobs_for(sources, tmp_path / "out"),
        settings(workers=1, recycle_above_gb=0.0, recycle_when_free_below_gb=0.0),
        events.append,
    ).run()
    assert all(o.ok for o in outcomes)
    assert types(events).count("worker_loading") == 1


def test_crash_reasons_name_the_signal() -> None:
    assert "SIGILL" in crash_reason(-signal.SIGILL)
    assert "segmentation fault" in crash_reason(-signal.SIGSEGV)
    assert "exit code 3" in crash_reason(3)
    if hasattr(signal, "SIGKILL"):
        assert "out of memory" in crash_reason(-signal.SIGKILL)


@pytest.mark.skipif(
    sys.platform != "linux",
    reason="the crash happens on Linux VMs; elsewhere a real SIGILL opens a crash dialog",
)
def test_a_worker_killed_by_an_illegal_instruction_says_so(
    make_image: MakeImage, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    make_image("in/a.jpg")
    code = run(
        [str(tmp_path / "in"), "--json", "--yes", "--min-free-ram", "0.1"],
        model_factory=ILLEGAL_FACTORY,
    )
    events = [json.loads(line) for line in capfd.readouterr().out.splitlines() if line]
    assert code == 1
    failed = events[-1]["failed"]
    assert len(failed) == 1 and "SIGILL" in failed[0]["reason"]
