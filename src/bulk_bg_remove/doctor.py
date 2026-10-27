"""`bulk-bg-remove doctor` — check the install, the model and this machine."""

from __future__ import annotations

import argparse
import json
import platform
from collections.abc import Sequence

from PIL import features
from rich.console import Console

from . import __version__
from .bulk.events import JSONObject
from .bulk.models import AUTO_MODEL, Profile, choose_model
from .bulk.plan import MODEL_PEAK_GB, minimum_free_ram_gb, model_peak_gb
from .bulk.scan import probe_hardware
from .core.matting import EdgeMode
from .engine.model import downloaded_model_path

DOCTOR_MEGAPIXELS = 12.0  # a typical phone photo


def collect(model: str) -> JSONObject:
    hardware = probe_hardware()
    if model == AUTO_MODEL:  # what a run on product photos would use right now
        model = choose_model(
            Profile.UNIFORM, hardware.ram_available_gb, EdgeMode.MATTE, DOCTOR_MEGAPIXELS
        ).model
    weights = downloaded_model_path(model)
    needed = minimum_free_ram_gb(model)
    try:
        import pillow_heif  # noqa: F401

        heic = True
    except ImportError:
        heic = False
    checks: dict[str, bool] = {
        "heic": heic,
        "avif": bool(features.check("avif")),
        "webp": bool(features.check("webp")),
        "model_downloaded": weights is not None,
        "enough_free_ram": hardware.ram_available_gb >= needed,
    }
    return {
        "version": __version__,
        "python": platform.python_version(),
        "hardware": hardware.to_dict(),
        "model": model,
        "model_path": str(weights) if weights else None,
        "model_peak_ram_gb": model_peak_gb(model),
        "min_free_ram_gb": round(needed, 1),
        "checks": dict(checks),
        "ready": all(v for k, v in checks.items() if k != "model_downloaded"),
        # What `--model auto` would pick right now for each kind of image.
        "auto": {
            profile.value: choose_model(
                profile, hardware.ram_available_gb, EdgeMode.MATTE, DOCTOR_MEGAPIXELS
            ).to_dict()
            for profile in Profile
        },
        "models": [
            {
                "name": name,
                "min_free_ram_gb": round(minimum_free_ram_gb(name), 1),
                "fits": hardware.ram_available_gb >= minimum_free_ram_gb(name),
            }
            for name in MODEL_PEAK_GB
        ],
    }


def doctor(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="bulk-bg-remove doctor")
    parser.add_argument("--model", default=AUTO_MODEL)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = collect(args.model)
    if args.json:
        print(json.dumps(report))
        return 0 if report["ready"] else 2

    console = Console(highlight=False)
    hw = report["hardware"]
    checks = report["checks"]
    assert isinstance(hw, dict) and isinstance(checks, dict)
    console.print(f"[bold green]bulk-bg-remove {__version__}[/] · Python {report['python']}")
    console.print(
        f"  machine   {hw['cpu_name']} · {hw['cpu_performance']} perf cores · "
        f"{hw['ram_total_gb']} GB RAM ({hw['ram_available_gb']} GB free) · {hw['device']}"
    )
    labels = {
        "heic": "HEIC/HEIF input",
        "avif": "AVIF input",
        "webp": "WebP input/output",
        "model_downloaded": f"model {report['model']}",
        "enough_free_ram": f"free RAM ≥ {report['min_free_ram_gb']} GB (12 MP photos)",
    }
    for key, label in labels.items():
        ok = bool(checks[key])
        if key == "model_downloaded" and not ok:
            console.print(f"  [yellow]…[/] {label} — downloads automatically on first run")
        else:
            console.print(f"  {'[green]✓[/]' if ok else '[red]✗[/]'} {label}")
    console.print("[bold green]READY[/]" if report["ready"] else "[bold red]NOT READY[/]")
    return 0 if report["ready"] else 2
