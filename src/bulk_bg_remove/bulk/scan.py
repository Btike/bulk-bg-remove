"""Phase 1 — scan: hardware capacity, the image inventory and free disk space."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import psutil

from ..formats import sniff
from ..paths import is_output_dir
from .events import JSONObject, JSONValue

GB = 1e9


@dataclass(frozen=True, slots=True)
class Hardware:
    platform: str
    cpu_name: str
    cpu_logical: int
    cpu_performance: int
    ram_total_gb: float
    ram_available_gb: float
    device: str  # "cuda" | "cpu"
    coreml_listed: bool

    def to_dict(self) -> JSONObject:
        return {
            "platform": self.platform,
            "cpu_name": self.cpu_name,
            "cpu_logical": self.cpu_logical,
            "cpu_performance": self.cpu_performance,
            "ram_total_gb": round(self.ram_total_gb, 1),
            "ram_available_gb": round(self.ram_available_gb, 1),
            "device": self.device,
        }


def available_ram_gb() -> float:
    return float(psutil.virtual_memory().available) / GB


def _sysctl(key: str) -> str | None:
    try:
        out = subprocess.run(["sysctl", "-n", key], capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip() or None


def _performance_cores() -> int:
    """Apple Silicon: performance cores only (efficiency cores are slow for inference)."""
    if sys.platform == "darwin":
        value = _sysctl("hw.perflevel0.physicalcpu")
        if value and value.isdigit():
            return int(value)
    return psutil.cpu_count(logical=False) or os.cpu_count() or 1


def _cpu_name() -> str:
    if sys.platform == "darwin":
        name = _sysctl("machdep.cpu.brand_string")
        if name:
            return name
    return platform.processor() or platform.machine()


def _device() -> tuple[str, bool]:
    try:
        import onnxruntime as ort

        providers: list[str] = ort.get_available_providers()
    except ImportError:
        return "cpu", False
    # CoreML is listed on macOS but BiRefNet does not compile to it: treat as CPU.
    device = "cuda" if "CUDAExecutionProvider" in providers else "cpu"
    return device, "CoreMLExecutionProvider" in providers


def probe_hardware() -> Hardware:
    memory = psutil.virtual_memory()
    device, coreml = _device()
    return Hardware(
        platform=f"{platform.system()} {platform.machine()}",
        cpu_name=_cpu_name(),
        cpu_logical=os.cpu_count() or 1,
        cpu_performance=_performance_cores(),
        ram_total_gb=float(memory.total) / GB,
        ram_available_gb=float(memory.available) / GB,
        device=device,
        coreml_listed=coreml,
    )


@dataclass(frozen=True, slots=True)
class ImageSource:
    path: Path
    format: str
    width: int
    height: int
    size_bytes: int

    @property
    def megapixels(self) -> float:
        return self.width * self.height / 1e6


@dataclass(frozen=True, slots=True)
class Skipped:
    path: Path
    reason: str


@dataclass(frozen=True, slots=True)
class Inventory:
    input_path: Path
    input_root: Path
    sources: tuple[ImageSource, ...]
    skipped: tuple[Skipped, ...]

    @property
    def total_bytes(self) -> int:
        return sum(s.size_bytes for s in self.sources)

    @property
    def total_megapixels(self) -> float:
        return sum(s.megapixels for s in self.sources)

    @property
    def max_megapixels(self) -> float:
        return max((s.megapixels for s in self.sources), default=0.0)

    def to_dict(self) -> JSONObject:
        formats: dict[str, JSONValue] = dict(Counter(s.format for s in self.sources))
        return {
            "input": str(self.input_path),
            "images": len(self.sources),
            "total_mb": round(self.total_bytes / 1e6, 1),
            "max_megapixels": round(self.max_megapixels, 1),
            "formats": formats,
            "skipped": [{"path": str(s.path), "reason": s.reason} for s in self.skipped],
        }


def _walk(root: Path) -> Iterator[Path]:
    """Every visible file under root. Skips hidden and `-no-bg` folders; no symlinked dirs."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if not d.startswith(".") and not is_output_dir(Path(d))
        )
        for name in sorted(filenames):
            if not name.startswith("."):
                yield Path(dirpath) / name


def discover(input_path: Path) -> Inventory:
    """Find every supported image in a file or folder (recursively), by content."""
    path = input_path.expanduser().resolve()
    if path.is_file():
        root, candidates = path.parent, [path]
    elif path.is_dir():
        root, candidates = path, list(_walk(path))
    else:
        raise FileNotFoundError(f"no such file or folder: {input_path}")

    sources: list[ImageSource] = []
    skipped: list[Skipped] = []
    for candidate in candidates:
        result = sniff(candidate)
        if result.reason is not None or result.format is None:
            skipped.append(Skipped(candidate, result.reason or "not an image"))
        else:
            sources.append(
                ImageSource(
                    path=candidate,
                    format=result.format,
                    width=result.width or 0,
                    height=result.height or 0,
                    size_bytes=candidate.stat().st_size,
                )
            )
    return Inventory(path, root, tuple(sources), tuple(skipped))


def free_disk_gb(path: Path) -> float:
    """Free space on the filesystem that will hold `path` (which may not exist yet)."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free / GB
