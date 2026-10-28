"""Pick a model for the kind of images and the RAM that is free (`--model auto`).

The scores are the blind-judged means (0-10) from the bench behind
`skills/bulk-bg-remove/references/models.md`: 11 models on 80 images, each
image tagged by content. A profile groups those tags into the kinds of work an
agent can tell apart by looking at a few photos.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..core.finish import NO_FINISH, Finish
from ..core.matting import EdgeMode
from .events import JSONObject
from .plan import minimum_free_ram_gb

AUTO_MODEL = "auto"


class Profile(StrEnum):
    UNIFORM = "uniform"  # products and objects with clean outlines (n=39)
    COMPLEX = "complex"  # hair, fur, plants, wires, thin parts (n=32)
    PEOPLE = "people"  # portraits and groups (n=8)
    SCENE = "scene"  # cluttered scenes with no clean background (n=6)
    DRAWING = "drawing"  # cartoons, anime, flat illustration (n=1)


PROFILE_SCORES: dict[Profile, dict[str, float]] = {
    Profile.UNIFORM: {
        "birefnet-general-lite": 8.6,
        "birefnet-general": 8.6,
        "birefnet-hrsod": 8.5,
        "birefnet-massive": 8.5,
        "birefnet-dis": 8.2,
        "u2net": 8.1,
        "silueta": 8.1,
        "isnet-general-use": 7.7,
    },
    Profile.COMPLEX: {
        "birefnet-general": 7.6,
        "birefnet-general-lite": 7.4,
        "birefnet-hrsod": 7.3,
        "birefnet-massive": 7.0,
        "birefnet-dis": 6.9,
        "isnet-general-use": 6.0,
        "silueta": 5.2,
        "u2net": 5.2,
    },
    Profile.PEOPLE: {
        "birefnet-general": 7.8,
        "birefnet-dis": 7.4,
        "birefnet-massive": 7.2,
        "birefnet-general-lite": 7.1,
        "birefnet-portrait": 7.1,
        "u2net": 6.0,
        "silueta": 5.8,
        "isnet-general-use": 5.2,
    },
    Profile.SCENE: {
        "birefnet-general": 6.3,
        "birefnet-hrsod": 5.7,
        "birefnet-massive": 5.3,
        "birefnet-dis": 5.3,
        "birefnet-general-lite": 5.2,
        "u2net": 4.0,
        "silueta": 4.0,
        "isnet-general-use": 3.7,
    },
    Profile.DRAWING: {
        "isnet-anime": 9.0,
        "birefnet-general-lite": 8.0,
        "birefnet-massive": 7.0,
    },
}

# Gaps up to this many points are ties on the bench, so auto may take the
# lighter model on its own; a larger loss needs the user's OK (--allow-fallback).
ALLOWED_GAP = 0.5


@dataclass(frozen=True, slots=True)
class ModelChoice:
    profile: Profile
    model: str
    score: float
    best_model: str
    best_score: float
    best_needs_gb: float
    needs_gb: float  # free RAM the chosen model needs
    free_ram_gb: float

    @property
    def gap(self) -> float:
        return round(self.best_score - self.score, 2)

    @property
    def fits(self) -> bool:
        return self.free_ram_gb >= self.needs_gb

    @property
    def is_fallback(self) -> bool:
        """True when the chosen model is clearly worse than the best for this profile."""
        return self.gap > ALLOWED_GAP

    def to_dict(self) -> JSONObject:
        return {
            "profile": self.profile.value,
            "model": self.model,
            "score": self.score,
            "best_model": self.best_model,
            "best_score": self.best_score,
            "best_needs_gb": round(self.best_needs_gb, 1),
            "needs_gb": round(self.needs_gb, 1),
            "fits": self.fits,
            "gap": self.gap,
        }


def choose_model(
    profile: Profile,
    free_ram_gb: float,
    edges: EdgeMode,
    megapixels: float,
    finish: Finish = NO_FINISH,
) -> ModelChoice:
    """The best-scoring model for `profile` that fits in `free_ram_gb`.

    Ties go to the lighter model. If nothing fits, the lightest one is returned
    and the plan's free-RAM check blocks the run with its real requirement.
    """
    needs = {
        model: minimum_free_ram_gb(model, edges, megapixels, finish)
        for model in PROFILE_SCORES[profile]
    }
    ranked = sorted(PROFILE_SCORES[profile].items(), key=lambda item: (-item[1], needs[item[0]]))
    best_model, best_score = ranked[0]
    fitting = [(model, score) for model, score in ranked if needs[model] <= free_ram_gb]
    model, score = fitting[0] if fitting else min(ranked, key=lambda item: needs[item[0]])
    return ModelChoice(
        profile, model, score, best_model, best_score, needs[best_model], needs[model], free_ram_gb
    )
