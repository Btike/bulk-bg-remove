"""Phase 2 — plan: queue or parallel, how many workers, and whether it is safe to start.

Pure functions over the scan results, so every rule here is unit-tested.

Each worker process holds the whole model in RAM, so free RAM — not CPU — is
usually what caps parallelism. The run refuses to start below a free-RAM floor,
and while running it pauses dispatching new images whenever free RAM dips
below what one more image needs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..domain import OutputFormat
from .events import JSONObject
from .scan import Hardware, Inventory

# Resident RAM per worker for the model weights + ONNX session (measured).
MODEL_RAM_GB: dict[str, float] = {
    "birefnet-general-lite": 1.6,
    "birefnet-general": 3.0,
    "birefnet-portrait": 3.0,
    "birefnet-massive": 3.0,
    "birefnet-hrsod": 3.0,
    "birefnet-dis": 3.0,
    "isnet-general-use": 1.0,
    "u2net": 0.8,
}
UNKNOWN_MODEL_RAM_GB = 3.0

# Peak working memory per image, per megapixel (RGB + RGBA + mask buffers).
IMAGE_GB_PER_MEGAPIXEL = 0.03
# Alpha matting allocates several float64 arrays at full resolution.
MATTING_GB_PER_MEGAPIXEL = 0.12

MIN_FREE_RAM_GB = 4.0
MIN_FREE_RAM_GB_MATTING = 5.0
RESERVE_RAM_GB = 2.0  # left for the OS and other apps
DEFAULT_MAX_WORKERS = 4

# Lossless RGBA output size per pixel (photos; conservative).
OUTPUT_BYTES_PER_PIXEL: dict[OutputFormat, float] = {
    OutputFormat.PNG: 3.0,
    OutputFormat.WEBP: 2.2,
}
DISK_MARGIN = 1.2
DISK_HEADROOM_GB = 0.2

# Rough per-image time; the live ETA is refined from measured durations.
SECONDS_PER_IMAGE: dict[str, float] = {"cpu": 8.0, "cuda": 1.5}
SECONDS_PER_MEGAPIXEL = 0.4
MATTING_SLOWDOWN = 2.5


@dataclass(frozen=True, slots=True)
class PlanSettings:
    model: str
    output_format: OutputFormat
    alpha_matting: bool = False
    workers: int | None = None  # explicit override
    max_workers: int = DEFAULT_MAX_WORKERS
    min_free_ram_gb: float | None = None  # explicit override of the floor


@dataclass(frozen=True, slots=True)
class Plan:
    images: int
    workers: int
    threads_per_worker: int
    binding_limit: str
    ram_per_worker_gb: float
    image_peak_gb: float
    min_free_ram_gb: float
    pause_below_gb: float
    estimated_output_gb: float
    free_disk_gb: float
    eta_seconds: float
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.blockers

    @property
    def mode(self) -> str:
        return "parallel" if self.workers > 1 else "queue"

    def to_dict(self) -> JSONObject:
        return {
            "images": self.images,
            "mode": self.mode,
            "workers": self.workers,
            "threads_per_worker": self.threads_per_worker,
            "binding_limit": self.binding_limit,
            "ram_per_worker_gb": round(self.ram_per_worker_gb, 2),
            "min_free_ram_gb": round(self.min_free_ram_gb, 1),
            "pause_below_gb": round(self.pause_below_gb, 1),
            "estimated_output_gb": round(self.estimated_output_gb, 2),
            "free_disk_gb": round(self.free_disk_gb, 1),
            "eta_seconds": round(self.eta_seconds),
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
        }


def model_ram_gb(model: str) -> float:
    return MODEL_RAM_GB.get(model, UNKNOWN_MODEL_RAM_GB)


def image_peak_gb(max_megapixels: float, alpha_matting: bool) -> float:
    per_mp = MATTING_GB_PER_MEGAPIXEL if alpha_matting else IMAGE_GB_PER_MEGAPIXEL
    return max(0.2, max_megapixels * per_mp)


def estimate_eta_seconds(
    device: str, total_megapixels: float, images: int, workers: int, alpha_matting: bool
) -> float:
    per_image = SECONDS_PER_IMAGE.get(device, SECONDS_PER_IMAGE["cpu"])
    total = images * per_image + total_megapixels * SECONDS_PER_MEGAPIXEL
    if alpha_matting:
        total *= MATTING_SLOWDOWN
    # Parallel workers share memory bandwidth: assume ~80% scaling per extra worker.
    speedup = 1 + 0.8 * (workers - 1)
    return total / speedup


def make_plan(
    hardware: Hardware,
    inventory: Inventory,
    free_disk_gb: float,
    settings: PlanSettings,
) -> Plan:
    images = len(inventory.sources)
    blockers: list[str] = []
    warnings: list[str] = []

    peak = image_peak_gb(inventory.max_megapixels, settings.alpha_matting)
    per_worker = model_ram_gb(settings.model) + peak
    default_floor = MIN_FREE_RAM_GB_MATTING if settings.alpha_matting else MIN_FREE_RAM_GB
    floor = settings.min_free_ram_gb or max(default_floor, per_worker + RESERVE_RAM_GB)

    by_ram = max(1, math.floor((hardware.ram_available_gb - RESERVE_RAM_GB) / per_worker))
    by_cpu = max(1, hardware.cpu_performance)
    limits = {
        "RAM": by_ram,
        "CPU": by_cpu,
        "max workers": settings.max_workers,
        "image count": max(1, images),
    }
    binding_limit, workers = min(limits.items(), key=lambda item: item[1])

    if settings.workers is not None:
        if settings.workers > by_ram:
            warnings.append(
                f"--workers {settings.workers} exceeds what free RAM fits ({by_ram}); "
                "the run may pause often or swap."
            )
        workers, binding_limit = max(1, settings.workers), "--workers"

    threads = max(1, hardware.cpu_logical // workers)

    if images == 0:
        blockers.append("no supported images found")
    if hardware.ram_available_gb < floor:
        blockers.append(
            f"free RAM {hardware.ram_available_gb:.1f} GB is below the "
            f"{floor:.1f} GB minimum — close some apps and retry"
        )

    total_pixels = inventory.total_megapixels * 1e6
    output_gb = total_pixels * OUTPUT_BYTES_PER_PIXEL[settings.output_format] / 1e9
    if free_disk_gb < output_gb * DISK_MARGIN + DISK_HEADROOM_GB:
        blockers.append(
            f"not enough disk space: ~{output_gb:.2f} GB needed, {free_disk_gb:.2f} GB free"
        )

    if inventory.max_megapixels > 50:
        warnings.append(
            f"very large image ({inventory.max_megapixels:.0f} MP): expect high RAM use per image"
        )
    if hardware.coreml_listed and hardware.device == "cpu":
        warnings.append("CoreML is listed but BiRefNet runs on CPU; timings assume CPU")

    eta = estimate_eta_seconds(
        hardware.device, inventory.total_megapixels, images, workers, settings.alpha_matting
    )
    return Plan(
        images=images,
        workers=workers,
        threads_per_worker=threads,
        binding_limit=binding_limit,
        ram_per_worker_gb=per_worker,
        image_peak_gb=peak,
        min_free_ram_gb=floor,
        pause_below_gb=peak + 0.5,
        estimated_output_gb=output_gb,
        free_disk_gb=free_disk_gb,
        eta_seconds=eta,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )
