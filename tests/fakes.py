"""Fake models for tests: real pipeline, no ONNX weights.

Importable by spawned worker processes (pytest adds `tests/` to the path).
"""

from __future__ import annotations

from PIL import Image, ImageDraw

FAKE_FACTORY = "fakes:FakeRemover"
FAILING_FACTORY = "fakes:FailingRemover"

# A source whose top-left pixel is pure magenta yields an empty mask.
NO_SUBJECT_MARKER = (255, 0, 255)


class FakeRemover:
    """Keeps a centred rectangle, removes everything else."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    def alpha_mask(self, image: Image.Image) -> Image.Image:
        mask = Image.new("L", image.size, 0)
        if image.getpixel((0, 0)) == NO_SUBJECT_MARKER:
            return mask
        w, h = image.size
        ImageDraw.Draw(mask).rectangle((w // 4, h // 4, 3 * w // 4, 3 * h // 4), fill=255)
        return mask


class FailingRemover:
    def __init__(self, model_name: str) -> None:
        raise RuntimeError("model weights are corrupted")


NOISY_FACTORY = "fakes:NoisyRemover"


class NoisyRemover(FakeRemover):
    """Prints to stdout like pymatting does; must never reach `--json` output."""

    def alpha_mask(self, image: Image.Image) -> Image.Image:
        print("PERFORMANCE WARNING: something a library printed")
        return super().alpha_mask(image)
