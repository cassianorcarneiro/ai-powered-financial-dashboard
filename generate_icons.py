"""Generates every icon asset from the dashboard's own palette in config.py.

Run once at design time (`python generate_icons.py`); the output files are
committed to the repo like any other static asset. Not part of the running
app and not a runtime dependency: Pillow is only needed to regenerate these
files if the design changes later. Kept at the project root, outside assets/,
because Dash serves every file in assets/ publicly.

Outputs, all in assets/:
  icon.svg                          purpose "any", vector favicon
  icon-192.png, icon-512.png        purpose "any": rounded tile, transparent corners
  icon-maskable-192.png, -512.png   purpose "maskable": full bleed, bars in the safe zone
  apple-touch-icon.png              180x180, full bleed, opaque
  favicon.ico                       16 (hand-hinted), 32, 48

Why separate "any" and "maskable" files: Android applies its launcher mask
(circle, squircle...) only to icons declared purpose "maskable". Without one,
it uses the "any" icon and places it inside its own shape, which is what made
the old square icon look cropped. A single file declared "any maskable" is
discouraged (web.dev/maskable-icon): it gets the safe-zone padding everywhere,
including the browser tab and desktop, where no mask is applied.

iOS ignores the manifest icons and uses apple-touch-icon. It applies its own
rounded mask and fills transparency with black, so that file is opaque and
full bleed.
"""

import math
import sys
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw

from config import Config as config

_rgba = lambda hex_color: (*ImageColor.getrgb(hex_color), 255)

BG = _rgba(config.bg)
BARS = (_rgba(config.series_yellow), _rgba(config.accent), _rgba(config.series_green))

# ── Design grid (32 x 32) ────────────────────────────────────────────────────
# Bars 6 wide with 2-unit gaps: the group spans x 5..27, centred. Heights
# 12/18/15 keep the previous 0.67 : 1 : 0.83 ratios. The group spans y 7..25,
# centred on 16 (the previous icon sat about 5 % low).
BAR_X0 = (5, 13, 21)
BAR_W = 6
BAR_H = (12, 18, 15)
BOTTOM = 25
TILE_RX = 7                            # tile corner radius, about 22 %

# scale: px per design unit = scale * size / 32.
# maskable 0.8 puts the group at 0.55 x 0.45 of the canvas, with its corners
# 0.355 of the canvas from the centre: inside the 0.40-radius safe zone that
# the W3C manifest spec guarantees under any mask.
VARIANTS = {
    "any":      dict(scale=1.0, bg="tile"),
    "maskable": dict(scale=0.8, bg="bleed"),
    "apple":    dict(scale=1.0, bg="bleed"),
}


def render(size: int, variant: str, ss: int = 8) -> Image.Image:
    """Draw at `ss` times the size, then average down.

    Edges are snapped to the *target* pixel grid before scaling up, so they
    stay crisp at non-integer scales such as 48 px (1.5 px per unit). BOX is
    an exact area average for an integer factor; Lanczos would ring at the
    hard bar edges and leave a dark halo around them.
    """
    v = VARIANTS[variant]
    S = size * ss
    k = v["scale"] * S / 32
    o = S / 2 - 16 * k
    T = lambda u: math.floor((o + u * k) / ss + 0.5) * ss

    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if v["bg"] == "bleed":
        d.rectangle((0, 0, S, S), fill=BG)
    else:
        d.rounded_rectangle((0, 0, S - 1, S - 1), radius=TILE_RX * S / 32, fill=BG)

    for x0, h, color in zip(BAR_X0, BAR_H, BARS):
        d.rectangle((T(x0), T(BOTTOM - h), T(x0 + BAR_W) - 1, T(BOTTOM) - 1), fill=color)
    return img.resize((size, size), Image.BOX)


def render_16() -> Image.Image:
    """Hand-hinted 16 px.

    Halving the 32 grid gives 3 px bars with 1 px gaps. That is 11 px, an odd
    width that cannot centre on 16 px, so the group sits 0.5 px right instead
    of having every edge blurred across two pixels.
    """
    tile = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle((0, 0, 127, 127), radius=28, fill=BG)
    img = tile.resize((16, 16), Image.BOX)
    d = ImageDraw.Draw(img)
    for x0, top, color in zip((3, 7, 11), (7, 4, 5), BARS):
        d.rectangle((x0, top, x0 + 2, 12), fill=color)
    return img


def svg() -> str:
    hx = lambda c: "#%02X%02X%02X" % c[:3]
    bars = "".join(
        f'<rect x="{x0}" y="{BOTTOM - h}" width="{BAR_W}" height="{h}" fill="{hx(c)}"/>'
        for x0, h, c in zip(BAR_X0, BAR_H, BARS)
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" shape-rendering="crispEdges">'
        f'<rect width="32" height="32" rx="{TILE_RX}" fill="{hx(BG)}" shape-rendering="auto"/>'
        f"{bars}</svg>\n"
    )


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for s in (192, 512):
        render(s, "any").save(out / f"icon-{s}.png", optimize=True)
        render(s, "maskable").save(out / f"icon-maskable-{s}.png", optimize=True)
    render(180, "apple").convert("RGB").save(out / "apple-touch-icon.png", optimize=True)

    small = [render_16()] + [render(s, "any", ss=16) for s in (32, 48)]
    small[-1].save(out / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)],
                   append_images=small[:-1])
    (out / "icon.svg").write_text(svg(), encoding="utf-8")
    print(f"Generated icon set in {out}/")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("assets"))
