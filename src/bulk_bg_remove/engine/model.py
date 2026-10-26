"""Background-removal model wrapper.

Loading a session is expensive (weights + ONNX runtime init), so a single
instance is created once per worker process and reused for every job.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, cast

from PIL import Image
from rembg import new_session, remove
from rembg.sessions import sessions_class

DEFAULT_MODEL = "birefnet-general-lite"


class MaskModel(Protocol):
    """Anything that turns an RGB image into an 'L' alpha mask of the same size."""

    def alpha_mask(
        self, image: Image.Image, *, alpha_matting: bool, post_process: bool = False
    ) -> Image.Image: ...


class BackgroundRemover:
    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        # rembg picks CUDA automatically when onnxruntime-gpu is installed.
        self._session = new_session(model_name)

    @property
    def model_name(self) -> str:
        return self._model_name

    def alpha_mask(
        self,
        image: Image.Image,
        *,
        alpha_matting: bool,
        post_process: bool = False,
    ) -> Image.Image:
        """Return a single-channel ('L') alpha mask at the input resolution.

        `post_process` cleans the mask but binarises edges (no antialiasing) —
        keep it off for soft edges. `alpha_matting` refines edges (slower).
        rembg resizes internally for inference, then upsamples the mask back to
        the input size, so the returned mask already matches the source image.
        """
        mask = remove(
            image,
            session=self._session,
            only_mask=True,
            post_process_mask=post_process,
            alpha_matting=alpha_matting,
        )
        if not isinstance(mask, Image.Image):
            raise TypeError("rembg did not return a PIL mask")
        return mask if mask.mode == "L" else mask.convert("L")


class _ModelFiles(Protocol):
    """The class-level download helpers every rembg session class has."""

    def resolve_existing(self, fname: str) -> str | None: ...

    def download_models(self) -> str: ...


def available_models() -> list[str]:
    return [cls.name() for cls in sessions_class]


def _session_class(model_name: str) -> _ModelFiles:
    for cls in sessions_class:
        if cls.name() == model_name:
            return cast(_ModelFiles, cls)
    raise ValueError(f"unknown model '{model_name}'. Available: {', '.join(available_models())}")


def downloaded_model_path(model_name: str) -> Path | None:
    """Where the model's weights live, or None if they still need downloading."""
    cls = _session_class(model_name)
    existing = cls.resolve_existing(f"{model_name}.onnx")
    return Path(existing) if existing else None


def ensure_model(model_name: str) -> Path:
    """Download the weights once, before workers start (avoids N parallel downloads)."""
    return Path(_session_class(model_name).download_models())
