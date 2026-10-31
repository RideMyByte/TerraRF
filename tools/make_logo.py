"""Generate the TerraRF logo files (font-independent SVG: text is converted to paths).

    python tools/make_logo.py [path/to/Inter-ExtraBold.otf] [path/to/Inter-Medium.otf]

Writes assets/icon.svg, assets/logo.svg, assets/logo-dark.svg (+ PNGs via rsvg-convert if present).
Font: Inter (SIL Open Font License); only glyph outlines end up in the files.
"""
import os
import shutil
import subprocess
import sys

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "..", "assets")
BOLD = sys.argv[1] if len(sys.argv) > 1 else "/usr/share/fonts/opentype/inter/Inter-ExtraBold.otf"
MEDIUM = sys.argv[2] if len(sys.argv) > 2 else "/usr/share/fonts/opentype/inter/Inter-Medium.otf"

GREEN, LIGHTGREEN, ORANGE, YELLOW = "#1a9641", "#a6d96a", "#fdae61", "#ffd200"
NAVY, NAVY2 = "#0d2236", "#16466c"


def text_path(font_file, text, size, x, y, tracking=0.0):
    """Return (svg path data, advance width) for `text` with baseline at (x, y)."""
    f = TTFont(font_file)
    gs = f.getGlyphSet()
    cmap = f.getBestCmap()
    upm = f["head"].unitsPerEm
    s = size / upm
    pen = SVGPathPen(gs)
    cx = 0.0
    for ch in text:
        g = cmap[ord(ch)]
        tp = TransformPen(pen, (s, 0, 0, -s, x + cx, y))
        gs[g].draw(tp)
        cx += gs[g].width * s + tracking * size
    return pen.getCommands(), cx - tracking * size


def icon_group(dx=0, dy=0, scale=1.0):
    """The mark, drawn in a 512x512 box."""
    return f'''<g transform="translate({dx} {dy}) scale({scale})">
  <defs>
    <linearGradient id="tbg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{NAVY}"/><stop offset="1" stop-color="{NAVY2}"/>
    </linearGradient>
    <clipPath id="tclip"><rect width="512" height="512" rx="104"/></clipPath>
  </defs>
  <g clip-path="url(#tclip)">
    <rect width="512" height="512" fill="url(#tbg)"/>
    <g fill="none" stroke-linecap="round">
      <path d="M 104 262 A 92 92 0 0 1 276 262" stroke="{GREEN}" stroke-width="22"/>
      <path d="M 58 250 A 142 142 0 0 1 322 250" stroke="{LIGHTGREEN}" stroke-width="20" opacity="0.95"/>
      <path d="M 14 236 A 192 192 0 0 1 366 236" stroke="{ORANGE}" stroke-width="18" opacity="0.9"/>
    </g>
    <path d="M0 372 L70 330 L128 348 L190 300 L262 352 L318 336 L362 356 L404 338 L456 366 L512 350 L512 512 L0 512 Z" fill="#3a8a5c"/>
    <path d="M0 420 L84 392 L160 410 L238 384 L320 412 L404 390 L512 414 L512 512 L0 512 Z" fill="#256b47"/>
    <path d="M0 462 L120 444 L250 458 L380 440 L512 456 L512 512 L0 512 Z" fill="#1d5539"/>
    <line x1="190" y1="300" x2="404" y2="338" stroke="#ffffff" stroke-width="7" stroke-dasharray="16 12" stroke-linecap="round" opacity="0.9"/>
    <circle cx="404" cy="338" r="15" fill="#ffffff"/>
    <circle cx="190" cy="300" r="25" fill="{YELLOW}" stroke="#ffffff" stroke-width="7"/>
  </g>
</g>'''


def svg(w, h, body, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'role="img" aria-label="{title}"><title>{title}</title>{body}</svg>\n')


def main():
    os.makedirs(ASSETS, exist_ok=True)
    with open(os.path.join(ASSETS, "icon.svg"), "w") as f:
        f.write(svg(512, 512, icon_group(), "TerraRF"))

    for name, fg, accent, sub in (("logo.svg", NAVY, GREEN, "#4b5b6b"), ("logo-dark.svg", "#ffffff", "#7bd88f", "#b8c7d6")):
        h = 160
        icon = icon_group(0, 8, 144 / 512)
        p1, w1 = text_path(BOLD, "Terra", 92, 172, 104, tracking=-0.02)
        p2, w2 = text_path(BOLD, "RF", 92, 172 + w1 + 1, 104, tracking=-0.01)
        p3, w3 = text_path(MEDIUM, "Funkplanung über echtem Gelände", 25, 176, 145, tracking=0.01)
        w = int(max(172 + w1 + 1 + w2, 176 + w3) + 12)
        body = (icon + f'<path d="{p1}" fill="{fg}"/><path d="{p2}" fill="{accent}"/>'
                f'<path d="{p3}" fill="{sub}"/>')
        with open(os.path.join(ASSETS, name), "w") as f:
            f.write(svg(w, h, body, "TerraRF – Funkplanung über echtem Gelände"))

    if shutil.which("rsvg-convert"):
        for size in (256, 512):
            subprocess.run(["rsvg-convert", "-w", str(size), "-h", str(size), os.path.join(ASSETS, "icon.svg"),
                            "-o", os.path.join(ASSETS, f"icon-{size}.png" if size != 256 else "icon.png")], check=True)
        subprocess.run(["rsvg-convert", "-h", "320", os.path.join(ASSETS, "logo.svg"), "-o",
                        os.path.join(ASSETS, "logo.png")], check=True)
        subprocess.run(["rsvg-convert", "-w", "64", "-h", "64", os.path.join(ASSETS, "icon.svg"), "-o",
                        os.path.join(ASSETS, "favicon-64.png")], check=True)
    print("ok")


if __name__ == "__main__":
    main()
