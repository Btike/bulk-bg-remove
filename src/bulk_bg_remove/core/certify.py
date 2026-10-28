"""Chroma-key certification: a cut-out that provably matches its plain background
needs no visual review.

A cut-out is certified only if all of these hold, checked on a downscaled copy:

1. the photo's background is plain (the outer ring is nearly one colour),
2. the subject stands out from it (it is not mostly close to that colour),
3. nothing removed differs from that colour (no part of the subject is missing),
4. nothing is half-transparent away from the cut line (no half-erased part, no haze),
5. the kept edge does not carry that colour (no halo, even a faint one),
6. no background colour is kept anywhere, e.g. inside a handle or a ring,
7. the model draws the same mask on the mirrored photo (it is not guessing).

Anything else goes to review: busy backgrounds, hair, glass, and white or clear
products on white (their own white cannot be told from background left inside).
Measured on the bench's catalogue and hard sets: 18 of 35 catalogue photos
certified and none of the cut-outs the blind judge scored 6 or less (BENCHMARK.md).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageOps
from scipy import ndimage

ANALYSIS_SIDE = 768
RING = 0.03  # outer ring of the photo used to read the background, share of its side
PLAIN_BACKGROUND_STD = 8.0  # per-channel standard deviation of that ring
FAR_FROM_BACKGROUND = 40.0  # RGB distance: clearly not background
NEAR_BACKGROUND = 12.0  # RGB distance: background colour
MAX_LOW_CONTRAST = 0.5  # subject pixels within FAR_FROM_BACKGROUND of it, share of its area
MAX_MISSING = 0.01  # removed-but-not-background pixels, share of the subject's area
MAX_PARTIAL = 0.01  # half-transparent pixels > DEEP px from the cut line, share of area
DEEP = 4.0
MAX_LEFTOVER = 0.02  # background-coloured pixels on the kept edge, share of that edge
MAX_KEPT_BACKGROUND = 0.02  # background-coloured kept pixels anywhere, share of area
MIN_MIRROR_IOU = 0.98  # mask on the photo vs mask on the mirrored photo
MIRROR_SIDE = 1024  # the mirror pass runs on a copy this size (models infer at ~1024)


@dataclass(frozen=True, slots=True)
class Certification:
    certified: bool
    reason: str  # "certified", or the first check that failed, in plain words


def _small(image: Image.Image, size: tuple[int, int] | None = None) -> Image.Image:
    if size is not None:
        return image.resize(size, Image.Resampling.BILINEAR)
    copy = image.copy()
    copy.thumbnail((ANALYSIS_SIDE, ANALYSIS_SIDE), Image.Resampling.BILINEAR)
    return copy


def check_cutout(rgb: Image.Image, alpha: Image.Image) -> Certification:
    """Checks 1-6, from the photo and the cut-out's alpha alone (no model run)."""
    photo = _small(rgb)
    width, height = photo.size
    colours = np.asarray(photo, dtype=np.float32)
    a = np.asarray(_small(alpha.convert("L"), (width, height)), dtype=np.float32) / 255
    kept = a > 0.5
    area = int(kept.sum())
    if area == 0:
        return Certification(False, "no subject found")
    if area == kept.size:
        return Certification(False, "nothing was removed")

    k = max(2, int(RING * min(width, height)))
    ring = np.zeros_like(kept)
    ring[:k] = ring[-k:] = True
    ring[:, :k] = ring[:, -k:] = True
    outer = ring & ~kept
    background = colours[outer] if outer.sum() > 50 else colours[ring]
    if float(background.std(axis=0).mean()) > PLAIN_BACKGROUND_STD:
        return Certification(False, "background is not plain")

    distance = np.linalg.norm(colours - np.median(background, axis=0), axis=2)
    like_background = distance < NEAR_BACKGROUND
    if (kept & (distance < FAR_FROM_BACKGROUND)).sum() / area > MAX_LOW_CONTRAST:
        return Certification(False, "subject too close to the background colour")

    missing = ((a < 0.1) & (distance > FAR_FROM_BACKGROUND)).sum() / area
    if missing > MAX_MISSING:
        return Certification(False, "something unlike the background was removed")

    deep = (ndimage.distance_transform_edt(kept) > DEEP) | (
        ndimage.distance_transform_edt(~kept) > DEEP
    )
    if ((a > 0.1) & (a < 0.9) & deep).sum() / area > MAX_PARTIAL:
        return Certification(False, "half-transparent areas away from the edge")

    edge = ndimage.binary_dilation(~kept, iterations=3) & kept
    leftover = (edge & like_background).sum() / max(1, int(edge.sum()))
    if leftover > MAX_LEFTOVER:
        return Certification(False, "background colour left on the edge")

    if (kept & like_background).sum() / area > MAX_KEPT_BACKGROUND:
        return Certification(False, "background colour kept inside the cut-out")
    return Certification(True, "certified")


def mirror_agrees(mask: Image.Image, mirrored_mask: Image.Image) -> bool:
    """Check 4: the mask on the mirrored photo, flipped back, matches the mask."""
    small = _small(mask.convert("L"))
    first = np.asarray(small) > 127
    second = np.fliplr(np.asarray(_small(mirrored_mask.convert("L"), small.size)) > 127)
    union = int((first | second).sum())
    return union == 0 or float((first & second).sum()) / union >= MIN_MIRROR_IOU


def certify(
    rgb: Image.Image,
    alpha: Image.Image,
    mask: Image.Image,
    mask_of_mirror: Callable[[], Image.Image],
) -> Certification:
    """All seven checks. `mask_of_mirror` runs the model on the mirrored photo; it
    is only called when the cheap checks pass, so hard images cost nothing extra.
    A check that cannot run (e.g. out of memory) means "needs review", never a
    failed image."""
    try:
        result = check_cutout(rgb, alpha)
        if not result.certified:
            return result
        if not mirror_agrees(mask, mask_of_mirror()):
            return Certification(False, "the model is unsure (mirrored photo differs)")
    except Exception as exc:  # noqa: BLE001 — a failed check only means "look at it"
        return Certification(False, f"check could not run ({type(exc).__name__})")
    return result


def mirrored_copy(rgb: Image.Image) -> Image.Image:
    """The photo mirrored, at most MIRROR_SIDE px: enough for the mask comparison,
    and far less memory than a mirrored 45 MP original."""
    copy = rgb.copy()
    copy.thumbnail((MIRROR_SIDE, MIRROR_SIDE), Image.Resampling.BILINEAR)
    return ImageOps.mirror(copy)
