"""Background-removal model wrapper.

Loading a session is expensive (weights + ONNX runtime init), so a single
instance is created once per worker process and reused for every job.
"""

from __future__ import annotations

from PIL import Image
from rembg import new_session, remove


def coreml_providers() -> list[str]:
    """ONNX providers that use Apple's Neural Engine/GPU, with CPU fallback."""
    return ["CoreMLExecutionProvider", "CPUExecutionProvider"]


class BackgroundRemover:
    def __init__(self, model_name: str, *, providers: list[str] | None = None) -> None:
        self._model_name = model_name
        self._session = new_session(model_name, providers=providers)

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
