"""Edge refinement: what each mode may and may not change."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter

from bulk_bg_remove.core import matting
from bulk_bg_remove.core.matting import EdgeMode, refine_edges


def scene(size: int = 160) -> tuple[Image.Image, Image.Image]:
    """A red disc on a blue background, with a soft-edged mask like a model's."""
    rgb = Image.new("RGB", (size, size), (20, 40, 200))
    ImageDraw.Draw(rgb).ellipse((40, 40, size - 40, size - 40), fill=(220, 30, 30))
    rgb = rgb.filter(ImageFilter.GaussianBlur(2))
    mask = Image.new("L", rgb.size, 0)
    ImageDraw.Draw(mask).ellipse((40, 40, size - 40, size - 40), fill=255)
    return rgb, mask.filter(ImageFilter.GaussianBlur(4))


def test_mask_mode_returns_the_model_mask_untouched() -> None:
    rgb, mask = scene()
    result = refine_edges(rgb, mask, EdgeMode.MASK)
    assert result.alpha is mask
    assert result.rgb is rgb


def test_matte_refines_alpha_and_never_touches_rgb() -> None:
    rgb, mask = scene()
    result = refine_edges(rgb, mask, EdgeMode.MATTE)
    assert np.array_equal(np.asarray(result.rgb), np.asarray(rgb))
    alpha = np.asarray(result.alpha)
    assert alpha.shape == (160, 160)
    assert alpha[80, 80] == 255  # centre of the subject
    assert alpha[2, 2] == 0  # corner of the background
    assert not np.array_equal(alpha, np.asarray(mask))  # the band was refined


def test_clean_only_recolours_semi_transparent_pixels() -> None:
    rgb, mask = scene()
    result = refine_edges(rgb, mask, EdgeMode.CLEAN)
    alpha = np.asarray(result.alpha)
    before, after = np.asarray(rgb), np.asarray(result.rgb)
    solid = (alpha == 0) | (alpha == 255)
    assert np.array_equal(before[solid], after[solid])
    assert not np.array_equal(before[~solid], after[~solid])


def test_hard_mask_without_uncertain_band_is_returned_as_is() -> None:
    rgb = Image.new("RGB", (64, 64), (0, 0, 0))
    mask = Image.new("L", (64, 64), 255)
    assert refine_edges(rgb, mask, EdgeMode.MATTE).alpha is mask


def test_large_images_are_matted_at_reduced_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(matting, "MATTING_MAX_MEGAPIXELS", 0.01)  # force the downscale path
    rgb, mask = scene(size=200)
    result = refine_edges(rgb, mask, EdgeMode.CLEAN)
    assert result.alpha.size == (200, 200)
    assert result.rgb.size == (200, 200)
    assert np.asarray(result.alpha)[100, 100] == 255
