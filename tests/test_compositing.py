"""Compositing guarantees: dimensions preserved and subject pixels untouched."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from bulk_bg_remove.core.compositing import composite_rgba


def test_preserves_dimensions_and_mode() -> None:
    rgb = Image.new("RGB", (640, 427), (10, 20, 30))
    mask = Image.new("L", (640, 427), 255)
    out = composite_rgba(rgb, mask)
    assert out.mode == "RGBA"
    assert out.size == (640, 427)


def test_subject_rgb_intact_where_opaque() -> None:
    arr = np.arange(20 * 30 * 3, dtype=np.uint8).reshape(20, 30, 3)
    rgb = Image.fromarray(arr, mode="RGB")
    mask = Image.new("L", rgb.size, 255)  # fully opaque

    out = composite_rgba(rgb, mask)

    out_rgb = np.array(out)[:, :, :3]
    assert np.array_equal(out_rgb, arr)  # RGB never resampled or altered


def test_mask_resized_to_source_size() -> None:
    rgb = Image.new("RGB", (200, 100), (0, 0, 0))
    mask = Image.new("L", (50, 25), 128)  # different resolution
    out = composite_rgba(rgb, mask)
    assert out.size == (200, 100)


def test_alpha_channel_matches_mask() -> None:
    rgb = Image.new("RGB", (16, 16), (5, 5, 5))
    mask = Image.new("L", (16, 16), 200)
    out = composite_rgba(rgb, mask)
    assert np.array_equal(np.array(out)[:, :, 3], np.full((16, 16), 200, dtype=np.uint8))


def test_rejects_non_rgb_source() -> None:
    with pytest.raises(ValueError):
        composite_rgba(Image.new("RGBA", (10, 10)), Image.new("L", (10, 10)))
