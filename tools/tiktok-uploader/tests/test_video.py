"""Tests for the slideshow builder and caption templates.

Renders a real video with ffmpeg and inspects the result, because the failure
modes here are visual (reversed Hebrew, wrong aspect, unplayable pixel format)
and none of them raise an exception on their own.

Run:  .venv/bin/python -m tests.test_video
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from tiktok_uploader import textrender as tr  # noqa: E402
from tiktok_uploader import videobuild as vb  # noqa: E402
from tiktok_uploader.captions import build_caption  # noqa: E402
from tiktok_uploader.config import Config  # noqa: E402
from tiktok_uploader.projects import build_project, list_projects  # noqa: E402

FAILURES: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label} {detail if not cond else ''}".rstrip())
    if not cond:
        FAILURES.append(label)


def probe(path: pathlib.Path) -> dict:
    ff = vb.ffmpeg_exe()
    out = subprocess.run([ff, "-hide_banner", "-i", str(path)],
                         capture_output=True, text=True).stderr
    return {"raw": out}


def make_photos(folder: pathlib.Path) -> None:
    for i, size in enumerate([(1600, 1200), (1080, 1440), (1920, 1080)], 1):
        Image.new("RGB", size, (150 + i * 20, 80, 40)).save(folder / f"{i}.jpg")


def main() -> int:
    print("\n[1] bidi correctness")
    s = "שווארמה טרייה"
    once = tr.shape(s)
    check("shape() reorders", once != s)
    check("shape() is an involution", tr.shape(once) == s)
    # The first logical char must end up rightmost => last in the display string.
    check("first letter lands rightmost", once[-1] == s[0],
          f"{once[-1]!r} vs {s[0]!r}")

    print("\n[2] wrapping keeps words intact")
    font_path = tr.find_font(ROOT)
    from PIL import ImageFont
    f = ImageFont.truetype(str(font_path), 60)
    lines = tr.wrap("שווארמה טרייה כל היום בבאר שבע", f, 400)
    check("wrapped to multiple lines", len(lines) > 1, f"got {len(lines)}")
    joined = " ".join(tr.shape(l) for l in lines)  # back to logical
    check("no characters lost",
          sorted(joined.replace(" ", "")) ==
          sorted("שווארמה טרייה כל היום בבאר שבע".replace(" ", "")))

    print("\n[3] cover_crop fills exactly")
    for size in [(1600, 1200), (600, 2000), (1080, 1080)]:
        out = tr.cover_crop(Image.new("RGB", size), 1080, 1920)
        if out.size != (1080, 1920):
            check(f"cover_crop {size}", False, f"got {out.size}")
            break
    else:
        check("cover_crop always 1080x1920", True)

    print("\n[4] caption templates")
    c1 = build_caption(kind="menu", headline="בדיקה", business="עסק",
                       phone="050", seed="a")
    c2 = build_caption(kind="menu", headline="בדיקה", business="עסק",
                       phone="050", seed="b")
    check("caption has hashtags", "#" in c1)
    check("different seeds vary the hook", c1 != c2)
    check("caption within TikTok limit", len(c1) <= 2200)
    check("deterministic for same seed",
          c1 == build_caption(kind="menu", headline="בדיקה", business="עסק",
                              phone="050", seed="a"))

    print("\n[5] full render")
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="vidtest-"))
    proj = tmp / "projects" / "demo"
    proj.mkdir(parents=True)
    make_photos(proj)
    (proj / "project.json").write_text(json.dumps({
        "kind": "menu",
        "headline": "שווארמה טרייה כל היום",
        "slides": [{"image": "1.jpg", "text": "שווארמה טרייה"},
                   {"image": "2.jpg", "text": "פיתה או לאפה"}],
    }, ensure_ascii=False), encoding="utf-8")

    cfg = Config(videos_dir=tmp / "videos", state_dir=tmp / "state",
                 projects_dir=tmp / "projects", music_dir=tmp / "music",
                 business_name="שווארמה בשכונה", phone="050-686-3955",
                 seconds_per_slide=1.5, max_video_seconds=20, outro_seconds=1.5)
    for d in (cfg.videos_dir, cfg.state_dir, cfg.music_dir):
        d.mkdir(parents=True, exist_ok=True)

    listed = list_projects(cfg)
    check("project discovered", len(listed) == 1 and listed[0]["images"] == 3)
    check("reported as unbuilt", listed[0]["built"] is False)

    dest = build_project(proj, cfg, log=lambda m: None)
    check("video file created", dest.exists() and dest.stat().st_size > 10_000)

    info = probe(dest)["raw"]
    check("portrait 1080x1920", "1080x1920" in info, info[:200])
    check("yuv420p (phone-safe)", "yuv420p" in info)
    check("not 4:4:4", "yuv444" not in info)
    check("30 fps", "30 fps" in info)
    check("no audio when music dir empty", "Audio:" not in info)

    sidecar = dest.with_suffix(".json")
    check("sidecar written", sidecar.exists())
    caption = json.loads(sidecar.read_text(encoding="utf-8"))["title"]
    check("caption mentions the business", "שווארמה בשכונה" in caption)
    check("caption carries hashtags", "#" in caption)

    check("second build refuses to overwrite",
          _raises(lambda: build_project(proj, cfg, log=lambda m: None)))
    check("now reported as built", list_projects(cfg)[0]["built"] is True)

    print("\n[6] music path")
    ff = vb.ffmpeg_exe()
    subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "sine=frequency=200:duration=5",
                    str(cfg.music_dir / "t.mp3")], check=True)
    dest2 = build_project(proj, cfg, log=lambda m: None, overwrite=True)
    check("audio track muxed in", "Audio:" in probe(dest2)["raw"])

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: " + ", ".join(FAILURES))
        return 1
    print("all checks passed")
    return 0


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except Exception:
        return True


if __name__ == "__main__":
    raise SystemExit(main())
