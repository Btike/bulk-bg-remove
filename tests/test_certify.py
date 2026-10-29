"""Chroma-key certification: which cut-outs may skip the visual review."""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from bulk_bg_remove.core.certify import certify, check_cutout, mirror_agrees

WHITE = (250, 250, 250)
BOTTLE = (60, 140, 40)
BOX = (240, 120, 400, 380)  # left, top, right, bottom (exclusive)


def photo(background: tuple[int, int, int] = WHITE) -> Image.Image:
    image = Image.new("RGB", (640, 480), background)
    ImageDraw.Draw(image).rectangle((BOX[0], BOX[1], BOX[2] - 1, BOX[3] - 1), fill=BOTTLE)
    return image


def alpha(box: tuple[int, int, int, int] = BOX) -> Image.Image:
    mask = Image.new("L", (640, 480), 0)
    ImageDraw.Draw(mask).rectangle((box[0], box[1], box[2] - 1, box[3] - 1), fill=255)
    return mask


def test_a_clean_product_on_a_plain_background_is_certified() -> None:
    result = certify(photo(), alpha(), alpha(), lambda: ImageOps.mirror(alpha()))
    assert result.certified and result.reason == "certified"


def test_a_missing_part_of_the_subject_needs_review() -> None:
    cut = alpha((240, 120, 400, 300))  # the bottom of the bottle is gone
    result = check_cutout(photo(), cut)
    assert not result.certified and "removed" in result.reason


def test_background_left_on_the_edge_needs_review() -> None:
    cut = alpha((220, 100, 420, 400))  # a band of white kept around the bottle
    result = check_cutout(photo(), cut)
    assert not result.certified and "left on the edge" in result.reason


def test_a_busy_background_always_needs_review() -> None:
    noisy = np.random.default_rng(0).integers(0, 255, (480, 640, 3), dtype=np.uint8)
    busy = Image.fromarray(noisy)
    ImageDraw.Draw(busy).rectangle((240, 120, 399, 379), fill=BOTTLE)
    result = check_cutout(busy, alpha())
    assert not result.certified and "not plain" in result.reason


def test_an_unsure_model_needs_review_and_the_mirror_runs_only_when_needed() -> None:
    calls: list[int] = []

    def shifted_mirror() -> Image.Image:
        calls.append(1)
        return ImageOps.mirror(alpha((180, 120, 340, 380)))  # a different guess

    result = certify(photo(), alpha(), alpha(), shifted_mirror)
    assert not result.certified and "unsure" in result.reason
    assert calls == [1]
    certify(photo(), alpha((240, 120, 400, 300)), alpha(), shifted_mirror)
    assert calls == [1]  # failed a cheap check: the model is not run again


def test_empty_and_untouched_cut_outs_are_never_certified() -> None:
    assert check_cutout(photo(), Image.new("L", (640, 480), 0)).reason == "no subject found"
    assert check_cutout(photo(), Image.new("L", (640, 480), 255)).reason == "nothing was removed"


def test_mirror_agreement_tolerates_resampling_noise() -> None:
    assert mirror_agrees(alpha(), ImageOps.mirror(alpha()))
    assert not mirror_agrees(alpha(), alpha((0, 0, 100, 100)))


def test_background_kept_inside_the_subject_needs_review() -> None:
    mug = photo()
    ImageDraw.Draw(mug).rectangle((290, 200, 349, 299), fill=WHITE)  # the hole of a handle
    result = check_cutout(mug, alpha())  # ...cut out as if it were part of the mug
    assert not result.certified and "kept inside" in result.reason


def test_a_faint_halo_on_the_edge_needs_review() -> None:
    halo = alpha((228, 108, 412, 392))
    ImageDraw.Draw(halo).rectangle((228, 108, 411, 391), fill=153)  # alpha 0.6 band
    ImageDraw.Draw(halo).rectangle((240, 120, 399, 379), fill=255)
    result = check_cutout(photo(), halo)
    assert not result.certified


def test_a_half_erased_part_needs_review() -> None:
    cut = alpha()
    ImageDraw.Draw(cut).rectangle((250, 280, 389, 369), fill=70)  # alpha 0.27 inside
    result = check_cutout(photo(), cut)
    assert not result.certified and "half-transparent" in result.reason


def test_a_subject_close_to_the_background_colour_needs_review() -> None:
    grey = Image.new("RGB", (640, 480), WHITE)
    ImageDraw.Draw(grey).rectangle((240, 120, 399, 379), fill=(228, 228, 228))
    result = check_cutout(grey, alpha((240, 120, 400, 300)))  # bottom 30% cut away
    assert not result.certified and "too close" in result.reason


def test_a_check_that_cannot_run_means_review_not_failure() -> None:
    def broken() -> Image.Image:
        raise MemoryError

    result = certify(photo(), alpha(), alpha(), broken)
    assert not result.certified and "could not run" in result.reason


def bottle_with_label(text: bool, ink: tuple[int, int, int] = (30, 30, 30)) -> Image.Image:
    """A white label on the bottle; with text, marks perforate it like print."""
    image = photo()
    draw = ImageDraw.Draw(image)
    draw.rectangle((270, 160, 369, 279), fill=WHITE)  # 100 x 120 label, 25 % of the bottle
    if text:
        for row in range(12):
            for column in range(10):
                x, y = 274 + column * 10, 164 + row * 10
                draw.rectangle((x, y, x + 5, y + 1), fill=ink)
    return image


def test_a_white_label_printed_with_text_is_certified() -> None:
    result = check_cutout(bottle_with_label(text=True), alpha())
    assert result.certified, result.reason


def test_a_plain_white_patch_inside_still_counts_as_background() -> None:
    result = check_cutout(bottle_with_label(text=False), alpha())
    assert not result.certified and "kept inside" in result.reason


def test_a_perforated_blob_with_a_soft_rim_is_not_excused() -> None:
    faint = bottle_with_label(text=True, ink=(232, 232, 232))  # as perforated, but barely visible
    result = check_cutout(faint, alpha())
    assert not result.certified and "kept inside" in result.reason
