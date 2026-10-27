"""Phase 1 inventory: what gets processed, and what is left out *and reported*."""

from __future__ import annotations

from pathlib import Path

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
