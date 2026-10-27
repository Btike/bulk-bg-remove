"""Where outputs go: a new `<name>-no-bg/` folder next to the input.

`shoe.jpg` -> `shoe-no-bg/shoe.png`; `products/` -> `products-no-bg/` with the
same sub-folder layout. Originals are never written to, and output folders are
never scanned as input.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .domain import OutputFormat

OUTPUT_SUFFIX = "-no-bg"


@dataclass(frozen=True, slots=True)
class OutputTarget:
    source: Path
    destination: Path


def output_root_for(input_path: Path) -> Path:
    """The output folder for a file or folder input, created next to it."""
    resolved = input_path.resolve()
    name = resolved.stem if resolved.is_file() else resolved.name
    if not name:
        raise ValueError(f"cannot derive an output folder name for {input_path}")
    return resolved.parent / f"{name}{OUTPUT_SUFFIX}"


def is_output_dir(path: Path) -> bool:
    return path.name.endswith(OUTPUT_SUFFIX)


def map_outputs(
    sources: Sequence[Path],
    input_root: Path,
    output_root: Path,
    output_format: OutputFormat,
) -> list[OutputTarget]:
    """Map each source to a unique destination under `output_root`.

    Sources sharing a folder and a stem (`shoe.jpg` + `shoe.png`) would write the
    same file, so each gets its source extension appended (`shoe_jpg.png`,
    `shoe_png.png`). Names are compared case-insensitively because macOS and
    Windows filesystems are.
    """
    ext = output_format.extension
    groups: dict[tuple[Path, str], list[Path]] = defaultdict(list)
    for source in sources:
        rel = source.relative_to(input_root)
        groups[(rel.parent, source.stem.lower())].append(source)

    candidates: list[tuple[Path, Path, str]] = []
    for (rel_dir, _), members in groups.items():
        for source in members:
            if len(members) == 1:
                stem = source.stem
            else:
                stem = f"{source.stem}_{source.suffix.lstrip('.').lower()}"
            candidates.append((source, rel_dir, stem))

    taken: set[str] = set()
    targets: list[OutputTarget] = []
    for source, rel_dir, stem in sorted(candidates, key=lambda c: str(c[0])):
        name = f"{stem}{ext}"
        counter = 2
        while (key := str(rel_dir / name).lower()) in taken:
            name = f"{stem}-{counter}{ext}"
            counter += 1
        taken.add(key)
        targets.append(OutputTarget(source=source, destination=output_root / rel_dir / name))
    return targets
