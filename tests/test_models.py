"""`--model auto`: the best model for the kind of images that fits in free RAM."""

from __future__ import annotations

import pytest

from bulk_bg_remove.bulk.models import PROFILE_SCORES, Profile, choose_model
from bulk_bg_remove.bulk.plan import MODEL_PEAK_GB
from bulk_bg_remove.core.matting import EdgeMode

MATTE = EdgeMode.MATTE


def test_plenty_of_ram_gets_the_best_model() -> None:
    assert choose_model(Profile.UNIFORM, 30.0, MATTE, 12).model == "birefnet-general-lite"
    assert choose_model(Profile.COMPLEX, 30.0, MATTE, 12).model == "birefnet-general"
    assert choose_model(Profile.DRAWING, 30.0, MATTE, 12).model == "isnet-anime"


def test_ties_go_to_the_lighter_model(monkeypatch: pytest.MonkeyPatch) -> None:
    # u2net and silueta both score 8.1 on uniform products and both fit in 5 GB;
    # u2net is listed first, so only the tie-break can pick silueta (~1 GB less).
    assert choose_model(Profile.UNIFORM, 5.0, MATTE, 12).model == "silueta"
    # Same for the best model: list general before lite, lite must still win.
    scores = {"birefnet-general": 8.6, "birefnet-general-lite": 8.6}
    monkeypatch.setitem(PROFILE_SCORES, Profile.UNIFORM, scores)
    choice = choose_model(Profile.UNIFORM, 30.0, MATTE, 12)
    assert choice.model == choice.best_model == "birefnet-general-lite"
    assert not choice.is_fallback


def test_a_4_gb_laptop_still_cuts_products_well_on_its_own() -> None:
    choice = choose_model(Profile.UNIFORM, 4.0, MATTE, 12)
    assert choice.model == "silueta"
    assert choice.gap == 0.5
    assert not choice.is_fallback  # within a tie: no need to ask


def test_a_big_quality_loss_is_flagged_for_the_user() -> None:
    choice = choose_model(Profile.COMPLEX, 4.0, MATTE, 12)
    assert choice.model == "silueta"
    assert choice.is_fallback
    assert choice.best_model == "birefnet-general"
    assert choice.best_needs_gb > 13


def test_nothing_fits_returns_the_lightest_so_the_plan_blocks() -> None:
    choice = choose_model(Profile.UNIFORM, 1.0, MATTE, 12)
    assert choice.model == "silueta"
    assert not choice.fits


@pytest.mark.parametrize("profile", list(Profile))
def test_every_ranked_model_has_measured_ram(profile: Profile) -> None:
    assert set(PROFILE_SCORES[profile]) <= set(MODEL_PEAK_GB)
