"""Font discovery per target language.

Bundled in the image: Patrick Hand (hand-lettered look, full Vietnamese diacritics; `backend/fonts/`),
DejaVu Sans and Noto Sans CJK (Debian packages). `MT_RENDER_FONT_LATIN` / `MT_RENDER_FONT_CJK` point
at another file (absolute, or relative to `MT_FONT_DIR`).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

from ..config import get_settings

CJK_LANGS = {"ja", "zh", "zh-Hans", "zh-Hant", "ko"}

_LATIN_CANDIDATES = [
    "truetype/patrickhand/PatrickHand-Regular.ttf",
    "patrickhand/PatrickHand-Regular.ttf",
    "truetype/dejavu/DejaVuSans.ttf",
    "truetype/noto/NotoSans-Regular.ttf",
    "truetype/liberation/LiberationSans-Regular.ttf",
]
_CJK_CANDIDATES = [
    "opentype/noto/NotoSansCJK-Regular.ttc",
    "truetype/noto/NotoSansCJK-Regular.ttc",
    "truetype/dejavu/DejaVuSans.ttf",
]


def is_cjk(lang: str | None) -> bool:
    return (lang or "").split("-")[0] in CJK_LANGS or (lang or "") in CJK_LANGS


def _resolve(root: Path, override: str, candidates: list[str]) -> Path | None:
    if override:
        p = Path(override)
        if not p.is_absolute():
            p = root / p
        if p.exists():
            return p
    for rel in candidates:
        p = root / rel
        if p.exists():
            return p
    for p in root.rglob("*.ttf"):
        return p
    return None


@lru_cache(maxsize=8)
def font_path_for(lang: str | None) -> Path | None:
    s = get_settings()
    if is_cjk(lang):
        return _resolve(s.font_dir, s.render_font_cjk, _CJK_CANDIDATES)
    return _resolve(s.font_dir, s.render_font_latin, _LATIN_CANDIDATES)


def default_font_path() -> Path | None:
    return font_path_for(None)


@lru_cache(maxsize=512)
def load_font(
    size: int, path: str | None = None, lang: str | None = None
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    p = Path(path) if path else font_path_for(lang)
    if p is None:
        return ImageFont.load_default(size=size)
    try:
        return ImageFont.truetype(str(p), size=size)
    except OSError:
        return ImageFont.load_default(size=size)
