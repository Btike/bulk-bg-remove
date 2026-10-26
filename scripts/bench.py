"""Benchmark models on a folder of test photos: quality sheets, time and peak RAM.

    uv run python scripts/bench.py INPUT_DIR RESULTS_DIR [--model M ...] [--matting]
                                   [--reference REF_DIR]

For every configuration it runs the real CLI (`--json`, one worker), samples
the worker's resident memory, and writes:

- RESULTS_DIR/<config>/output/          the cut-outs
- RESULTS_DIR/<config>/sheet-<n>.png    original | cut-out on a checkerboard [| reference]
- RESULTS_DIR/summary.md                time per image, peak RAM, failures, warnings

`--reference` points to earlier cut-outs (same file stems) to compare against.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import psutil
from PIL import Image, ImageDraw

from bulk_bg_remove.core.io import load_source

THUMB = 300
PER_SHEET = 12


@dataclass
class Result:
    config: str
    images: int = 0
    processed: int = 0
    failed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    seconds: list[float] = field(default_factory=list)
    peak_rss_gb: float = 0.0
    pairs: list[tuple[Path, Path]] = field(default_factory=list)


def run_config(input_dir: Path, out_dir: Path, model: str, matting: bool, label: str) -> Result:
    cmd = [
        sys.executable, "-m", "bulk_bg_remove.cli", str(input_dir), "-o", str(out_dir),
        "--json", "--yes", "--force", "--workers", "1", "--model", model,
    ]  # fmt: skip
    if matting:
        cmd.append("--alpha-matting")
    result = Result(label)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    root = psutil.Process(proc.pid)
    done = threading.Event()

    def sample() -> None:
        while not done.is_set():
            try:
                for child in root.children(recursive=True):
                    try:
                        rss = child.memory_info().rss / 1e9
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                    result.peak_rss_gb = max(result.peak_rss_gb, rss)
            except psutil.NoSuchProcess:
                return
            time.sleep(0.2)

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    sources: dict[int, Path] = {}
    destinations: dict[int, Path] = {}
    assert proc.stdout is not None
    for line in proc.stdout:
        event = json.loads(line)
        kind = event["type"]
        if kind == "job_started":
            sources[event["job"]] = Path(event["source"])
            destinations[event["job"]] = Path(event["destination"])
        elif kind == "job_done":
            result.seconds.append(event["seconds"])
            job = event["job"]
            result.pairs.append((sources[job], destinations[job]))
        elif kind == "finished":
            result.images = event["images"]
            result.processed = event["processed"]
            result.failed = [f"{Path(f['path']).name}: {f['reason']}" for f in event["failed"]]
            result.warnings = [f"{Path(w['path']).name}: {w['warning']}" for w in event["warnings"]]
        elif kind == "plan":
            result.failed += [f"blocked: {b}" for b in event["blockers"]]
        elif kind == "error":
            result.failed.append(event["message"])
    proc.wait()
    done.set()
    sampler.join()
    return result


def checkerboard(size: tuple[int, int], cell: int = 12) -> Image.Image:
    board = Image.new("RGB", size, (255, 255, 255))
    draw = ImageDraw.Draw(board)
    for y in range(0, size[1], cell):
        for x in range(0, size[0], cell):
            if (x // cell + y // cell) % 2:
                draw.rectangle((x, y, x + cell - 1, y + cell - 1), fill=(204, 204, 204))
    return board


def tile(path: Path | None, transparent: bool) -> Image.Image:
    canvas = (
        checkerboard((THUMB, THUMB)) if transparent else Image.new("RGB", (THUMB, THUMB), "white")
    )
    if path is None or not path.is_file():
        return canvas
    if not transparent:
        image = load_source(path).rgb  # same decoding as the tool: EXIF, 16-bit, HEIC
        image.thumbnail((THUMB, THUMB))
        canvas.paste(image, ((THUMB - image.width) // 2, (THUMB - image.height) // 2))
        return canvas
    with Image.open(path) as image:
        image.thumbnail((THUMB, THUMB))
        offset = ((THUMB - image.width) // 2, (THUMB - image.height) // 2)
        if image.mode in ("RGBA", "LA", "P"):
            rgba = image.convert("RGBA")
            canvas.paste(rgba, offset, rgba)
        else:
            canvas.paste(image.convert("RGB"), offset)
    return canvas


def write_sheets(result: Result, folder: Path, reference: Path | None) -> None:
    columns = 3 if reference else 2
    pairs = sorted(result.pairs, key=lambda p: str(p[0]))
    for start in range(0, len(pairs), PER_SHEET):
        chunk = pairs[start : start + PER_SHEET]
        sheet = Image.new("RGB", (columns * (THUMB + 8), len(chunk) * (THUMB + 26)), "white")
        draw = ImageDraw.Draw(sheet)
        for row, (source, output) in enumerate(chunk):
            y = row * (THUMB + 26)
            tiles = [tile(source, False), tile(output, True)]
            if reference:
                tiles.append(tile(reference / f"{source.stem}.png", True))
            for col, image in enumerate(tiles):
                sheet.paste(image, (col * (THUMB + 8), y))
            draw.text((4, y + THUMB + 6), source.name, fill="black")
        sheet.save(folder / f"sheet-{start // PER_SHEET + 1}.png")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("results", type=Path)
    parser.add_argument("--model", action="append", dest="models")
    parser.add_argument("--matting", action="store_true", help="also run each model with matting")
    parser.add_argument("--reference", type=Path)
    args = parser.parse_args()

    models = args.models or ["birefnet-general-lite"]
    configs = [(m, False) for m in models] + ([(m, True) for m in models] if args.matting else [])
    results: list[Result] = []
    for model, matting in configs:
        label = model + ("+matting" if matting else "")
        folder = args.results / label
        folder.mkdir(parents=True, exist_ok=True)
        print(f"running {label} …", flush=True)
        result = run_config(args.input, folder / "output", model, matting, label)
        write_sheets(result, folder, args.reference)
        results.append(result)
        avg = sum(result.seconds) / len(result.seconds) if result.seconds else 0.0
        for problem in result.failed:
            print(f"  ✗ {problem}", flush=True)
        print(f"  {result.processed}/{result.images} ok · {avg:.1f} s/img · "
              f"peak {result.peak_rss_gb:.1f} GB", flush=True)  # fmt: skip

    lines = [
        f"# Bench — {args.input}",
        "",
        "| Config | OK | Failed | Warnings | Avg s/img | Max s/img | Peak RAM (GB) |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        avg = sum(r.seconds) / len(r.seconds) if r.seconds else 0.0
        worst = max(r.seconds, default=0.0)
        lines.append(
            f"| {r.config} | {r.processed}/{r.images} | {len(r.failed)} | {len(r.warnings)} "
            f"| {avg:.1f} | {worst:.1f} | {r.peak_rss_gb:.1f} |"
        )
    for r in results:
        if r.failed or r.warnings:
            lines += ["", f"## {r.config}", *[f"- ✗ {f}" for f in r.failed]]
            lines += [f"- ! {w}" for w in r.warnings]
    (args.results / "summary.md").write_text("\n".join(lines) + "\n")
    print(f"summary → {args.results / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
