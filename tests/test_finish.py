"""Finishing for shops: background colour, square canvas, margin and size."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from bulk_bg_remove.core.finish import Finish, finish_image, parse_colour
from bulk_bg_remove.core.io import save_image
from bulk_bg_remove.domain import OutputFormat

SUBJECT = (200, 40, 60)
OLD_BACKGROUND = (10, 220, 10)


def cutout(size: tuple[int, int] = (200, 100), box: tuple[int, int, int, int] = (80, 20, 120, 80)):
    """An RGBA cut-out: an opaque subject in `box`, a half-transparent pixel at
    its edge, and the old background's RGB kept under transparent pixels."""
    image = Image.new("RGBA", size, (*OLD_BACKGROUND, 0))
    image.paste((*SUBJECT, 255), box)
    if box[0] > 0:
        image.putpixel((box[0] - 1, box[1]), (*SUBJECT, 128))
    return image


def test_colours_by_name_or_hex() -> None:
    assert parse_colour("white") == (255, 255, 255)
    assert parse_colour("#E6F6EA") == (230, 246, 234)
    assert parse_colour("fce4ef") == (252, 228, 239)
    with pytest.raises(ValueError, match="not a colour"):
        parse_colour("pinkish")


def test_limits_are_checked() -> None:
    with pytest.raises(ValueError, match="--pad"):
        Finish(pad=45)
    with pytest.raises(ValueError, match="--size"):
        Finish(size=8)


def test_no_finish_returns_the_cut_out_untouched() -> None:
    image = cutout()
    result, note = finish_image(image, Finish())
    assert result is image and note is None


def test_background_keeps_size_and_subject_pixels() -> None:
    result, note = finish_image(cutout(), Finish(background=(255, 255, 255)))
    assert note is None
    assert result.mode == "RGB" and result.size == (200, 100)
    assert result.getpixel((100, 50)) == SUBJECT  # opaque subject: copied exactly
    assert result.getpixel((5, 5)) == (255, 255, 255)  # old background gone
    edge = result.getpixel((79, 20))  # half-transparent: blended with the new colour
    assert all(s <= c <= 255 for s, c in zip(SUBJECT, edge, strict=True))
    assert edge not in (SUBJECT, (255, 255, 255))


def test_square_with_margin_centres_the_subject() -> None:
    result, _ = finish_image(cutout(), Finish(background=(255, 255, 255), square=True, pad=10))
    side = result.width
    assert result.size == (side, side)
    # The subject (41 x 60 with its edge pixel) spans 80% of the side: 10% each side.
    assert side == round(60 / 0.8)
    rows = np.where((np.array(result) != 255).any(axis=(1, 2)))[0]
    assert abs(rows[0] - (side - 1 - rows[-1])) <= 1  # centred vertically
    assert rows[0] == pytest.approx(side * 0.10, abs=1)


def test_square_without_background_stays_transparent() -> None:
    result, _ = finish_image(cutout(), Finish(square=True))
    assert result.mode == "RGBA"
    assert result.width == result.height == 60
    assert result.getpixel((0, 0))[3] == 0


def test_size_sets_the_longest_side_and_reports_upscaling() -> None:
    result, note = finish_image(cutout(), Finish(size=100))
    assert max(result.size) == 100 and note is None
    result, note = finish_image(cutout(), Finish(square=True, size=300))
    assert result.size == (300, 300)
    assert note is not None and "upscaled ×5.0" in note


def test_an_empty_cut_out_is_reported_and_still_framed() -> None:
    empty = Image.new("RGBA", (120, 80), (*OLD_BACKGROUND, 0))
    result, note = finish_image(empty, Finish(background=(255, 255, 255), square=True))
    assert note == "empty cut-out: no subject detected"
    assert result.width == result.height


def test_jpeg_needs_an_opaque_image(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no transparency"):
        save_image(cutout(), tmp_path / "x.jpg", OutputFormat.JPG)
    flat, _ = finish_image(cutout(), Finish(background=(255, 255, 255)))
    save_image(flat, tmp_path / "x.jpg", OutputFormat.JPG)
    with Image.open(tmp_path / "x.jpg") as reloaded:
        assert reloaded.format == "JPEG" and reloaded.size == (200, 100)
        pixel = reloaded.getpixel((100, 50))
        assert max(abs(a - b) for a, b in zip(pixel, SUBJECT, strict=True)) <= 3


def test_a_subject_cut_by_the_frame_stays_flush_with_that_edge() -> None:
    bust = cutout(box=(80, 40, 120, 100))  # touches the bottom of the photo
    result, _ = finish_image(bust, Finish(background=(255, 255, 255), square=True, pad=10))
    rows = np.where((np.array(result) != 255).any(axis=(1, 2)))[0]
    assert rows[-1] == result.height - 1  # no margin under the cut
    cols = np.where((np.array(result) != 255).any(axis=(0, 2)))[0]
    assert abs(cols[0] - (result.width - 1 - cols[-1])) <= 1  # still centred sideways
    tall = cutout(box=(80, 0, 120, 100))  # touches the top too: the bottom wins
    result, _ = finish_image(tall, Finish(square=True, pad=10))
    assert result.getchannel("A").getbbox()[3] == result.height


def test_a_subject_cut_on_one_side_stays_flush_with_it() -> None:
    left = cutout(box=(0, 20, 40, 80))
    result, _ = finish_image(left, Finish(square=True, pad=10))
    assert result.getchannel("A").getbbox()[0] == 0
    right = cutout(box=(160, 20, 200, 80))
    result, _ = finish_image(right, Finish(square=True, pad=10))
    assert result.getchannel("A").getbbox()[2] == result.width


def test_an_unchanged_cut_out_is_reported_even_when_framed_transparent() -> None:
    solid = Image.new("RGBA", (200, 100), (*SUBJECT, 255))
    result, note = finish_image(solid, Finish(square=True, pad=5))
    assert result.getchannel("A").getextrema()[0] == 0  # the margin is transparent...
    assert note == "nothing removed: background kept"  # ...so the note must come from here


def test_each_finish_has_its_own_signature() -> None:
    shop = Finish(background=(255, 255, 255), square=True, pad=7.5, size=2000)
    mint = Finish(background=(230, 246, 234), square=True, pad=7.5, size=2000)
    unpadded = Finish(background=(255, 255, 255), square=True, size=2000)
    assert len({shop.signature, mint.signature, unpadded.signature}) == 3
    assert Finish().canvas_growth == 1.0
    assert Finish(square=True, pad=25).canvas_growth == 8.0
