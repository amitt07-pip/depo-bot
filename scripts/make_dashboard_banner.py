"""Generate the light-theme Vex banners in assets/ (same style as the balance cards)."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from address_card import BANNER_ACCENTS, render_banner  # noqa: E402

ASSETS = os.path.join(ROOT, "assets")
HANDLE = "@UseVexBot"

BANNERS = {
    "welcome": ("VEX", "Secure Multi-Chain Wallet", "WELCOME TO"),
    "welcome_public": ("VEX", "Secure Multi-Chain Wallet", "WELCOME TO"),
    "balance": ("Balance", "Your Assets", "VEX"),
    "convert": ("Convert", "Swap Assets", "VEX"),
    "deposit": ("Deposit", "Receive Crypto", "VEX"),
    "generate": ("Generate", "New Wallet", "VEX"),
    "help": ("Help", "Commands & Guide", "VEX"),
    "profile": ("Profile", "Your Account", "VEX"),
    "tokens": ("Tokens", "Supported Assets", "VEX"),
    "transaction": ("Transaction", "Details", "VEX"),
    "wallets": ("Wallets", "Your Addresses", "VEX"),
    "withdraw": ("Withdraw", "Send Crypto", "VEX"),
}

if __name__ == "__main__":
    for name, (title, subtitle, kicker) in BANNERS.items():
        tagline = "Deposit \u00b7 Withdraw \u00b7 Swap" if name.startswith("welcome") else "Secure Multi-Chain Wallet"
        art = "welcome" if name.startswith("welcome") else name
        png = render_banner(title, subtitle, BANNER_ACCENTS.get(name), HANDLE, tagline, kicker, art)
        out = os.path.join(ASSETS, f"{name}.png")
        with open(out, "wb") as f:
            f.write(png)
        print("saved", out)
