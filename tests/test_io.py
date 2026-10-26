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


def test_exif_orientation_is_applied(tmp_path) -> None:
    path = tmp_path / "phone.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: rotate 90 CW to display
    Image.new("RGB", (60, 40), (9, 9, 9)).save(path, exif=exif)
    source = load_source(path)
    assert source.rgb.size == (40, 60)  # upright, as displayed


def test_heic_loads_as_rgb(tmp_path) -> None:
    path = tmp_path / "iphone.heic"
    Image.new("RGB", (32, 24), (120, 60, 30)).save(path, format="HEIF")
    source = load_source(path)
    assert source.rgb.mode == "RGB"
    assert source.rgb.size == (32, 24)


def test_icc_profile_is_carried_over(tmp_path) -> None:
    from PIL import ImageCms

    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    path = tmp_path / "with-icc.png"
    Image.new("RGB", (8, 8)).save(path, icc_profile=icc)
    assert load_source(path).icc_profile == icc


def test_cmyk_profile_is_dropped(tmp_path) -> None:
    path = tmp_path / "print.jpg"
    Image.new("CMYK", (8, 8)).save(path, icc_profile=b"fake-cmyk-profile")
    source = load_source(path)
    assert source.rgb.mode == "RGB"
    assert source.icc_profile is None
