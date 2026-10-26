"""Format recognition by content: accepted formats, disguised and unsupported files."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from bulk_bg_remove.formats import sniff


@pytest.mark.parametrize(
    ("pil_format", "suffix", "label"),
    [
        ("JPEG", ".jpg", "JPEG"),
        ("PNG", ".png", "PNG"),
        ("HEIF", ".heic", "HEIC/HEIF"),
        ("WEBP", ".webp", "WebP"),
        ("AVIF", ".avif", "AVIF"),
        ("TIFF", ".tiff", "TIFF"),
        ("BMP", ".bmp", "BMP"),
    ],
)
def test_supported_formats_are_recognised(
    tmp_path: Path, pil_format: str, suffix: str, label: str
) -> None:
    path = tmp_path / f"img{suffix}"
    Image.new("RGB", (64, 48), (200, 30, 30)).save(path, format=pil_format)
    result = sniff(path)
    assert result.supported
    assert result.format == label
    assert (result.width, result.height) == (64, 48)


def test_detects_by_content_not_extension(tmp_path: Path) -> None:
    path = tmp_path / "actually-a-jpeg.png"
    Image.new("RGB", (10, 10)).save(path, format="JPEG")
    assert sniff(path).format == "JPEG"


def test_html_disguised_as_jpg_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "broken.jpg"
    path.write_text("<html><body>404 Not Found</body></html>")
    result = sniff(path)
    assert not result.supported
    assert result.reason == "not an image"


def test_gif_is_unsupported(tmp_path: Path) -> None:
    path = tmp_path / "anim.gif"
    Image.new("RGB", (10, 10)).save(path, format="GIF")
    result = sniff(path)
    assert not result.supported
    assert result.reason == "unsupported format: GIF"


def test_empty_file_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "empty.jpg"
    path.write_bytes(b"")
    assert not sniff(path).supported
