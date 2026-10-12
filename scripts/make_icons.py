"""Draws the Aurum mark: a gold 'A' whose crossbar is a rising line. Writes static/icons/*.png and logo.svg. Run: python scripts/make_icons.py"""
from PIL import Image, ImageDraw
import os
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "icons")
S = 4   # supersampling
LEG = [(128, 392), (256, 112), (384, 392)]
BAR = [(166, 326), (230, 272), (270, 306), (356, 210)]
GOLD_T, GOLD_B = (255, 226, 140), (198, 130, 28)


def line(d, pts, w, fill, s):
    p = [(x * s, y * s) for x, y in pts]
    d.line(p, fill=fill, width=int(w * s), joint="curve")
    for x, y in p:
        r = w * s / 2
        d.ellipse([x - r, y - r, x + r, y + r], fill=fill)


def render(size):
    s = S * size / 512
    bg = Image.new("RGB", (size * S, size * S))
    px = ImageDraw.Draw(bg)
    for y in range(size * S):                                   # deep night gradient with a faint warm glow from the top
        t = y / (size * S)
        px.line([(0, y), (size * S, y)], fill=(int(24 - 17 * t), int(26 - 18 * t), int(40 - 27 * t)))
    glow = Image.new("RGB", bg.size, (60, 46, 14))
    m = Image.new("L", bg.size, 0)
    ImageDraw.Draw(m).ellipse([size * S * .1, -size * S * .35, size * S * .9, size * S * .55], fill=70)
    from PIL import ImageFilter
    bg = Image.composite(glow, bg, m.filter(ImageFilter.GaussianBlur(size * S * .12)))
    mask = Image.new("L", bg.size, 0)                           # the A legs
    line(ImageDraw.Draw(mask), LEG, 58, 255, s)
    grad = Image.new("RGB", bg.size)
    g = ImageDraw.Draw(grad)
    for y in range(bg.size[1]):
        t = min(max((y / s - 112) / 300, 0), 1)
        g.line([(0, y), (bg.size[0], y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(GOLD_T, GOLD_B)))
    bg.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(bg)
    line(d, BAR, 50, (14, 15, 24), s)                           # cut-out so the rising line reads over the legs
    line(d, BAR, 24, (255, 247, 222), s)
    x, y = BAR[-1]
    d.polygon([((x + 24) * s, (y - 24) * s), ((x - 20) * s, (y - 28) * s), ((x + 20) * s, (y + 16) * s)], fill=(255, 247, 222))   # arrowhead
    return bg.resize((size, size), Image.LANCZOS)


for name, size in (("icon-512.png", 512), ("icon-192.png", 192), ("apple-touch-icon.png", 180), ("favicon-64.png", 64)):
    render(size).save(os.path.join(OUT, name))

open(os.path.join(OUT, "logo.svg"), "w", encoding="utf-8").write('''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
<defs><linearGradient id="g" x1="0" y1="112" x2="0" y2="400" gradientUnits="userSpaceOnUse"><stop stop-color="#ffe28c"/><stop offset="1" stop-color="#c6821c"/></linearGradient>
<linearGradient id="b" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#181a28"/><stop offset="1" stop-color="#07080d"/></linearGradient></defs>
<rect width="512" height="512" rx="116" fill="url(#b)"/>
<path d="M128 392 256 112 384 392" fill="none" stroke="url(#g)" stroke-width="58" stroke-linecap="round" stroke-linejoin="round"/>
<path d="M166 326 230 272 270 306 356 210" fill="none" stroke="#0e0f18" stroke-width="50" stroke-linecap="round" stroke-linejoin="round"/>
<path d="M166 326 230 272 270 306 356 210" fill="none" stroke="#fff7de" stroke-width="24" stroke-linecap="round" stroke-linejoin="round"/>
<path d="M380 186 336 182 376 226z" fill="#fff7de"/></svg>''')
