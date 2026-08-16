"""Template-based Hebrew captions and hashtags.

No API and no cost. Variety comes from rotating through template banks with a
per-project seed, so two videos in a row never read the same.
"""

from __future__ import annotations

import random
from typing import Any

# Hooks are the first line — the only part most viewers read.
HOOKS: dict[str, list[str]] = {
    "menu": [
        "מה בא לכם היום?",
        "התפריט שכולם מדברים עליו 👀",
        "אי אפשר לבחור רק אחד.",
        "זה מה שיוצא מהמטבח עכשיו 🔥",
    ],
    "promo": [
        "רק לשבוע הזה 👇",
        "שווה לרוץ.",
        "המבצע שחיכיתם לו.",
        "תגידו לחברים שיזדרזו.",
    ],
    "behind": [
        "ככה זה נראה מבפנים.",
        "5 בבוקר, והכול מתחיל.",
        "הסוד? עבודה מאפס כל יום.",
        "אף אחד לא רואה את זה — אבל אתם כן.",
    ],
    "place": [
        "מחכים לכם 🧡",
        "בואו לשבת אצלנו.",
        "המקום שלכם בשכונה.",
        "פתוח עכשיו.",
    ],
    "general": [
        "רגע אחד מאצלנו.",
        "זה מה שיש לנו להראות היום.",
        "בואו נראה לכם משהו.",
    ],
}

CLOSERS = [
    "מזמינים בוואטסאפ — הקישור בפרופיל 📲",
    "להזמנות: {phone}",
    "שלחו הודעה ונשמור לכם 🙌",
    "תייגו את מי שחייב לראות את זה 👇",
]

BASE_TAGS = ["באר_שבע", "עסקים_מקומיים", "fyp", "foryou"]

KIND_TAGS: dict[str, list[str]] = {
    "menu": ["אוכל", "תפריט", "food"],
    "promo": ["מבצע", "דיל", "הנחה"],
    "behind": ["מאחורי_הקלעים", "עסק_קטן"],
    "place": ["המקום_שלנו", "יוצאים_לאכול"],
    "general": [],
}


def _rng(seed: str) -> random.Random:
    return random.Random(seed)


def build_caption(*, kind: str, headline: str = "", business: str = "",
                  phone: str = "", extra_tags: list[str] | None = None,
                  seed: str = "", max_len: int = 2200) -> str:
    """Assemble hook + headline + closer + hashtags."""
    kind = kind if kind in HOOKS else "general"
    rng = _rng(seed or headline or kind)

    parts: list[str] = [rng.choice(HOOKS[kind])]
    if headline:
        parts.append(headline)
    if business:
        parts.append(business)
    parts.append(rng.choice(CLOSERS).format(phone=phone or ""))

    tags = [*KIND_TAGS.get(kind, []), *BASE_TAGS, *(extra_tags or [])]
    seen: list[str] = []
    for tag in tags:
        clean = tag.strip().lstrip("#").replace(" ", "_")
        if clean and clean not in seen:
            seen.append(clean)
    hashtags = " ".join("#" + t for t in seen)

    body = "\n".join(p for p in parts if p).strip()
    caption = f"{body}\n\n{hashtags}".strip()

    if len(caption) > max_len:  # trim hashtags before prose
        caption = caption[:max_len].rsplit(" ", 1)[0]
    return caption


def caption_for_project(meta: dict[str, Any], *, business: str, phone: str,
                        seed: str) -> str:
    """Use an explicit caption when the project supplies one, else generate."""
    if meta.get("caption"):
        return str(meta["caption"])
    return build_caption(
        kind=str(meta.get("kind", "general")),
        headline=str(meta.get("headline", "")),
        business=business,
        phone=phone,
        extra_tags=list(meta.get("hashtags", [])),
        seed=seed,
    )
