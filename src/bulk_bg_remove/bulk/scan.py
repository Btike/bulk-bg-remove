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

from ..formats import SUPPORTED_EXTENSIONS, sniff
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


# Linux control groups: a container's memory limit lives here, not in /proc/meminfo.
CGROUP_ROOT = Path("/sys/fs/cgroup")
PROC_SELF_CGROUP = Path("/proc/self/cgroup")  # where this process sits in the hierarchy


def _read_number(path: Path) -> int | None:
    try:
        text = path.read_text().strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None  # "max" (no limit) -> None


def _inactive_file(stat: Path, key: str) -> int:
    """Page cache the kernel can drop: counted as used by the cgroup, but free to us."""
    try:
        for line in stat.read_text().splitlines():
            name, _, value = line.partition(" ")
            if name == key and value.strip().isdigit():
                return int(value)
    except OSError:
        pass
    return 0


def _cgroup_v2_dirs() -> list[Path]:
    """This process's cgroup v2 directory and its ancestors, innermost first. In a
    container with a private namespace that is just the root; under systemd or a
    host-namespace container it is a nested slice."""
    own = CGROUP_ROOT
    try:
        for line in PROC_SELF_CGROUP.read_text().splitlines():
            if line.startswith("0::"):
                own = CGROUP_ROOT / line[3:].strip().lstrip("/")
    except OSError:
        pass
    dirs = [own, *own.parents]
    return [d for d in dirs if d == CGROUP_ROOT or CGROUP_ROOT in d.parents]


def _cgroup_v2_limit() -> tuple[int, int] | None:
    """(limit, used) from the tightest memory.max on the way up, or None."""
    best: tuple[int, int] | None = None
    for directory in _cgroup_v2_dirs():
        limit = _read_number(directory / "memory.max")
        if limit is None:
            continue
        used = _read_number(directory / "memory.current") or 0
        used -= _inactive_file(directory / "memory.stat", "inactive_file")
        if best is None or limit - used < best[0] - best[1]:
            best = (limit, used)
    return best


def _cgroup_memory() -> tuple[float, float] | None:
    """(limit, used) in bytes when this process runs under a cgroup memory limit
    (Docker, Kubernetes...), cgroup v2 or v1; None otherwise."""
    if not sys.platform.startswith("linux"):
        return None
    v1 = CGROUP_ROOT / "memory"
    v2 = _cgroup_v2_limit()
    if v2 is not None:
        limit, used = v2
    else:
        limit = _read_number(v1 / "memory.limit_in_bytes")
        if limit is None:
            return None
        used = _read_number(v1 / "memory.usage_in_bytes") or 0
        used -= _inactive_file(v1 / "memory.stat", "total_inactive_file")
    if limit >= psutil.virtual_memory().total:  # v1 "no limit" is a huge number
        return None
    return float(limit), float(max(0, used))


def _memory() -> tuple[float, float]:
    """(total, available) RAM in GB, capped by a container's memory limit."""
    memory = psutil.virtual_memory()
    total, available = float(memory.total), float(memory.available)
    cgroup = _cgroup_memory()
    if cgroup is not None:
        limit, used = cgroup
        total, available = min(total, limit), min(available, max(0.0, limit - used))
    return total / GB, available / GB


def available_ram_gb() -> float:
    return _memory()[1]


def process_rss_gb() -> float:
    return float(psutil.Process().memory_info().rss) / GB


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
    total, available = _memory()
    device, coreml = _device()
    return Hardware(
        platform=f"{platform.system()} {platform.machine()}",
        cpu_name=_cpu_name(),
        cpu_logical=os.cpu_count() or 1,
        cpu_performance=_performance_cores(),
        ram_total_gb=total,
        ram_available_gb=available,
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


def _looks_like_image(name: str) -> bool:
    return Path(name).suffix.lower() in SUPPORTED_EXTENSIONS


def _walk(root: Path, skipped: list[Skipped]) -> Iterator[Path]:
    """Every visible file under root; no symlinked dirs.

    Hidden folders are skipped silently (tooling: .git, .venv). Earlier `-no-bg`
    output folders and hidden files named like images are skipped *and reported*,
    so an agent never claims "all photos" while some were left out.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        kept = []
        for d in sorted(dirnames):
            if d.startswith("."):
                continue
            if is_output_dir(Path(d)):
                skipped.append(Skipped(Path(dirpath) / d, "earlier output folder (-no-bg)"))
                continue
            kept.append(d)
        dirnames[:] = kept
        for name in sorted(filenames):
            if not name.startswith("."):
                yield Path(dirpath) / name
            elif not name.startswith("._") and _looks_like_image(name):
                skipped.append(Skipped(Path(dirpath) / name, "hidden file"))


def discover(input_path: Path) -> Inventory:
    """Find every supported image in a file or folder (recursively), by content."""
    path = input_path.expanduser().resolve()
    skipped: list[Skipped] = []
    if path.is_file():
        root, candidates = path.parent, [path]
    elif path.is_dir():
        root, candidates = path, list(_walk(path, skipped))
    else:
        raise FileNotFoundError(f"no such file or folder: {input_path}")

    sources: list[ImageSource] = []
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
