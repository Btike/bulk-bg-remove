"""Blind comparison grids for judging models: one grid per image, models as letters.

    uv run python scripts/bench_grids.py INPUT_DIR RESULTS_DIR GRIDS_DIR [--model M ...]

INPUT_DIR holds the photos (sub-folders allowed); RESULTS_DIR is what
`scripts/bench.py` wrote for that input (`<model>/output/<stem>.png`). Each grid
shows the original and every model's cut-out on a checkerboard, labelled A, B,
C… in a fixed shuffled order, so a judge cannot tell which model is which.
`GRIDS_DIR/index.json` maps the letters back to models.

For close-ups while judging:

    uv run python scripts/bench_grids.py --zoom GRIDS_DIR STEM LETTERS X0 Y0 X1 Y1

crops the same region (fractions 0-1 of the image) of the original and the
given letters at full resolution and prints the path of the PNG it wrote.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw

from bulk_bg_remove.core.io import load_source

TILE = 360
COLUMNS = 4
DEFAULT_MODELS = [
    "birefnet-general-lite",
    "birefnet-general",
    "birefnet-hrsod",
    "birefnet-massive",
    "birefnet-dis",
    "birefnet-portrait",
    "isnet-general-use",
    "isnet-anime",
    "u2net",
    "u2net_human_seg",
    "silueta",
]


def checker(size: tuple[int, int], cell: int = 14) -> Image.Image:
    board = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(board)
    for y in range(0, size[1], cell):
        for x in range(0, size[0], cell):
            if (x // cell + y // cell) % 2:
                draw.rectangle((x, y, x + cell - 1, y + cell - 1), fill=(200, 200, 200))
    return board


def fit(image: Image.Image, size: int) -> Image.Image:
    copy = image.copy()
    copy.thumbnail((size, size), Image.Resampling.LANCZOS)
    return copy


def make_grids(inputs: Path, results: Path, grids: Path, models: list[str]) -> None:
    order = models[:]
    random.Random(7).shuffle(order)  # fixed, so re-runs give the same letters
    letters = {chr(ord("A") + i): model for i, model in enumerate(order)}
    sources = sorted(p for p in inputs.rglob("*") if p.is_file() and not p.name.startswith("."))
    grids.mkdir(parents=True, exist_ok=True)
    done = []
    for source in sources:
        try:
            original = load_source(source).rgb
        except (OSError, ValueError):
            continue
        tiles = [("ORIGINAL", None), *letters.items()]
        rows = (len(tiles) + COLUMNS - 1) // COLUMNS
        sheet = Image.new("RGB", (COLUMNS * (TILE + 10), rows * (TILE + 40)), "white")
        draw = ImageDraw.Draw(sheet)
        for k, (label, model) in enumerate(tiles):
            x, y = (k % COLUMNS) * (TILE + 10), (k // COLUMNS) * (TILE + 40)
            if model is None:
                image = fit(original, TILE)
                tile = Image.new("RGB", (TILE, TILE), "white")
                tile.paste(image, ((TILE - image.width) // 2, (TILE - image.height) // 2))
            else:
                tile = checker((TILE, TILE))
                output = results / model / "output" / f"{source.stem}.png"
                if output.is_file():
                    with Image.open(output) as opened:
                        image = fit(opened.convert("RGBA"), TILE)
                    offset = ((TILE - image.width) // 2, (TILE - image.height) // 2)
                    tile.paste(image, offset, image)
                else:
                    label += " (missing)"
            sheet.paste(tile, (x, y + 34))
            draw.text((x + 4, y + 8), label, fill="black")
        sheet.save(grids / f"{source.stem}.png")
        done.append(str(source.relative_to(inputs)))
    index = {"letters": letters, "inputs": str(inputs), "results": str(results), "images": done}
    (grids / "index.json").write_text(json.dumps(index, indent=1))
    print(f"{len(done)} grids → {grids}")


def zoom(grids: Path, stem: str, names: list[str], box: tuple[float, ...]) -> None:
    index = json.loads((grids / "index.json").read_text())
    inputs, results = Path(index["inputs"]), Path(index["results"])
    source = next(p for p in inputs.rglob(f"{stem}.*") if p.is_file())
    crops = []
    for name in names:
        if name == "ORIGINAL":
            image = load_source(source).rgb.convert("RGBA")
        else:
            model = index["letters"][name]
            with Image.open(results / model / "output" / f"{stem}.png") as opened:
                image = opened.convert("RGBA")
        w, h = image.size
        crop = image.crop((int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)))
        crop.thumbnail((700, 700), Image.Resampling.LANCZOS)
        base = checker(crop.size, 12)
        base.paste(crop, (0, 0), crop)
        crops.append((name, base))
    width = sum(c.width for _, c in crops) + 10 * len(crops)
    sheet = Image.new("RGB", (width, max(c.height for _, c in crops) + 30), "white")
    draw = ImageDraw.Draw(sheet)
    x = 0
    for name, crop in crops:
        draw.text((x + 4, 8), name, fill="black")
        sheet.paste(crop, (x, 30))
        x += crop.width + 10
    out = grids / "zoom" / f"{stem}-{'-'.join(names)}-{box[0]}-{box[1]}.png"
    out.parent.mkdir(exist_ok=True)
    sheet.save(out)
    print(out)


def main() -> int:
    if sys.argv[1:2] == ["--zoom"]:
        _, _, grids, stem, letters, *box = sys.argv
        names = letters.split(",") if "," in letters else list(letters)
        zoom(Path(grids), stem, names, tuple(float(v) for v in box))
        return 0
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", type=Path)
    parser.add_argument("results", type=Path)
    parser.add_argument("grids", type=Path)
    parser.add_argument("--model", action="append", dest="models")
    args = parser.parse_args()
    make_grids(args.inputs, args.results, args.grids, args.models or DEFAULT_MODELS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
