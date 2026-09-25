"""Open Graph share image: 'N of your M videos appear in AI training datasets'."""

from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 630
BG = (18, 18, 22)
FG = (245, 245, 245)
ACCENT = (255, 196, 0)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("Helvetica.ttc", "Arial.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def render_og_png(headline: str, subtitle: str, footer: str = "youtrained") -> bytes:
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    draw.rectangle([(0, 0), (16, H)], fill=ACCENT)
    y = 140
    for line in _wrap(draw, headline, _font(64), W - 160):
        draw.text((80, y), line, font=_font(64), fill=FG)
        y += 78
    draw.text((80, y + 24), subtitle[:90], font=_font(34), fill=(190, 190, 200))
    draw.text((80, H - 90), footer, font=_font(28), fill=ACCENT)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_width:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines
