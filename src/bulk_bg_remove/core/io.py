"""Image loading and saving that preserve the subject's original quality."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from .. import formats  # noqa: F401  (registers the HEIC/HEIF opener)
from ..domain import OutputFormat


@dataclass(frozen=True, slots=True)
class SourceImage:
    """An original image loaded at full resolution.

    `rgb` is the 3-channel view used for compositing; `icc_profile` is carried
    over so the output can re-embed it without touching any pixel data.
    """

    rgb: Image.Image
    icc_profile: bytes | None


def load_source(path: Path) -> SourceImage:
    """Load an image at full resolution as RGB, keeping its colour profile.

    EXIF orientation is applied first (a lossless rotate/flip), so phone photos
    come out the way they are displayed instead of sideways.
    """
    with Image.open(path) as handle:
        handle.load()
        icc = handle.info.get("icc_profile")
        # A CMYK profile cannot describe the RGB pixels we write out.
        if handle.mode == "CMYK":
            icc = None
        upright = ImageOps.exif_transpose(handle)
        rgb = _to_8bit(upright).convert("RGB")
    return SourceImage(rgb=rgb, icc_profile=icc)


def _to_8bit(image: Image.Image) -> Image.Image:
    """Scale 16/32-bit greyscale down to 8 bits.

    Pillow's own conversion clips instead of scaling, which turns a 16-bit
    greyscale photo almost entirely white. Output is 8 bits per channel, so the
    extra depth cannot be kept either way.
    """
    if not image.mode.startswith("I"):
        return image
    data = np.asarray(image, dtype=np.float64)
    return Image.fromarray(np.clip(np.round(data / 257.0), 0, 255).astype(np.uint8))


def save_rgba(
    image: Image.Image,
    path: Path,
    output_format: OutputFormat,
    *,
    icc_profile: bytes | None = None,
) -> None:
    """Write an RGBA image losslessly, preserving exact dimensions and ICC."""
    if image.mode != "RGBA":
        raise ValueError(f"expected RGBA image, got {image.mode}")

    params: dict[str, bytes] = {}
    if icc_profile is not None:
        params["icc_profile"] = icc_profile

    if output_format is OutputFormat.WEBP:
        # exact=True keeps RGB under fully transparent pixels (no cleanup), so
        # the save is bit-exact everywhere.
        image.save(path, format="WEBP", lossless=True, quality=100, method=6, exact=True, **params)
    else:
        image.save(path, format="PNG", compress_level=6, **params)
