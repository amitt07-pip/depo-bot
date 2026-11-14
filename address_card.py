"""Render a deposit-address card image (PNG) in the style of the bot's banners."""
import io
import os

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

_ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
_FONT_DIR = os.path.join(_ASSETS, "fonts")
_TOKEN_DIR = os.path.join(_ASSETS, "tokens")
_LOGO_ALIASES = {"POL": "matic", "TONCOIN": "ton"}


def _token_logo(symbol: str, size: int):
    name = _LOGO_ALIASES.get(symbol, symbol.lower())
    path = os.path.join(_TOKEN_DIR, f"{name}.png")
    if not os.path.exists(path):
        return None
    try:
        return Image.open(path).convert("RGBA").resize((size, size), Image.LANCZOS)
    except Exception:
        return None

TOKEN_COLORS = {
    "USDT": (38, 161, 123), "USDC": (39, 117, 202), "ETH": (98, 126, 234),
    "BNB": (243, 186, 47), "MATIC": (130, 71, 229), "POL": (130, 71, 229),
    "SOL": (153, 69, 255), "TRX": (235, 0, 41), "BTC": (247, 147, 26),
    "LTC": (52, 93, 157), "TON": (0, 152, 234),
    "DAI": (245, 172, 55), "WBTC": (240, 146, 66), "BTCB": (247, 147, 26), "WETH": (236, 72, 153),
    "LINK": (42, 90, 218), "UNI": (255, 0, 122), "AAVE": (182, 80, 158), "SHIB": (228, 96, 20),
    "PEPE": (60, 150, 60), "DOGE": (194, 166, 51), "XRP": (35, 41, 47), "ADA": (0, 51, 173),
    "CAKE": (209, 136, 76), "FDUSD": (0, 0, 0), "BONK": (248, 160, 40), "JUP": (100, 190, 160),
    "WIF": (180, 140, 110),
}
_DEFAULT_COLOR = (120, 120, 140)

W, H = 1200, 600
BG = (246, 247, 250)
GRID = (234, 236, 241)
PANEL = (255, 255, 255)
PANEL_EDGE = (224, 227, 234)
MUTED = (112, 117, 132)
TEXT = (22, 24, 33)
SOFT = (74, 79, 94)
CHIP = (255, 255, 255)
TRACK = (229, 231, 237)
FRAME = (214, 217, 226)
AMBER = (240, 160, 0)


def _font(name: str, size: int, weight: str):
    path = os.path.join(_FONT_DIR, name)
    try:
        font = ImageFont.truetype(path, size)
        try:
            font.set_variation_by_name(weight)
        except Exception:
            pass
        return font
    except Exception:
        return ImageFont.load_default(size=size)


def _spaced(draw, xy, text, font, fill, spacing):
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + spacing
    return x


def _spaced_width(draw, text, font, spacing):
    return sum(draw.textlength(ch, font=font) for ch in text) + spacing * max(len(text) - 1, 0)


def _fit(draw, text, name, weight, max_w, start, minimum):
    size = start
    while size > minimum:
        font = _font(name, size, weight)
        if draw.textlength(text, font=font) <= max_w:
            return font
        size -= 2
    return _font(name, minimum, weight)


def _canvas(accent, glow_box=(30, 20, 430, 420)):
    """Dark grid background, accent glow, rounded frame and white corner brackets."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    for x in range(0, W, 40):
        d.line([(x, 0), (x, H)], fill=GRID)
    for y in range(0, H, 40):
        d.line([(0, y), (W, y)], fill=GRID)

    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).ellipse(glow_box, fill=70)
    img = Image.composite(Image.new("RGB", (W, H), accent), img, mask.filter(ImageFilter.GaussianBlur(110)))
    d = ImageDraw.Draw(img)

    m = 36
    d.rounded_rectangle([m, m, W - m, H - m], radius=34, outline=FRAME, width=2)
    b, t = 70, 5
    for cx, cy, sx, sy in ((m, m, 1, 1), (W - m, m, -1, 1), (m, H - m, 1, -1), (W - m, H - m, -1, -1)):
        d.line([(cx + sx * 34, cy), (cx + sx * b, cy)], fill=TEXT, width=t)
        d.line([(cx, cy + sy * 34), (cx, cy + sy * b)], fill=TEXT, width=t)
    return img, d


def _logo_or_badge(img, d, symbol, x, y, size):
    logo = _token_logo(symbol, size)
    if logo is not None:
        img.paste(logo, (x, y), logo)
        return
    d.ellipse([x, y, x + size, y + size], fill=TOKEN_COLORS.get(symbol, _DEFAULT_COLOR))
    f = _fit(d, symbol, "SpaceGrotesk.ttf", b"Bold", size - 8, size // 3, 8)
    d.text((x + size / 2, y + size / 2), symbol, font=f, fill=(255, 255, 255), anchor="mm")


def render_address_card(symbol: str, network_name: str, address: str,
                        balance: str = None, brand: str = None, title: str = "DEPOSIT") -> bytes:
    symbol = (symbol or "").upper()
    accent = TOKEN_COLORS.get(symbol, _DEFAULT_COLOR)

    img, d = _canvas(accent)

    # Coin: real token logo, text badge if no logo is bundled
    cx0, cy0, r = 96, 96, 78
    logo = _token_logo(symbol, 2 * r)
    if logo is not None:
        img.paste(logo, (cx0, cy0), logo)
    else:
        d.ellipse([cx0, cy0, cx0 + 2 * r, cy0 + 2 * r], fill=accent)
        d.ellipse([cx0 + 8, cy0 + 8, cx0 + 2 * r - 8, cy0 + 2 * r - 8], outline=(255, 255, 255), width=3)
        coin_font = _fit(d, symbol, "SpaceGrotesk.ttf", b"Bold", 2 * r - 40, 54, 22)
        d.text((cx0 + r, cy0 + r), symbol, font=coin_font, fill=(255, 255, 255), anchor="mm")

    # Title block
    x0 = cx0 + 2 * r + 34
    _spaced(d, (x0, 104), title, _font("Inter.ttf", 22, b"SemiBold"), MUTED, 5)
    sym_font = _fit(d, symbol, "SpaceGrotesk.ttf", b"Bold", 420, 76, 40)
    d.text((x0 - 3, 130), symbol, font=sym_font, fill=TEXT)
    pill_font = _font("Inter.ttf", 24, b"Medium")
    pw = d.textlength(network_name, font=pill_font) + 52
    d.rounded_rectangle([x0, 222, x0 + pw, 262], radius=20, fill=CHIP, outline=accent, width=2)
    d.ellipse([x0 + 16, 236, x0 + 28, 248], fill=accent)
    d.text((x0 + 38, 242), network_name, font=pill_font, fill=TEXT, anchor="lm")

    # Balance
    if balance is not None:
        right = W - 96
        lab_font = _font("Inter.ttf", 22, b"SemiBold")
        _spaced(d, (right - _spaced_width(d, "BALANCE", lab_font, 5), 104), "BALANCE", lab_font, MUTED, 5)
        bal_font = _fit(d, f"{balance} {symbol}", "SpaceGrotesk.ttf", b"Bold", 420, 48, 26)
        d.text((right, 140), f"{balance} {symbol}", font=bal_font, fill=TEXT, anchor="ra")

    # Address panel
    px0, py0, px1, py1 = 96, 300, W - 96, 452
    d.rounded_rectangle([px0, py0, px1, py1], radius=22, fill=PANEL, outline=PANEL_EDGE, width=2)
    _spaced(d, (px0 + 30, py0 + 24), "ADDRESS", _font("Inter.ttf", 20, b"SemiBold"), MUTED, 5)
    max_w = px1 - px0 - 60
    lines = [address]
    font = _fit(d, address, "JetBrainsMono.ttf", b"Medium", max_w, 40, 30)
    if d.textlength(address, font=font) > max_w:
        half = (len(address) + 1) // 2
        lines = [address[:half], address[half:]]
        font = _fit(d, lines[0], "JetBrainsMono.ttf", b"Medium", max_w, 34, 18)
    line_h = font.size + 8
    y = py0 + 60 + (py1 - py0 - 60 - line_h * len(lines)) // 2
    for i, line in enumerate(lines):
        x = px0 + 30
        head = line[:4] if i == 0 else ""
        tail = line[-4:] if i == len(lines) - 1 else ""
        mid = line[len(head):len(line) - len(tail)]
        for part, colour in ((head, accent), (mid, TEXT), (tail, accent)):
            if part:
                d.text((x, y), part, font=font, fill=colour)
                x += d.textlength(part, font=font)
        y += line_h

    # Footer: warning + brand
    fy = 500
    d.polygon([(110, fy + 22), (124, fy - 2), (138, fy + 22)], fill=AMBER)
    d.text((124, fy + 14), "!", font=_font("Inter.ttf", 16, b"Bold"), fill=(255, 255, 255), anchor="mm")
    warn = f"Only send {symbol} on {network_name} to this address"
    d.text((152, fy + 10), warn, font=_font("Inter.ttf", 22, b"Medium"), fill=SOFT, anchor="lm")
    if brand:
        d.text((W - 96, fy + 10), brand, font=_font("Inter.ttf", 24, b"Bold"), fill=TEXT, anchor="rm")

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()



def _usd(v) -> str:
    return f"${v:,.2f}"


def _amount(v) -> str:
    v = float(v)
    if v == 0:
        return "0"
    if v >= 1000:
        return f"{v:,.2f}"
    if v >= 1:
        return f"{v:,.4f}".rstrip("0").rstrip(".")
    return f"{v:.6f}".rstrip("0").rstrip(".")


def render_dashboard_card(name: str, total_usd, holdings, wallet_count: int,
                          brand: str = None, updated: str = None, avatar: bytes = None) -> bytes:
    """holdings: list of (symbol, amount, usd_value), any order."""
    assets = sum(1 for h in holdings if float(h[2]) > 0 or float(h[1]) > 0)
    return render_portfolio_card(
        f"Welcome back, {name}" if name else "Welcome back", total_usd, holdings,
        [f"{wallet_count} wallet{'s' if wallet_count != 1 else ''}",
         f"{assets} asset{'s' if assets != 1 else ''}"],
        brand=brand, updated=updated, avatar=avatar if avatar is not None else b"", initials=name,
    )


def _draw_avatar(img, data: bytes, initials: str, x: int, y: int, size: int) -> int:
    """Round user photo (or initials disc) with a white ring; returns the width used."""
    k = 3
    mask = Image.new("L", (size * k, size * k), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size * k - 1, size * k - 1], fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)
    face = None
    if data:
        try:
            face = Image.open(io.BytesIO(data)).convert("RGB")
            side = min(face.size)
            face = face.crop(((face.width - side) // 2, (face.height - side) // 2,
                              (face.width + side) // 2, (face.height + side) // 2)).resize((size, size), Image.LANCZOS)
        except Exception:
            face = None
    if face is None:
        face = Image.new("RGB", (size, size), (88, 101, 242))
        letters = "".join(w[0] for w in (initials or "").split()[:2] if w and w[0].isalnum()).upper() or "V"
        ImageDraw.Draw(face).text((size / 2, size / 2), letters, fill=(255, 255, 255), anchor="mm",
                                  font=_font("Inter.ttf", int(size * 0.4), b"Bold"))
    ring = Image.new("L", (size + 8, size + 8), 0)
    ImageDraw.Draw(ring).ellipse([0, 0, size + 7, size + 7], fill=255)
    img.paste((255, 255, 255), (x - 4, y - 4), ring)
    img.paste(face, (x, y), mask)
    return size


def render_portfolio_card(heading: str, total_usd, holdings, chips, brand: str = None,
                          updated: str = None, accent_symbol: str = None,
                          empty_hint: str = "Tap Deposit to get started",
                          avatar: bytes = None, initials: str = "") -> bytes:
    """Total USD value, chips, allocation bar and a holdings panel.
    holdings: list of (symbol, amount, usd_value), any order."""
    accent = TOKEN_COLORS.get((accent_symbol or "").upper(), (38, 161, 123))
    img, d = _canvas(accent, glow_box=(-60, -40, 520, 460))
    holdings = sorted((h for h in holdings if float(h[2]) > 0 or float(h[1]) > 0),
                      key=lambda h: float(h[2]), reverse=True)
    total = float(total_usd)

    # Left: heading, total, chips
    x0 = 96
    hx = x0
    if avatar is not None:
        hx = x0 + _draw_avatar(img, avatar, initials, x0, 84, 56) + 16
        d = ImageDraw.Draw(img)
    d.text((hx, 96), heading, font=_fit(d, heading, "Inter.ttf", b"Medium", 540 - (hx - x0), 28, 18), fill=MUTED)
    _spaced(d, (x0, 158), "TOTAL BALANCE", _font("Inter.ttf", 20, b"SemiBold"), MUTED, 5)
    total_s = _usd(total)
    d.text((x0 - 4, 184), total_s, font=_fit(d, total_s, "SpaceGrotesk.ttf", b"Bold", 540, 104, 44), fill=TEXT)

    chip_font = _font("Inter.ttf", 22, b"Medium")
    cx = x0
    for label in chips:
        w = d.textlength(label, font=chip_font) + 50
        if cx + w > 640:
            break
        d.rounded_rectangle([cx, 316, cx + w, 354], radius=19, fill=CHIP, outline=PANEL_EDGE, width=2)
        d.ellipse([cx + 16, 329, cx + 28, 341], fill=accent)
        d.text((cx + 36, 335), label, font=chip_font, fill=TEXT, anchor="lm")
        cx += w + 14

    # Allocation bar
    bx0, bx1, by = x0, 620, 412
    _spaced(d, (bx0, by - 30), "ALLOCATION", _font("Inter.ttf", 18, b"SemiBold"), MUTED, 4)
    d.rounded_rectangle([bx0, by, bx1, by + 16], radius=8, fill=TRACK)
    if total > 0:
        bar = Image.new("RGB", (bx1 - bx0, 16), TRACK)
        bd = ImageDraw.Draw(bar)
        pos = 0.0
        for sym, _, usd in holdings:
            seg = (bx1 - bx0) * float(usd) / total
            bd.rectangle([int(pos), 0, int(pos + seg), 16], fill=TOKEN_COLORS.get(sym, _DEFAULT_COLOR))
            pos += seg
        mask = Image.new("L", bar.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, bar.width - 1, 15], radius=8, fill=255)
        img.paste(bar, (bx0, by), mask)

    # Right: holdings panel
    px0, py0, px1, py1 = 676, 96, W - 96, 452
    d.rounded_rectangle([px0, py0, px1, py1], radius=22, fill=PANEL, outline=PANEL_EDGE, width=2)
    _spaced(d, (px0 + 26, py0 + 22), "HOLDINGS", _font("Inter.ttf", 18, b"SemiBold"), MUTED, 4)
    rows = holdings[:4]
    if len(holdings) > 4:
        rest = holdings[3:]
        rows = holdings[:3] + [("OTHER", None, sum(float(h[2]) for h in rest))]
    sym_font = _font("Inter.ttf", 25, b"SemiBold")
    sub_font = _font("Inter.ttf", 19, b"Regular")
    usd_font = _font("SpaceGrotesk.ttf", 25, b"Bold")
    y = py0 + 64
    if not rows:
        d.text(((px0 + px1) / 2, (py0 + py1) / 2 + 10), "No funds yet",
               font=_font("Inter.ttf", 26, b"SemiBold"), fill=TEXT, anchor="mm")
        d.text(((px0 + px1) / 2, (py0 + py1) / 2 + 46), empty_hint,
               font=sub_font, fill=MUTED, anchor="mm")
    for sym, amt, usd in rows:
        if sym == "OTHER":
            d.ellipse([px0 + 24, y + 6, px0 + 68, y + 50], fill=TRACK)
            d.text((px0 + 46, y + 28), "+", font=_font("Inter.ttf", 26, b"Bold"), fill=TEXT, anchor="mm")
            d.text((px0 + 84, y + 4), "Others", font=sym_font, fill=TEXT)
            d.text((px0 + 84, y + 34), f"{len(holdings) - 3} more", font=sub_font, fill=MUTED)
        else:
            _logo_or_badge(img, d, sym, px0 + 24, y + 6, 44)
            d.text((px0 + 84, y + 4), sym, font=sym_font, fill=TEXT)
            d.text((px0 + 84, y + 34), _amount(amt), font=sub_font, fill=MUTED)
        pct = (float(usd) / total * 100) if total > 0 else 0
        d.text((px1 - 26, y + 4), _usd(float(usd)), font=usd_font, fill=TEXT, anchor="ra")
        d.text((px1 - 26, y + 34), f"{pct:.1f}%", font=sub_font, fill=MUTED, anchor="ra")
        y += 84 if len(rows) < 4 else 72

    # Footer
    fy = 510
    d.ellipse([x0 + 2, fy - 6, x0 + 14, fy + 6], fill=accent)
    foot = "Total value in USD" + (f"  \u00b7  Updated {updated}" if updated else "")
    d.text((x0 + 26, fy), foot, font=_font("Inter.ttf", 21, b"Medium"), fill=SOFT, anchor="lm")
    if brand:
        d.text((W - 96, fy), brand, font=_font("Inter.ttf", 24, b"Bold"), fill=TEXT, anchor="rm")

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


BANNER_ACCENTS = {
    "welcome": (88, 101, 242), "welcome_public": (88, 101, 242),
    "deposit": (38, 161, 123), "withdraw": (229, 72, 77), "convert": (39, 117, 202),
    "balance": (243, 160, 20), "wallets": (130, 71, 229), "generate": (20, 170, 160),
    "help": (90, 110, 140), "tokens": (0, 152, 234), "profile": (200, 80, 160),
    "transaction": (98, 126, 234),
}


def _glyph_tile(name, size, accent):
    """Accent gradient rounded tile with a white feature glyph (drawn at 3x, downsampled)."""
    k = 3
    S = size * k
    top = tuple(min(255, int(c + (255 - c) * 0.35)) for c in accent)
    tile = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    grad = Image.new("RGB", (S, S))
    gd = ImageDraw.Draw(grad)
    for i in range(S):
        t = i / S
        gd.line([(0, i), (S, i)], fill=tuple(int(top[j] * (1 - t) + accent[j] * t) for j in range(3)))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=S // 4, fill=255)
    tile.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(tile)
    Wt = (255, 255, 255, 255)
    w = int(S * 0.075)

    def P(x, y):
        return (S * x, S * y)

    def line(*pts):
        d.line([P(*q) for q in pts], fill=Wt, width=w, joint="curve")
        for q in (pts[0], pts[-1]):
            x, y = P(*q)
            d.ellipse([x - w / 2, y - w / 2, x + w / 2, y + w / 2], fill=Wt)

    if name == "deposit":
        line((0.5, 0.22), (0.5, 0.56)); line((0.36, 0.43), (0.5, 0.57), (0.64, 0.43))
        line((0.24, 0.58), (0.24, 0.76), (0.76, 0.76), (0.76, 0.58))
    elif name == "withdraw":
        line((0.5, 0.58), (0.5, 0.22)); line((0.36, 0.36), (0.5, 0.22), (0.64, 0.36))
        line((0.24, 0.58), (0.24, 0.76), (0.76, 0.76), (0.76, 0.58))
    elif name == "convert":
        line((0.26, 0.38), (0.72, 0.38)); line((0.6, 0.27), (0.73, 0.38), (0.6, 0.49))
        line((0.74, 0.62), (0.28, 0.62)); line((0.4, 0.51), (0.27, 0.62), (0.4, 0.73))
    elif name == "balance":
        for x0, h in ((0.26, 0.22), (0.44, 0.38), (0.62, 0.5)):
            d.rounded_rectangle([*P(x0, 0.74 - h), *P(x0 + 0.12, 0.74)], radius=S * 0.03, fill=Wt)
        line((0.22, 0.79), (0.78, 0.79))
    elif name == "wallets":
        d.rounded_rectangle([*P(0.2, 0.3), *P(0.8, 0.74)], radius=S * 0.07, outline=Wt, width=w)
        d.rounded_rectangle([*P(0.56, 0.44), *P(0.84, 0.6)], radius=S * 0.05, fill=Wt)
        d.ellipse([*P(0.62, 0.48), *P(0.70, 0.56)], fill=accent + (255,))
        line((0.3, 0.3), (0.62, 0.2), (0.68, 0.3))
    elif name == "generate":
        d.ellipse([*P(0.2, 0.3), *P(0.48, 0.58)], outline=Wt, width=w)
        line((0.48, 0.44), (0.8, 0.44)); line((0.7, 0.44), (0.7, 0.58)); line((0.8, 0.44), (0.8, 0.54))
        line((0.62, 0.18), (0.62, 0.3)); line((0.56, 0.24), (0.68, 0.24))
    elif name == "help":
        d.text(P(0.5, 0.52), "?", font=_font("SpaceGrotesk.ttf", int(S * 0.62), b"Bold"), fill=Wt, anchor="mm")
    elif name == "profile":
        d.ellipse([*P(0.37, 0.2), *P(0.63, 0.46)], fill=Wt)
        d.pieslice([*P(0.22, 0.52), *P(0.78, 1.04)], 180, 360, fill=Wt)
    elif name == "transaction":
        pts = [P(0.28, 0.2), P(0.72, 0.2), P(0.72, 0.8)]
        for i in range(6):
            pts.append(P(0.72 - (i + 0.5) * 0.44 / 6, 0.75 if i % 2 == 0 else 0.8))
        pts.append(P(0.28, 0.8))
        d.polygon(pts, fill=Wt)
        for y, x1 in ((0.33, 0.62), (0.45, 0.62), (0.57, 0.5)):
            d.rounded_rectangle([*P(0.38, y), *P(x1, y + 0.05)], radius=S * 0.02, fill=accent + (255,))
    else:
        line((0.28, 0.30), (0.50, 0.72), (0.72, 0.30))
    return tile.resize((size, size), Image.LANCZOS)


def _paste_shadowed(img, tile, x, y, accent, alpha=60):
    shadow = Image.new("L", (W, H), 0)
    r = tile.width // 4
    ImageDraw.Draw(shadow).rounded_rectangle([x + 6, y + 14, x + tile.width + 6, y + tile.height + 14],
                                             radius=r, fill=alpha)
    img.paste(Image.composite(Image.new("RGB", (W, H), accent), img, shadow.filter(ImageFilter.GaussianBlur(18))))
    img.paste(tile, (x, y), tile)


def _ring_logo(img, symbol, x, y, size):
    ring = Image.new("RGBA", (size + 12, size + 12), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse([0, 0, size + 11, size + 11], fill=(255, 255, 255, 255))
    img.paste(ring, (x - 6, y - 6), ring)
    logo = _token_logo(symbol, size)
    if logo is not None:
        img.paste(logo, (x, y), logo)


def _feature_art(img, name, box, accent):
    px0, py0, px1, py1 = box
    cx, cy = (px0 + px1) // 2, (py0 + py1) // 2 - 6
    if name == "tokens":
        syms = ["BTC", "ETH", "USDT", "SOL", "BNB", "TON"]
        size, gap = 86, 22
        gx = cx - (3 * size + 2 * gap) // 2
        gy = cy - (2 * size + gap) // 2
        for i, sym in enumerate(syms):
            _ring_logo(img, sym, gx + (i % 3) * (size + gap), gy + (i // 3) * (size + gap), size)
        return ImageDraw.Draw(img)
    size = 190
    _paste_shadowed(img, _glyph_tile(name, size, accent), cx - size // 2, cy - size // 2, accent)
    badges = {"convert": (("USDT", -1, 1), ("ETH", 1, -1)), "deposit": (("USDT", 1, 1),),
              "withdraw": (("ETH", 1, 1),), "balance": (("BTC", 1, 1),)}
    for sym, sx, sy in badges.get(name, ()):
        bs = 64
        _ring_logo(img, sym, cx + sx * (size // 2) - bs // 2, cy + sy * (size // 2) - bs // 2, bs)
    return ImageDraw.Draw(img)


def render_banner(title: str, subtitle: str, accent=None, brand: str = "@UseVexBot",
                  tagline: str = "Secure Multi-Chain Wallet", kicker: str = "VEX",
                  art: str = None) -> bytes:
    """Section banner in the card style: kicker, big title, subtitle, emblem panel, footer."""
    accent = accent or (88, 101, 242)
    img, d = _canvas(accent, glow_box=(-60, -40, 560, 480))
    x0 = 96
    _spaced(d, (x0, 120), kicker, _font("Inter.ttf", 22, b"SemiBold"), MUTED, 6)
    d.text((x0 - 5, 150), title, font=_fit(d, title, "SpaceGrotesk.ttf", b"Bold", 600, 120, 60), fill=TEXT)
    d.rounded_rectangle([x0, 306, x0 + 120, 314], radius=4, fill=accent)
    d.rounded_rectangle([x0 + 132, 306, x0 + 168, 314], radius=4, fill=TRACK)
    d.text((x0, 340), subtitle, font=_fit(d, subtitle, "Inter.ttf", b"Medium", 600, 36, 22), fill=SOFT)

    px0, py0, px1, py1 = 760, 96, W - 96, 452
    d.rounded_rectangle([px0, py0, px1, py1], radius=28, fill=PANEL, outline=PANEL_EDGE, width=2)
    d = _feature_art(img, art or "welcome", (px0, py0, px1, py1), accent)

    fy = 510
    d.ellipse([x0 + 2, fy - 6, x0 + 14, fy + 6], fill=accent)
    d.text((x0 + 26, fy), tagline, font=_font("Inter.ttf", 21, b"Medium"), fill=SOFT, anchor="lm")
    if brand:
        d.text((W - 96, fy), brand, font=_font("Inter.ttf", 24, b"Bold"), fill=TEXT, anchor="rm")
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


def render_profile_picture(size: int = 640) -> bytes:
    """Square bot avatar: light grid backdrop with the Vex mark centred (safe for circle crop)."""
    accent = BANNER_ACCENTS["welcome"]
    img = Image.new("RGB", (size, size), BG)
    d = ImageDraw.Draw(img)
    for x in range(0, size, 32):
        d.line([(x, 0), (x, size)], fill=GRID)
        d.line([(0, x), (size, x)], fill=GRID)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([size * 0.1, size * 0.1, size * 0.9, size * 0.9], fill=90)
    img = Image.composite(Image.new("RGB", (size, size), accent), img, mask.filter(ImageFilter.GaussianBlur(size // 8)))
    t = int(size * 0.5)
    tile = _glyph_tile("welcome", t, accent)
    img.paste(tile, ((size - t) // 2, (size - t) // 2), tile)
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


_STATUS_STYLES = {
    "success": ((22, 163, 74), "Confirmed"),
    "failed": ((220, 38, 38), "Failed"),
    "pending": ((217, 119, 6), "Pending"),
}


def _short(value: str, head: int = 8, tail: int = 6) -> str:
    value = value or "Unknown"
    return value if len(value) <= head + tail + 1 else f"{value[:head]}\u2026{value[-tail:]}"


def render_transaction_card(network_name: str, status: str, amount, asset: str, sender: str,
                            receiver: str, when: str, tx_hash: str, brand: str = None) -> bytes:
    """Receipt-style card: amount, status, network, from/to, hash and time."""
    asset = (asset or "").upper()
    accent = TOKEN_COLORS.get(asset, (98, 126, 234))
    img, d = _canvas(accent)

    # Coin and amount
    x0, size = 96, 132
    if asset:
        _logo_or_badge(img, d, asset, x0, 100, size)
    else:
        d.ellipse([x0, 100, x0 + size, 100 + size], fill=TRACK)
    tx = x0 + size + 32
    _spaced(d, (tx, 104), "TRANSACTION", _font("Inter.ttf", 22, b"SemiBold"), MUTED, 5)
    amount_s = f"{_amount(amount)} {asset}".strip() if amount not in (None, "") else "Unknown amount"
    try:
        float(amount)
    except (TypeError, ValueError):
        amount_s = f"{amount} {asset}".strip() if amount not in (None, "") else "Unknown amount"
    d.text((tx - 3, 132), amount_s, font=_fit(d, amount_s, "SpaceGrotesk.ttf", b"Bold", 560, 68, 32), fill=TEXT)
    pill_font = _font("Inter.ttf", 24, b"Medium")
    pw = d.textlength(network_name, font=pill_font) + 52
    d.rounded_rectangle([tx, 218, tx + pw, 258], radius=20, fill=CHIP, outline=accent, width=2)
    d.ellipse([tx + 16, 232, tx + 28, 244], fill=accent)
    d.text((tx + 38, 238), network_name, font=pill_font, fill=TEXT, anchor="lm")

    # Status badge
    key = (status or "").lower()
    key = "success" if key in ("success", "confirmed") else "failed" if key in ("failed", "fail", "revert", "error") \
        else "pending" if key in ("pending", "") else None
    colour, label = _STATUS_STYLES.get(key, ((90, 110, 140), (status or "Unknown").title()))
    sf = _font("Inter.ttf", 24, b"SemiBold")
    sw = d.textlength(label, font=sf) + 64
    sx1 = W - 96
    tint = tuple(int(c * 0.14 + 255 * 0.86) for c in colour)
    d.rounded_rectangle([sx1 - sw, 100, sx1, 146], radius=23, fill=tint, outline=colour, width=2)
    d.ellipse([sx1 - sw + 20, 116, sx1 - sw + 34, 130], fill=colour)
    d.text((sx1 - sw + 44, 123), label, font=sf, fill=colour, anchor="lm")

    # From / To panel with hash and time
    px0, py0, px1, py1 = 96, 288, W - 96, 466
    d.rounded_rectangle([px0, py0, px1, py1], radius=22, fill=PANEL, outline=PANEL_EDGE, width=2)
    lab = _font("Inter.ttf", 18, b"SemiBold")
    mono = _font("JetBrainsMono.ttf", 28, b"Medium")
    mid = (px0 + px1) // 2
    _spaced(d, (px0 + 30, py0 + 20), "FROM", lab, MUTED, 4)
    d.text((px0 + 30, py0 + 48), _short(sender), font=mono, fill=TEXT)
    _spaced(d, (mid + 40, py0 + 20), "TO", lab, MUTED, 4)
    d.text((mid + 40, py0 + 48), _short(receiver), font=mono, fill=TEXT)
    ay = py0 + 64
    d.line([(mid - 22, ay), (mid + 18, ay)], fill=accent, width=4)
    d.polygon([(mid + 24, ay), (mid + 10, ay - 10), (mid + 10, ay + 10)], fill=accent)
    d.line([(px0 + 30, py0 + 104), (px1 - 30, py0 + 104)], fill=TRACK, width=2)
    _spaced(d, (px0 + 30, py0 + 118), "TX HASH", lab, MUTED, 4)
    d.text((px0 + 30, py0 + 140), _short(tx_hash, 10, 8), font=_font("JetBrainsMono.ttf", 22, b"Medium"), fill=SOFT)
    _spaced(d, (px1 - 30 - _spaced_width(d, "DATE", lab, 4), py0 + 118), "DATE", lab, MUTED, 4)
    d.text((px1 - 30, py0 + 140), when or "Unknown", font=_font("Inter.ttf", 22, b"Medium"), fill=SOFT, anchor="ra")

    # Footer
    fy = 512
    d.ellipse([x0 + 2, fy - 6, x0 + 14, fy + 6], fill=accent)
    d.text((x0 + 26, fy), f"On-chain details \u00b7 {network_name}", font=_font("Inter.ttf", 21, b"Medium"),
           fill=SOFT, anchor="lm")
    if brand:
        d.text((W - 96, fy), brand, font=_font("Inter.ttf", 24, b"Bold"), fill=TEXT, anchor="rm")

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()
