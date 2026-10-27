"""Core domain types shared across the engine, bulk runner, CLI and UI."""

from __future__ import annotations

from enum import StrEnum


class OutputFormat(StrEnum):
    PNG = "png"
    WEBP = "webp"
    JPG = "jpg"  # only with an opaque --background: JPEG has no transparency

    @property
    def extension(self) -> str:
        return f".{self.value}"
