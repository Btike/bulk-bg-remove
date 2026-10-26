"""Planning rules: worker count, RAM floor, disk check — pure, no I/O."""

from __future__ import annotations

from pathlib import Path

from bulk_bg_remove.bulk.plan import PlanSettings, make_plan, minimum_free_ram_gb
from bulk_bg_remove.bulk.scan import Hardware, ImageSource, Inventory
from bulk_bg_remove.core.matting import EdgeMode
from bulk_bg_remove.domain import OutputFormat


def hardware(free_gb: float = 16.0, perf: int = 4, logical: int = 10) -> Hardware:
    return Hardware(
        platform="test",
        cpu_name="test-cpu",
        cpu_logical=logical,
        cpu_performance=perf,
        ram_total_gb=64.0,
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


def settings(**overrides: object) -> PlanSettings:
    base: dict[str, object] = {"model": "birefnet-general-lite", "output_format": OutputFormat.PNG}
    return PlanSettings(**(base | overrides))  # type: ignore[arg-type]


def test_typical_laptop_runs_as_a_queue() -> None:
    plan = make_plan(hardware(free_gb=12.0), inventory(), 100.0, settings())
    assert plan.ok
    assert plan.workers == 1
    assert plan.mode == "queue"
    assert plan.binding_limit == "RAM"


def test_big_machine_runs_in_parallel_capped_by_cpu() -> None:
    plan = make_plan(hardware(free_gb=40.0, perf=4), inventory(), 100.0, settings())
    assert plan.workers == 2  # two workers per four performance cores
    assert plan.mode == "parallel"
    assert plan.binding_limit == "CPU"
    assert plan.threads_per_worker == 4


def test_never_more_workers_than_images() -> None:
    plan = make_plan(hardware(free_gb=60.0, perf=8), inventory(count=2), 100.0, settings())
    assert plan.workers == 2


def test_refuses_to_start_below_what_the_model_needs() -> None:
    plan = make_plan(hardware(free_gb=10.0), inventory(), 100.0, settings())
    assert not plan.ok
    assert "free RAM 10.0 GB is below the" in plan.blockers[0]
    assert plan.min_free_ram_gb > 11.0


def test_matting_needs_more_ram_on_large_photos() -> None:
    big = inventory(megapixels=24)
    matte = make_plan(hardware(), big, 100.0, settings())
    fast = make_plan(hardware(), big, 100.0, settings(edges=EdgeMode.MASK))
    assert matte.min_free_ram_gb > fast.min_free_ram_gb


def test_explicit_floor_override() -> None:
    assert not make_plan(
        hardware(free_gb=15.0), inventory(), 100.0, settings(min_free_ram_gb=20.0)
    ).ok


def test_blocks_when_disk_is_too_small() -> None:
    plan = make_plan(hardware(), inventory(count=100, megapixels=12), 1.0, settings())
    assert any("not enough disk space" in b for b in plan.blockers)


def test_blocks_when_there_are_no_images() -> None:
    plan = make_plan(hardware(), inventory(count=0), 100.0, settings())
    assert plan.blockers == ("no supported images found",)


def test_worker_override_is_honoured_with_a_warning() -> None:
    plan = make_plan(hardware(free_gb=12.0), inventory(), 100.0, settings(workers=3))
    assert plan.workers == 3
    assert plan.binding_limit == "--workers"
    assert plan.warnings


def test_heavier_model_fits_fewer_workers() -> None:
    lite = make_plan(hardware(free_gb=52.0, perf=16), inventory(), 100.0, settings())
    full = make_plan(
        hardware(free_gb=52.0, perf=16), inventory(), 100.0, settings(model="birefnet-general")
    )
    assert full.workers < lite.workers


def test_lighter_model_needs_far_less_ram() -> None:
    assert minimum_free_ram_gb("isnet-general-use") < 5.0
    assert minimum_free_ram_gb("birefnet-general-lite") > 11.0


def test_parallel_needs_headroom_beyond_two_model_copies() -> None:
    assert make_plan(hardware(free_gb=22.0), inventory(), 100.0, settings()).workers == 1
    assert make_plan(hardware(free_gb=25.0), inventory(), 100.0, settings()).workers == 2
