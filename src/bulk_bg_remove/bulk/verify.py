"""Phase 4 — verify: re-open every output and check it independently.

Checks that the file exists, is RGBA and has the source's dimensions (either
orientation, since EXIF rotation can swap them). Flags masks that removed
everything (no subject detected) or nothing (background kept).

With finishing (`--background`, `--square`, `--pad`, `--size`) the expected
mode and size follow the finish instead, and the pipeline reports the
empty/nothing-removed cases itself: margins or a background hide them here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from ..core.finish import NO_FINISH, Finish
from ..core.io import read_tag
from .scan import ImageSource


@dataclass(frozen=True, slots=True)
class Check:
    destination: Path
    ok: bool
    problem: str | None = None
    warning: str | None = None


def _matches(size: tuple[int, int], source: ImageSource) -> bool:
    return size in ((source.width, source.height), (source.height, source.width))


def _expected_mode(finish: Finish) -> str:
    return "RGB" if finish.opaque else "RGBA"


def _size_problem(size: tuple[int, int], source: ImageSource, finish: Finish) -> str | None:
    if finish.square and size[0] != size[1]:
        return f"size {size} is not square"
    if finish.size is not None and max(size) != finish.size:
        return f"size {size} does not have a {finish.size} px longest side"
    if not finish.crops and finish.size is None and not _matches(size, source):
        return f"size {size} differs from source {(source.width, source.height)}"
    return None


def verify_output(destination: Path, source: ImageSource, finish: Finish = NO_FINISH) -> Check:
    if not destination.is_file():
        return Check(destination, ok=False, problem="output file missing")
    mode = _expected_mode(finish)
    try:
        with Image.open(destination) as image:
            image.load()
            if image.mode != mode:
                return Check(destination, ok=False, problem=f"expected {mode}, got {image.mode}")
            problem = _size_problem(image.size, source, finish)
            if problem is not None:
                return Check(destination, ok=False, problem=problem)
            if finish.active:
                return Check(destination, ok=True)
            low, high = image.getchannel("A").getextrema()
    except OSError as exc:
        return Check(destination, ok=False, problem=f"unreadable output: {exc}")

    if high == 0:
        return Check(destination, ok=True, warning="empty cut-out: no subject detected")
    if low == 255:
        return Check(destination, ok=True, warning="nothing removed: background kept")
    return Check(destination, ok=True)


def is_complete(destination: Path, source: ImageSource, finish: Finish = NO_FINISH) -> bool:
    """Cheap check used to resume: an output exists with the right mode and size,
    made with the same finish (stored in its metadata) or with none."""
    if not destination.is_file():
        return False
    try:
        with Image.open(destination) as image:
            expected_tag = finish.signature if finish.active else None
            return (
                image.mode == _expected_mode(finish)
                and _size_problem(image.size, source, finish) is None
                and read_tag(image) == expected_tag
            )
    except OSError:
        return False
