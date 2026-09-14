"""Generates every icon asset from the dashboard's own palette in config.py.

Run once at design time (`python generate_icons.py`); the output PNGs/ICO are
committed to the repo like any other static asset. Not part of the running
app and not a runtime dependency — Pillow is only needed to regenerate these
files if the design changes later.
"""

from PIL import Image, ImageDraw

from config import Config as config

BG = config.bg               # matches the page background
BAR_1 = config.series_yellow
BAR_2 = config.accent
BAR_3 = config.series_green


def draw_icon(size: int) -> Image.Image:
    """A simple three-bar chart glyph, legible down to 16px."""
    img = Image.new("RGB", (size, size), BG)
    draw = ImageDraw.Draw(img)

    margin = round(size * 0.16)
    gap = round(size * 0.08)
    bar_width = round((size - 2 * margin - 2 * gap) / 3)
    baseline = size - margin
    bar_area_height = size - 2 * margin

    heights = [0.55, 0.85, 0.70]  # fractions of the available bar area
    colors = [BAR_1, BAR_2, BAR_3]

    x = margin
    for h, color in zip(heights, colors):
        bar_h = round(bar_area_height * h)
        draw.rectangle(
            [x, baseline - bar_h, x + bar_width, baseline],
            fill=color,
        )
        x += bar_width + gap

    return img


def rounded(img: Image.Image, radius_ratio: float = 0.22) -> Image.Image:
    """Rounds the corners; used for the tab favicon, left square for iOS/Android
    since both platforms already apply their own mask on home-screen icons."""
    size = img.size[0]
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size, size], radius=round(size * radius_ratio), fill=255
    )
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.paste(img, mask=mask)
    return out


base = draw_icon(512)

# Browser tab favicon: multi-resolution .ico, rounded so it doesn't look like a
# plain square against the tab's own rounded shape.
favicon_sizes = [16, 32, 48]
rounded(base, 0.22).resize((512, 512)).save(
    "assets/favicon.ico",
    sizes=[(s, s) for s in favicon_sizes],
)

# Android home-screen / PWA manifest icons: square, no pre-applied corner
# rounding. Chrome and Android mask these into whatever shape the launcher
# uses (circle, squircle, etc); rounding them here would double up and look
# wrong under the OS's own mask.
draw_icon(192).save("assets/icon-192.png")
draw_icon(512).save("assets/icon-512.png")

# iOS home-screen icon: Safari does not mask this one itself, and iOS expects
# a fully opaque square (a transparent background shows as black).
draw_icon(180).save("assets/apple-touch-icon.png")

print("Generated: assets/favicon.ico, icon-192.png, icon-512.png, apple-touch-icon.png")
