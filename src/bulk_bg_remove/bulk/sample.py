"""Contact sheets for agents with vision, always written to a temporary folder.

- Sample sheet: a few input photos, to pick `--profile` before a run.
- Review sheets: every cut-out next to its original, on black and on white,
  to spot halos, leftovers and missing parts after a run.
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


# Review sheets: each cut-out next to its original, on black and on white, so a
# reviewer (an agent with vision) can spot halos, leftovers and missing parts.
REVIEW_TILE = 220
REVIEW_PER_SHEET = 12
REVIEW_PER_ROW = 2


@dataclass(frozen=True, slots=True)
class ReviewEntry:
    number: int  # 1-based across all sheets of this run
    label: str  # the output path relative to the output folder: stable across runs
    source: Path
    output: Path


@dataclass(frozen=True, slots=True)
class ReviewSheet:
    path: Path
    entries: tuple[ReviewEntry, ...]


def _on(background: str, cutout: Image.Image) -> Image.Image:
    tile = Image.new("RGB", (REVIEW_TILE, REVIEW_TILE), background)
    tile.paste(
        cutout, ((REVIEW_TILE - cutout.width) // 2, (REVIEW_TILE - cutout.height) // 2), cutout
    )
    return tile


def _review_tiles(source: Path, output: Path) -> list[Image.Image]:
    original = load_source(source).rgb
    original.thumbnail((REVIEW_TILE, REVIEW_TILE))
    first = Image.new("RGB", (REVIEW_TILE, REVIEW_TILE), (40, 40, 40))
    first.paste(
        original, ((REVIEW_TILE - original.width) // 2, (REVIEW_TILE - original.height) // 2)
    )
    with Image.open(output) as opened:
        cutout = opened.convert("RGBA")
    cutout.thumbnail((REVIEW_TILE, REVIEW_TILE))
    return [first, _on("black", cutout), _on("white", cutout)]


def _label(output: Path, output_root: Path) -> str:
    try:
        return output.relative_to(output_root).as_posix()
    except ValueError:
        return output.name


def write_review_sheets(pairs: Sequence[tuple[Path, Path]], output_root: Path) -> list[ReviewSheet]:
    """Sheets of up to 12 (source, output) pairs: original | on black | on white."""
    folder = Path(tempfile.mkdtemp(prefix="bulk-bg-remove-review-"))
    entry_w = 3 * REVIEW_TILE + 16
    entry_h = REVIEW_TILE + LABEL_HEIGHT + 6
    sheets = []
    for start in range(0, len(pairs), REVIEW_PER_SHEET):
        chunk = pairs[start : start + REVIEW_PER_SHEET]
        rows = (len(chunk) + REVIEW_PER_ROW - 1) // REVIEW_PER_ROW
        sheet = Image.new("RGB", (REVIEW_PER_ROW * entry_w, rows * entry_h), (128, 128, 128))
        draw = ImageDraw.Draw(sheet)
        entries = []
        for index, (source, output) in enumerate(chunk):
            number = start + index + 1
            label = _label(output, output_root)
            x, y = (index % REVIEW_PER_ROW) * entry_w, (index // REVIEW_PER_ROW) * entry_h
            draw.text((x + 4, y + 3), f"#{number}  {label[-70:]}", fill="white")
            try:
                tiles = _review_tiles(source, output)
            except Exception:  # noqa: BLE001 — show the gap, keep the sheet
                draw.text((x + 8, y + entry_h // 2), "unreadable", fill="red")
                tiles = []
            for column, tile in enumerate(tiles):
                sheet.paste(tile, (x + column * (REVIEW_TILE + 4), y + LABEL_HEIGHT))
            entries.append(ReviewEntry(number, label, source, output))
        path = folder / f"review-{start // REVIEW_PER_SHEET + 1}.png"
        sheet.save(path)
        sheets.append(ReviewSheet(path, tuple(entries)))
    return sheets
