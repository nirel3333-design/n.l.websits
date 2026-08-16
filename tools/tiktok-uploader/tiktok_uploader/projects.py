"""Turn project folders into finished, captioned videos ready to post.

    projects/<name>/  -->  videos/<name>.mp4  +  videos/<name>.json

The sidecar carries the generated caption, so the existing folder scanner picks
the video up and queues it exactly like a hand-made clip.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, Callable

from . import textrender as tr
from . import videobuild as vb
from .captions import caption_for_project
from .config import Config


def brand_from_config(cfg: Config) -> vb.Brand:
    font = tr.find_font(cfg.projects_dir.parent, cfg.font_path)
    logo = pathlib.Path(cfg.logo_path).expanduser() if cfg.logo_path else None
    return vb.Brand(
        business=cfg.business_name,
        phone=cfg.phone,
        logo=logo,
        font=font,
    )


def list_projects(cfg: Config) -> list[dict[str, Any]]:
    """Every project folder plus whether its video already exists."""
    out: list[dict[str, Any]] = []
    if not cfg.projects_dir.exists():
        return out
    for folder in sorted(p for p in cfg.projects_dir.iterdir() if p.is_dir()):
        images = [p for p in folder.iterdir()
                  if p.suffix.lower() in vb.IMAGE_SUFFIXES]
        target = cfg.videos_dir / f"{folder.name}.mp4"
        out.append({
            "name": folder.name,
            "images": len(images),
            "built": target.exists(),
            "video": target.name if target.exists() else "",
        })
    return out


def build_project(folder: pathlib.Path, cfg: Config, *,
                  log: Callable[[str], None] = lambda m: None,
                  overwrite: bool = False) -> pathlib.Path:
    """Build one project and write its caption sidecar."""
    dest = cfg.videos_dir / f"{folder.name}.mp4"
    if dest.exists() and not overwrite:
        raise FileExistsError(f"{dest.name} already built")

    meta = vb.load_project(folder)
    brand = brand_from_config(cfg)

    log(f"בונה את {folder.name} ({len(meta['slides'])} שקופיות)…")
    vb.build(
        folder, dest, brand,
        seconds_per_slide=cfg.seconds_per_slide,
        max_duration=cfg.max_video_seconds,
        music_dir=cfg.music_dir,
        outro_seconds=cfg.outro_seconds,
        log=log,
    )

    caption = caption_for_project(
        meta, business=cfg.business_name, phone=cfg.phone, seed=folder.name
    )
    sidecar = dest.with_suffix(".json")
    payload: dict[str, Any] = {"title": caption}
    for key in ("privacy_level", "publish_at", "disable_comment",
                "disable_duet", "disable_stitch", "cover_timestamp_ms"):
        if key in meta:
            payload[key] = meta[key]
    sidecar.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    log(f"נוצר {dest.name} + כיתוב")
    return dest


def build_all(cfg: Config, *, log: Callable[[str], None] = lambda m: None,
              overwrite: bool = False) -> dict[str, int]:
    built = skipped = failed = 0
    for entry in list_projects(cfg):
        folder = cfg.projects_dir / entry["name"]
        if entry["built"] and not overwrite:
            skipped += 1
            continue
        try:
            build_project(folder, cfg, log=log, overwrite=overwrite)
            built += 1
        except Exception as exc:
            failed += 1
            log(f"נכשל {entry['name']}: {exc}")
    return {"built": built, "skipped": skipped, "failed": failed}
