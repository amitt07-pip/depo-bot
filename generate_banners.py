from PIL import Image, ImageDraw, ImageFont, ImageFilter
import os
import math

def create_modern_gradient(width, height, colors):
    """Create a multi-color gradient with smooth transitions."""
    img = Image.new('RGB', (width, height))
    pixels = img.load()
    
    for y in range(height):
        for x in range(width):
            t = (x + y * 0.5) / (width + height * 0.5)
            t = max(0, min(1, t))
            
            if t < 0.5:
                t2 = t * 2
                r = int(colors[0][0] * (1 - t2) + colors[1][0] * t2)
                g = int(colors[0][1] * (1 - t2) + colors[1][1] * t2)
                b = int(colors[0][2] * (1 - t2) + colors[1][2] * t2)
            else:
                t2 = (t - 0.5) * 2
                r = int(colors[1][0] * (1 - t2) + colors[2][0] * t2)
                g = int(colors[1][1] * (1 - t2) + colors[2][1] * t2)
                b = int(colors[1][2] * (1 - t2) + colors[2][2] * t2)
            
            pixels[x, y] = (r, g, b)
    
    return img

def add_noise_texture(img, intensity=10):
    """Add subtle noise texture for depth."""
    import random
    pixels = img.load()
    width, height = img.size
    for y in range(height):
        for x in range(width):
            r, g, b = pixels[x, y]
            noise = random.randint(-intensity, intensity)
            r = max(0, min(255, r + noise))
            g = max(0, min(255, g + noise))
            b = max(0, min(255, b + noise))
            pixels[x, y] = (r, g, b)
    return img

def add_glitch_effect(img, accent_color, intensity=3):
    """Add a subtle glitch/scan line effect."""
    import random
    width, height = img.size
    pixels = img.load()
    
    for i in range(intensity):
        y = random.randint(0, height - 1)
        shift = random.randint(2, 8)
        for x in range(width - shift):
            pixels[x, y] = pixels[x + shift, y]
    
    for y in range(0, height, 4):
        for x in range(width):
            r, g, b = pixels[x, y]
            pixels[x, y] = (max(0, r - 5), max(0, g - 5), max(0, b - 5))
    
    draw = ImageDraw.Draw(img)
    for i in range(2):
        y = random.randint(height // 4, 3 * height // 4)
        glitch_color = (accent_color[0], accent_color[1], accent_color[2])
        x_start = random.randint(0, width // 4)
        x_end = x_start + random.randint(50, 150)
        draw.line([(x_start, y), (x_end, y)], fill=glitch_color, width=1)
        draw.line([(width - x_end, y + 2), (width - x_start, y + 2)], fill=glitch_color, width=1)
    
    return img

def draw_glow_circle(draw, center, radius, color, alpha_steps=20):
    """Draw a glowing circle effect."""
    for i in range(alpha_steps, 0, -1):
        r = radius + (alpha_steps - i) * 3
        alpha = int(30 * (i / alpha_steps))
        glow_color = (color[0], color[1], color[2])
        draw.ellipse([center[0] - r, center[1] - r, center[0] + r, center[1] + r], 
                     fill=None, outline=glow_color, width=2)

def create_banner(title, subtitle, filename, accent_color, gradient_colors):
    """Create a professional banner image with modern design."""
    width, height = 800, 400
    
    img = create_modern_gradient(width, height, gradient_colors)
    img = add_noise_texture(img, 5)
    img = add_glitch_effect(img, accent_color, intensity=4)
    
    draw = ImageDraw.Draw(img)
    
    draw.rounded_rectangle([(40, 40), (width - 40, height - 40)], 
                          radius=20, outline=accent_color, width=2)
    
    corner_size = 25
    draw.line([(50, 50), (50 + corner_size, 50)], fill=accent_color, width=2)
    draw.line([(50, 50), (50, 50 + corner_size)], fill=accent_color, width=2)
    draw.line([(width - 50 - corner_size, 50), (width - 50, 50)], fill=accent_color, width=2)
    draw.line([(width - 50, 50), (width - 50, 50 + corner_size)], fill=accent_color, width=2)
    draw.line([(50, height - 50 - corner_size), (50, height - 50)], fill=accent_color, width=2)
    draw.line([(50, height - 50), (50 + corner_size, height - 50)], fill=accent_color, width=2)
    draw.line([(width - 50, height - 50 - corner_size), (width - 50, height - 50)], fill=accent_color, width=2)
    draw.line([(width - 50 - corner_size, height - 50), (width - 50, height - 50)], fill=accent_color, width=2)
    
    try:
        title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 52)
        subtitle_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
        handle_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
        tagline_font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-BoldItalic.ttf", 18)
    except:
        title_font = ImageFont.load_default()
        subtitle_font = ImageFont.load_default()
        handle_font = ImageFont.load_default()
        tagline_font = ImageFont.load_default()
    
    title_bbox = draw.textbbox((0, 0), title, font=title_font)
    title_width = title_bbox[2] - title_bbox[0]
    title_x = (width - title_width) // 2
    title_y = 130
    
    for offset in range(8, 0, -2):
        shadow_color = (accent_color[0]//4, accent_color[1]//4, accent_color[2]//4)
        draw.text((title_x, title_y + offset), title, font=title_font, fill=shadow_color)
    
    draw.text((title_x, title_y), title, font=title_font, fill=(255, 255, 255))
    
    subtitle_bbox = draw.textbbox((0, 0), subtitle, font=subtitle_font)
    subtitle_width = subtitle_bbox[2] - subtitle_bbox[0]
    subtitle_x = (width - subtitle_width) // 2
    subtitle_y = 200
    draw.text((subtitle_x, subtitle_y), subtitle, font=subtitle_font, fill=(200, 200, 220))
    
    line_y = 260
    line_width = 200
    draw.line([(width//2 - line_width//2, line_y), (width//2 + line_width//2, line_y)], 
              fill=accent_color, width=2)
    
    tagline = "Securely Made By Venom"
    draw.text((70, height - 65), tagline, font=tagline_font, fill=accent_color)
    
    handle = "@VMDepoBot"
    handle_bbox = draw.textbbox((0, 0), handle, font=handle_font)
    handle_width = handle_bbox[2] - handle_bbox[0]
    draw.text((width - handle_width - 70, height - 65), handle, font=handle_font, fill=accent_color)
    
    assets_dir = "/home/ubuntu/repos/depo-bot/assets"
    os.makedirs(assets_dir, exist_ok=True)
    img.save(os.path.join(assets_dir, filename), quality=95)
    print(f"Created {filename}")

banners = [
    ("VM DEPO BOT", "Secure Crypto Wallet", "welcome.png", 
     (99, 102, 241), [(10, 10, 30), (20, 20, 50), (30, 25, 60)]),
    
    ("BALANCE", "View Your Assets", "balance.png", 
     (34, 197, 94), [(10, 25, 20), (15, 40, 30), (20, 50, 35)]),
    
    ("DEPOSIT", "Receive Crypto", "deposit.png", 
     (59, 130, 246), [(10, 15, 35), (15, 25, 55), (20, 35, 70)]),
    
    ("WITHDRAW", "Send Crypto", "withdraw.png", 
     (239, 68, 68), [(35, 10, 15), (50, 15, 20), (60, 20, 25)]),
    
    ("WALLETS", "Manage Wallets", "wallets.png", 
     (168, 85, 247), [(25, 10, 35), (35, 15, 50), (45, 20, 60)]),
    
    ("CONVERT", "Swap Assets", "convert.png", 
     (236, 72, 153), [(35, 10, 25), (50, 15, 35), (60, 20, 45)]),
    
    ("GENERATE", "Create Wallet", "generate.png", 
     (20, 184, 166), [(10, 30, 28), (15, 45, 40), (20, 55, 50)]),
    
    ("HELP", "Support & Guide", "help.png", 
     (251, 191, 36), [(30, 25, 10), (45, 38, 15), (55, 48, 20)]),
    
    ("TOKENS", "Supported Assets", "tokens.png", 
     (249, 115, 22), [(35, 20, 10), (50, 30, 15), (60, 38, 20)]),
    
    ("TRANSACTION", "Activity Log", "transaction.png", 
     (139, 92, 246), [(20, 15, 35), (30, 22, 50), (40, 28, 60)]),
]

for title, subtitle, filename, accent, gradient in banners:
    create_banner(title, subtitle, filename, accent, gradient)

def create_profile_picture():
    """Create a professional profile picture matching the banner theme."""
    size = 512
    accent_color = (99, 102, 241)
    gradient_colors = [(10, 10, 30), (20, 20, 50), (30, 25, 60)]
    
    img = create_modern_gradient(size, size, gradient_colors)
    img = add_noise_texture(img, 5)
    img = add_glitch_effect(img, accent_color, intensity=3)
    
    draw = ImageDraw.Draw(img)
    
    draw.rounded_rectangle([(40, 40), (size - 40, size - 40)], 
                          radius=25, outline=accent_color, width=3)
    
    corner_size = 30
    draw.line([(50, 50), (50 + corner_size, 50)], fill=accent_color, width=2)
    draw.line([(50, 50), (50, 50 + corner_size)], fill=accent_color, width=2)
    draw.line([(size - 50 - corner_size, 50), (size - 50, 50)], fill=accent_color, width=2)
    draw.line([(size - 50, 50), (size - 50, 50 + corner_size)], fill=accent_color, width=2)
    draw.line([(50, size - 50 - corner_size), (50, size - 50)], fill=accent_color, width=2)
    draw.line([(50, size - 50), (50 + corner_size, size - 50)], fill=accent_color, width=2)
    draw.line([(size - 50, size - 50 - corner_size), (size - 50, size - 50)], fill=accent_color, width=2)
    draw.line([(size - 50 - corner_size, size - 50), (size - 50, size - 50)], fill=accent_color, width=2)
    
    try:
        title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 100)
        subtitle_font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-BoldItalic.ttf", 32)
    except:
        title_font = ImageFont.load_default()
        subtitle_font = ImageFont.load_default()
    
    text = "VM"
    text_bbox = draw.textbbox((0, 0), text, font=title_font)
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]
    text_x = (size - text_width) // 2
    text_y = (size - text_height) // 2 - 50
    
    for offset in range(10, 0, -2):
        glow_alpha = int(40 * (offset / 10))
        glow_color = (accent_color[0]//3, accent_color[1]//3, accent_color[2]//3)
        draw.text((text_x - offset//2, text_y + offset), text, font=title_font, fill=glow_color)
        draw.text((text_x + offset//2, text_y + offset), text, font=title_font, fill=glow_color)
    
    draw.text((text_x, text_y), text, font=title_font, fill=(255, 255, 255))
    
    subtitle = "DEPO BOT"
    sub_bbox = draw.textbbox((0, 0), subtitle, font=subtitle_font)
    sub_width = sub_bbox[2] - sub_bbox[0]
    sub_x = (size - sub_width) // 2
    sub_y = text_y + text_height + 20
    draw.text((sub_x, sub_y), subtitle, font=subtitle_font, fill=accent_color)
    
    line_y = sub_y + 50
    line_width = 150
    draw.line([(size//2 - line_width//2, line_y), (size//2 + line_width//2, line_y)], 
              fill=accent_color, width=2)
    
    tagline = "Securely Made By Venom"
    try:
        tagline_font = ImageFont.truetype("/usr/share/fonts/truetype/liberation/LiberationSans-BoldItalic.ttf", 18)
    except:
        tagline_font = ImageFont.load_default()
    tag_bbox = draw.textbbox((0, 0), tagline, font=tagline_font)
    tag_width = tag_bbox[2] - tag_bbox[0]
    tag_x = (size - tag_width) // 2
    tag_y = size - 85
    draw.text((tag_x, tag_y), tagline, font=tagline_font, fill=(150, 150, 180))
    
    assets_dir = "/home/ubuntu/repos/depo-bot/assets"
    os.makedirs(assets_dir, exist_ok=True)
    img.save(os.path.join(assets_dir, "profile.png"), quality=95)
    print("Created profile.png")

create_profile_picture()

print("\nAll professional banners and profile picture created successfully!")
