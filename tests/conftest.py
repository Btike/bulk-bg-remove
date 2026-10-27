"""Shared fixtures: small real images written to a temp folder."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image

MakeImage = Callable[..., Path]


@pytest.fixture
def make_image(tmp_path: Path) -> MakeImage:
    def make(
        rel: str,
        size: tuple[int, int] = (64, 48),
        color: tuple[int, int, int] = (40, 120, 200),
        fmt: str | None = None,
    ) -> Path:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", size, color).save(path, format=fmt)
        return path

    return make
