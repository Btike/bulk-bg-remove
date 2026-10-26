"""Planning rules: worker count, RAM floor, disk check — pure, no I/O."""

from __future__ import annotations

from pathlib import Path

from bulk_bg_remove.bulk.plan import PlanSettings, make_plan
from bulk_bg_remove.bulk.scan import Hardware, ImageSource, Inventory
from bulk_bg_remove.domain import OutputFormat


def hardware(free_gb: float = 16.0, perf: int = 4, logical: int = 10) -> Hardware:
    return Hardware(
        platform="test",
        cpu_name="test-cpu",
        cpu_logical=logical,
        cpu_performance=perf,
        ram_total_gb=32.0,
        ram_available_gb=free_gb,
        device="cpu",
        coreml_listed=False,
    )


def inventory(count: int = 10, megapixels: float = 1.0) -> Inventory:
    side = int((megapixels * 1e6) ** 0.5)
    sources = tuple(
        ImageSource(Path(f"/in/{i}.jpg"), "JPEG", side, side, 500_000) for i in range(count)
    )
    return Inventory(Path("/in"), Path("/in"), sources, ())


SETTINGS = PlanSettings(model="birefnet-general-lite", output_format=OutputFormat.PNG)


def test_small_machine_runs_as_a_queue() -> None:
    plan = make_plan(hardware(free_gb=4.5), inventory(), 100.0, SETTINGS)
    assert plan.ok
    assert plan.workers == 1
    assert plan.mode == "queue"
    assert plan.binding_limit == "RAM"


def test_big_machine_runs_in_parallel_capped_by_performance_cores() -> None:
    plan = make_plan(hardware(free_gb=30.0, perf=3), inventory(), 100.0, SETTINGS)
    assert plan.workers == 3
    assert plan.mode == "parallel"
    assert plan.binding_limit == "CPU"
    assert plan.threads_per_worker == 3  # 10 logical cores // 3 workers


def test_never_more_workers_than_images() -> None:
    plan = make_plan(hardware(free_gb=30.0), inventory(count=2), 100.0, SETTINGS)
    assert plan.workers == 2


def test_refuses_to_start_below_the_ram_floor() -> None:
    plan = make_plan(hardware(free_gb=3.0), inventory(), 100.0, SETTINGS)
    assert not plan.ok
    assert "free RAM 3.0 GB is below the 4.0 GB minimum" in plan.blockers[0]


def test_alpha_matting_raises_the_floor() -> None:
    settings = PlanSettings(
        model="birefnet-general-lite", output_format=OutputFormat.PNG, alpha_matting=True
    )
    plan = make_plan(hardware(free_gb=4.5), inventory(), 100.0, settings)
    assert not plan.ok
    assert plan.min_free_ram_gb >= 5.0


def test_explicit_floor_override() -> None:
    settings = PlanSettings(
        model="birefnet-general-lite", output_format=OutputFormat.PNG, min_free_ram_gb=10.0
    )
    assert not make_plan(hardware(free_gb=8.0), inventory(), 100.0, settings).ok


def test_blocks_when_disk_is_too_small() -> None:
    plan = make_plan(hardware(), inventory(count=100, megapixels=12), 1.0, SETTINGS)
    assert any("not enough disk space" in b for b in plan.blockers)


def test_blocks_when_there_are_no_images() -> None:
    plan = make_plan(hardware(), inventory(count=0), 100.0, SETTINGS)
    assert plan.blockers == ("no supported images found",)


def test_worker_override_is_honoured_with_a_warning() -> None:
    settings = PlanSettings(
        model="birefnet-general-lite", output_format=OutputFormat.PNG, workers=8
    )
    plan = make_plan(hardware(free_gb=6.0), inventory(), 100.0, settings)
    assert plan.workers == 8
    assert plan.binding_limit == "--workers"
    assert plan.warnings


def test_heavier_model_needs_more_ram_per_worker() -> None:
    lite = make_plan(hardware(free_gb=12.0, perf=8), inventory(), 100.0, SETTINGS)
    full = make_plan(
        hardware(free_gb=12.0, perf=8),
        inventory(),
        100.0,
        PlanSettings(model="birefnet-general", output_format=OutputFormat.PNG),
    )
    assert full.workers < lite.workers


def test_pause_threshold_tracks_image_size() -> None:
    small = make_plan(hardware(), inventory(megapixels=1), 100.0, SETTINGS)
    large = make_plan(hardware(), inventory(megapixels=48), 100.0, SETTINGS)
    assert large.pause_below_gb > small.pause_below_gb
