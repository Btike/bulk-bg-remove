"""Phase 4 — verify: re-open every output and check it independently.

Checks that the file exists, is RGBA and has the source's dimensions (either
orientation, since EXIF rotation can swap them). Flags masks that removed
everything (no subject detected) or nothing (background kept).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .scan import ImageSource


@dataclass(frozen=True, slots=True)
class Check:
    destination: Path
    ok: bool
    problem: str | None = None
    warning: str | None = None


def _matches(size: tuple[int, int], source: ImageSource) -> bool:
    return size in ((source.width, source.height), (source.height, source.width))


def verify_output(destination: Path, source: ImageSource) -> Check:
    if not destination.is_file():
        return Check(destination, ok=False, problem="output file missing")
    try:
        with Image.open(destination) as image:
            image.load()
            if image.mode != "RGBA":
                return Check(destination, ok=False, problem=f"expected RGBA, got {image.mode}")
            if not _matches(image.size, source):
                return Check(
                    destination,
                    ok=False,
                    problem=f"size {image.size} differs from source "
                    f"{(source.width, source.height)}",
                )
            low, high = image.getchannel("A").getextrema()
    except OSError as exc:
        return Check(destination, ok=False, problem=f"unreadable output: {exc}")

    if high == 0:
        return Check(destination, ok=True, warning="empty cut-out: no subject detected")
    if low == 255:
        return Check(destination, ok=True, warning="nothing removed: background kept")
    return Check(destination, ok=True)


def is_complete(destination: Path, source: ImageSource) -> bool:
    """Cheap check used to resume: a finished output exists with the right size."""
    if not destination.is_file():
        return False
    try:
        with Image.open(destination) as image:
            return image.mode == "RGBA" and _matches(image.size, source)
    except OSError:
        return False
