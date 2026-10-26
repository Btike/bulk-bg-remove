"""Alpha compositing — the step that guarantees the subject stays untouched."""

from __future__ import annotations

from PIL import Image


def composite_rgba(rgb: Image.Image, mask: Image.Image) -> Image.Image:
    """Build an RGBA image from an untouched RGB source and an alpha mask.

    The RGB channels are copied verbatim — never resampled or re-encoded — so
    the subject keeps its original quality. Only the mask is resized (with a
    high-quality Lanczos filter) when its size differs from the source.
    """
    if rgb.mode != "RGB":
        raise ValueError(f"expected RGB source, got {rgb.mode}")

    alpha = mask if mask.mode == "L" else mask.convert("L")
    if alpha.size != rgb.size:
        alpha = alpha.resize(rgb.size, Image.Resampling.LANCZOS)

    result = rgb.copy()
    result.putalpha(alpha)
    return result
