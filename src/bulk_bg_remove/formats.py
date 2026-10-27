"""Supported input formats, recognised by file content — never by extension.

A file named `.jpg` that is really an HTML error page is rejected, and a JPEG
saved as `.png` is still accepted. Only the header is read, so sniffing a large
folder is cheap.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pillow_heif
from PIL import Image, UnidentifiedImageError

pillow_heif.register_heif_opener()

# Pillow format name -> human label. MPO is the multi-picture JPEG some cameras
# and iPhones write; it decodes as a regular JPEG.
SUPPORTED_FORMATS: dict[str, str] = {
    "JPEG": "JPEG",
    "MPO": "JPEG",
    "PNG": "PNG",
    "HEIF": "HEIC/HEIF",
    "WEBP": "WebP",
    "AVIF": "AVIF",
    "TIFF": "TIFF",
    "BMP": "BMP",
}

# Shown in docs, the UI and `--help`.
SUPPORTED_EXTENSIONS: tuple[str, ...] = (
    ".jpg",
    ".jpeg",
    ".png",
    ".heic",
    ".heif",
    ".webp",
    ".avif",
    ".tif",
    ".tiff",
    ".bmp",
)


@dataclass(frozen=True, slots=True)
class Sniffed:
    """Result of inspecting one file's header."""

    path: Path
    format: str | None
    width: int | None
    height: int | None
    reason: str | None

    @property
    def supported(self) -> bool:
        return self.reason is None


def sniff(path: Path) -> Sniffed:
    """Identify an image by its content. `reason` explains any rejection."""
    try:
        with Image.open(path) as image:
            fmt = image.format or "unknown"
            width, height = image.size
    except UnidentifiedImageError:
        return Sniffed(path, None, None, None, "not an image")
    except OSError as exc:
        return Sniffed(path, None, None, None, f"unreadable: {exc}")

    if fmt not in SUPPORTED_FORMATS:
        return Sniffed(path, fmt, width, height, f"unsupported format: {fmt}")
    return Sniffed(path, SUPPORTED_FORMATS[fmt], width, height, None)
