"""Hebrew-safe text rendering onto images.

ffmpeg's drawtext has no bidi support, so Hebrew comes out reversed. Everything
visual is therefore composed here with Pillow: we wrap the text in *logical*
order, then run each finished line through the bidi algorithm for display.
Doing it in that order matters — reordering before wrapping breaks the words.
"""

from __future__ import annotations

import pathlib
from typing import Iterable

from PIL import Image, ImageDraw, ImageFilter, ImageFont

try:  # python-bidi >= 0.5
    from bidi import get_display
except ImportError:  # pragma: no cover - older releases
    from bidi.algorithm import get_display

# Fonts that ship on most Linux/macOS boxes and cover Hebrew. First hit wins.
FONT_CANDIDATES = (
    "assets/fonts/Heebo-Bold.ttf",
    "assets/fonts/Assistant-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
)


class FontNotFound(RuntimeError):
    pass


def find_font(root: pathlib.Path, override: str = "") -> pathlib.Path:
    """Locate a font with Hebrew coverage."""
    candidates: Iterable[str] = ([override] if override else ())
    for raw in (*candidates, *FONT_CANDIDATES):
        path = pathlib.Path(raw)
        if not path.is_absolute():
            path = root / raw
        if path.exists() and _covers_hebrew(path):
            return path
    raise FontNotFound(
        "No Hebrew-capable font found. Drop a .ttf into assets/fonts/ "
        "(Heebo-Bold.ttf works well) or set FONT_PATH in .env"
    )


def _covers_hebrew(path: pathlib.Path) -> bool:
    try:
        font = ImageFont.truetype(str(path), 40)
        return bool(font.getmask("א").getbbox())
    except Exception:
        return False


def shape(text: str) -> str:
    """Logical order -> visual order for rendering."""
    return get_display(text)


def wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Word-wrap in logical order, returning display-ordered lines."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            trial = f"{current} {word}"
            if font.getbbox(shape(trial))[2] <= max_width:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return [shape(line) for line in lines]


def fit_font(text: str, font_path: pathlib.Path, max_width: int, max_height: int,
             start: int, minimum: int = 28) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """Shrink the type until the wrapped block fits the box."""
    size = start
    while size >= minimum:
        font = ImageFont.truetype(str(font_path), size)
        lines = wrap(text, font, max_width)
        line_h = int(size * 1.32)
        if len(lines) * line_h <= max_height:
            return font, lines
        size -= 4
    font = ImageFont.truetype(str(font_path), minimum)
    return font, wrap(text, font, max_width)


def cover_crop(image: Image.Image, width: int, height: int) -> Image.Image:
    """Scale-and-crop to exactly fill width x height, preserving aspect."""
    src_ratio = image.width / image.height
    dst_ratio = width / height
    if src_ratio > dst_ratio:
        new_h = height
        new_w = max(width, int(round(height * src_ratio)))
    else:
        new_w = width
        new_h = max(height, int(round(width / src_ratio)))
    resized = image.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - width) // 2
    top = (new_h - height) // 2
    return resized.crop((left, top, left + width, top + height))


def bottom_scrim(size: tuple[int, int], height_frac: float = 0.55,
                 strength: int = 220) -> Image.Image:
    """A soft dark gradient so text stays readable over any photo."""
    width, height = size
    scrim = Image.new("L", (1, height), 0)
    start = int(height * (1 - height_frac))
    for y in range(start, height):
        t = (y - start) / max(1, height - start)
        scrim.putpixel((0, y), int((t ** 1.6) * strength))
    scrim = scrim.resize((width, height))
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    layer.putalpha(scrim)
    return layer


def draw_block(draw: ImageDraw.ImageDraw, lines: list[str],
               font: ImageFont.FreeTypeFont, *, box: tuple[int, int, int],
               fill: tuple[int, int, int], anchor_bottom: int,
               line_spacing: float = 1.32) -> int:
    """Draw right-aligned display-ordered lines. Returns the block's top y."""
    right, _, _ = box
    line_h = int(font.size * line_spacing)
    top = anchor_bottom - len(lines) * line_h
    y = top
    for line in lines:
        w = font.getbbox(line)[2]
        draw.text((right - w, y), line, font=font, fill=fill)
        y += line_h
    return top


def glow_text(base: Image.Image, lines: list[str], font: ImageFont.FreeTypeFont,
              *, right: int, anchor_bottom: int,
              fill: tuple[int, int, int] = (245, 247, 250)) -> None:
    """Text with a soft dark halo — legible on bright and dark footage alike."""
    shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw_block(ImageDraw.Draw(shadow), lines, font,
               box=(right, 0, 0), fill=(0, 0, 0), anchor_bottom=anchor_bottom)
    shadow = shadow.filter(ImageFilter.GaussianBlur(9))
    base.alpha_composite(shadow)
    draw_block(ImageDraw.Draw(base), lines, font,
               box=(right, 0, 0), fill=fill, anchor_bottom=anchor_bottom)
