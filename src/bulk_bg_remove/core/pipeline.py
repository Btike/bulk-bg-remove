"""The quality-preserving pipeline: load -> mask -> refine edges -> composite ->
(optional) finish -> save."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from PIL import Image

from ..domain import OutputFormat
from ..engine.model import MaskModel
from .compositing import composite_rgba
from .finish import Finish, finish_image
from .io import load_source, save_image
from .matting import EdgeMode, refine_edges

# Longest side of the cut-out preview kept for review sheets.
PREVIEW_SIZE = 800


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
    finish: Finish | None = None,
    cutout_preview: Path | None = None,
) -> ProcessResult:
    """Remove the background from one image.

    The subject's RGB is copied verbatim; only in `EdgeMode.CLEAN` are the
    semi-transparent edge pixels recoloured to remove the old background's halo.
    `matting_budget_gb(megapixels, edges)` is asked right before edge matting,
    once the model has run, so it can size the matting to the RAM actually free.
    `finish` (background, square, margin, size) is applied after compositing.
    `cutout_preview`, if given, receives a small transparent copy of the cut-out
    before finishing, for review sheets: a cut-out on white hides white leftovers.
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
    if cutout_preview is not None:
        preview = rgba.copy()
        preview.thumbnail((PREVIEW_SIZE, PREVIEW_SIZE), Image.Resampling.LANCZOS)
        preview.save(cutout_preview, format="PNG")
    note, tag = refined.note, None
    if finish is not None and finish.active:
        tag = finish.signature
        result, finish_note = finish_image(rgba, finish)
        if finish_note:
            note = finish_note if note is None else f"{note}; {finish_note}"
    else:
        result = rgba
    stage(Stage.SAVING)
    save_image(result, output_path, output_format, icc_profile=source.icc_profile, tag=tag)
    return ProcessResult(output_path, result.width, result.height, note)
