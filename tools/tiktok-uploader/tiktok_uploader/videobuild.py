"""Build a 9:16 TikTok slideshow from a folder of photos.

Division of labour: Pillow composes every pixel of text and branding (so Hebrew
is correct and crisp), ffmpeg only supplies motion, transitions and audio.

A project is a folder:

    projects/shawarma-promo/
        project.json
        1.jpg
        2.jpg

project.json (all optional):

    {
      "kind": "menu",
      "headline": "שווארמה טרייה כל היום",
      "slides": [{"image": "1.jpg", "text": "פיתה או לאפה"}],
      "hashtags": ["שווארמה"],
      "music": "upbeat.mp3"
    }
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageDraw

from . import textrender as tr

W, H = 1080, 1920           # TikTok full-screen portrait
FPS = 30
RENDER_SCALE = 1.5          # photos rendered larger so the zoom stays smooth
FADE = 0.4                  # crossfade seconds
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

INK = (245, 247, 250)
CYAN = (0, 229, 255)
GOLD = (240, 180, 41)
GROUND = (10, 11, 15)


def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "ffmpeg not found. Install it, or `pip install imageio-ffmpeg`."
        ) from exc


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-12:])
        raise RuntimeError(f"ffmpeg failed:\n{tail}")


@dataclass
class Brand:
    business: str = "N.L Studio"
    phone: str = ""
    whatsapp: str = ""
    logo: pathlib.Path | None = None
    font: pathlib.Path | None = None


# ---------------------------------------------------------------- composition


def compose_photo(src: pathlib.Path) -> Image.Image:
    """Photo cropped to portrait at render scale, ready for the zoom."""
    with Image.open(src) as im:
        im = im.convert("RGB")
        return tr.cover_crop(im, int(W * RENDER_SCALE), int(H * RENDER_SCALE))


def compose_overlay(text: str, brand: Brand, *, index: int, total: int) -> Image.Image:
    """Transparent 1080x1920 layer: scrim, caption, logo, progress ticks."""
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if text:
        layer.alpha_composite(tr.bottom_scrim((W, H)))

    margin = 84
    if text:
        font, lines = tr.fit_font(
            text, brand.font, max_width=W - margin * 2,
            max_height=int(H * 0.34), start=92,
        )
        tr.glow_text(layer, lines, font, right=W - margin,
                     anchor_bottom=H - 300, fill=INK)

    draw = ImageDraw.Draw(layer)

    # Logo, or the business name set as a wordmark.
    if brand.logo and brand.logo.exists():
        with Image.open(brand.logo) as logo:
            logo = logo.convert("RGBA")
            target_w = 190
            ratio = target_w / logo.width
            logo = logo.resize((target_w, max(1, int(logo.height * ratio))),
                               Image.LANCZOS)
            layer.alpha_composite(logo, (W - margin - logo.width, 90))
    elif brand.business:
        wordmark = tr.ImageFont.truetype(str(brand.font), 44)
        shaped = tr.shape(brand.business)
        w = wordmark.getbbox(shaped)[2]
        draw.text((W - margin - w, 96), shaped, font=wordmark, fill=INK)
        draw.ellipse(
            (W - margin - w - 34, 112, W - margin - w - 16, 130), fill=CYAN
        )

    # Slide progress ticks, only when there is more than one slide.
    if total > 1:
        tick_w = (W - margin * 2 - (total - 1) * 10) / total
        x = margin
        for i in range(total):
            colour = INK if i <= index else (255, 255, 255, 70)
            draw.rounded_rectangle((x, 58, x + tick_w, 64), radius=3,
                                   fill=colour if i <= index else (255, 255, 255, 70))
            x += tick_w + 10
    return layer


def compose_outro(brand: Brand) -> Image.Image:
    """Closing card: business, phone, WhatsApp prompt."""
    card = Image.new("RGB", (W, H), GROUND)
    draw = ImageDraw.Draw(card)

    # Soft cyan bloom behind the centre. Heavy blur, otherwise the ellipse
    # reads as a hard-edged shape rather than light.
    from PIL import ImageFilter

    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((W // 2 - 420, H // 2 - 480, W // 2 + 420,
                                  H // 2 + 160), fill=(0, 229, 255, 40))
    glow = glow.filter(ImageFilter.GaussianBlur(180))
    card = Image.alpha_composite(card.convert("RGBA"), glow).convert("RGB")
    draw = ImageDraw.Draw(card)

    name_font = tr.ImageFont.truetype(str(brand.font), 104)
    lines = tr.wrap(brand.business, name_font, W - 160)
    y = H // 2 - 300
    for line in lines:
        w = name_font.getbbox(line)[2]
        draw.text(((W - w) / 2, y), line, font=name_font, fill=INK)
        y += int(104 * 1.25)

    if brand.phone:
        phone_font = tr.ImageFont.truetype(str(brand.font), 84)
        # A phone number is LTR even inside Hebrew copy — do not reorder it.
        w = phone_font.getbbox(brand.phone)[2]
        draw.text(((W - w) / 2, y + 40), brand.phone, font=phone_font, fill=GOLD)
        y += 150

    cta_font = tr.ImageFont.truetype(str(brand.font), 58)
    cta = tr.shape("הזמינו בוואטסאפ")
    w = cta_font.getbbox(cta)[2]
    pad_x, pad_y = 52, 28
    bx0 = (W - w) / 2 - pad_x
    by0 = y + 90
    draw.rounded_rectangle((bx0, by0, bx0 + w + pad_x * 2, by0 + 58 + pad_y * 2),
                           radius=999, fill=(37, 211, 102))
    draw.text(((W - w) / 2, by0 + pad_y), cta, font=cta_font, fill=(6, 43, 20))
    return card


# ------------------------------------------------------------------- assembly


def _clip_from_slide(ff: str, photo: pathlib.Path, overlay: pathlib.Path,
                     out: pathlib.Path, seconds: float, zoom_in: bool) -> None:
    frames = max(2, int(round(seconds * FPS)))
    # zoompan walks the zoom per output frame; d= is the frame count.
    if zoom_in:
        z = "min(zoom+0.00045,1.10)"
    else:
        z = "if(lte(zoom,1.0),1.10,max(1.001,zoom-0.00045))"
    vf = (
        f"[0:v]zoompan=z='{z}':d={frames}"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":s={W}x{H}:fps={FPS}[bg];"
        f"[bg][1:v]overlay=0:0:format=auto,format=yuv420p[v]"
    )
    run([ff, "-y", "-loglevel", "error",
         "-i", str(photo), "-i", str(overlay),
         "-filter_complex", vf, "-map", "[v]",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-r", str(FPS), str(out)])


def _still_clip(ff: str, image: pathlib.Path, out: pathlib.Path,
                seconds: float) -> None:
    run([ff, "-y", "-loglevel", "error", "-loop", "1", "-t", f"{seconds}",
         "-i", str(image), "-vf", f"format=yuv420p,fps={FPS},scale={W}:{H}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(out)])


def _xfade_chain(ff: str, clips: list[pathlib.Path], durations: list[float],
                 out: pathlib.Path) -> float:
    """Crossfade clips together. Returns the final duration."""
    if len(clips) == 1:
        shutil.copy(clips[0], out)
        return durations[0]

    cmd = [ff, "-y", "-loglevel", "error"]
    for clip in clips:
        cmd += ["-i", str(clip)]

    steps: list[str] = []
    label = "0:v"
    cumulative = 0.0
    for i in range(1, len(clips)):
        cumulative += durations[i - 1]
        offset = cumulative - i * FADE
        nxt = f"x{i}"
        steps.append(
            f"[{label}][{i}:v]xfade=transition=fade:duration={FADE}"
            f":offset={max(0.0, offset):.3f}[{nxt}]"
        )
        label = nxt

    # xfade promotes to 4:4:4; force 4:2:0 or phones refuse the file.
    cmd += ["-filter_complex", ";".join(steps), "-map", f"[{label}]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.0",
            str(out)]
    run(cmd)
    return sum(durations) - (len(clips) - 1) * FADE


def _add_music(ff: str, video: pathlib.Path, music: pathlib.Path,
               out: pathlib.Path, duration: float, volume: float = 0.35) -> None:
    fade_at = max(0.0, duration - 1.5)
    run([ff, "-y", "-loglevel", "error",
         "-i", str(video), "-stream_loop", "-1", "-i", str(music),
         "-filter_complex",
         f"[1:a]volume={volume},afade=t=out:st={fade_at:.2f}:d=1.5[a]",
         "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac",
         "-b:a", "128k", "-t", f"{duration:.2f}", "-shortest", str(out)])


# ---------------------------------------------------------------------- entry


def load_project(folder: pathlib.Path) -> dict[str, Any]:
    meta_path = folder / "project.json"
    meta: dict[str, Any] = {}
    if meta_path.exists():
        try:
            loaded = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                meta = loaded
        except json.JSONDecodeError as exc:
            raise ValueError(f"{meta_path.name} is not valid JSON: {exc}") from exc

    images = sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
    )
    if not images:
        raise ValueError(f"no images in {folder}")

    slides = meta.get("slides")
    if not isinstance(slides, list) or not slides:
        # No explicit slides: use every photo, headline on the first only.
        slides = [{"image": p.name} for p in images]
        if meta.get("headline"):
            slides[0]["text"] = meta["headline"]
    meta["slides"] = slides
    meta["_images"] = images
    return meta


def build(folder: pathlib.Path, dest: pathlib.Path, brand: Brand, *,
          seconds_per_slide: float = 2.8, max_duration: float = 45.0,
          music_dir: pathlib.Path | None = None,
          outro_seconds: float = 2.4,
          log=lambda m: None) -> pathlib.Path:
    """Render one project folder into an MP4 at `dest`."""
    ff = ffmpeg_exe()
    meta = load_project(folder)
    slides = meta["slides"]

    # Keep the whole thing inside max_duration, outro included.
    budget = max_duration - outro_seconds
    per_slide = min(seconds_per_slide, budget / max(1, len(slides)))
    if per_slide < 1.2:
        keep = max(1, int(budget // 1.2))
        slides = slides[:keep]
        per_slide = budget / len(slides)
        log(f"קוצץ ל-{len(slides)} שקופיות כדי לעמוד במגבלת האורך")

    work = pathlib.Path(tempfile.mkdtemp(prefix="tiktok-build-"))
    try:
        clips: list[pathlib.Path] = []
        durations: list[float] = []

        for i, slide in enumerate(slides):
            name = slide.get("image")
            src = folder / name if name else meta["_images"][i]
            if not src.exists():
                raise ValueError(f"missing image: {src.name}")

            photo_path = work / f"photo{i}.png"
            overlay_path = work / f"over{i}.png"
            compose_photo(src).save(photo_path)
            compose_overlay(str(slide.get("text", "")), brand,
                            index=i, total=len(slides)).save(overlay_path)

            clip = work / f"clip{i}.mp4"
            _clip_from_slide(ff, photo_path, overlay_path, clip, per_slide,
                             zoom_in=(i % 2 == 0))
            clips.append(clip)
            durations.append(per_slide)
            log(f"שקופית {i + 1}/{len(slides)} נבנתה")

        outro_png = work / "outro.png"
        compose_outro(brand).save(outro_png)
        outro_clip = work / "outro.mp4"
        _still_clip(ff, outro_png, outro_clip, outro_seconds)
        clips.append(outro_clip)
        durations.append(outro_seconds)

        silent = work / "silent.mp4"
        total = _xfade_chain(ff, clips, durations, silent)
        log(f"אורך סופי: {total:.1f} שניות")

        track = _pick_music(music_dir, seed=folder.name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if track:
            _add_music(ff, silent, track, dest, total)
            log(f"מוזיקה: {track.name}")
        else:
            shutil.copy(silent, dest)
            log("ללא מוזיקה (התיקייה ריקה)")
        return dest
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _pick_music(music_dir: pathlib.Path | None, seed: str) -> pathlib.Path | None:
    if not music_dir or not music_dir.exists():
        return None
    tracks = sorted(p for p in music_dir.iterdir()
                    if p.suffix.lower() in {".mp3", ".m4a", ".wav", ".aac"})
    if not tracks:
        return None
    import random

    return random.Random(seed).choice(tracks)
