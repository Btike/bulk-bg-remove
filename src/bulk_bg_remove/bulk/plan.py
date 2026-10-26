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

from ..core.matting import MATTING_MAX_GB, MATTING_MIN_GB, EdgeMode, fullres_matting_gb
from ..domain import OutputFormat
from .events import JSONObject
from .scan import Hardware, Inventory

# Peak RSS per worker during inference (model + ONNX activations), measured on
# CPU with the engine's session options and edge matting off. Roughly constant
# per model: each model infers at a fixed size whatever the photo size.
MODEL_PEAK_GB: dict[str, float] = {
    "birefnet-general-lite": 9.4,
    "birefnet-general": 11.6,
    "birefnet-portrait": 11.6,
    "birefnet-dis": 11.6,
    "birefnet-hrsod": 11.6,
    "birefnet-massive": 11.6,
    "isnet-general-use": 2.4,
    "isnet-anime": 2.2,
    "u2net": 1.8,
    "u2net_human_seg": 1.8,
    "silueta": 0.7,
}
UNKNOWN_MODEL_PEAK_GB = 12.0

# Full-resolution buffers per image (RGB, mask, RGBA).
IMAGE_GB_PER_MEGAPIXEL = 0.02
# Edge matting sizes itself to the RAM that is free when it runs, between
# MATTING_MIN_GB and MATTING_MAX_GB (see core/matting.py). The start floor
# counts the minimum; extra parallel workers must leave room for the maximum.

RESERVE_RAM_GB = 1.5  # kept free for the OS and other apps
# A worker that keeps more than this above its model's peak after an image is
# restarted, so heap fragmentation never stacks up across a long batch.
RECYCLE_MARGIN_GB = 1.0
# Each extra parallel worker must also leave this much headroom: two BiRefNet
# lite workers (each sized for full matting) need ~28 GB free to go parallel.
PARALLEL_HEADROOM_GB = 1.0
DEFAULT_MAX_WORKERS = 4

# Lossless RGBA output size per pixel (photos; conservative).
OUTPUT_BYTES_PER_PIXEL: dict[OutputFormat, float] = {
    OutputFormat.PNG: 3.0,
    OutputFormat.WEBP: 2.2,
}
DISK_MARGIN = 1.2
DISK_HEADROOM_GB = 0.2

# Measured seconds per image on an Apple M5 CPU (product photos, matting on);
# the live ETA refines this.
SECONDS_PER_IMAGE: dict[str, float] = {
    "birefnet-general-lite": 3.2,
    "birefnet-general": 5.6,
    "birefnet-portrait": 5.5,
    "birefnet-dis": 5.5,
    "birefnet-hrsod": 5.6,
    "birefnet-massive": 5.6,
    "isnet-general-use": 1.3,
    "isnet-anime": 1.2,
    "u2net": 0.5,
    "u2net_human_seg": 0.8,
    "silueta": 0.4,
}
UNKNOWN_MODEL_SECONDS = 6.0
CUDA_SPEEDUP = 5.0
MATTING_SECONDS = 1.5
# A second CPU worker adds ~25% throughput (measured); more add little.
EXTRA_WORKER_SPEEDUP = 0.25


@dataclass(frozen=True, slots=True)
class PlanSettings:
    model: str
    output_format: OutputFormat
    edges: EdgeMode = EdgeMode.MATTE
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


def model_peak_gb(model: str) -> float:
    return MODEL_PEAK_GB.get(model, UNKNOWN_MODEL_PEAK_GB)


def image_peak_gb(max_megapixels: float, edges: EdgeMode, *, full_matting: bool = False) -> float:
    """RAM for one image on top of the model: buffers plus edge matting.

    The start floor counts matting at its minimum (it shrinks to fit); sizing
    extra parallel workers counts it at its maximum.
    """
    buffers = max(0.2, max_megapixels * IMAGE_GB_PER_MEGAPIXEL)
    if edges is EdgeMode.MASK:
        return buffers
    solver = MATTING_MAX_GB if full_matting else MATTING_MIN_GB
    return buffers + solver + fullres_matting_gb(max_megapixels, edges)


def minimum_free_ram_gb(
    model: str, edges: EdgeMode = EdgeMode.MATTE, megapixels: float = 12.0
) -> float:
    """Free RAM one worker needs for photos up to `megapixels` (default: a 12 MP photo)."""
    return model_peak_gb(model) + image_peak_gb(megapixels, edges) + RESERVE_RAM_GB


def estimate_eta_seconds(
    model: str, device: str, images: int, workers: int, edges: EdgeMode
) -> float:
    per_image = SECONDS_PER_IMAGE.get(model, UNKNOWN_MODEL_SECONDS)
    if device == "cuda":
        per_image /= CUDA_SPEEDUP
    if edges is not EdgeMode.MASK:
        per_image += MATTING_SECONDS
    return images * per_image / (1 + EXTRA_WORKER_SPEEDUP * (workers - 1))


def make_plan(
    hardware: Hardware,
    inventory: Inventory,
    free_disk_gb: float,
    settings: PlanSettings,
) -> Plan:
    images = len(inventory.sources)
    blockers: list[str] = []
    warnings: list[str] = []

    peak = image_peak_gb(inventory.max_megapixels, settings.edges)
    floor = settings.min_free_ram_gb or model_peak_gb(settings.model) + peak + RESERVE_RAM_GB
    # A worker with room for full-resolution matting: what each parallel worker needs.
    full_peak = image_peak_gb(inventory.max_megapixels, settings.edges, full_matting=True)
    per_worker = model_peak_gb(settings.model) + full_peak

    spare = hardware.ram_available_gb - (per_worker + RESERVE_RAM_GB)
    by_ram = 1 + max(0, math.floor(spare / (per_worker + PARALLEL_HEADROOM_GB)))
    # Each worker uses every performance core; a second worker still adds ~25%.
    by_cpu = max(1, hardware.cpu_performance // 2)
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

    threads = max(2, hardware.cpu_performance)

    if images == 0:
        blockers.append("no supported images found")
    if hardware.ram_available_gb < floor:
        blockers.append(
            f"free RAM {hardware.ram_available_gb:.1f} GB is below the "
            f"{floor:.1f} GB this model needs — close some apps and retry"
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

    eta = estimate_eta_seconds(settings.model, hardware.device, images, workers, settings.edges)
    return Plan(
        images=images,
        workers=workers,
        threads_per_worker=threads,
        binding_limit=binding_limit,
        ram_per_worker_gb=per_worker,
        image_peak_gb=full_peak,
        min_free_ram_gb=floor,
        pause_below_gb=RESERVE_RAM_GB,
        estimated_output_gb=output_gb,
        free_disk_gb=free_disk_gb,
        eta_seconds=eta,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
    )
