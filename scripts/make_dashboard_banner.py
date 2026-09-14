"""Generate the black & white VM CRYPTO BOT banners in assets/.

Produces welcome.png (dashboard), logo.png and one banner per bot section.
"""
import math
import os
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")

FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"

BLACK = (0, 0, 0, 255)
WHITE = (255, 255, 255, 255)
GREY = (200, 200, 200, 255)
DIM = (140, 140, 140, 255)

HANDLE = "@VM_CryptoBOT"
FOOTER = "Securely Made By Venom"

SECTION_BANNERS = {
    "balance": ("BALANCE", "Your Assets"),
    "convert": ("CONVERT", "Swap Assets"),
    "deposit": ("DEPOSIT", "Receive Crypto"),
    "generate": ("GENERATE", "New Wallet"),
    "help": ("HELP", "Commands & Guide"),
    "profile": ("PROFILE", "Your Account"),
    "tokens": ("TOKENS", "Supported Assets"),
    "transaction": ("TRANSACTION", "Details"),
    "wallets": ("WALLETS", "Your Addresses"),
    "welcome_public": ("VM CRYPTO BOT", "Secure Multi-Chain Wallet"),
    "withdraw": ("WITHDRAW", "Send Crypto"),
}


def font(path, size):
    return ImageFont.truetype(path, size)


def hexagon(x, y, rad, rot=math.pi / 6):
    return [(x + rad * math.cos(rot + k * math.pi / 3),
             y + rad * math.sin(rot + k * math.pi / 3)) for k in range(6)]


def overlay(img, draw_fn):
    """Draw semi-transparent shapes on a layer and alpha-composite it."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(layer))
    return Image.alpha_composite(img, layer)


def background(w, h, nodes=True):
    img = Image.new("RGBA", (w, h), BLACK)
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for x in range(0, w, 40):
        d.line((x, 0, x, h), fill=(255, 255, 255, 10))
    for y in range(0, h, 40):
        d.line((0, y, w, y), fill=(255, 255, 255, 10))
    if nodes:
        random.seed(7)
        pts = [(random.randint(40, w - 40), random.randint(40, h - 40)) for _ in range(28)]
        for i, (x1, y1) in enumerate(pts):
            for x2, y2 in pts[i + 1:]:
                if math.hypot(x2 - x1, y2 - y1) < 190:
                    d.line((x1, y1, x2, y2), fill=(255, 255, 255, 22), width=1)
        for x, y in pts:
            d.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(255, 255, 255, 90))
    return Image.alpha_composite(img, layer)


def card_frame(img, card):
    img = overlay(img, lambda d: d.rounded_rectangle(
        card, radius=30, fill=(255, 255, 255, 8), outline=(255, 255, 255, 120), width=2))
    d = ImageDraw.Draw(img)
    L = 40
    for (cx, cy, sx, sy) in [(card[0], card[1], 1, 1), (card[2], card[1], -1, 1),
                             (card[0], card[3], 1, -1), (card[2], card[3], -1, -1)]:
        d.line((cx + sx * 30, cy, cx + sx * (30 + L), cy), fill=WHITE, width=4)
        d.line((cx, cy + sy * 30, cx, cy + sy * (30 + L)), fill=WHITE, width=4)
    return img


def draw_logo(img, cx, cy, r):
    """Monochrome emblem: hexagonal shield, coin with cross mark, orbit ring."""
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).polygon(hexagon(cx, cy, r + 14), fill=(255, 255, 255, 60))
    glow = glow.filter(ImageFilter.GaussianBlur(22))
    img = Image.alpha_composite(img, glow)

    d = ImageDraw.Draw(img)
    d.polygon(hexagon(cx, cy, r), fill=WHITE)
    d.polygon(hexagon(cx, cy, r - 10), fill=BLACK)
    img = overlay(img, lambda d: d.polygon(hexagon(cx, cy, r - 18), outline=(255, 255, 255, 90), width=2))

    coin_r = int(r * 0.42)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.ellipse((cx - coin_r, cy - coin_r, cx + coin_r, cy + coin_r), fill=WHITE)
    inner = coin_r - max(8, coin_r // 4)
    ld.ellipse((cx - inner, cy - inner, cx + inner, cy + inner), outline=BLACK, width=3)
    bar = max(3, coin_r // 10)
    arm = int(coin_r * 0.36)
    ld.rectangle((cx - bar, cy - arm, cx + bar, cy + arm), fill=BLACK)
    ld.rectangle((cx - arm, cy - bar, cx + arm, cy + bar), fill=BLACK)

    orbit = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ox_r, oy_r = int(r * 0.74), int(r * 0.29)
    ImageDraw.Draw(orbit).ellipse((cx - ox_r, cy - oy_r, cx + ox_r, cy + oy_r), outline=WHITE, width=max(4, r // 18))
    orbit = orbit.rotate(-28, center=(cx, cy), resample=Image.BICUBIC)
    od = ImageDraw.Draw(orbit)
    for ang in (20, 200):
        a = math.radians(ang)
        ox, oy = ox_r * math.cos(a), oy_r * math.sin(a)
        rot = math.radians(28)
        nx = cx + ox * math.cos(rot) - oy * math.sin(rot)
        ny = cy + ox * math.sin(rot) + oy * math.cos(rot)
        nr = max(5, r // 12)
        od.ellipse((nx - nr, ny - nr, nx + nr, ny + nr), fill=BLACK, outline=WHITE, width=3)

    img = Image.alpha_composite(img, orbit)
    img = Image.alpha_composite(img, layer)
    return img


def footer(img, card):
    d = ImageDraw.Draw(img, "RGBA")
    f_foot = font(FONT_BOLD, 22)
    d.text((card[0] + 40, card[3] - 50), FOOTER, font=f_foot, fill=WHITE)
    hw = d.textlength(HANDLE, font=f_foot)
    d.text((card[2] - 40 - hw, card[3] - 50), HANDLE, font=f_foot, fill=GREY)


def make_dashboard():
    W, H = 1200, 600
    img = background(W, H)
    card = (70, 60, W - 70, H - 60)
    img = card_frame(img, card)

    cx, cy, r = 190, 300, 105
    img = draw_logo(img, cx, cy, r)
    logo_box = (cx - r - 30, cy - r - 30, cx + r + 30, cy + r + 30)
    img.crop(logo_box).save(os.path.join(ASSETS, "logo.png"), "PNG", optimize=True)

    d = ImageDraw.Draw(img, "RGBA")
    tx, ty = 330, 130
    d.text((tx, ty), "VM CRYPTO BOT", font=font(FONT_BOLD, 84), fill=WHITE)
    d.rounded_rectangle((tx, ty + 104, tx + 300, ty + 110), radius=3, fill=WHITE)
    d.rounded_rectangle((tx + 312, ty + 104, tx + 360, ty + 110), radius=3, fill=DIM)
    d.text((tx, ty + 130), "Secure Multi-Chain Wallet Dashboard", font=font(FONT_REG, 34), fill=GREY)

    f_pill = font(FONT_REG, 22)
    px_, py_ = tx, ty + 200
    pill_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    pd = ImageDraw.Draw(pill_layer)
    for p in ["Deposit", "Withdraw", "Convert", "Balances", "Explorer"]:
        w = pd.textlength(p, font=f_pill) + 36
        pd.rounded_rectangle((px_, py_, px_ + w, py_ + 42), radius=21, fill=(255, 255, 255, 18),
                             outline=(255, 255, 255, 170), width=1)
        pd.text((px_ + 18, py_ + 9), p, font=f_pill, fill=WHITE)
        px_ += w + 12
    img = Image.alpha_composite(img, pill_layer)

    d = ImageDraw.Draw(img, "RGBA")
    f_mono = font(FONT_MONO, 21)
    d.text((tx, ty + 270), "Networks  ETH · BSC · Polygon · Solana · Tron · LTC · BTC · TON", font=f_mono, fill=GREY)
    d.text((tx, ty + 302), "Tokens    USDT · USDC · ETH · BNB · MATIC · SOL · TRX · LTC", font=f_mono, fill=GREY)

    footer(img, card)
    out = os.path.join(ASSETS, "welcome.png")
    img.convert("RGB").save(out, "PNG", optimize=True)
    print("saved", out)


def make_section(name, title, subtitle):
    W, H = 800, 400
    img = background(W, H)
    card = (40, 40, W - 40, H - 40)
    img = card_frame(img, card)

    img = draw_logo(img, 130, 200, 62)

    d = ImageDraw.Draw(img, "RGBA")
    f_title = font(FONT_BOLD, 64 if len(title) <= 11 else 52)
    tw = d.textlength(title, font=f_title)
    tx = 230 + (W - 40 - 230 - tw) / 2
    d.text((tx, 130), title, font=f_title, fill=WHITE)
    d.rounded_rectangle((tx, 208, tx + min(tw, 160), 213), radius=2, fill=WHITE)
    f_sub = font(FONT_REG, 28)
    d.text((tx, 228), subtitle, font=f_sub, fill=GREY)

    footer(img, card)
    out = os.path.join(ASSETS, f"{name}.png")
    img.convert("RGB").save(out, "PNG", optimize=True)
    print("saved", out)


if __name__ == "__main__":
    make_dashboard()
    for name, (title, subtitle) in SECTION_BANNERS.items():
        make_section(name, title, subtitle)
