"""The quality-preserving pipeline: infer mask -> composite -> save."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..domain import OutputFormat
from ..engine.model import BackgroundRemover
from .compositing import composite_rgba
from .io import load_source, save_rgba


@dataclass(frozen=True, slots=True)
class ProcessResult:
    output_path: Path
    width: int
    height: int


def process_image(
    remover: BackgroundRemover,
    source_path: Path,
    output_path: Path,
    *,
    output_format: OutputFormat,
    alpha_matting: bool,
    post_process: bool = False,
) -> ProcessResult:
    """Remove the background from one image, preserving the subject verbatim."""
    source = load_source(source_path)
    mask = remover.alpha_mask(source.rgb, alpha_matting=alpha_matting, post_process=post_process)
    rgba = composite_rgba(source.rgb, mask)
    save_rgba(rgba, output_path, output_format, icc_profile=source.icc_profile)
    return ProcessResult(output_path=output_path, width=rgba.width, height=rgba.height)
