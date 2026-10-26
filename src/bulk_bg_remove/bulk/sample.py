"""A contact sheet of a few input photos, so an agent can see what it is about to cut out.

The agent looks at it to pick `--profile` (uniform products, hair and fur,
people, cluttered scenes, drawings). It is written to a temporary folder,
never next to the user's photos.
"""

from __future__ import annotations

import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from ..core.io import load_source

SAMPLE_SIZE = 12
TILE = 256
COLUMNS = 4
LABEL_HEIGHT = 18


@dataclass(frozen=True, slots=True)
class SampleSheet:
    path: Path
    names: tuple[str, ...]


def pick_sample(paths: Sequence[Path], size: int = SAMPLE_SIZE) -> list[Path]:
    """Up to `size` paths spread evenly over the (sorted) list, first and last included."""
    ordered = sorted(paths)
    if len(ordered) <= size:
        return ordered
    step = (len(ordered) - 1) / (size - 1)
    return [ordered[round(i * step)] for i in range(size)]


def write_sample_sheet(paths: Sequence[Path], size: int = SAMPLE_SIZE) -> SampleSheet:
    sample = pick_sample(paths, size)
    rows = (len(sample) + COLUMNS - 1) // COLUMNS
    sheet = Image.new("RGB", (COLUMNS * TILE, rows * (TILE + LABEL_HEIGHT)), "white")
    draw = ImageDraw.Draw(sheet)
    names = []
    for index, path in enumerate(sample):
        x, y = (index % COLUMNS) * TILE, (index // COLUMNS) * (TILE + LABEL_HEIGHT)
        try:
            image = load_source(path).rgb
            image.thumbnail((TILE - 8, TILE - 8))
            sheet.paste(image, (x + (TILE - image.width) // 2, y + (TILE - image.height) // 2))
        except Exception:  # noqa: BLE001 — any decoder error: show it, keep the sheet
            draw.text((x + 8, y + TILE // 2), "unreadable", fill="red")
        draw.text((x + 4, y + TILE + 3), path.name[:40], fill="black")
        names.append(path.name)
    folder = Path(tempfile.mkdtemp(prefix="bulk-bg-remove-sample-"))
    destination = folder / "sample.png"
    sheet.save(destination)
    return SampleSheet(destination, tuple(names))
