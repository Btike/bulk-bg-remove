"""The quality-preserving pipeline: load -> mask -> refine edges -> composite -> save."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ..domain import OutputFormat
from ..engine.model import MaskModel
from .compositing import composite_rgba
from .io import load_source, save_rgba
from .matting import EdgeMode, refine_edges


class Stage(StrEnum):
    LOADING = "loading"
    REMOVING = "removing"
    REFINING = "refining"
    COMPOSITING = "compositing"
    SAVING = "saving"


@dataclass(frozen=True, slots=True)
class ProcessResult:
    output_path: Path
    width: int
    height: int
    note: str | None = None


def process_image(
    remover: MaskModel,
    source_path: Path,
    output_path: Path,
    *,
    output_format: OutputFormat,
    edges: EdgeMode = EdgeMode.MATTE,
    on_stage: Callable[[Stage], None] | None = None,
    matting_budget_gb: Callable[[float, EdgeMode], float] | None = None,
) -> ProcessResult:
    """Remove the background from one image.

    The subject's RGB is copied verbatim; only in `EdgeMode.CLEAN` are the
    semi-transparent edge pixels recoloured to remove the old background's halo.
    `matting_budget_gb(megapixels, edges)` is asked right before edge matting,
    once the model has run, so it can size the matting to the RAM actually free.
    """

    def stage(value: Stage) -> None:
        if on_stage is not None:
            on_stage(value)

    stage(Stage.LOADING)
    source = load_source(source_path)
    stage(Stage.REMOVING)
    mask = remover.alpha_mask(source.rgb)
    stage(Stage.REFINING)
    if matting_budget_gb is None:
        refined = refine_edges(source.rgb, mask, edges)
    else:
        megapixels = source.rgb.width * source.rgb.height / 1e6
        budget = matting_budget_gb(megapixels, edges)
        refined = refine_edges(source.rgb, mask, edges, budget_gb=budget)
    stage(Stage.COMPOSITING)
    rgba = composite_rgba(refined.rgb, refined.alpha)
    stage(Stage.SAVING)
    save_rgba(rgba, output_path, output_format, icc_profile=source.icc_profile)
    return ProcessResult(output_path, rgba.width, rgba.height, refined.note)
