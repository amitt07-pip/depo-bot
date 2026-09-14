"""Generate the VM CRYPTO BOT dashboard banner (assets/welcome.png)."""
import math
import os
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1200, 600
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "welcome.png")

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"


def font(path, size):
    return ImageFont.truetype(path, size)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


# Background gradient (deep navy -> violet)
img = Image.new("RGB", (W, H))
px = img.load()
c1, c2, c3 = (8, 10, 28), (22, 16, 60), (40, 12, 70)
for y in range(H):
    for x in range(W):
        t = (x / W) * 0.6 + (y / H) * 0.4
        base = lerp(c1, c2, t) if t < 0.5 else lerp(c2, c3, (t - 0.5) * 2)
        px[x, y] = base

# Glow blobs
glow = Image.new("RGB", (W, H), (0, 0, 0))
gd = ImageDraw.Draw(glow)
gd.ellipse((-150, -200, 450, 400), fill=(60, 40, 180))
gd.ellipse((800, 250, 1400, 800), fill=(0, 140, 160))
gd.ellipse((500, -250, 900, 150), fill=(120, 30, 150))
glow = glow.filter(ImageFilter.GaussianBlur(140))
img = Image.blend(img, Image.composite(glow, img, Image.new("L", (W, H), 110)), 0.75)

# Subtle grid
d = ImageDraw.Draw(img, "RGBA")
for x in range(0, W, 40):
    d.line((x, 0, x, H), fill=(255, 255, 255, 8))
for y in range(0, H, 40):
    d.line((0, y, W, y), fill=(255, 255, 255, 8))

# Network nodes
random.seed(7)
nodes = [(random.randint(40, W - 40), random.randint(40, H - 40)) for _ in range(28)]
for i, (x1, y1) in enumerate(nodes):
    for x2, y2 in nodes[i + 1:]:
        if math.hypot(x2 - x1, y2 - y1) < 190:
            d.line((x1, y1, x2, y2), fill=(140, 120, 255, 28), width=1)
for x, y in nodes:
    d.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(170, 150, 255, 120))

# Glass card
card = (70, 60, W - 70, H - 60)
overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
od = ImageDraw.Draw(overlay)
od.rounded_rectangle(card, radius=34, fill=(255, 255, 255, 14), outline=(160, 140, 255, 110), width=2)
img = Image.alpha_composite(img.convert("RGBA"), overlay)
d = ImageDraw.Draw(img, "RGBA")

# Accent corner marks
acc = (130, 110, 255, 220)
L = 46
for (cx, cy, sx, sy) in [(card[0], card[1], 1, 1), (card[2], card[1], -1, 1),
                         (card[0], card[3], 1, -1), (card[2], card[3], -1, -1)]:
    d.line((cx + sx * 34, cy, cx + sx * (34 + L), cy), fill=acc, width=4)
    d.line((cx, cy + sy * 34, cx, cy + sy * (34 + L)), fill=acc, width=4)

# Logo: hexagonal shield with gradient, stylised "V" and coin ring
cx, cy, r = 190, 300, 105


def hexagon(x, y, rad, rot=math.pi / 6):
    return [(x + rad * math.cos(rot + k * math.pi / 3), y + rad * math.sin(rot + k * math.pi / 3)) for k in range(6)]


# Glow behind the logo
logo_glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
lg = ImageDraw.Draw(logo_glow)
lg.polygon(hexagon(cx, cy, r + 26), fill=(130, 110, 255, 170))
logo_glow = logo_glow.filter(ImageFilter.GaussianBlur(28))
img = Image.alpha_composite(img, logo_glow)

# Gradient fill inside the hexagon (violet -> cyan)
grad = Image.new("RGBA", (W, H), (0, 0, 0, 0))
gp = grad.load()
g1, g2 = (110, 80, 255), (0, 200, 215)
for yy in range(cy - r - 2, cy + r + 2):
    for xx in range(cx - r - 2, cx + r + 2):
        t = ((xx - (cx - r)) + (yy - (cy - r))) / (4 * r)
        gp[xx, yy] = lerp(g1, g2, max(0.0, min(1.0, t))) + (255,)
mask = Image.new("L", (W, H), 0)
ImageDraw.Draw(mask).polygon(hexagon(cx, cy, r), fill=255)
img = Image.composite(grad, img, mask)

d = ImageDraw.Draw(img, "RGBA")
# Inner dark hexagon + rings
d.polygon(hexagon(cx, cy, r - 12), fill=(20, 16, 56, 255))
d.polygon(hexagon(cx, cy, r), outline=(240, 236, 255, 255), width=3)
d.ellipse((cx - 64, cy - 64, cx + 64, cy + 64), outline=(150, 130, 255, 120), width=2)

# Stylised "V" made of two thick strokes with a coin dot at the vertex
v_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
vd = ImageDraw.Draw(v_layer)
vd.line((cx - 50, cy - 52, cx, cy + 38), fill=(255, 255, 255, 255), width=20)
vd.line((cx + 50, cy - 52, cx, cy + 38), fill=(0, 220, 230, 255), width=20)
for (ex, ey) in [(cx - 50, cy - 52), (cx + 50, cy - 52)]:
    vd.ellipse((ex - 10, ey - 10, ex + 10, ey + 10), fill=(255, 255, 255, 255) if ex < cx else (0, 220, 230, 255))
vd.ellipse((cx - 16, cy + 24, cx + 16, cy + 56), fill=(255, 205, 60, 255), outline=(255, 240, 180, 255), width=3)
img = Image.alpha_composite(img, v_layer)

# Standalone logo export
logo_box = (cx - r - 30, cy - r - 30, cx + r + 30, cy + r + 30)
img.crop(logo_box).save(os.path.join(os.path.dirname(OUT), "logo.png"), "PNG", optimize=True)
d = ImageDraw.Draw(img, "RGBA")

# Title with shadow
title = "VM CRYPTO BOT"
f_title = font(FONT_BOLD, 84)
tx, ty = 330, 130
d.text((tx + 4, ty + 6), title, font=f_title, fill=(0, 0, 0, 140))
d.text((tx, ty), title, font=f_title, fill=(255, 255, 255, 255))

# Accent underline
d.rounded_rectangle((tx, ty + 104, tx + 300, ty + 110), radius=3, fill=(140, 120, 255, 255))
d.rounded_rectangle((tx + 312, ty + 104, tx + 360, ty + 110), radius=3, fill=(0, 200, 210, 255))

# Subtitle
f_sub = font(FONT_REG, 34)
d.text((tx, ty + 130), "Secure Multi-Chain Wallet Dashboard", font=f_sub, fill=(200, 195, 235, 255))

# Feature pills
f_pill = font(FONT_REG, 22)
pills = ["Deposit", "Withdraw", "Convert", "Balances", "Explorer"]
px_, py_ = tx, ty + 200
pill_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
pd = ImageDraw.Draw(pill_layer)
for p in pills:
    w = pd.textlength(p, font=f_pill) + 36
    pd.rounded_rectangle((px_, py_, px_ + w, py_ + 42), radius=21, fill=(255, 255, 255, 22),
                         outline=(160, 140, 255, 140), width=1)
    pd.text((px_ + 18, py_ + 9), p, font=f_pill, fill=(230, 226, 255, 255))
    px_ += w + 12
img = Image.alpha_composite(img, pill_layer)
d = ImageDraw.Draw(img, "RGBA")

# Networks / tokens lines
f_mono = font(FONT_MONO, 21)
d.text((tx, ty + 270), "Networks  ETH · BSC · Polygon · Solana · Tron · LTC · BTC · TON",
       font=f_mono, fill=(170, 165, 210, 255))
d.text((tx, ty + 302), "Tokens    USDT · USDC · ETH · BNB · MATIC · SOL · TRX · LTC",
       font=f_mono, fill=(170, 165, 210, 255))

# Footer
f_foot = font(FONT_BOLD, 22)
d.text((card[0] + 40, card[3] - 50), "Securely Made By Venom", font=f_foot, fill=(150, 135, 255, 255))
handle = "@VM_CryptoBOT"
hw = d.textlength(handle, font=f_foot)
d.text((card[2] - 40 - hw, card[3] - 50), handle, font=f_foot, fill=(200, 195, 235, 255))

img.convert("RGB").save(OUT, "PNG", optimize=True)
print("saved", OUT)
