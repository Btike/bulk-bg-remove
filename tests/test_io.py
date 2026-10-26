"""I/O guarantees: lossless round-trips and dimension preservation."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from bulk_bg_remove.core.io import load_source, save_rgba
from bulk_bg_remove.domain import OutputFormat


def test_load_source_converts_to_rgb(tmp_path) -> None:
    path = tmp_path / "in.png"
    Image.new("RGBA", (40, 30), (1, 2, 3, 255)).save(path)
    source = load_source(path)
    assert source.rgb.mode == "RGB"
    assert source.rgb.size == (40, 30)


def test_png_roundtrip_is_lossless(tmp_path) -> None:
    arr = np.random.default_rng(0).integers(0, 256, size=(30, 40, 4), dtype=np.uint8)
    out = tmp_path / "out.png"
    save_rgba(Image.fromarray(arr, mode="RGBA"), out, OutputFormat.PNG)
    assert np.array_equal(np.array(Image.open(out)), arr)


def test_webp_lossless_roundtrip_is_exact(tmp_path) -> None:
    arr = np.random.default_rng(1).integers(0, 256, size=(30, 40, 4), dtype=np.uint8)
    out = tmp_path / "out.webp"
    save_rgba(Image.fromarray(arr, mode="RGBA"), out, OutputFormat.WEBP)
    reloaded = np.array(Image.open(out).convert("RGBA"))
    assert np.array_equal(reloaded, arr)


def test_save_rejects_non_rgba(tmp_path) -> None:
    with pytest.raises(ValueError):
        save_rgba(Image.new("RGB", (10, 10)), tmp_path / "x.png", OutputFormat.PNG)
