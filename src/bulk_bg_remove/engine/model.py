"""Background-removal model wrapper.

Loading a session is expensive (weights + ONNX runtime init), so a single
instance is created once per worker process and reused for every job.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol, cast

import onnxruntime as ort
from PIL import Image
from rembg import remove
from rembg.sessions import sessions_class

DEFAULT_MODEL = "birefnet-general-lite"


class MaskModel(Protocol):
    """Anything that turns an RGB image into an 'L' alpha mask of the same size."""

    def alpha_mask(self, image: Image.Image) -> Image.Image: ...


def _session_options() -> ort.SessionOptions:
    """Measured on BiRefNet: same speed, ~30% lower peak RAM than the defaults."""
    options = ort.SessionOptions()
    threads = os.environ.get("OMP_NUM_THREADS")
    if threads and threads.isdigit():
        options.intra_op_num_threads = int(threads)
    options.inter_op_num_threads = 1
    options.enable_mem_pattern = False
    return options


class BackgroundRemover:
    def __init__(self, model_name: str) -> None:
        self._model_name = model_name
        # No explicit providers: rembg picks CUDA when onnxruntime-gpu is installed.
        session_class = _session_class(model_name)
        self._session = session_class(model_name, _session_options())

    @property
    def model_name(self) -> str:
        return self._model_name

    def alpha_mask(self, image: Image.Image) -> Image.Image:
        """Return a single-channel ('L') alpha mask at the input resolution.

        rembg resizes internally for inference, then upsamples the mask back to
        the input size, so the returned mask already matches the source image.
        Edge refinement (matting) happens afterwards, in `core.matting`.
        """
        mask = remove(image, session=self._session, only_mask=True)
        if not isinstance(mask, Image.Image):
            raise TypeError("rembg did not return a PIL mask")
        return mask if mask.mode == "L" else mask.convert("L")


class _RembgSession(Protocol):
    """An inference session that `rembg.remove` accepts (rembg is untyped)."""


class _ModelFiles(Protocol):
    """The class-level helpers every rembg session class has."""

    def __call__(self, model_name: str, sess_opts: ort.SessionOptions) -> _RembgSession: ...

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
