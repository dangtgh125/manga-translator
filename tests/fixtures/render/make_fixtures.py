"""Regenerate the render regression fixtures (run on a Mac: the Japanese page needs Hiragino).

    python tests/fixtures/render/make_fixtures.py

ja_bubble.png   vertical Japanese dialogue in a speech bubble over screentone (manga path)
art_letters.png white lettering with a black outline straight over coloured art (webtoon path)
art_solid.png   solid dark-red lettering, no outline, over the same art
art_layered.png orange fill + white outline + dark-red shadow over art with white stripes
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
W, H = 520, 380
JA_FONT = "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc"
LATIN_FONT = HERE.parents[2] / "backend" / "fonts" / "patrickhand" / "PatrickHand-Regular.ttf"


def tone(d: ImageDraw.ImageDraw) -> None:
    for y in range(0, H, 6):
        for x in range(0, W, 6):
            d.ellipse((x, y, x + 2, y + 2), fill=(120, 120, 120))


def ja_bubble() -> tuple[Image.Image, dict]:
    im = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(im)
    tone(d)
    d.polygon([(300, 290), (330, 350), (340, 285)], fill=(255, 255, 255), outline=(0, 0, 0), width=3)
    d.ellipse((120, 60, 420, 300), fill=(255, 255, 255), outline=(0, 0, 0), width=4)
    font = ImageFont.truetype(JA_FONT, 30)
    x = 330
    for col in ["ここは", "どこだ？", "早く逃げろ！"]:
        y = 100
        for ch in col:
            d.text((x, y), ch, font=font, fill=(0, 0, 0))
            y += 34
        x -= 44
    return im, {
        "polygon": [[235, 95], [365, 95], [365, 310], [235, 310]],
        "direction": "vertical",
        "bubble": [120, 60, 421, 301],
        "glyph_px": [20, 36],
    }


def art() -> Image.Image:
    im = Image.new("RGB", (W, H))
    px = im.load()
    for y in range(H):
        for x in range(W):
            px[x, y] = (40 + x * 160 // W, 90 + y * 120 // H, 200 - x * 100 // W)
    d = ImageDraw.Draw(im)
    d.ellipse((60, 40, 300, 260), fill=(230, 120, 60))
    d.ellipse((330, 150, 500, 360), fill=(70, 160, 90))
    for i in range(-H, W, 36):
        d.line((i, 0, i + H, H), fill=(250, 230, 120), width=6)
    d.rectangle((0, 300, W, H), fill=(60, 40, 110))
    return im


def art_letters() -> tuple[Image.Image, dict]:
    im = art()
    d = ImageDraw.Draw(im)
    font = ImageFont.truetype(str(LATIN_FONT), 64)
    d.text((110, 120), "BOOM!!", font=font, fill=(255, 255, 255), stroke_width=4, stroke_fill=(0, 0, 0))
    box = d.textbbox((110, 120), "BOOM!!", font=font, stroke_width=4)
    poly = [[box[0] - 3, box[1] - 3], [box[2] + 3, box[1] - 3], [box[2] + 3, box[3] + 3], [box[0] - 3, box[3] + 3]]
    return im, {"polygon": poly, "direction": "horizontal", "ink": [255, 255, 255]}


def art_solid() -> tuple[Image.Image, dict]:
    im = art()
    d = ImageDraw.Draw(im)
    font = ImageFont.truetype(str(LATIN_FONT), 58)
    d.text((150, 60), "KRAK", font=font, fill=(130, 10, 20))
    box = d.textbbox((150, 60), "KRAK", font=font)
    poly = [[box[0] - 3, box[1] - 3], [box[2] + 3, box[1] - 3], [box[2] + 3, box[3] + 3], [box[0] - 3, box[3] + 3]]
    return im, {"polygon": poly, "direction": "horizontal", "ink": [130, 10, 20]}


def art_layered() -> tuple[Image.Image, dict]:
    """Orange fill + white outline + dark-red drop shadow over art that itself has white stripes."""
    im = art()
    d = ImageDraw.Draw(im)
    for i in range(-H, W, 48):
        d.line((i, H, i + H, 0), fill=(255, 255, 255), width=4)
    font = ImageFont.truetype(str(LATIN_FONT), 72)
    d.text((104, 134), "SWOOSH", font=font, fill=(130, 10, 20), stroke_width=5, stroke_fill=(130, 10, 20))
    d.text((100, 130), "SWOOSH", font=font, fill=(240, 90, 200), stroke_width=5, stroke_fill=(255, 255, 255))
    box = d.textbbox((100, 130), "SWOOSH", font=font, stroke_width=5)
    poly = [[box[0] - 3, box[1] - 3], [box[2] + 7, box[1] - 3], [box[2] + 7, box[3] + 7], [box[0] - 3, box[3] + 7]]
    # Ground truth: every pixel any lettering layer touches.
    gt = Image.new("L", (W, H), 0)
    dg = ImageDraw.Draw(gt)
    dg.text((104, 134), "SWOOSH", font=font, fill=255, stroke_width=5, stroke_fill=255)
    dg.text((100, 130), "SWOOSH", font=font, fill=255, stroke_width=5, stroke_fill=255)
    gt.save(HERE / "art_layered_mask.png", optimize=True)
    return im, {
        "polygon": poly,
        "direction": "horizontal",
        "ink": [240, 90, 200],
        "layers": [[240, 90, 200], [255, 255, 255], [130, 10, 20]],
    }


def main() -> int:
    meta = {}
    for name, fn in (
        ("ja_bubble", ja_bubble),
        ("art_letters", art_letters),
        ("art_solid", art_solid),
        ("art_layered", art_layered),
    ):
        im, info = fn()
        im.save(HERE / f"{name}.png", optimize=True)
        meta[name] = info
    (HERE / "fixtures.json").write_text(json.dumps(meta, indent=2) + "\n")
    print("wrote", ", ".join(meta))
    return 0


if __name__ == "__main__":
    sys.exit(main())
