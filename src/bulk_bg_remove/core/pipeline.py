"""The quality-preserving pipeline: load -> infer mask -> composite -> save."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ..domain import OutputFormat
from ..engine.model import MaskModel
from .compositing import composite_rgba
from .io import load_source, save_rgba


class Stage(StrEnum):
    LOADING = "loading"
    REMOVING = "removing"
    COMPOSITING = "compositing"
    SAVING = "saving"


@dataclass(frozen=True, slots=True)
class ProcessResult:
    output_path: Path
    width: int
    height: int


def process_image(
    remover: MaskModel,
    source_path: Path,
    output_path: Path,
    *,
    output_format: OutputFormat,
    alpha_matting: bool,
    post_process: bool = False,
    on_stage: Callable[[Stage], None] | None = None,
) -> ProcessResult:
    """Remove the background from one image, preserving the subject verbatim."""

    def stage(value: Stage) -> None:
        if on_stage is not None:
            on_stage(value)

    stage(Stage.LOADING)
    source = load_source(source_path)
    stage(Stage.REMOVING)
    mask = remover.alpha_mask(source.rgb, alpha_matting=alpha_matting, post_process=post_process)
    stage(Stage.COMPOSITING)
    rgba = composite_rgba(source.rgb, mask)
    stage(Stage.SAVING)
    save_rgba(rgba, output_path, output_format, icc_profile=source.icc_profile)
    return ProcessResult(output_path=output_path, width=rgba.width, height=rgba.height)
