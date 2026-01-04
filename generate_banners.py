#!/usr/bin/env python3
"""Generate dark theme banner images for VM Depo Bot."""

from PIL import Image, ImageDraw, ImageFont
import os

ASSETS_DIR = "assets"
BOT_USERNAME = "@VMDepoBot"

BANNERS = {
    "welcome": {
        "title": "VM DEPO BOT",
        "subtitle": "Secure Crypto Wallet",
        "tagline": "Securely Made By Venom",
        "icon": "\U0001F3E6"
    },
    "deposit": {
        "title": "DEPOSIT",
        "subtitle": "Receive Crypto",
        "icon": "\U0001F4E5"
    },
    "withdraw": {
        "title": "WITHDRAW",
        "subtitle": "Send Crypto",
        "icon": "\U0001F4E4"
    },
    "balance": {
        "title": "BALANCE",
        "subtitle": "View Assets",
        "icon": "\U0001F4CA"
    },
    "wallets": {
        "title": "WALLETS",
        "subtitle": "Manage Wallets",
        "icon": "\U0001F4B3"
    },
    "convert": {
        "title": "CONVERT",
        "subtitle": "Swap Tokens",
        "icon": "\U0001F504"
    },
    "generate": {
        "title": "GENERATE",
        "subtitle": "Create Wallet",
        "icon": "\u2795"
    },
    "tokens": {
        "title": "TOKENS",
        "subtitle": "Token Balances",
        "icon": "\U0001F4B0"
    },
    "help": {
        "title": "HELP",
        "subtitle": "Bot Guide",
        "icon": "\u2753"
    },
    "transaction": {
        "title": "TRANSACTION",
        "subtitle": "Activity Detected",
        "icon": "\U0001F514"
    }
}

DARK_BG = (18, 18, 24)
ACCENT_COLOR = (99, 102, 241)
ACCENT_LIGHT = (129, 140, 248)
TEXT_WHITE = (255, 255, 255)
TEXT_GRAY = (156, 163, 175)
GRADIENT_TOP = (30, 30, 45)
GRADIENT_BOTTOM = (15, 15, 22)


def create_gradient(width, height, top_color, bottom_color):
    """Create a vertical gradient image."""
    img = Image.new('RGB', (width, height))
    for y in range(height):
        ratio = y / height
        r = int(top_color[0] * (1 - ratio) + bottom_color[0] * ratio)
        g = int(top_color[1] * (1 - ratio) + bottom_color[1] * ratio)
        b = int(top_color[2] * (1 - ratio) + bottom_color[2] * ratio)
        for x in range(width):
            img.putpixel((x, y), (r, g, b))
    return img


def draw_rounded_rect(draw, coords, radius, fill):
    """Draw a rounded rectangle."""
    x1, y1, x2, y2 = coords
    draw.rectangle([x1 + radius, y1, x2 - radius, y2], fill=fill)
    draw.rectangle([x1, y1 + radius, x2, y2 - radius], fill=fill)
    draw.ellipse([x1, y1, x1 + 2*radius, y1 + 2*radius], fill=fill)
    draw.ellipse([x2 - 2*radius, y1, x2, y1 + 2*radius], fill=fill)
    draw.ellipse([x1, y2 - 2*radius, x1 + 2*radius, y2], fill=fill)
    draw.ellipse([x2 - 2*radius, y2 - 2*radius, x2, y2], fill=fill)


def create_banner(name, config, width=800, height=400):
    """Create a single banner image."""
    img = create_gradient(width, height, GRADIENT_TOP, GRADIENT_BOTTOM)
    draw = ImageDraw.Draw(img)

    draw_rounded_rect(draw, (30, 30, width - 30, height - 30), 20, (25, 25, 35))

    for i in range(3):
        alpha = 80 - i * 20
        draw.line([(50, 80 + i * 100), (width - 50, 80 + i * 100)],
                  fill=(99, 102, 241, alpha), width=1)

    try:
        title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 72)
        subtitle_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
        username_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
    except OSError:
        title_font = ImageFont.load_default()
        subtitle_font = ImageFont.load_default()
        username_font = ImageFont.load_default()

    title = config["title"]
    subtitle = config["subtitle"]

    title_bbox = draw.textbbox((0, 0), title, font=title_font)
    title_width = title_bbox[2] - title_bbox[0]
    title_x = (width - title_width) // 2
    title_y = height // 2 - 50

    for offset in [(2, 2), (-2, -2), (2, -2), (-2, 2)]:
        draw.text((title_x + offset[0], title_y + offset[1]), title,
                  font=title_font, fill=(20, 20, 30))

    draw.text((title_x, title_y), title, font=title_font, fill=TEXT_WHITE)

    subtitle_bbox = draw.textbbox((0, 0), subtitle, font=subtitle_font)
    subtitle_width = subtitle_bbox[2] - subtitle_bbox[0]
    subtitle_x = (width - subtitle_width) // 2
    subtitle_y = title_y + 90

    draw.text((subtitle_x, subtitle_y), subtitle, font=subtitle_font, fill=TEXT_GRAY)

    tagline = config.get("tagline")
    if tagline:
        try:
            tagline_font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf", 18
            )
        except OSError:
            tagline_font = subtitle_font
        tagline_bbox = draw.textbbox((0, 0), tagline, font=tagline_font)
        tagline_width = tagline_bbox[2] - tagline_bbox[0]
        tagline_x = (width - tagline_width) // 2
        tagline_y = subtitle_y + 40
        draw.text((tagline_x, tagline_y), tagline, font=tagline_font, fill=ACCENT_LIGHT)

    draw_rounded_rect(draw, (width // 2 - 60, title_y - 80, width // 2 + 60, title_y - 20),
                      10, ACCENT_COLOR)

    accent_line_y = subtitle_y + 50
    draw.rectangle([width // 2 - 80, accent_line_y, width // 2 + 80, accent_line_y + 4],
                   fill=ACCENT_COLOR)

    username_bbox = draw.textbbox((0, 0), BOT_USERNAME, font=username_font)
    username_width = username_bbox[2] - username_bbox[0]
    draw.text((width - username_width - 50, height - 60), BOT_USERNAME,
              font=username_font, fill=ACCENT_LIGHT)

    return img


def main():
    """Generate all banner images."""
    os.makedirs(ASSETS_DIR, exist_ok=True)

    for name, config in BANNERS.items():
        print(f"Generating {name} banner...")
        img = create_banner(name, config)
        filepath = os.path.join(ASSETS_DIR, f"{name}.png")
        img.save(filepath, "PNG", quality=95)
        print(f"  Saved to {filepath}")

    print(f"\nGenerated {len(BANNERS)} banner images in {ASSETS_DIR}/")


if __name__ == "__main__":
    main()
