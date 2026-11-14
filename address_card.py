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
                          brand: str = None, updated: str = None) -> bytes:
    """holdings: list of (symbol, amount, usd_value), any order."""
    assets = sum(1 for h in holdings if float(h[2]) > 0 or float(h[1]) > 0)
    return render_portfolio_card(
        f"Welcome back, {name}" if name else "Welcome back", total_usd, holdings,
        [f"{wallet_count} wallet{'s' if wallet_count != 1 else ''}",
         f"{assets} asset{'s' if assets != 1 else ''}"],
        brand=brand, updated=updated,
    )


def render_portfolio_card(heading: str, total_usd, holdings, chips, brand: str = None,
                          updated: str = None, accent_symbol: str = None,
                          empty_hint: str = "Tap Deposit to get started") -> bytes:
    """Total USD value, chips, allocation bar and a holdings panel.
    holdings: list of (symbol, amount, usd_value), any order."""
    accent = TOKEN_COLORS.get((accent_symbol or "").upper(), (38, 161, 123))
    img, d = _canvas(accent, glow_box=(-60, -40, 520, 460))
    holdings = sorted((h for h in holdings if float(h[2]) > 0 or float(h[1]) > 0),
                      key=lambda h: float(h[2]), reverse=True)
    total = float(total_usd)

    # Left: heading, total, chips
    x0 = 96
    d.text((x0, 96), heading, font=_fit(d, heading, "Inter.ttf", b"Medium", 540, 28, 18), fill=MUTED)
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


def _emblem(img, x, y, size, accent):
    """Rounded-square Vex mark: accent gradient tile with a white V."""
    tile = Image.new("RGB", (size, size), accent)
    top = tuple(min(255, int(c + (255 - c) * 0.35)) for c in accent)
    td = ImageDraw.Draw(tile)
    for i in range(size):
        t = i / size
        td.line([(0, i), (size, i)], fill=tuple(int(top[k] * (1 - t) + accent[k] * t) for k in range(3)))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=size // 4, fill=255)
    shadow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(shadow).rounded_rectangle([x + 6, y + 14, x + size + 6, y + size + 14], radius=size // 4, fill=60)
    img.paste(Image.composite(Image.new("RGB", (W, H), accent), img, shadow.filter(ImageFilter.GaussianBlur(18))))
    img.paste(tile, (x, y), mask)
    d = ImageDraw.Draw(img)
    s = size
    d.line([(x + s * 0.28, y + s * 0.30), (x + s * 0.50, y + s * 0.72), (x + s * 0.72, y + s * 0.30)],
           fill=(255, 255, 255), width=max(6, s // 11), joint="curve")
    return d


def render_banner(title: str, subtitle: str, accent=None, brand: str = "@UseVexBot",
                  tagline: str = "Secure Multi-Chain Wallet", kicker: str = "VEX") -> bytes:
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
    size = 190
    d = _emblem(img, (px0 + px1) // 2 - size // 2, (py0 + py1) // 2 - size // 2 - 6, size, accent)

    fy = 510
    d.ellipse([x0 + 2, fy - 6, x0 + 14, fy + 6], fill=accent)
    d.text((x0 + 26, fy), tagline, font=_font("Inter.ttf", 21, b"Medium"), fill=SOFT, anchor="lm")
    if brand:
        d.text((W - 96, fy), brand, font=_font("Inter.ttf", 24, b"Bold"), fill=TEXT, anchor="rm")
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()
