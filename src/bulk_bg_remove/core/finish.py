"""Optional finishing for shops: a solid background, a square canvas, a margin
around the subject and a fixed size.

With no finishing the cut-out is saved as is (bit-exact subject pixels). Each
step below changes pixels on purpose, and only what it has to:

- `background` flattens onto a colour: only semi-transparent edge pixels blend.
- `square` / `pad` crop to the subject and re-frame it: a crop, no resampling.
- `size` scales the finished image so its longest side is that many pixels.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from PIL import Image

# Alpha above this counts as subject when cropping (matting leaves faint noise).
SUBJECT_ALPHA = 8
# Enlarging by less than this is not worth a warning.
UPSCALE_NOTE_ABOVE = 1.25
MAX_PAD = 25.0  # percent per side: the subject still spans half the canvas

NAMED_COLOURS: dict[str, tuple[int, int, int]] = {
    "white": (255, 255, 255),
    "black": (0, 0, 0),
}
_HEX = re.compile(r"#?([0-9a-fA-F]{6})")


def parse_colour(value: str) -> tuple[int, int, int]:
    """`white`, `black`, `#RRGGBB` or `RRGGBB`."""
    named = NAMED_COLOURS.get(value.strip().lower())
    if named is not None:
        return named
    match = _HEX.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"not a colour: {value!r} (use white, black or #RRGGBB)")
    digits = match.group(1)
    return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))


@dataclass(frozen=True, slots=True)
class Finish:
    background: tuple[int, int, int] | None = None  # None: keep transparency
    square: bool = False
    pad: float = 0.0  # percent of the canvas side, on each side of the subject
    size: int | None = None  # longest side of the result, in pixels

    def __post_init__(self) -> None:
        if not 0 <= self.pad <= MAX_PAD:
            raise ValueError(f"--pad must be between 0 and {MAX_PAD:g} (percent)")
        if self.size is not None and self.size < 16:
            raise ValueError("--size must be at least 16 pixels")

    @property
    def active(self) -> bool:
        return self != Finish()

    @property
    def crops(self) -> bool:
        """Whether the result is re-framed around the subject."""
        return self.square or self.pad > 0

    @property
    def opaque(self) -> bool:
        return self.background is not None

    @property
    def signature(self) -> str:
        """Stored in each finished output, so a re-run with another finish redoes it."""
        return (
            f"bulk-bg-remove finish: background={self.background_hex or 'none'} "
            f"square={int(self.square)} pad={self.pad:g} size={self.size or 'none'}"
        )

    @property
    def canvas_growth(self) -> float:
        """At most how many times the photo's pixel count the framed canvas holds
        before any --size (square: up to 2x for a 2:1 photo; the margin adds more)."""
        if not self.crops:
            return 1.0
        fill = 1 - 2 * self.pad / 100
        return (2.0 if self.square else 1.0) / fill**2

    @property
    def background_hex(self) -> str | None:
        if self.background is None:
            return None
        return "#{:02X}{:02X}{:02X}".format(*self.background)


NO_FINISH = Finish()


def finish_image(rgba: Image.Image, finish: Finish) -> tuple[Image.Image, str | None]:
    """Apply `finish` to a cut-out. Returns the image (RGB when the background
    is opaque, RGBA otherwise) and a note for the report, if any."""
    if not finish.active:
        return rgba, None
    image, note = rgba, None
    # Checked here, before framing adds transparent margins or a background
    # hides the alpha: verify cannot see these on a finished output.
    alpha = rgba.getchannel("A")
    low, high = alpha.getextrema()
    if high == 0:
        note = "empty cut-out: no subject detected"
    elif low == 255:
        note = "nothing removed: background kept"

    if finish.crops:
        # No subject found: frame the whole image, so the layout still holds.
        found = alpha.point(lambda a: 255 if a > SUBJECT_ALPHA else 0).getbbox()
        box = found or (0, 0, *rgba.size)
        width, height = rgba.size
        cut = (box[0] == 0, box[1] == 0, box[2] == width, box[3] == height)
        image = _frame(rgba.crop(box), finish, cut)

    if finish.size is not None:
        longest = max(image.size)
        scale = finish.size / longest
        if scale != 1:
            target = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
            # Pillow premultiplies RGBA when resampling, so the old background
            # under transparent pixels does not bleed into the edges.
            image = image.resize(target, Image.Resampling.LANCZOS)
            if scale > UPSCALE_NOTE_ABOVE:
                upscaled = f"upscaled ×{scale:.1f} from a small original ({longest} px)"
                note = upscaled if note is None else f"{note}; {upscaled}"

    # Last, on the final size: flattening a large canvas first would cost RAM.
    if finish.background is not None:
        flat = Image.new("RGB", image.size, finish.background)
        flat.paste(image, (0, 0), image)
        image = flat
    return image, note


def _frame(subject: Image.Image, finish: Finish, cut: tuple[bool, bool, bool, bool]) -> Image.Image:
    """Place the cropped subject on a transparent canvas with the margin, centred.

    `cut` says which edges of the photo (left, top, right, bottom) the subject
    touches: a bust cut by the bottom of the frame stays flush with the bottom
    of the canvas instead of floating in the middle. Cut at both top and bottom
    (an ear touching the top), the bottom wins.
    """
    fill = 1 - 2 * finish.pad / 100  # share of the canvas the subject spans
    width, height = subject.size
    if finish.square:
        side = round(max(width, height) / fill)
        canvas_size = (side, side)
    else:
        margin = round(max(width, height) / fill) - max(width, height)
        canvas_size = (width + margin, height + margin)
    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    offset = (
        _place(canvas_size[0] - width, start=cut[0], end=cut[2]),
        _place(canvas_size[1] - height, start=cut[1] and not cut[3], end=cut[3]),
    )
    canvas.paste(subject, offset)
    return canvas


def _place(room: int, *, start: bool, end: bool) -> int:
    """Offset along one axis: flush with the side the subject was cut on."""
    if start and not end:
        return 0
    if end and not start:
        return room
    return room // 2
