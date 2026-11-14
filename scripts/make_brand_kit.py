"""Generate the Vex profile picture and animated description GIF in assets/brand/."""
import math
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import address_card as ac  # noqa: E402

OUT = os.path.join(ROOT, "assets", "brand")
GW, GH = 640, 360
ACCENT = ac.BANNER_ACCENTS["welcome"]
FEATURES = [("deposit", "Deposit"), ("withdraw", "Withdraw"), ("convert", "Swap"), ("balance", "Balance")]


def ease(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def backdrop():
    img = Image.new("RGB", (GW, GH), ac.BG)
    d = ImageDraw.Draw(img)
    for x in range(0, GW, 24):
        d.line([(x, 0), (x, GH)], fill=ac.GRID)
    for y in range(0, GH, 24):
        d.line([(0, y), (GW, y)], fill=ac.GRID)
    mask = Image.new("L", (GW, GH), 0)
    ImageDraw.Draw(mask).ellipse([-40, -60, 380, 360], fill=80)
    return Image.composite(Image.new("RGB", (GW, GH), ACCENT), img, mask.filter(ImageFilter.GaussianBlur(70)))


def frame(i, n, base, tile, logos):
    t = i / n
    img = base.copy()
    d = ImageDraw.Draw(img)
    # Emblem pops in, then floats gently
    s = ease(t / 0.18)
    bob = math.sin(2 * math.pi * t) * 4
    size = max(1, int(tile.width * (0.6 + 0.4 * s)))
    if s > 0:
        tl = tile.resize((size, size), Image.LANCZOS)
        a = tl.getchannel("A").point(lambda v: int(v * s))
        tl.putalpha(a)
        img.paste(tl, (150 - size // 2, int(150 - size // 2 + bob)), tl)
    # Title slides in
    p = ease((t - 0.12) / 0.2)
    if p > 0:
        col = tuple(int(ac.BG[k] + (ac.TEXT[k] - ac.BG[k]) * p) for k in range(3))
        d.text((262 + (1 - p) * 40, 82), "VEX", font=ac._font("SpaceGrotesk.ttf", 96, b"Bold"), fill=col)
        d.rounded_rectangle([266, 196, 266 + int(84 * p), 202], radius=3, fill=ACCENT)
    q = ease((t - 0.24) / 0.2)
    if q > 0:
        col = tuple(int(ac.BG[k] + (ac.SOFT[k] - ac.BG[k]) * q) for k in range(3))
        d.text((266, 216), "Secure Multi-Chain Wallet", font=ac._font("Inter.ttf", 24, b"Medium"), fill=col)
    # Feature chips rise in one by one, then bob in a wave
    gap = 12
    x = (GW - sum(c.width for c in logos) - gap * (len(logos) - 1)) // 2
    for k, chip in enumerate(logos):
        r = ease((t - 0.34 - k * 0.07) / 0.15)
        if r > 0:
            y = int(296 + (1 - r) * 24 + math.sin(2 * math.pi * (t * 2 - k / 4)) * 2 * r)
            ch = chip.copy()
            ch.putalpha(ch.getchannel("A").point(lambda v: int(v * r)))
            img.paste(ch, (x, y - chip.height // 2), ch)
        x += chip.width + gap
    d.text((GW - 22, GH - 18), "@UseVexBot", font=ac._font("Inter.ttf", 16, b"Bold"), fill=ac.TEXT, anchor="rm")
    return img


def feature_chip(name, label):
    font = ac._font("Inter.ttf", 17, b"SemiBold")
    w = int(ImageDraw.Draw(Image.new("L", (1, 1))).textlength(label, font=font)) + 64
    chip = Image.new("RGBA", (w, 44), (0, 0, 0, 0))
    d = ImageDraw.Draw(chip)
    d.rounded_rectangle([0, 0, w - 1, 43], radius=22, fill=ac.PANEL + (255,), outline=ac.PANEL_EDGE + (255,), width=2)
    accent = ac.BANNER_ACCENTS.get(name, ACCENT)
    icon = ac._glyph_tile(name, 30, accent)
    chip.paste(icon, (7, 7), icon if icon.mode == "RGBA" else None)
    d.text((44, 22), label, font=font, fill=ac.TEXT + (255,), anchor="lm")
    return chip


def make_gif(path, n=48, ms=70):
    base = backdrop()
    tile = ac._glyph_tile("welcome", 150, ACCENT)
    logos = [feature_chip(name, label) for name, label in FEATURES]
    frames = [frame(i, n, base, tile, logos) for i in range(n)]
    hold = [frames[-1]] * 0
    pal = frames[-1].quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    q = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in frames + hold]
    durations = [ms] * (n - 1) + [1600]
    q[0].save(path, save_all=True, append_images=q[1:], duration=durations, loop=0, optimize=True)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "profile.png"), "wb") as f:
        f.write(ac.render_profile_picture(640))
    make_gif(os.path.join(OUT, "description.gif"))
    for name in os.listdir(OUT):
        print(name, os.path.getsize(os.path.join(OUT, name)))
