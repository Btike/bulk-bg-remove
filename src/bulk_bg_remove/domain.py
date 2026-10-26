"""Core domain types shared across the engine, bulk runner, CLI and UI."""

from __future__ import annotations

from enum import StrEnum


class OutputFormat(StrEnum):
    PNG = "png"
    WEBP = "webp"

    @property
    def extension(self) -> str:
        return ".png" if self is OutputFormat.PNG else ".webp"
