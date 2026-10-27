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


def test_matting_shrinks_to_fit_its_ram_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    rgb, mask = scene(400)
    shapes: list[tuple[int, ...]] = []
    real = matting.estimate_alpha_cf

    def spy(image: np.ndarray, trimap: np.ndarray) -> np.ndarray:
        shapes.append(trimap.shape)
        return real(image, trimap)

    monkeypatch.setattr(matting, "estimate_alpha_cf", spy)
    # Make this small scene "cost" ~8 GB so any budget forces a downscale.
    monkeypatch.setattr(matting, "MATTING_GB_PER_MEGAPIXEL", 50.0)

    plenty = refine_edges(rgb, mask, EdgeMode.MATTE)
    tight = refine_edges(rgb, mask, EdgeMode.MATTE, budget_gb=0.1)

    assert shapes[0][0] < 400  # even the default budget caps it
    assert shapes[1][0] < shapes[0][0]  # a tighter budget shrinks it further
    assert plenty.note is None  # within the normal budget: nothing to report
    assert tight.note is not None and "to fit free RAM" in tight.note
    assert tight.alpha.size == rgb.size  # the alpha is always full resolution
    assert tight.rgb is rgb  # matte mode never touches RGB


def test_shrinking_re_measures_until_the_budget_holds(monkeypatch: pytest.MonkeyPatch) -> None:
    # A wire grid: its eroded band does not shrink with the area, the hard case.
    wire = np.zeros((900, 1200), np.uint8)
    for i in range(0, 1200, 40):
        wire[:, i : i + 3] = 255
    for j in range(0, 900, 40):
        wire[j : j + 3, :] = 255
    mask = Image.fromarray(wire)
    rgb = Image.new("RGB", mask.size, (200, 200, 200))

    def cost(scale: float) -> float:
        _, fg, bg = matting._small_trimap(mask, scale)
        return matting.matting_gb(fg.size / 1e6, float((~(fg | bg)).sum()) / 1e6)

    budget = cost(1.0) / 3
    scales: list[float] = []
    real = matting._small_trimap

    def spy(m: Image.Image, scale: float) -> tuple[Image.Image, np.ndarray, np.ndarray]:
        scales.append(scale)
        return real(m, scale)

    monkeypatch.setattr(matting, "MATTING_MIN_GB", 0.0)
    monkeypatch.setattr(matting, "_small_trimap", spy)
    monkeypatch.setattr(matting, "estimate_alpha_cf", lambda image, trimap: trimap)
    refine_edges(rgb, mask, EdgeMode.MATTE, budget_gb=budget)
    monkeypatch.setattr(matting, "_small_trimap", real)
    assert cost(scales[-1]) <= budget
