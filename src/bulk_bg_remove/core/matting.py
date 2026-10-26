"""Edge refinement: alpha matting on the uncertain band around the subject.

Modes:
- `mask`  — the model's mask as is (fastest).
- `matte` — closed-form alpha matting refines hair, fur and fine detail. RGB is
  untouched, so the subject stays bit-exact.
- `clean` — `matte` plus colour decontamination of semi-transparent edge
  pixels, which removes the halo of the old background. Only edge pixels change.

Matting cost grows with pixel count, so it runs on a copy capped at
`MATTING_MAX_MEGAPIXELS`. Definite foreground and background come from the
full-resolution mask; only the uncertain band uses the upscaled matte.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
from numpy.typing import NDArray
from PIL import Image
from pymatting import estimate_alpha_cf, estimate_foreground_ml
from scipy.ndimage import binary_erosion

MATTING_MAX_MEGAPIXELS = 4.0
FOREGROUND_THRESHOLD = 240
BACKGROUND_THRESHOLD = 10
ERODE_SIZE = 10


class EdgeMode(StrEnum):
    MASK = "mask"
    MATTE = "matte"
    CLEAN = "clean"


@dataclass(frozen=True, slots=True)
class RefinedEdges:
    alpha: Image.Image  # 'L', full resolution
    rgb: Image.Image  # the source RGB, or with decontaminated edge pixels in `clean` mode
    note: str | None = None


def _erode(region: NDArray[np.bool_], size: int, border_value: int) -> NDArray[np.bool_]:
    """Square erosion done as a row pass then a column pass: identical, ~40x faster."""
    rows = binary_erosion(region, np.ones((size, 1), bool), border_value=border_value)
    result: NDArray[np.bool_] = binary_erosion(
        rows, np.ones((1, size), bool), border_value=border_value
    )
    return result


def _trimap(mask: NDArray[np.uint8], erode: int) -> tuple[NDArray[np.bool_], NDArray[np.bool_]]:
    foreground = _erode(mask > FOREGROUND_THRESHOLD, erode, border_value=0)
    background = _erode(mask < BACKGROUND_THRESHOLD, erode, border_value=1)
    return foreground, background


def _resize_float(array: NDArray[np.float64], size: tuple[int, int]) -> NDArray[np.float64]:
    """Resize a [0, 1] float array (H×W or H×W×C) with Lanczos, per channel."""
    if array.ndim == 2:
        image = Image.fromarray(array.astype(np.float32))
        return np.asarray(image.resize(size, Image.Resampling.LANCZOS), dtype=np.float64)
    channels = [_resize_float(array[:, :, i], size) for i in range(array.shape[2])]
    return np.stack(channels, axis=2)


def refine_edges(rgb: Image.Image, mask: Image.Image, mode: EdgeMode) -> RefinedEdges:
    if mode is EdgeMode.MASK:
        return RefinedEdges(alpha=mask, rgb=rgb)

    width, height = rgb.size
    scale = min(1.0, math.sqrt(MATTING_MAX_MEGAPIXELS * 1e6 / (width * height)))
    full_mask = np.asarray(mask, dtype=np.uint8)
    # The band is ERODE_SIZE px wide at matting resolution; keep it the same
    # physical width at full resolution.
    sure_fg, sure_bg = _trimap(full_mask, max(ERODE_SIZE, round(ERODE_SIZE / scale)))
    if (sure_fg | sure_bg).all():
        return RefinedEdges(alpha=mask, rgb=rgb)  # no uncertain band to refine

    small_size = (max(1, round(width * scale)), max(1, round(height * scale)))
    small_rgb = rgb if scale == 1.0 else rgb.resize(small_size, Image.Resampling.LANCZOS)
    small_mask = mask if scale == 1.0 else mask.resize(small_size, Image.Resampling.LANCZOS)
    image = np.asarray(small_rgb, dtype=np.float64) / 255.0
    fg, bg = _trimap(np.asarray(small_mask, dtype=np.uint8), ERODE_SIZE)
    trimap = np.full(fg.shape, 0.5)
    trimap[fg] = 1.0
    trimap[bg] = 0.0

    try:
        small_alpha = estimate_alpha_cf(image, trimap)
    except (ValueError, np.linalg.LinAlgError, MemoryError) as exc:
        return RefinedEdges(alpha=mask, rgb=rgb, note=f"edge matting skipped: {exc}")

    alpha = small_alpha if scale == 1.0 else _resize_float(small_alpha, (width, height))
    alpha = np.where(sure_fg, 1.0, np.where(sure_bg, 0.0, np.clip(alpha, 0.0, 1.0)))
    alpha_u8 = np.round(alpha * 255).astype(np.uint8)
    alpha_image = Image.fromarray(alpha_u8)

    if mode is EdgeMode.MATTE:
        return RefinedEdges(alpha=alpha_image, rgb=rgb)

    small_fg = estimate_foreground_ml(image, small_alpha)
    foreground = small_fg if scale == 1.0 else _resize_float(small_fg, (width, height))
    edge = (alpha_u8 > 0) & (alpha_u8 < 255)
    source = np.asarray(rgb, dtype=np.uint8)
    cleaned = source.copy()
    cleaned[edge] = np.round(np.clip(foreground[edge], 0.0, 1.0) * 255).astype(np.uint8)
    return RefinedEdges(alpha=alpha_image, rgb=Image.fromarray(cleaned))
