"""Output naming: `<name>-no-bg/` next to the input, unique destinations."""

from __future__ import annotations

from pathlib import Path

from bulk_bg_remove.domain import OutputFormat
from bulk_bg_remove.paths import is_output_dir, map_outputs, output_root_for


def test_single_file_output_folder_is_named_after_the_photo(tmp_path: Path) -> None:
    photo = tmp_path / "shoe.jpg"
    photo.write_bytes(b"x")
    assert output_root_for(photo) == tmp_path.resolve() / "shoe-no-bg"


def test_folder_output_is_a_sibling_named_after_the_folder(tmp_path: Path) -> None:
    folder = tmp_path / "products"
    folder.mkdir()
    assert output_root_for(folder) == tmp_path.resolve() / "products-no-bg"


def test_output_dirs_are_recognised() -> None:
    assert is_output_dir(Path("/a/products-no-bg"))
    assert not is_output_dir(Path("/a/products"))


def test_mirrors_subfolders_and_switches_extension(tmp_path: Path) -> None:
    root = tmp_path / "products"
    sources = [root / "a.jpg", root / "shoes" / "b.heic"]
    out = tmp_path / "products-no-bg"
    targets = map_outputs(sources, root, out, OutputFormat.PNG)
    assert [t.destination for t in targets] == [out / "a.png", out / "shoes" / "b.png"]


def test_same_stem_different_extension_does_not_collide(tmp_path: Path) -> None:
    root = tmp_path / "in"
    sources = [root / "shoe.jpg", root / "shoe.png", root / "hat.jpg"]
    targets = map_outputs(sources, root, tmp_path / "out", OutputFormat.PNG)
    names = sorted(t.destination.name for t in targets)
    assert names == ["hat.png", "shoe_jpg.png", "shoe_png.png"]


def test_case_insensitive_collisions_get_a_counter(tmp_path: Path) -> None:
    root = tmp_path / "in"
    sources = [root / "Shoe.jpg", root / "shoe.JPG"]
    targets = map_outputs(sources, root, tmp_path / "out", OutputFormat.WEBP)
    names = {t.destination.name.lower() for t in targets}
    assert len(names) == 2
    assert all(n.endswith(".webp") for n in names)


def test_same_name_in_different_subfolders_is_fine(tmp_path: Path) -> None:
    root = tmp_path / "in"
    sources = [root / "a" / "x.jpg", root / "b" / "x.jpg"]
    targets = map_outputs(sources, root, tmp_path / "out", OutputFormat.PNG)
    assert {t.destination.name for t in targets} == {"x.png"}
