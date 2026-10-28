"""Phase 1 inventory: what gets processed, and what is left out *and reported*."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from bulk_bg_remove.bulk.scan import discover
from conftest import MakeImage


def test_left_out_images_are_reported(make_image: MakeImage, tmp_path: Path) -> None:
    make_image("photos/a.jpg")
    make_image("photos/.hidden-photo.jpg")
    make_image("photos/old-run-no-bg/b.jpg")
    make_image("photos/.git/c.jpg")  # tooling folder: skipped silently
    (tmp_path / "photos" / ".DS_Store").write_bytes(b"\0")  # OS junk: silent
    (tmp_path / "photos" / "._a.jpg").write_bytes(b"\0")  # AppleDouble: silent

    inventory = discover(tmp_path / "photos")

    assert [s.path.name for s in inventory.sources] == ["a.jpg"]
    reported = {s.path.name: s.reason for s in inventory.skipped}
    assert reported == {
        ".hidden-photo.jpg": "hidden file",
        "old-run-no-bg": "earlier output folder (-no-bg)",
    }


def test_a_single_hidden_file_given_directly_is_processed(
    make_image: MakeImage, tmp_path: Path
) -> None:
    path = make_image(".hidden-photo.jpg")
    assert [s.path for s in discover(path).sources] == [path.resolve()]


def _cgroup(tmp_path, monkeypatch, files: dict[str, str]):
    import bulk_bg_remove.bulk.scan as scan_module

    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    monkeypatch.setattr(scan_module, "CGROUP_ROOT", tmp_path)
    monkeypatch.setattr(scan_module.sys, "platform", "linux")
    # A roomy host, so the container limit is what caps (CI runners can be small).
    host = SimpleNamespace(total=64 * 10**9, available=60 * 10**9)
    monkeypatch.setattr(scan_module.psutil, "virtual_memory", lambda: host)
    return scan_module


def test_a_container_limit_caps_the_ram_the_planner_sees(tmp_path, monkeypatch) -> None:
    gib = 10**9  # the engine counts GB as 10^9 bytes
    scan_module = _cgroup(
        tmp_path,
        monkeypatch,
        {
            "memory.max": str(4 * gib),
            "memory.current": str(gib),
            "memory.stat": f"anon 123\ninactive_file {gib // 2}\n",
        },
    )
    total, available = scan_module._memory()
    assert total == 4.0
    assert available == 3.5  # 4 GB limit - (1 GB used - 0.5 GB droppable cache)
    assert scan_module.probe_hardware().ram_available_gb == 3.5


def test_cgroup_v1_and_no_limit(tmp_path, monkeypatch) -> None:
    gib = 10**9  # the engine counts GB as 10^9 bytes
    scan_module = _cgroup(
        tmp_path,
        monkeypatch,
        {
            "memory/memory.limit_in_bytes": str(8 * gib),
            "memory/memory.usage_in_bytes": str(2 * gib),
            "memory/memory.stat": "total_inactive_file 0\n",
        },
    )
    assert scan_module._memory() == (8.0, 6.0)
    (tmp_path / "memory.max").write_text("max\n")  # v2 without a limit wins over v1 files
    (tmp_path / "memory" / "memory.limit_in_bytes").write_text(str(2**62))
    assert scan_module._memory() == (64.0, 60.0)  # no limit: the host's own RAM


def test_a_nested_cgroup_v2_limit_is_found(tmp_path, monkeypatch) -> None:
    gib = 10**9  # the engine counts GB as 10^9 bytes
    scan_module = _cgroup(
        tmp_path,
        monkeypatch,
        {
            "memory.max": "max\n",  # the root has no limit...
            "system.slice/job.scope/memory.max": str(4 * gib),  # ...the job's slice does
            "system.slice/job.scope/memory.current": str(gib),
            "proc": "0::/system.slice/job.scope\n",
        },
    )
    monkeypatch.setattr(scan_module, "PROC_SELF_CGROUP", tmp_path / "proc")
    assert scan_module._memory() == (4.0, 3.0)
