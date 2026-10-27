"""Edge refinement: alpha matting on the uncertain band around the subject.

Modes:
- `mask`  — the model's mask as is (fastest).
- `matte` — closed-form alpha matting refines hair, fur and fine detail. RGB is
  untouched, so the subject stays bit-exact.
- `clean` — `matte` plus colour decontamination of semi-transparent edge
  pixels, which removes the halo of the old background. Only edge pixels change.

Matting runs on a copy capped at `MATTING_MAX_MEGAPIXELS`, and further
downscaled when its estimated RAM exceeds the budget it is given. Its RAM grows
with the pixel count and, much more, with the size of the uncertain band (hair,
wire, foliage). Definite foreground and background always come from the
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
# Closed-form matting RAM, measured on 13 photos (Apple M5): ~0.35 GB per
# megapixel matted plus ~2.3 GB per million uncertain-band pixels. A 4 MP wire
# chair needed 4.9 GB; a 4 MP product shot 1.8 GB.
MATTING_GB_PER_MEGAPIXEL = 0.35
MATTING_GB_PER_UNKNOWN_MEGAPIXEL = 2.3
MATTING_MAX_GB = 3.0  # budget when free RAM is plentiful
# Scaling the matte back to full resolution (float64 work arrays) costs extra,
# outside the budget: ~28 B/px for the alpha, ~62 B/px with the foreground too.
FULLRES_GB_PER_MEGAPIXEL: dict[str, float] = {"matte": 0.03, "clean": 0.065}
MATTING_MIN_GB = 0.6  # smallest budget: edges are still matted, at a lower resolution
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


def fullres_matting_gb(megapixels: float, mode: EdgeMode) -> float:
    """RAM for bringing the matte back to full resolution (0 without matting)."""
    return megapixels * FULLRES_GB_PER_MEGAPIXEL.get(mode.value, 0.0)


def matting_gb(megapixels: float, unknown_megapixels: float) -> float:
    return MATTING_GB_PER_MEGAPIXEL * megapixels + MATTING_GB_PER_UNKNOWN_MEGAPIXEL * (
        unknown_megapixels
    )


def _small_trimap(
    mask: Image.Image, scale: float
) -> tuple[Image.Image, NDArray[np.bool_], NDArray[np.bool_]]:
    width, height = mask.size
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    small = mask if scale == 1.0 else mask.resize(size, Image.Resampling.LANCZOS)
    fg, bg = _trimap(np.asarray(small, dtype=np.uint8), ERODE_SIZE)
    return small, fg, bg


def refine_edges(
    rgb: Image.Image, mask: Image.Image, mode: EdgeMode, *, budget_gb: float = MATTING_MAX_GB
) -> RefinedEdges:
    if mode is EdgeMode.MASK:
        return RefinedEdges(alpha=mask, rgb=rgb)

    width, height = rgb.size
    scale = min(1.0, math.sqrt(MATTING_MAX_MEGAPIXELS * 1e6 / (width * height)))
    small_mask, fg, bg = _small_trimap(mask, scale)
    note = None
    budget = max(MATTING_MIN_GB, budget_gb)
    for _ in range(4):
        estimate = matting_gb(fg.size / 1e6, float((~(fg | bg)).sum()) / 1e6)
        if estimate <= budget:
            break
        # The pixel term scales with the area; the band (eroded a fixed number of
        # pixels) shrinks more slowly, so re-measure after each step.
        scale *= math.sqrt(budget / estimate) * 0.97
        small_mask, fg, bg = _small_trimap(mask, scale)
        if budget_gb < MATTING_MAX_GB:
            note = f"edge matting at {fg.size / 1e6:.1f} MP to fit free RAM"

    full_mask = np.asarray(mask, dtype=np.uint8)
    # The band is ERODE_SIZE px wide at matting resolution; keep it the same
    # physical width at full resolution.
    sure_fg, sure_bg = _trimap(full_mask, max(ERODE_SIZE, round(ERODE_SIZE / scale)))
    if (sure_fg | sure_bg).all():
        return RefinedEdges(alpha=mask, rgb=rgb)  # no uncertain band to refine

    small_rgb = rgb if scale == 1.0 else rgb.resize(small_mask.size, Image.Resampling.LANCZOS)
    image = np.asarray(small_rgb, dtype=np.float64) / 255.0
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
        return RefinedEdges(alpha=alpha_image, rgb=rgb, note=note)

    small_fg = estimate_foreground_ml(image, small_alpha)
    foreground = small_fg if scale == 1.0 else _resize_float(small_fg, (width, height))
    edge = (alpha_u8 > 0) & (alpha_u8 < 255)
    source = np.asarray(rgb, dtype=np.uint8)
    cleaned = source.copy()
    cleaned[edge] = np.round(np.clip(foreground[edge], 0.0, 1.0) * 255).astype(np.uint8)
    return RefinedEdges(alpha=alpha_image, rgb=Image.fromarray(cleaned), note=note)
