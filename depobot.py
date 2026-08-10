import os
import io
import json
import html
import sqlite3
import logging
import hashlib
import base64
import asyncio
import aiohttp
import re
import inspect
from typing import Optional
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

try:
    import qrcode
    QR_AVAILABLE = True
except ImportError:
    QR_AVAILABLE = False

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, MessageEntity
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from eth_account import Account
from web3 import Web3
from solders.keypair import Keypair
from tronpy.keys import PrivateKey as TronPrivateKey

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

# Configure logging - cleaner output
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
    level=logging.INFO
)
logger = logging.getLogger(__name__)
import traceback

# Suppress noisy HTTP request logs from httpx and telegram
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


# ---------------------------------------------------------------------------
# UI styling helpers
# ---------------------------------------------------------------------------
# Map plain ASCII to Unicode "Mathematical Sans-Serif" glyphs so headings get a
# modern, premium look without relying on dash/line separators.
_BOLD_UPPER = 0x1D5D4   # 𝗔
_BOLD_LOWER = 0x1D5EE   # 𝗮
_BOLD_DIGIT = 0x1D7EC   # 𝟬


def fancy(text: str) -> str:
    """Render text in a modern sans-serif bold Unicode font for headings."""
    out = []
    for ch in text:
        if "A" <= ch <= "Z":
            out.append(chr(_BOLD_UPPER + (ord(ch) - 65)))
        elif "a" <= ch <= "z":
            out.append(chr(_BOLD_LOWER + (ord(ch) - 97)))
        elif "0" <= ch <= "9":
            out.append(chr(_BOLD_DIGIT + (ord(ch) - 48)))
        else:
            out.append(ch)
    return "".join(out)


def heading(emoji: str, title: str) -> str:
    """Build a styled section heading: emoji + fancy font title."""
    title = fancy(title)
    return f"{emoji}  {title}" if emoji else title


# Soft dot divider used sparingly instead of heavy dash/line separators.
SOFT_DIVIDER = "·  ·  ·"


# ---------------------------------------------------------------------------
# Premium custom emoji (Telegram Premium) — rendered via HTML <tg-emoji> tags.
# UI icons come from the "Telegram Android Icons" pack, keyed by the standard
# emoji they fall back to. Token icons come from the "KkPay" pack.
# ---------------------------------------------------------------------------
# name -> (custom_emoji_id or None, fallback standard emoji)
UI_EMOJI = {
    "home": ("5967822972931542886", "\U0001F3E0"),       # 🏠
    "search": ("5874960879434338403", "\U0001F50E"),     # 🔎
    "deposit": ("5877307202888273539", "\U0001F4E5"),    # 📥
    "withdraw": ("5877540355187937244", "\U0001F4E4"),   # 📤
    "money": ("5811989245761426317", "\U0001F4B0"),      # 💰
    "wallet": ("5769403330761593044", "\U0001F45B"),     # 👛
    "portfolio": ("5967389567781703494", "\U0001F4BC"),  # 💼
    "settings": ("5877260593903177342", "\u2699\ufe0f"),  # ⚙️
    "convert": ("5839200986022812209", "\U0001F504"),    # 🔄
    "refresh": ("6005843436479975944", "\U0001F501"),    # 🔁
    "help": ("5897850551156084824", "\U0001F4D6"),       # 📖
    "success": ("5776375003280838798", "\u2705"),         # ✅
    "check": ("5825794181183836432", "\u2714\ufe0f"),     # ✔️
    "error": ("5778527486270770928", "\u274c"),           # ❌
    "time": ("5778605968208170641", "\U0001F552"),       # 🕒
    "link": ("5877465816030515018", "\U0001F517"),       # 🔗
    "explorer": ("5879585266426973039", "\U0001F310"),   # 🌐
    "address": ("5944940516754853337", "\U0001F4CD"),    # 📍
    "warning": ("5881702736843511327", "\u26a0\ufe0f"),   # ⚠️
    "new": ("5886306834410640699", "\U0001F195"),        # 🆕
    "cash": ("5967390100357648692", "\U0001F4B5"),       # 💵
    "card": ("5967548335542767952", "\U0001F4B3"),       # 💳
    "coin": ("5992430854909989581", "\U0001FA99"),       # 🪙
    "chart": ("5877485980901971030", "\U0001F4CA"),      # 📊
    "balance": ("5778546461436284629", "\U0001F50B"),    # 🔋
    "user": ("5771887475421090729", "\U0001F464"),       # 👤
    "note": ("5886330010054168711", "\U0001F4DD"),       # 📝
    "gem": ("5963312935148195483", "\U0001F48E"),        # 💎
    "bell": ("5909201569898827582", "\U0001F514"),       # 🔔
    "idea": (None, "\U0001F4A1"),                          # 💡 (no custom icon)
}

TOKEN_EMOJI = {
    "USDT": "6213220344614882714",
    "USDC": "6255735869395702119",
    "ETH": "6314132993531187956",
    "BNB": "6253755610299372930",
    "MATIC": "6253279916901535974",
    "SOL": "6255864821493796954",
    "TRX": "6314155632303804943",
    "LTC": "6253341983473931070",
    "BTC": "5388637433246003258",  # IconsEmoji_JABA Bitcoin icon
}

# Which token icon represents each network.
NETWORK_TOKEN_ICON = {
    "ETH": "ETH",
    "BSC": "BNB",
    "POLYGON": "MATIC",
    "SOLANA": "SOL",
    "TRON": "TRX",
    "LTC": "LTC",
    "BTC": "BTC",
}


def ce(emoji_id: str, fallback: str) -> str:
    """Build a custom-emoji HTML tag with a standard-emoji fallback."""
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def ui(name: str) -> str:
    """Return the premium UI icon for a named slot (HTML), else its fallback emoji."""
    entry = UI_EMOJI.get(name)
    if not entry:
        return ""
    eid, fallback = entry
    return ce(eid, fallback) if eid else fallback


def tok(symbol: str, fallback: str = "\U0001FA99") -> str:
    """Return the premium token icon for a symbol (HTML), else a coin emoji."""
    eid = TOKEN_EMOJI.get((symbol or "").upper())
    return ce(eid, fallback) if eid else fallback


def net_icon(network: str) -> str:
    """Premium token icon representing a network."""
    sym = NETWORK_TOKEN_ICON.get(network)
    return tok(sym) if sym else ui("explorer")


def esc(value) -> str:
    """HTML-escape a dynamic value for safe interpolation."""
    return html.escape(str(value), quote=False)


def h_html(name: str, title: str) -> str:
    """HTML section heading: premium icon + fancy-font title."""
    return f"{ui(name)}  {fancy(title)}"


# Reverse lookup: standard emoji char -> premium custom emoji id.
_EMOJI_TO_ID = {fb: eid for eid, fb in UI_EMOJI.values() if eid}

_MD_CODE_RE = re.compile(r"`([^`]*)`")
_MD_BOLD_RE = re.compile(r"\*(.+?)\*", re.S)
_MD_ITALIC_RE = re.compile(r"_(.+?)_", re.S)


def _emojify(text: str) -> str:
    """Swap standard emoji for their premium custom-emoji HTML tags."""
    for ch, eid in _EMOJI_TO_ID.items():
        if ch in text:
            text = text.replace(ch, ce(eid, ch))
    return text


def tg_html(text: str) -> str:
    """Convert the bot's Markdown subset (*bold*, _italic_, `code`) to HTML and
    upgrade standard emoji to premium custom emoji. Safe for already-plain text."""
    codes = []

    def _stash(m):
        codes.append(m.group(1))
        return f"\x00C{len(codes) - 1}\x00"

    t = _MD_CODE_RE.sub(_stash, text)
    t = html.escape(t, quote=False)
    t = _MD_BOLD_RE.sub(r"<b>\1</b>", t)
    t = _MD_ITALIC_RE.sub(r"<i>\1</i>", t)
    t = _emojify(t)

    def _unstash(m):
        return f"<code>{html.escape(codes[int(m.group(1))], quote=False)}</code>"

    return re.sub(r"\x00C(\d+)\x00", _unstash, t)


# Whether the installed python-telegram-bot supports custom emoji on buttons
# (Bot API 9.4, Feb 2026). Falls back to a standard-emoji prefix otherwise.
_IKB_SUPPORTS_ICON = "icon_custom_emoji_id" in inspect.signature(
    InlineKeyboardButton.__init__
).parameters


def net_emoji_id(network: str):
    """Premium custom-emoji id representing a network (for buttons)."""
    sym = NETWORK_TOKEN_ICON.get(network)
    return TOKEN_EMOJI.get(sym) if sym else None


def ikb(text, callback_data=None, url=None, ui_name=None, token=None,
        emoji_id=None, emoji_fallback="", **kwargs):
    """Build an InlineKeyboardButton with a premium custom emoji icon when the
    Bot API/library supports it, otherwise prefix a standard emoji to the label."""
    if ui_name:
        entry = UI_EMOJI.get(ui_name)
        if entry:
            emoji_id = emoji_id or entry[0]
            emoji_fallback = emoji_fallback or entry[1]
    if token:
        emoji_id = emoji_id or TOKEN_EMOJI.get(token.upper())
        emoji_fallback = emoji_fallback or "\U0001FA99"
    if callback_data is not None:
        kwargs["callback_data"] = callback_data
    if url is not None:
        kwargs["url"] = url
    if emoji_id and _IKB_SUPPORTS_ICON:
        return InlineKeyboardButton(text, icon_custom_emoji_id=emoji_id, **kwargs)
    label = f"{emoji_fallback} {text}".strip() if emoji_fallback else text
    return InlineKeyboardButton(label, **kwargs)


def _install_markdown_to_html_bridge():
    """Globally route legacy `parse_mode="Markdown"` messages through tg_html() so
    every screen renders as modern HTML with premium custom emoji, without having to
    touch each of the ~120 call sites. PTB shortcut methods (reply_text, edit_caption,
    reply_photo, edit_message_text, ...) all delegate to these Bot methods, so wrapping
    them here covers all of them. Calls that already pass parse_mode="HTML" are left
    untouched."""
    from telegram import Bot

    def _wrap(method_name, text_kw):
        orig = getattr(Bot, method_name)

        async def wrapper(self, *args, **kwargs):
            if kwargs.get("parse_mode") == "Markdown":
                value = kwargs.get(text_kw)
                if isinstance(value, str):
                    kwargs[text_kw] = tg_html(value)
                    kwargs["parse_mode"] = "HTML"
            return await orig(self, *args, **kwargs)

        wrapper.__name__ = getattr(orig, "__name__", method_name)
        wrapper.__doc__ = getattr(orig, "__doc__", None)
        setattr(Bot, method_name, wrapper)

    _wrap("send_message", "text")
    _wrap("send_photo", "caption")
    _wrap("edit_message_text", "text")
    _wrap("edit_message_caption", "caption")


_install_markdown_to_html_bridge()


def get_banner_path(name: str) -> str:
    """Get the path to a banner image."""
    return os.path.join(ASSETS_DIR, f"{name}.png")


def generate_qr_code(data: str) -> io.BytesIO:
    """Generate a QR code image for the given data and return as BytesIO."""
    if not QR_AVAILABLE:
        return None
    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        bio = io.BytesIO()
        img.save(bio, format="PNG")
        bio.seek(0)
        return bio
    except Exception as e:
        logger.warning(f"QR code generation failed: {e}")
        return None


async def edit_message_caption(query, caption: str, reply_markup):
    """Edit only the message caption and keyboard (keeps same image). For menu navigation within same section."""
    try:
        if query.message.photo:
            await query.message.edit_caption(
                caption=caption,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
        else:
            await query.message.edit_text(
                text=caption,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
    except Exception:
        pass


async def send_new_message_with_banner(query, banner_name: str, caption: str, reply_markup, parse_mode: str = "Markdown"):
    """Delete current message and send new one with banner image. For changing sections or returning to main menu."""
    chat_id = query.message.chat_id
    try:
        await query.message.delete()
    except Exception:
        pass
    
    banner_path = get_banner_path(banner_name)
    if os.path.exists(banner_path):
        with open(banner_path, "rb") as photo:
            await query.get_bot().send_photo(
                chat_id=chat_id,
                photo=photo,
                caption=caption,
                parse_mode=parse_mode,
                reply_markup=reply_markup
            )
    else:
        await query.get_bot().send_message(
            chat_id=chat_id,
            text=caption,
            parse_mode=parse_mode,
            reply_markup=reply_markup
        )


async def edit_message_with_banner(
    query, banner_name: str, caption: str, reply_markup, parse_mode: str = "Markdown"
):
    """Edit the message caption and keyboard. Falls back to delete/send if needed."""
    try:
        if query.message.photo:
            await query.message.edit_caption(
                caption=caption,
                parse_mode=parse_mode,
                reply_markup=reply_markup
            )
        else:
            await query.message.edit_text(
                text=caption,
                parse_mode=parse_mode,
                reply_markup=reply_markup
            )
    except Exception:
        await send_new_message_with_banner(query, banner_name, caption, reply_markup, parse_mode)


BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ALLOWED_USER_ID = 6643621069
ALLOWED_CHAT_ID = -1002215462357

USER_ACCESS = {
    6643621069: [1, 2],
    7103743713: [2],
}
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "default_key_change_me_32bytes!")
TRONGRID_API_KEY = os.getenv("TRONGRID_API_KEY", "")

wallet_balances_cache = {}
wallet_cache_initialized = False  # Flag to track if cache has been initialized on first run
notification_cooldowns = {}  # Track last notification time per token to prevent spam
notifications_enabled = True  # Global flag to enable/disable deposit notifications

NETWORKS = {
    "ETH": {
        "name": "Ethereum",
        "rpc": "https://ethereum.publicnode.com",
        "rpc_fallbacks": [
            "https://1rpc.io/eth",
            "https://eth.drpc.org",
            "https://rpc.builder0x69.io"
        ],
        "chain_id": 1,
        "symbol": "ETH",
        "explorer": "https://etherscan.io",
        "type": "evm",
        "icon": "\u26aa"
    },
    "BSC": {
        "name": "BNB Chain",
        "rpc": "https://bsc-dataseed.binance.org",
        "rpc_fallbacks": [
            "https://bsc-dataseed1.defibit.io",
            "https://bsc-dataseed1.ninicoin.io",
            "https://bsc.publicnode.com"
        ],
        "chain_id": 56,
        "symbol": "BNB",
        "explorer": "https://bscscan.com",
        "type": "evm",
        "icon": "\U0001F7E1"
    },
    "POLYGON": {
        "name": "Polygon",
        "rpc": "https://polygon-bor-rpc.publicnode.com",
        "rpc_fallbacks": [
            "https://polygon.publicnode.com",
            "https://1rpc.io/matic",
            "https://polygon-rpc.com",
            "https://rpc-mainnet.maticvigil.com",
            "https://matic-mainnet.chainstacklabs.com",
            "https://polygon.blockpi.network/v1/rpc/public",
            "https://rpc.ankr.com/polygon"
        ],
        "chain_id": 137,
        "symbol": "MATIC",
        "explorer": "https://polygonscan.com",
        "type": "evm",
        "icon": "\U0001F7E3"
    },
    "SOLANA": {
        "name": "Solana",
        "rpc": "https://api.mainnet-beta.solana.com",
        "rpc_fallbacks": [
            "https://solana.publicnode.com",
            "https://solana-mainnet.rpc.extrnode.com"
        ],
        "symbol": "SOL",
        "explorer": "https://solscan.io",
        "type": "solana",
        "icon": "\U0001F7E2"
    },
    "TRON": {
        "name": "Tron",
        "rpc": "https://api.trongrid.io",
        "rpc_fallbacks": [],
        "symbol": "TRX",
        "explorer": "https://tronscan.org",
        "type": "tron",
        "icon": "\U0001F534"
    },
    "LTC": {
        "name": "Litecoin",
        "rpc": "https://ltc.getblock.io/mainnet/",
        "rpc_fallbacks": [],
        "symbol": "LTC",
        "explorer": "https://blockchair.com/litecoin",
        "type": "ltc",
        "icon": "\U0001F315"
    },
    "BTC": {
        "name": "Bitcoin",
        "rpc": "",
        "rpc_fallbacks": [],
        "symbol": "BTC",
        "explorer": "https://blockchair.com/bitcoin",
        "type": "btc",
        "icon": "\U0001F7E0"
    }
}

# Helper function to get Web3 with retry and fallback
def get_web3_with_retry(network: str, max_retries: int = 3):
    """Get a Web3 instance, trying fallback RPCs if the primary fails."""
    network_info = NETWORKS.get(network)
    if not network_info:
        return None
    
    rpcs = [network_info["rpc"]] + network_info.get("rpc_fallbacks", [])
    
    for rpc in rpcs:
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 10}))
            # Test connection
            w3.eth.block_number
            return w3
        except Exception as e:
            logger.warning(f"RPC {rpc} failed: {e}, trying next...")
            continue
    
    # If all fail, return the primary anyway (let the caller handle the error)
    return Web3(Web3.HTTPProvider(network_info["rpc"]))

TOKENS = {
    "ETH": {
        "name": "Ethereum",
        "symbol": "ETH",
        "icon": "\u26aa",
        "native": True,
        "networks": {
            "ETH": {"native": True, "decimals": 18}
        }
    },
    "BNB": {
        "name": "BNB",
        "symbol": "BNB",
        "icon": "\U0001F7E1",
        "native": True,
        "networks": {
            "BSC": {"native": True, "decimals": 18}
        }
    },
    "MATIC": {
        "name": "Polygon",
        "symbol": "MATIC",
        "icon": "\U0001F7E3",
        "native": True,
        "networks": {
            "POLYGON": {"native": True, "decimals": 18}
        }
    },
    "SOL": {
        "name": "Solana",
        "symbol": "SOL",
        "icon": "\U0001F7E2",
        "native": True,
        "networks": {
            "SOLANA": {"native": True, "decimals": 9}
        }
    },
    "TRX": {
        "name": "Tron",
        "symbol": "TRX",
        "icon": "\U0001F534",
        "native": True,
        "networks": {
            "TRON": {"native": True, "decimals": 6}
        }
    },
    "USDT": {
        "name": "Tether USD",
        "symbol": "USDT",
        "icon": "\U0001F4B5",
        "networks": {
            "ETH": {
                "address": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
                "decimals": 6
            },
            "BSC": {
                "address": "0x55d398326f99059fF775485246999027B3197955",
                "decimals": 18
            },
            "POLYGON": {
                "address": "0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
                "decimals": 6
            },
            "TRON": {
                "address": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
                "decimals": 6
            },
            "SOLANA": {
                "address": "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
                "decimals": 6
            }
        }
    },
    "USDC": {
        "name": "USD Coin",
        "symbol": "USDC",
        "icon": "\U0001F4B2",
        "networks": {
            "ETH": {
                "address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
                "decimals": 6
            },
            "BSC": {
                "address": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
                "decimals": 18
            },
            "POLYGON": {
                "address": "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
                "decimals": 6
            },
            "SOLANA": {
                "address": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                "decimals": 6
            }
        }
    },
    "LTC": {
        "name": "Litecoin",
        "symbol": "LTC",
        "icon": "\U0001F315",
        "native": True,
        "networks": {
            "LTC": {"native": True, "decimals": 8}
        }
    }
}

ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [
            {"name": "_to", "type": "address"},
            {"name": "_value", "type": "uint256"}
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function"
    }
]

WITHDRAW_TOKEN, WITHDRAW_NETWORK, WITHDRAW_CONFIRM_SELECTION, WITHDRAW_AMOUNT, WITHDRAW_ADDRESS, WITHDRAW_CONFIRM = range(6)
DEPOSIT_TOKEN, DEPOSIT_NETWORK, DEPOSIT_CONFIRM_SELECTION = range(6, 9)
GENERATE_NETWORK, GENERATE_CONFIRM = range(9, 11)
BALANCE_NETWORK = 11
CONVERT_FROM_ASSET, CONVERT_TO_ASSET, CONVERT_AMOUNT_AI = range(12, 15)
WITHDRAW_QUICK_NETWORK = 15

pending_withdrawals = {}


def clear_withdrawal_state(user_id, context):
    """Remove stale withdrawal data so the next withdrawal starts fresh."""
    for key in list(context.user_data.keys()):
        if key.startswith("withdraw_") and key != "withdraw_msg_id":
            del context.user_data[key]
    if user_id in pending_withdrawals:
        del pending_withdrawals[user_id]


TOKEN_ALIASES = {
    "usdt": "USDT", "tether": "USDT", "usd tether": "USDT", "ut": "USDT",
    "tether usd": "USDT", "usdt trc20": "USDT", "usdt bep20": "USDT", "usdterc20": "USDT",
    "usdc": "USDC", "usd coin": "USDC", "circle": "USDC", "uc": "USDC",
    "usd": "USDT", "dollar": "USDT", "dollars": "USDT",
    "eth": "ETH", "ethereum": "ETH", "ether": "ETH", "etherium": "ETH", "etherem": "ETH",
    "bnb": "BNB", "binance": "BNB", "binance coin": "BNB", "bsc": "BNB",
    "matic": "MATIC", "polygon": "MATIC", "pol": "MATIC", "poly": "MATIC",
    "sol": "SOL", "solana": "SOL", "solan": "SOL",
    "trx": "TRX", "tron": "TRX", "tronix": "TRX", "trn": "TRX",
    "ltc": "LTC", "litecoin": "LTC", "lite coin": "LTC", "lite": "LTC", "lc": "LTC",
}

NETWORK_ALIASES = {
    "eth": "ETH", "ethereum": "ETH", "eth mainnet": "ETH", "ethereum mainnet": "ETH",
    "erc20": "ETH", "erc-20": "ETH", "etherium": "ETH", "etherem": "ETH", "ether": "ETH",
    "bsc": "BSC", "binance": "BSC", "binance smart chain": "BSC", "bnb chain": "BSC", "bnb": "BSC",
    "bep20": "BSC", "bep-20": "BSC", "bnb smart chain": "BSC", "smartchain": "BSC", "binance chain": "BSC",
    "polygon": "POLYGON", "matic": "POLYGON", "poly": "POLYGON", "pol": "POLYGON",
    "matic network": "POLYGON", "polygon mainnet": "POLYGON", "polyg": "POLYGON",
    "solana": "SOLANA", "sol": "SOLANA", "solan": "SOLANA",
    "tron": "TRON", "trx": "TRON", "trc20": "TRON", "trc-20": "TRON", "tronix": "TRON", "trn": "TRON",
    "ltc": "LTC", "litecoin": "LTC", "lite": "LTC", "lite coin": "LTC",
    "btc": "BTC", "bitcoin": "BTC", "bit coin": "BTC", "bit": "BTC",
}

def detect_token_from_text(text: str):
    """Detect token from user input with fuzzy matching."""
    text_lower = text.lower().strip()
    if text_lower in TOKEN_ALIASES:
        return TOKEN_ALIASES[text_lower]
    for token in TOKENS.keys():
        if token.lower() == text_lower:
            return token
    for alias, token in TOKEN_ALIASES.items():
        if alias in text_lower or text_lower in alias:
            return token
    return None

def detect_network_from_text(text: str):
    """Detect network from user input with fuzzy matching."""
    text_lower = text.lower().strip()
    if text_lower in NETWORK_ALIASES:
        return NETWORK_ALIASES[text_lower]
    for network in NETWORKS.keys():
        if network.lower() == text_lower:
            return network
    for alias, network in NETWORK_ALIASES.items():
        if alias in text_lower or text_lower in alias:
            return network
    return None

def get_available_networks_for_token(token: str):
    """Get list of networks that support a given token."""
    token_info = TOKENS.get(token)
    if not token_info:
        return []
    return list(token_info.get("networks", {}).keys())

def detect_network_from_address(address: str):
    """Detect network type from wallet address format.
    
    Returns:
        - Single network key (e.g., 'TRON', 'SOLANA', 'LTC') if uniquely identifiable
        - List of possible networks (e.g., ['ETH', 'BSC', 'POLYGON']) for EVM addresses
        - None if address format is not recognized
    """
    address = address.strip()
    
    if address.startswith('T') and len(address) == 34:
        return 'TRON'
    
    if address.startswith('L') or address.startswith('M') or address.startswith('ltc1'):
        if len(address) >= 26 and len(address) <= 62:
            return 'LTC'

    if address.startswith('bc1q') or address.startswith('bc1p'):
        if 42 <= len(address) <= 62:
            return 'BTC'
    if address.startswith('1') or address.startswith('3'):
        if 25 <= len(address) <= 34 and re.match(r'^[1-9A-HJ-NP-Za-km-z]+$', address):
            return 'BTC'

    if len(address) >= 32 and len(address) <= 44:
        if re.match(r'^[1-9A-HJ-NP-Za-km-z]+$', address):
            if not address.startswith('0x') and not address.startswith('T'):
                return 'SOLANA'
    
    if address.startswith('0x') and len(address) == 42:
        return ['ETH', 'BSC', 'POLYGON']
    
    return None

def is_valid_address(address: str, network: str) -> bool:
    """Validate if address format is correct for the given network."""
    address = address.strip()
    
    if network in ['ETH', 'BSC', 'POLYGON']:
        return address.startswith('0x') and len(address) == 42
    elif network == 'TRON':
        return address.startswith('T') and len(address) == 34
    elif network == 'SOLANA':
        import re
        return len(address) >= 32 and len(address) <= 44 and re.match(r'^[1-9A-HJ-NP-Za-km-z]+$', address)
    elif network == 'LTC':
        return (address.startswith('L') or address.startswith('M') or address.startswith('ltc1')) and len(address) >= 26
    elif network == 'BTC':
        if address.startswith('bc1q') or address.startswith('bc1p'):
            return 42 <= len(address) <= 62
        return (address.startswith('1') or address.startswith('3')) and 25 <= len(address) <= 34

    return False


EXPLORER_DOMAIN_NETWORK = {
    "etherscan.io": "ETH",
    "bscscan.com": "BSC",
    "polygonscan.com": "POLYGON",
    "solscan.io": "SOLANA",
    "tronscan.org": "TRON",
    "blockchair.com": None,  # resolved by path: /bitcoin or /litecoin
    "blockcypher.com": "BTC",
    "blockchain.com": "BTC",
    "mempool.space": "BTC",
}

TX_PATH_KEYWORDS = {"tx", "transaction", "transactions"}
ADDRESS_PATH_KEYWORDS = {"address", "account", "accounts", "addresses"}


def parse_explorer_link(text: str):
    """Parse a block explorer URL.

    Returns a tuple (kind, network, value) where kind is 'tx' or 'address',
    or None if the text is not a recognised explorer link.
    """
    text = text.strip()
    if "." not in text:
        return None

    url = text if "://" in text else "https://" + text
    try:
        parsed = urlparse(url)
    except Exception:
        return None

    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    host = host.split(":")[0]

    network = None
    for domain, net in EXPLORER_DOMAIN_NETWORK.items():
        if host == domain or host.endswith("." + domain):
            network = net
            break
    else:
        return None

    # blockchair.com serves multiple chains — resolve from path
    if network is None and host in ("blockchair.com",):
        path_lower = (parsed.path or "").lower()
        if "/bitcoin" in path_lower:
            network = "BTC"
        elif "/litecoin" in path_lower:
            network = "LTC"
        else:
            return None

    combined = (parsed.path or "") + "/" + (parsed.fragment or "")
    segments = [s for s in re.split(r"[/#?&=]", combined) if s]

    for i, seg in enumerate(segments):
        low = seg.lower()
        if low in TX_PATH_KEYWORDS and i + 1 < len(segments):
            return ("tx", network, segments[i + 1])
        if low in ADDRESS_PATH_KEYWORDS and i + 1 < len(segments):
            return ("address", network, segments[i + 1])
    return None


def detect_tx_hash(text: str):
    """Detect a raw transaction hash/signature.

    Returns (network_or_list, normalized_hash) or None.
    """
    t = text.strip()

    if re.fullmatch(r"0x[0-9a-fA-F]{64}", t):
        return (["ETH", "BSC", "POLYGON"], t)
    if re.fullmatch(r"[0-9a-fA-F]{64}", t):
        return (["TRON", "LTC", "BTC"], t)
    if re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{64,90}", t):
        return ("SOLANA", t)
    return None


def lookup_token_by_address(network: str, token_address: str):
    """Find a configured token (symbol + decimals) by its contract address."""
    if not token_address:
        return None
    ta = token_address.lower()
    for symbol, info in TOKENS.items():
        net = info.get("networks", {}).get(network)
        if net and net.get("address") and net["address"].lower() == ta:
            return {"symbol": symbol, "decimals": net.get("decimals", 18)}
    return None


def format_ist(ts) -> str:
    """Format a unix timestamp (seconds) as Indian Standard Time."""
    if not ts:
        return "Unknown"
    try:
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc) + timedelta(hours=5, minutes=30)
        return dt.strftime("%d %b %Y, %I:%M:%S %p") + " IST"
    except Exception:
        return "Unknown"


def tx_explorer_url(network: str, tx_hash: str) -> str:
    """Build the explorer URL for a transaction on a given network."""
    explorer = NETWORKS.get(network, {}).get("explorer", "")
    if network == "TRON":
        return f"{explorer}/#/transaction/{tx_hash}"
    if network in ("LTC", "BTC"):
        return f"{explorer}/transaction/{tx_hash}"
    return f"{explorer}/tx/{tx_hash}"


def address_explorer_url(network: str, address: str) -> str:
    """Build the explorer URL for an address on a given network."""
    explorer = NETWORKS.get(network, {}).get("explorer", "")
    if network == "TRON":
        return f"{explorer}/#/address/{address}"
    if network == "LTC":
        return f"{explorer}/address/{address}"
    return f"{explorer}/address/{address}"


COINGECKO_IDS = {
    "ETH": "ethereum",
    "BNB": "binancecoin",
    "MATIC": "matic-network",
    "SOL": "solana",
    "TRX": "tron",
    "LTC": "litecoin",
    "USDT": "tether",
    "USDC": "usd-coin",
}


class PriceFetcher:
    # Cache for prices with timestamp
    _price_cache = {}
    _cache_ttl = 300  # 5 minutes cache
    
    # Fallback prices (approximate, updated periodically)
    FALLBACK_PRICES = {
        "ETH": Decimal("3200"),
        "BNB": Decimal("600"),
        "MATIC": Decimal("0.5"),
        "SOL": Decimal("180"),
        "TRX": Decimal("0.12"),
        "LTC": Decimal("100"),
        "USDT": Decimal("1.0"),
        "USDC": Decimal("1.0"),
    }
    
    @staticmethod
    async def get_price(asset: str) -> Decimal:
        if asset in ["USDT", "USDC"]:
            return Decimal("1.0")
        
        # Check cache first
        import time
        cache_key = asset
        if cache_key in PriceFetcher._price_cache:
            cached_price, cached_time = PriceFetcher._price_cache[cache_key]
            if time.time() - cached_time < PriceFetcher._cache_ttl:
                return cached_price
        
        cg_id = COINGECKO_IDS.get(asset)
        if not cg_id:
            return PriceFetcher.FALLBACK_PRICES.get(asset, Decimal("0"))
        
        try:
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        price = data.get(cg_id, {}).get("usd", 0)
                        if price > 0:
                            price_decimal = Decimal(str(price))
                            # Cache the price
                            PriceFetcher._price_cache[cache_key] = (price_decimal, time.time())
                            return price_decimal
        except Exception as e:
            logger.warning(f"CoinGecko API error for {asset}: {e}")
        
        # Return fallback price if API fails
        return PriceFetcher.FALLBACK_PRICES.get(asset, Decimal("0"))

    @staticmethod
    async def get_conversion_rate(from_asset: str, to_asset: str) -> Decimal:
        if from_asset == to_asset:
            return Decimal("1.0")
        if from_asset in ["USDT", "USDC"] and to_asset in ["USDT", "USDC"]:
            return Decimal("1.0")
        from_price = await PriceFetcher.get_price(from_asset)
        to_price = await PriceFetcher.get_price(to_asset)
        if to_price == 0:
            return Decimal("0")
        return from_price / to_price

    @staticmethod
    async def calculate_conversion(from_asset: str, to_asset: str,
                                   amount: Decimal) -> tuple:
        rate = await PriceFetcher.get_conversion_rate(from_asset, to_asset)
        if rate == 0:
            return Decimal("0"), "0"
        to_amount = amount * rate
        to_amount = to_amount.quantize(Decimal("0.000001"))
        return to_amount, str(rate)


class WalletDatabase:
    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wallets.db")
        self.db_path = db_path
        self.init_db()

    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS wallets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                network TEXT NOT NULL,
                address TEXT NOT NULL,
                encrypted_private_key TEXT NOT NULL,
                interface_id INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, network, interface_id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                current_interface INTEGER NOT NULL DEFAULT 1,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("PRAGMA table_info(wallets)")
        columns = [col[1] for col in cursor.fetchall()]
        if "interface_id" not in columns:
            cursor.execute("ALTER TABLE wallets ADD COLUMN interface_id INTEGER NOT NULL DEFAULT 1")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                network TEXT NOT NULL,
                tx_type TEXT NOT NULL,
                amount TEXT NOT NULL,
                tx_hash TEXT,
                destination TEXT,
                token_address TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS internal_balances (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                asset TEXT NOT NULL,
                balance TEXT NOT NULL DEFAULT '0',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, asset)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                asset TEXT NOT NULL,
                tx_type TEXT NOT NULL,
                amount TEXT NOT NULL,
                network TEXT,
                tx_hash TEXT,
                from_asset TEXT,
                to_asset TEXT,
                rate TEXT,
                status TEXT DEFAULT 'completed',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS master_wallets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                network TEXT NOT NULL UNIQUE,
                address TEXT NOT NULL,
                encrypted_private_key TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS authorized_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE,
                username TEXT UNIQUE,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
        conn.close()

    def get_wallet(self, user_id: int, network: str) -> Optional[dict]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT address, encrypted_private_key FROM wallets "
            "WHERE user_id = ? AND network = ?",
            (user_id, network)
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            return {"address": row[0], "encrypted_private_key": row[1]}
        return None

    def save_wallet(
        self,
        user_id: int,
        network: str,
        address: str,
        encrypted_private_key: str
    ):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO wallets "
            "(user_id, network, address, encrypted_private_key) "
            "VALUES (?, ?, ?, ?)",
            (user_id, network, address, encrypted_private_key)
        )
        conn.commit()
        conn.close()

    def get_all_wallets(self, user_id: int) -> list:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT network, address FROM wallets WHERE user_id = ?",
            (user_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        return [{"network": row[0], "address": row[1]} for row in rows]

    def log_transaction(
        self,
        user_id: int,
        network: str,
        tx_type: str,
        amount: str,
        tx_hash: str = None,
        destination: str = None,
        token_address: str = None,
        status: str = "pending"
    ):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO transactions "
            "(user_id, network, tx_type, amount, tx_hash, destination, "
            "token_address, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, network, tx_type, amount, tx_hash, destination,
             token_address, status)
        )
        conn.commit()
        conn.close()

    def get_internal_balance(self, user_id: int, asset: str) -> Decimal:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT balance FROM internal_balances WHERE user_id = ? AND asset = ?",
            (user_id, asset)
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            return Decimal(row[0])
        return Decimal("0")

    def get_all_internal_balances(self, user_id: int) -> dict:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT asset, balance FROM internal_balances WHERE user_id = ?",
            (user_id,)
        )
        rows = cursor.fetchall()
        conn.close()
        return {row[0]: Decimal(row[1]) for row in rows}

    def update_internal_balance(self, user_id: int, asset: str, new_balance: Decimal):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO internal_balances (user_id, asset, balance, updated_at) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (user_id, asset, str(new_balance))
        )
        conn.commit()
        conn.close()

    def credit_balance(self, user_id: int, asset: str, amount: Decimal,
                       tx_type: str, network: str = None, tx_hash: str = None):
        current = self.get_internal_balance(user_id, asset)
        new_balance = current + amount
        self.update_internal_balance(user_id, asset, new_balance)
        self.log_ledger(user_id, asset, tx_type, str(amount), network, tx_hash)
        return new_balance

    def debit_balance(self, user_id: int, asset: str, amount: Decimal,
                      tx_type: str, network: str = None, tx_hash: str = None) -> bool:
        current = self.get_internal_balance(user_id, asset)
        if current < amount:
            return False
        new_balance = current - amount
        self.update_internal_balance(user_id, asset, new_balance)
        self.log_ledger(user_id, asset, tx_type, str(-amount), network, tx_hash)
        return True

    def log_ledger(self, user_id: int, asset: str, tx_type: str, amount: str,
                   network: str = None, tx_hash: str = None,
                   from_asset: str = None, to_asset: str = None, rate: str = None):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO ledger (user_id, asset, tx_type, amount, network, tx_hash, "
            "from_asset, to_asset, rate) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, asset, tx_type, amount, network, tx_hash, from_asset, to_asset, rate)
        )
        conn.commit()
        conn.close()

    def convert_balance(self, user_id: int, from_asset: str, to_asset: str,
                        from_amount: Decimal, to_amount: Decimal, rate: str) -> bool:
        current_from = self.get_internal_balance(user_id, from_asset)
        if current_from < from_amount:
            return False
        new_from = current_from - from_amount
        self.update_internal_balance(user_id, from_asset, new_from)
        current_to = self.get_internal_balance(user_id, to_asset)
        new_to = current_to + to_amount
        self.update_internal_balance(user_id, to_asset, new_to)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO ledger (user_id, asset, tx_type, amount, from_asset, to_asset, rate) "
            "VALUES (?, ?, 'convert', ?, ?, ?, ?)",
            (user_id, to_asset, str(to_amount), from_asset, to_asset, rate)
        )
        conn.commit()
        conn.close()
        return True

    def get_master_wallet(self, network: str) -> Optional[dict]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT address, encrypted_private_key FROM master_wallets WHERE network = ?",
            (network,)
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            return {"address": row[0], "encrypted_private_key": row[1]}
        return None

    def save_master_wallet(self, network: str, address: str, encrypted_private_key: str):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO master_wallets (network, address, encrypted_private_key) "
            "VALUES (?, ?, ?)",
            (network, address, encrypted_private_key)
        )
        conn.commit()
        conn.close()

    def get_all_master_wallets(self) -> list:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT network, address FROM master_wallets")
        rows = cursor.fetchall()
        conn.close()
        return [{"network": row[0], "address": row[1]} for row in rows]

    def add_authorized_user(self, user_id: int = None, username: str = None):
        if not user_id and not username:
            return
        norm_username = username.lower().lstrip("@") if username else None
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO authorized_users (user_id, username) VALUES (?, ?)",
            (user_id, norm_username)
        )
        conn.commit()
        conn.close()

    def get_authorized_users(self) -> list:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, username FROM authorized_users")
        rows = cursor.fetchall()
        conn.close()
        return [{"user_id": row[0], "username": row[1]} for row in rows]

    def remove_authorized_user(self, user_id: int = None, username: str = None):
        if not user_id and not username:
            return
        norm_username = username.lower().lstrip("@") if username else None
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM authorized_users WHERE user_id = ? OR username = ?",
            (user_id, norm_username)
        )
        conn.commit()
        conn.close()


class CryptoUtils:
    @staticmethod
    def get_encryption_key() -> bytes:
        key = ENCRYPTION_KEY.encode()
        return hashlib.sha256(key).digest()

    @staticmethod
    def encrypt_private_key(private_key: str) -> str:
        key = CryptoUtils.get_encryption_key()
        cipher = AES.new(key, AES.MODE_CBC)
        ct_bytes = cipher.encrypt(pad(private_key.encode(), AES.block_size))
        iv = base64.b64encode(cipher.iv).decode()
        ct = base64.b64encode(ct_bytes).decode()
        return json.dumps({"iv": iv, "ciphertext": ct})

    @staticmethod
    def decrypt_private_key(encrypted_data: str) -> str:
        key = CryptoUtils.get_encryption_key()
        data = json.loads(encrypted_data)
        iv = base64.b64decode(data["iv"])
        ct = base64.b64decode(data["ciphertext"])
        cipher = AES.new(key, AES.MODE_CBC, iv)
        pt = unpad(cipher.decrypt(ct), AES.block_size)
        return pt.decode()


class WalletGenerator:
    @staticmethod
    def generate_evm_wallet() -> tuple:
        Account.enable_unaudited_hdwallet_features()
        account = Account.create()
        return account.address, account.key.hex()

    @staticmethod
    def generate_solana_wallet() -> tuple:
        keypair = Keypair()
        address = str(keypair.pubkey())
        private_key = base64.b64encode(bytes(keypair)).decode()
        return address, private_key

    @staticmethod
    def generate_tron_wallet() -> tuple:
        priv_key = TronPrivateKey.random()
        address = priv_key.public_key.to_base58check_address()
        return address, priv_key.hex()

    @staticmethod
    def generate_ltc_wallet() -> tuple:
        import hashlib
        import secrets
        from Crypto.Hash import RIPEMD160
        private_key = secrets.token_bytes(32)
        from ecdsa import SigningKey, SECP256k1
        sk = SigningKey.from_string(private_key, curve=SECP256k1)
        vk = sk.get_verifying_key()
        public_key = b'\x04' + vk.to_string()
        sha256_hash = hashlib.sha256(public_key).digest()
        ripemd160 = RIPEMD160.new()
        ripemd160.update(sha256_hash)
        pubkey_hash = ripemd160.digest()
        version = b'\x30'
        versioned_payload = version + pubkey_hash
        checksum = hashlib.sha256(
            hashlib.sha256(versioned_payload).digest()
        ).digest()[:4]
        address_bytes = versioned_payload + checksum
        alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
        num = int.from_bytes(address_bytes, 'big')
        address = ''
        while num:
            num, rem = divmod(num, 58)
            address = alphabet[rem] + address
        for byte in address_bytes:
            if byte == 0:
                address = '1' + address
            else:
                break
        return address, private_key.hex()

    @staticmethod
    def generate_wallet(network: str) -> tuple:
        network_info = NETWORKS.get(network.upper())
        if not network_info:
            raise ValueError(f"Unsupported network: {network}")

        if network_info["type"] == "evm":
            return WalletGenerator.generate_evm_wallet()
        elif network_info["type"] == "solana":
            return WalletGenerator.generate_solana_wallet()
        elif network_info["type"] == "tron":
            return WalletGenerator.generate_tron_wallet()
        elif network_info["type"] == "ltc":
            return WalletGenerator.generate_ltc_wallet()
        else:
            raise ValueError(f"Unknown network type: {network_info['type']}")


class BalanceChecker:
    @staticmethod
    async def get_evm_balance(
        network: str,
        address: str,
        token_address: str = None
    ) -> dict:
        network_info = NETWORKS[network]
        w3 = get_web3_with_retry(network)

        if token_address:
            contract = w3.eth.contract(
                address=Web3.to_checksum_address(token_address),
                abi=ERC20_ABI
            )
            try:
                balance = contract.functions.balanceOf(
                    Web3.to_checksum_address(address)
                ).call()
                decimals = contract.functions.decimals().call()
                symbol = contract.functions.symbol().call()
                return {
                    "balance": str(Decimal(balance) / Decimal(10 ** decimals)),
                    "symbol": symbol,
                    "raw_balance": balance
                }
            except Exception as e:
                logger.error(f"Error getting token balance: {e}")
                return {"balance": "0", "symbol": "TOKEN", "error": str(e)}
        else:
            balance = w3.eth.get_balance(Web3.to_checksum_address(address))
            return {
                "balance": str(Web3.from_wei(balance, "ether")),
                "symbol": network_info["symbol"],
                "raw_balance": balance
            }

    @staticmethod
    async def get_solana_balance(address: str) -> dict:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getBalance",
                "params": [address]
            }
            async with session.post(
                NETWORKS["SOLANA"]["rpc"],
                json=payload
            ) as resp:
                data = await resp.json()
                if "result" in data:
                    lamports = data["result"]["value"]
                    return {
                        "balance": str(Decimal(lamports) / Decimal(10 ** 9)),
                        "symbol": "SOL",
                        "raw_balance": lamports
                    }
                return {"balance": "0", "symbol": "SOL", "error": data}

    @staticmethod
    async def get_tron_balance(address: str) -> dict:
        import aiohttp
        headers = {}
        if TRONGRID_API_KEY:
            headers["TRON-PRO-API-KEY"] = TRONGRID_API_KEY
        async with aiohttp.ClientSession() as session:
            url = f"{NETWORKS['TRON']['rpc']}/v1/accounts/{address}"
            async with session.get(url, headers=headers) as resp:
                data = await resp.json()
                if "data" in data and len(data["data"]) > 0:
                    balance = data["data"][0].get("balance", 0)
                    return {
                        "balance": str(Decimal(balance) / Decimal(10 ** 6)),
                        "symbol": "TRX",
                        "raw_balance": balance
                    }
                return {"balance": "0", "symbol": "TRX"}

    @staticmethod
    async def get_ltc_balance(address: str) -> dict:
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://api.blockcypher.com/v1/ltc/main/addrs/{address}/balance"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        balance_satoshi = data.get("balance", 0)
                        return {
                            "balance": str(Decimal(balance_satoshi) / Decimal(10 ** 8)),
                            "symbol": "LTC",
                            "raw_balance": balance_satoshi
                        }
                    return {"balance": "0", "symbol": "LTC", "error": "API error"}
        except Exception as e:
            logger.error(f"Error getting LTC balance: {e}")
            return {"balance": "0", "symbol": "LTC", "error": str(e)}

    @staticmethod
    async def get_btc_balance(address: str) -> dict:
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://mempool.space/api/address/{address}"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        chain = data.get("chain_stats", {})
                        balance_sat = chain.get("funded_txo_sum", 0) - chain.get("spent_txo_sum", 0)
                        return {
                            "balance": str(Decimal(balance_sat) / Decimal(10 ** 8)),
                            "symbol": "BTC",
                            "raw_balance": balance_sat
                        }
                    return {"balance": "0", "symbol": "BTC", "error": "API error"}
        except Exception as e:
            logger.error(f"Error getting BTC balance: {e}")
            return {"balance": "0", "symbol": "BTC", "error": str(e)}

    @staticmethod
    async def get_solana_token_balance(address: str, token_mint: str) -> dict:
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTokenAccountsByOwner",
                    "params": [
                        address,
                        {"mint": token_mint},
                        {"encoding": "jsonParsed"}
                    ]
                }
                async with session.post(
                    NETWORKS["SOLANA"]["rpc"],
                    json=payload
                ) as resp:
                    data = await resp.json()
                    if "result" in data and data["result"]["value"]:
                        account = data["result"]["value"][0]
                        token_amount = account["account"]["data"]["parsed"]["info"]
                        amount = token_amount["tokenAmount"]["uiAmount"]
                        return {
                            "balance": str(amount) if amount else "0",
                            "symbol": "USDC",
                            "raw_balance": int(token_amount["tokenAmount"]["amount"])
                        }
                    return {"balance": "0", "symbol": "USDC", "raw_balance": 0}
        except Exception as e:
            logger.error(f"Error getting Solana token balance: {e}")
            return {"balance": "0", "symbol": "USDC", "error": str(e)}

    @staticmethod
    async def get_tron_token_balance(address: str, token_address: str) -> dict:
        import aiohttp
        try:
            headers = {}
            if TRONGRID_API_KEY:
                headers["TRON-PRO-API-KEY"] = TRONGRID_API_KEY
            async with aiohttp.ClientSession() as session:
                url = f"{NETWORKS['TRON']['rpc']}/v1/accounts/{address}"
                async with session.get(url, headers=headers) as resp:
                    data = await resp.json()
                    if "data" in data and len(data["data"]) > 0:
                        trc20 = data["data"][0].get("trc20", [])
                        for token in trc20:
                            if token_address in token:
                                balance = int(token[token_address])
                                return {
                                    "balance": str(Decimal(balance) / Decimal(10 ** 6)),
                                    "symbol": "USDT",
                                    "raw_balance": balance
                                }
                    return {"balance": "0", "symbol": "USDT", "raw_balance": 0}
        except Exception as e:
            logger.error(f"Error getting Tron token balance: {e}")
            return {"balance": "0", "symbol": "USDT", "error": str(e)}

    @staticmethod
    async def get_token_balance(
        token: str,
        network: str,
        address: str
    ) -> dict:
        token = token.upper()
        network = network.upper()

        if token not in TOKENS:
            return {"error": f"Unsupported token: {token}"}

        token_info = TOKENS[token]
        if network not in token_info["networks"]:
            return {"error": f"{token} not available on {network}"}

        network_token = token_info["networks"][network]
        network_info = NETWORKS.get(network)

        try:
            if network_token.get("native"):
                return await BalanceChecker.get_balance(network, address)

            if network_info["type"] == "evm":
                return await BalanceChecker.get_evm_balance(
                    network, address, network_token["address"]
                )
            elif network_info["type"] == "solana":
                return await BalanceChecker.get_solana_token_balance(
                    address, network_token["address"]
                )
            elif network_info["type"] == "tron":
                return await BalanceChecker.get_tron_token_balance(
                    address, network_token["address"]
                )
        except Exception as e:
            logger.error(f"Error getting {token} balance on {network}: {e}")
            return {"error": str(e)}

    @staticmethod
    async def get_balance(
        network: str,
        address: str,
        token_address: str = None
    ) -> dict:
        network = network.upper()
        network_info = NETWORKS.get(network)
        if not network_info:
            return {"error": f"Unsupported network: {network}"}

        try:
            if network_info["type"] == "evm":
                return await BalanceChecker.get_evm_balance(
                    network, address, token_address
                )
            elif network_info["type"] == "solana":
                return await BalanceChecker.get_solana_balance(address)
            elif network_info["type"] == "tron":
                return await BalanceChecker.get_tron_balance(address)
            elif network_info["type"] == "ltc":
                return await BalanceChecker.get_ltc_balance(address)
            elif network_info["type"] == "btc":
                return await BalanceChecker.get_btc_balance(address)
        except Exception as e:
            logger.error(f"Error getting balance for {network}: {e}")
            return {"error": str(e)}


class TransactionChecker:
    """Fetch transaction details (sender, receiver, amount, time) by hash."""

    TRANSFER_TOPIC = (
        "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    )

    @staticmethod
    async def get_transaction(network: str, tx_hash: str) -> dict:
        network_info = NETWORKS.get(network)
        if not network_info:
            return {"error": f"Unsupported network: {network}"}
        try:
            net_type = network_info["type"]
            if net_type == "evm":
                return await TransactionChecker.get_evm_tx(network, tx_hash)
            elif net_type == "solana":
                return await TransactionChecker.get_solana_tx(tx_hash)
            elif net_type == "tron":
                return await TransactionChecker.get_tron_tx(tx_hash)
            elif net_type == "ltc":
                return await TransactionChecker.get_ltc_tx(tx_hash)
            elif net_type == "btc":
                return await TransactionChecker.get_btc_tx(tx_hash)
            return {"error": f"Unsupported network type for {network}"}
        except Exception as e:
            logger.error(f"Error getting {network} tx {tx_hash}: {e}")
            return {"error": str(e)}

    @staticmethod
    async def _evm_block_timestamp(network: str, block_number):
        """Fetch a block timestamp via raw JSON-RPC (avoids POA extraData errors)."""
        network_info = NETWORKS.get(network, {})
        rpcs = [network_info.get("rpc")] + network_info.get("rpc_fallbacks", [])
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_getBlockByNumber",
            "params": [hex(int(block_number)), False],
        }
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for rpc in rpcs:
                if not rpc:
                    continue
                try:
                    async with session.post(rpc, json=payload) as resp:
                        data = await resp.json()
                        result = data.get("result") or {}
                        ts = result.get("timestamp")
                        if ts:
                            return int(ts, 16)
                except Exception:
                    continue
        return None

    @staticmethod
    async def get_evm_tx(network: str, tx_hash: str) -> dict:
        w3 = get_web3_with_retry(network)
        if not w3:
            return {"error": "RPC unavailable"}

        tx_hash = tx_hash if tx_hash.startswith("0x") else "0x" + tx_hash
        try:
            tx = w3.eth.get_transaction(tx_hash)
        except Exception:
            return {"error": "not_found"}
        if not tx:
            return {"error": "not_found"}

        try:
            receipt = w3.eth.get_transaction_receipt(tx_hash)
        except Exception:
            receipt = None

        timestamp = None
        block_number = tx.get("blockNumber")
        if block_number is not None:
            timestamp = await TransactionChecker._evm_block_timestamp(
                network, block_number
            )

        sender = tx.get("from")
        receiver = tx.get("to")
        symbol = NETWORKS[network]["symbol"]
        value = tx.get("value", 0) or 0
        amount = str(Web3.from_wei(value, "ether"))
        asset = symbol

        if receipt is not None:
            for log in receipt.get("logs", []):
                topics = log.get("topics", [])
                if len(topics) < 3:
                    continue
                topic0 = topics[0]
                topic0_hex = topic0.hex() if hasattr(topic0, "hex") else str(topic0)
                if not topic0_hex.startswith("0x"):
                    topic0_hex = "0x" + topic0_hex
                if topic0_hex.lower() != TransactionChecker.TRANSFER_TOPIC:
                    continue

                token_meta = lookup_token_by_address(network, log["address"])

                def topic_to_addr(topic):
                    h = topic.hex() if hasattr(topic, "hex") else str(topic)
                    h = h[2:] if h.startswith("0x") else h
                    return Web3.to_checksum_address("0x" + h[-40:])

                data = log.get("data")
                if hasattr(data, "hex"):
                    raw = int(data.hex(), 16)
                elif isinstance(data, str):
                    raw = int(data, 16) if data not in ("", "0x") else 0
                else:
                    raw = int.from_bytes(data, "big") if data else 0

                decimals = token_meta["decimals"] if token_meta else 18
                sender = topic_to_addr(topics[1])
                receiver = topic_to_addr(topics[2])
                amount = str(Decimal(raw) / Decimal(10 ** decimals))
                asset = token_meta["symbol"] if token_meta else "Token"
                break

        status = None
        if receipt is not None:
            status = "Success" if receipt.get("status", 1) == 1 else "Failed"

        return {
            "network": network,
            "hash": tx_hash,
            "from": sender,
            "to": receiver,
            "amount": amount,
            "asset": asset,
            "timestamp": timestamp,
            "status": status,
            "explorer_url": tx_explorer_url(network, tx_hash),
        }

    @staticmethod
    async def get_tron_tx(tx_hash: str) -> dict:
        from tronpy.keys import to_base58check_address

        headers = {"Content-Type": "application/json"}
        if TRONGRID_API_KEY:
            headers["TRON-PRO-API-KEY"] = TRONGRID_API_KEY
        base = NETWORKS["TRON"]["rpc"]

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base}/wallet/gettransactionbyid",
                json={"value": tx_hash},
                headers=headers,
            ) as resp:
                tx = await resp.json()
            async with session.post(
                f"{base}/wallet/gettransactioninfobyid",
                json={"value": tx_hash},
                headers=headers,
            ) as resp:
                info = await resp.json()

        if not tx or "raw_data" not in tx:
            return {"error": "not_found"}

        timestamp = None
        if info and info.get("blockTimeStamp"):
            timestamp = int(info["blockTimeStamp"]) // 1000
        elif tx.get("raw_data", {}).get("timestamp"):
            timestamp = int(tx["raw_data"]["timestamp"]) // 1000

        contract_ret = None
        ret = tx.get("ret")
        if isinstance(ret, list) and ret:
            contract_ret = ret[0].get("contractRet")

        contract = tx["raw_data"]["contract"][0]
        ctype = contract.get("type")
        val = contract["parameter"]["value"]

        sender = receiver = amount = None
        asset = "TRX"

        def to_b58(hex_addr):
            try:
                if not hex_addr.startswith("41"):
                    hex_addr = "41" + hex_addr[-40:]
                return to_base58check_address(hex_addr)
            except Exception:
                return hex_addr

        if ctype == "TransferContract":
            sender = to_b58(val["owner_address"])
            receiver = to_b58(val["to_address"])
            amount = str(Decimal(val.get("amount", 0)) / Decimal(10 ** 6))
            asset = "TRX"
        elif ctype == "TriggerSmartContract":
            sender = to_b58(val["owner_address"])
            token_contract = to_b58(val.get("contract_address", ""))
            data = val.get("data", "")
            token_meta = lookup_token_by_address("TRON", token_contract)
            decimals = token_meta["decimals"] if token_meta else 6
            asset = token_meta["symbol"] if token_meta else "TRC20"
            if len(data) >= 136:
                to_hex = data[32:72]
                amt_hex = data[72:136]
                receiver = to_b58("41" + to_hex[-40:])
                amount = str(Decimal(int(amt_hex, 16)) / Decimal(10 ** decimals))

        return {
            "network": "TRON",
            "hash": tx_hash,
            "from": sender,
            "to": receiver,
            "amount": amount,
            "asset": asset,
            "timestamp": timestamp,
            "status": (
                "Success" if contract_ret == "SUCCESS"
                else contract_ret
                or ("Success" if (info and info.get("receipt")) else None)
            ),
            "explorer_url": tx_explorer_url("TRON", tx_hash),
        }

    @staticmethod
    async def get_solana_tx(tx_hash: str) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTransaction",
            "params": [
                tx_hash,
                {
                    "encoding": "jsonParsed",
                    "maxSupportedTransactionVersion": 0,
                    "commitment": "confirmed",
                },
            ],
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(NETWORKS["SOLANA"]["rpc"], json=payload) as resp:
                data = await resp.json()

        result = data.get("result")
        if not result:
            return {"error": "not_found"}

        timestamp = result.get("blockTime")
        message = result.get("transaction", {}).get("message", {})
        instructions = list(message.get("instructions", []))
        for inner in result.get("meta", {}).get("innerInstructions", []):
            instructions.extend(inner.get("instructions", []))

        sender = receiver = amount = None
        asset = "SOL"

        for instr in instructions:
            parsed = instr.get("parsed")
            if not isinstance(parsed, dict):
                continue
            itype = parsed.get("type")
            program = instr.get("program")
            info = parsed.get("info", {})

            if program == "system" and itype == "transfer":
                sender = info.get("source")
                receiver = info.get("destination")
                lamports = info.get("lamports", 0)
                amount = str(Decimal(lamports) / Decimal(10 ** 9))
                asset = "SOL"
                break
            if program == "spl-token" and itype in ("transfer", "transferChecked"):
                sender = info.get("source") or info.get("authority")
                receiver = info.get("destination")
                token_amount = info.get("tokenAmount")
                if token_amount:
                    amount = token_amount.get("uiAmountString") or str(
                        token_amount.get("uiAmount", "")
                    )
                elif "amount" in info:
                    amount = str(Decimal(info["amount"]) / Decimal(10 ** 6))
                asset = "SPL Token"
                break

        return {
            "network": "SOLANA",
            "hash": tx_hash,
            "from": sender,
            "to": receiver,
            "amount": amount,
            "asset": asset,
            "timestamp": timestamp,
            "status": "Failed" if result.get("meta", {}).get("err") else "Success",
            "explorer_url": tx_explorer_url("SOLANA", tx_hash),
        }

    @staticmethod
    async def get_ltc_tx(tx_hash: str) -> dict:
        url = f"https://api.blockchair.com/litecoin/dashboards/transaction/{tx_hash}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return {"error": "not_found"}
                data = await resp.json()

        data_field = data.get("data") if isinstance(data, dict) else None
        tx_data = data_field.get(tx_hash) if isinstance(data_field, dict) else None
        if not isinstance(tx_data, dict):
            return {"error": "not_found"}

        transaction = tx_data.get("transaction", {})
        inputs = tx_data.get("inputs", [])
        outputs = tx_data.get("outputs", [])

        sender = inputs[0].get("recipient") if inputs else None
        receiver = outputs[0].get("recipient") if outputs else None
        amount = None
        if outputs:
            amount = str(Decimal(outputs[0].get("value", 0)) / Decimal(10 ** 8))

        timestamp = None
        time_str = transaction.get("time")
        if time_str:
            try:
                dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=timezone.utc
                )
                timestamp = int(dt.timestamp())
            except Exception:
                timestamp = None

        return {
            "network": "LTC",
            "hash": tx_hash,
            "from": sender,
            "to": receiver,
            "amount": amount,
            "asset": "LTC",
            "timestamp": timestamp,
            "status": "Success" if transaction.get("block_id", -1) > 0 else "Pending",
            "explorer_url": tx_explorer_url("LTC", tx_hash),
        }

    @staticmethod
    async def get_btc_tx(tx_hash: str) -> dict:
        url = f"https://mempool.space/api/tx/{tx_hash}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return {"error": "not_found"}
                tx_data = await resp.json()

        if not tx_data or not isinstance(tx_data, dict):
            return {"error": "not_found"}

        vin = tx_data.get("vin", [])
        vout = tx_data.get("vout", [])

        sender = None
        if vin:
            prevout = vin[0].get("prevout") or {}
            sender = prevout.get("scriptpubkey_address")

        receiver = None
        amount = None
        # pick the largest non-change output as the primary recipient
        if vout:
            primary = max(vout, key=lambda o: o.get("value", 0))
            receiver = primary.get("scriptpubkey_address")
            amount = str(Decimal(primary.get("value", 0)) / Decimal(10 ** 8))

        status = tx_data.get("status", {})
        timestamp = status.get("block_time")

        return {
            "network": "BTC",
            "hash": tx_hash,
            "from": sender,
            "to": receiver,
            "amount": amount,
            "asset": "BTC",
            "timestamp": timestamp,
            "status": "Success" if status.get("confirmed") else "Pending",
            "explorer_url": tx_explorer_url("BTC", tx_hash),
        }


class WithdrawalHandler:
    @staticmethod
    async def check_gas_balance(network: str, address: str, token_address: str = None) -> dict:
        network_info = NETWORKS[network]
        w3 = get_web3_with_retry(network)
        try:
            native_balance = w3.eth.get_balance(address)
            gas_price = w3.eth.gas_price
            estimated_gas = 100000 if token_address else 21000
            estimated_fee = gas_price * estimated_gas
            fee_in_native = Web3.from_wei(estimated_fee, "ether")
            balance_in_native = Web3.from_wei(native_balance, "ether")
            has_enough = native_balance >= estimated_fee
            return {
                "has_enough": has_enough,
                "native_balance": str(balance_in_native),
                "estimated_fee": str(fee_in_native),
                "symbol": network_info["symbol"]
            }
        except Exception as e:
            logger.error(f"Gas check error: {e}")
            return {"has_enough": True, "error": str(e)}

    @staticmethod
    async def withdraw_evm(
        network: str,
        private_key: str,
        to_address: str,
        amount: str,
        token_address: str = None
    ) -> dict:
        network_info = NETWORKS[network]
        w3 = get_web3_with_retry(network)
        account = Account.from_key(private_key)

        try:
            gas_price = w3.eth.gas_price
            native_balance = w3.eth.get_balance(account.address)

            if token_address:
                contract = w3.eth.contract(
                    address=Web3.to_checksum_address(token_address),
                    abi=ERC20_ABI
                )
                decimals = contract.functions.decimals().call()
                amount_wei = int(Decimal(amount) * Decimal(10 ** decimals))

                tx_data = contract.functions.transfer(
                    Web3.to_checksum_address(to_address),
                    amount_wei
                )
                try:
                    estimated_gas = tx_data.estimate_gas({"from": account.address})
                    estimated_gas = int(estimated_gas * 1.2)
                except Exception:
                    estimated_gas = 100000

                estimated_fee = gas_price * estimated_gas
                if native_balance < estimated_fee:
                    fee_needed = Web3.from_wei(estimated_fee, "ether")
                    balance_have = Web3.from_wei(native_balance, "ether")
                    gas_price_gwei = Web3.from_wei(gas_price, "gwei")
                    return {
                        "success": False,
                        "error": (
                            f"Insufficient {network_info['symbol']} for gas fees.\n\n"
                            f"Your balance: {float(balance_have):.6f} {network_info['symbol']}\n"
                            f"Required fee: {float(fee_needed):.6f} {network_info['symbol']}\n"
                            f"Gas price: {float(gas_price_gwei):.2f} Gwei\n"
                            f"Gas limit: {estimated_gas}\n\n"
                            f"Please deposit more {network_info['symbol']} to cover gas."
                        )
                    }

                tx = tx_data.build_transaction({
                    "from": account.address,
                    "nonce": w3.eth.get_transaction_count(account.address),
                    "gas": estimated_gas,
                    "gasPrice": gas_price,
                    "chainId": network_info["chain_id"]
                })
            else:
                amount_wei = Web3.to_wei(amount, "ether")
                estimated_gas = 21000
                estimated_fee = gas_price * estimated_gas
                total_needed = amount_wei + estimated_fee

                if native_balance < total_needed:
                    total_in_native = Web3.from_wei(total_needed, "ether")
                    balance_have = Web3.from_wei(native_balance, "ether")
                    return {
                        "success": False,
                        "error": (
                            f"Insufficient balance. "
                            f"Need ~{total_in_native:.6f} {network_info['symbol']} "
                            f"(amount + gas), have {balance_have:.6f} {network_info['symbol']}"
                        )
                    }

                tx = {
                    "to": Web3.to_checksum_address(to_address),
                    "value": amount_wei,
                    "nonce": w3.eth.get_transaction_count(account.address),
                    "gas": estimated_gas,
                    "gasPrice": gas_price,
                    "chainId": network_info["chain_id"]
                }

            signed_tx = w3.eth.account.sign_transaction(tx, private_key)
            tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            return {
                "success": True,
                "tx_hash": tx_hash.hex(),
                "explorer_url": f"{network_info['explorer']}/tx/0x{tx_hash.hex()}"
            }
        except Exception as e:
            logger.error(f"EVM withdrawal error: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    async def withdraw_solana(
        private_key: str,
        to_address: str,
        amount: str
    ) -> dict:
        try:
            from solders.keypair import Keypair
            from solders.pubkey import Pubkey
            from solders.system_program import transfer, TransferParams
            from solders.transaction import Transaction
            from solders.message import Message
            from solders.hash import Hash
            import aiohttp

            keypair = Keypair.from_bytes(base64.b64decode(private_key))
            to_pubkey = Pubkey.from_string(to_address)
            lamports = int(Decimal(amount) * Decimal(10 ** 9))
            
            # Check balance before attempting withdrawal
            async with aiohttp.ClientSession() as session:
                balance_payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getBalance",
                    "params": [str(keypair.pubkey())]
                }
                async with session.post(
                    NETWORKS["SOLANA"]["rpc"],
                    json=balance_payload
                ) as resp:
                    balance_data = await resp.json()
                    current_balance = balance_data.get("result", {}).get("value", 0)
                    
                # Solana transaction fee is typically ~5000 lamports (0.000005 SOL)
                estimated_fee_lamports = 5000
                total_needed = lamports + estimated_fee_lamports
                
                if current_balance < total_needed:
                    balance_sol = Decimal(current_balance) / Decimal(10 ** 9)
                    needed_sol = Decimal(total_needed) / Decimal(10 ** 9)
                    fee_sol = Decimal(estimated_fee_lamports) / Decimal(10 ** 9)
                    return {
                        "success": False,
                        "error": (
                            f"Insufficient SOL for transaction.\n\n"
                            f"Your balance: {float(balance_sol):.6f} SOL\n"
                            f"Required: {float(needed_sol):.6f} SOL\n"
                            f"(Amount: {amount} SOL + Fee: ~{float(fee_sol):.6f} SOL)\n\n"
                            f"Please deposit more SOL to cover the transaction."
                        )
                    }

            async with aiohttp.ClientSession() as session:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getLatestBlockhash",
                    "params": []
                }
                async with session.post(
                    NETWORKS["SOLANA"]["rpc"],
                    json=payload
                ) as resp:
                    data = await resp.json()
                    blockhash = Hash.from_string(
                        data["result"]["value"]["blockhash"]
                    )

                ix = transfer(TransferParams(
                    from_pubkey=keypair.pubkey(),
                    to_pubkey=to_pubkey,
                    lamports=lamports
                ))
                msg = Message.new_with_blockhash([ix], keypair.pubkey(), blockhash)
                tx = Transaction.new_unsigned(msg)
                tx.sign([keypair], blockhash)

                tx_bytes = bytes(tx)
                tx_base64 = base64.b64encode(tx_bytes).decode()

                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "sendTransaction",
                    "params": [tx_base64, {"encoding": "base64"}]
                }
                async with session.post(
                    NETWORKS["SOLANA"]["rpc"],
                    json=payload
                ) as resp:
                    data = await resp.json()
                    if "result" in data:
                        return {
                            "success": True,
                            "tx_hash": data["result"],
                            "explorer_url": (
                                f"{NETWORKS['SOLANA']['explorer']}"
                                f"/tx/{data['result']}"
                            )
                        }
                    return {"success": False, "error": data.get("error", data)}
        except Exception as e:
            logger.error(f"Solana withdrawal error: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    async def withdraw_solana_token(
        private_key: str,
        to_address: str,
        amount: str,
        token_mint: str,
        decimals: int = 6
    ) -> dict:
        """Withdraw SPL tokens (USDT/USDC) on Solana."""
        try:
            from solders.keypair import Keypair
            from solders.pubkey import Pubkey
            from solders.transaction import Transaction
            from solders.message import Message
            from solders.hash import Hash
            from solders.instruction import Instruction, AccountMeta
            import aiohttp
            import struct

            keypair = Keypair.from_bytes(base64.b64decode(private_key))
            to_pubkey = Pubkey.from_string(to_address)
            mint_pubkey = Pubkey.from_string(token_mint)
            
            # SPL Token Program ID
            TOKEN_PROGRAM_ID = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
            ASSOCIATED_TOKEN_PROGRAM_ID = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
            
            # Calculate token amount with decimals
            token_amount = int(Decimal(amount) * Decimal(10 ** decimals))
            
            async with aiohttp.ClientSession() as session:
                # Check SOL balance for gas fees
                balance_payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getBalance",
                    "params": [str(keypair.pubkey())]
                }
                async with session.post(
                    NETWORKS["SOLANA"]["rpc"],
                    json=balance_payload
                ) as resp:
                    balance_data = await resp.json()
                    sol_balance = balance_data.get("result", {}).get("value", 0)
                
                # SPL token transfer needs ~5000 lamports for fee
                estimated_fee_lamports = 10000  # Slightly higher for token transfers
                
                if sol_balance < estimated_fee_lamports:
                    balance_sol = Decimal(sol_balance) / Decimal(10 ** 9)
                    fee_sol = Decimal(estimated_fee_lamports) / Decimal(10 ** 9)
                    return {
                        "success": False,
                        "error": (
                            f"Insufficient SOL for gas fees.\n\n"
                            f"Your SOL balance: {float(balance_sol):.6f} SOL\n"
                            f"Required fee: ~{float(fee_sol):.6f} SOL\n\n"
                            f"Please deposit some SOL to cover the transaction fee."
                        )
                    }
                
                # Get or derive Associated Token Accounts (ATAs)
                def get_associated_token_address(owner: Pubkey, mint: Pubkey) -> Pubkey:
                    seeds = [bytes(owner), bytes(TOKEN_PROGRAM_ID), bytes(mint)]
                    program_address, _ = Pubkey.find_program_address(seeds, ASSOCIATED_TOKEN_PROGRAM_ID)
                    return program_address
                
                from_ata = get_associated_token_address(keypair.pubkey(), mint_pubkey)
                to_ata = get_associated_token_address(to_pubkey, mint_pubkey)
                
                # Check if destination ATA exists
                ata_check_payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getAccountInfo",
                    "params": [str(to_ata), {"encoding": "base64"}]
                }
                async with session.post(
                    NETWORKS["SOLANA"]["rpc"],
                    json=ata_check_payload
                ) as resp:
                    ata_data = await resp.json()
                    to_ata_exists = ata_data.get("result", {}).get("value") is not None
                
                # Get latest blockhash
                blockhash_payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getLatestBlockhash",
                    "params": []
                }
                async with session.post(
                    NETWORKS["SOLANA"]["rpc"],
                    json=blockhash_payload
                ) as resp:
                    data = await resp.json()
                    blockhash = Hash.from_string(data["result"]["value"]["blockhash"])
                
                # Build instructions list
                instructions = []
                
                # If destination ATA doesn't exist, create it first
                if not to_ata_exists:
                    SYSTEM_PROGRAM_ID = Pubkey.from_string("11111111111111111111111111111111")
                    SYSVAR_RENT_ID = Pubkey.from_string("SysvarRent111111111111111111111111111111111")
                    
                    create_ata_ix = Instruction(
                        program_id=ASSOCIATED_TOKEN_PROGRAM_ID,
                        accounts=[
                            AccountMeta(pubkey=keypair.pubkey(), is_signer=True, is_writable=True),
                            AccountMeta(pubkey=to_ata, is_signer=False, is_writable=True),
                            AccountMeta(pubkey=to_pubkey, is_signer=False, is_writable=False),
                            AccountMeta(pubkey=mint_pubkey, is_signer=False, is_writable=False),
                            AccountMeta(pubkey=SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
                            AccountMeta(pubkey=TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
                        ],
                        data=bytes()
                    )
                    instructions.append(create_ata_ix)
                
                # Build SPL Token transfer instruction
                # Instruction data: [3] (transfer instruction) + [amount as u64 little-endian]
                transfer_data = bytes([3]) + struct.pack('<Q', token_amount)
                
                transfer_ix = Instruction(
                    program_id=TOKEN_PROGRAM_ID,
                    accounts=[
                        AccountMeta(pubkey=from_ata, is_signer=False, is_writable=True),
                        AccountMeta(pubkey=to_ata, is_signer=False, is_writable=True),
                        AccountMeta(pubkey=keypair.pubkey(), is_signer=True, is_writable=False),
                    ],
                    data=transfer_data
                )
                instructions.append(transfer_ix)
                
                msg = Message.new_with_blockhash(instructions, keypair.pubkey(), blockhash)
                tx = Transaction.new_unsigned(msg)
                tx.sign([keypair], blockhash)
                
                tx_bytes = bytes(tx)
                tx_base64 = base64.b64encode(tx_bytes).decode()
                
                # Send transaction
                send_payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "sendTransaction",
                    "params": [tx_base64, {"encoding": "base64"}]
                }
                async with session.post(
                    NETWORKS["SOLANA"]["rpc"],
                    json=send_payload
                ) as resp:
                    data = await resp.json()
                    if "result" in data:
                        return {
                            "success": True,
                            "tx_hash": data["result"],
                            "explorer_url": f"{NETWORKS['SOLANA']['explorer']}/tx/{data['result']}"
                        }
                    error_msg = data.get("error", {})
                    if isinstance(error_msg, dict):
                        error_msg = error_msg.get("message", str(error_msg))
                    return {"success": False, "error": str(error_msg)}
        except Exception as e:
            logger.error(f"Solana token withdrawal error: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    async def withdraw_tron(
        private_key: str,
        to_address: str,
        amount: str
    ) -> dict:
        try:
            from tronpy import Tron
            from tronpy.keys import PrivateKey
            from tronpy.providers import HTTPProvider

            if TRONGRID_API_KEY:
                provider = HTTPProvider(NETWORKS['TRON']['rpc'], api_key=TRONGRID_API_KEY)
                client = Tron(provider=provider)
            else:
                client = Tron()
            priv_key = PrivateKey(bytes.fromhex(private_key))
            from_address = priv_key.public_key.to_base58check_address()

            amount_sun = int(Decimal(amount) * Decimal(10 ** 6))
            
            # Check balance before attempting withdrawal
            # Tron bandwidth fee is typically ~0.1 TRX for simple transfers
            estimated_fee_sun = 100000  # 0.1 TRX in sun
            total_needed = amount_sun + estimated_fee_sun
            
            current_balance = client.get_account_balance(from_address)
            current_balance_sun = int(Decimal(str(current_balance)) * Decimal(10 ** 6))
            
            if current_balance_sun < total_needed:
                balance_trx = Decimal(current_balance_sun) / Decimal(10 ** 6)
                needed_trx = Decimal(total_needed) / Decimal(10 ** 6)
                fee_trx = Decimal(estimated_fee_sun) / Decimal(10 ** 6)
                return {
                    "success": False,
                    "error": (
                        f"Insufficient TRX for transaction.\n\n"
                        f"Your balance: {float(balance_trx):.6f} TRX\n"
                        f"Required: {float(needed_trx):.6f} TRX\n"
                        f"(Amount: {amount} TRX + Fee: ~{float(fee_trx):.6f} TRX)\n\n"
                        f"Please deposit more TRX to cover the transaction."
                    )
                }

            txn = (
                client.trx.transfer(from_address, to_address, amount_sun)
                .build()
                .sign(priv_key)
            )
            result = txn.broadcast()

            if result.get("result"):
                return {
                    "success": True,
                    "tx_hash": result["txid"],
                    "explorer_url": (
                        f"{NETWORKS['TRON']['explorer']}"
                        f"/#/transaction/{result['txid']}"
                    )
                }
            return {"success": False, "error": result}
        except Exception as e:
            logger.error(f"Tron withdrawal error: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    async def withdraw_tron_token(
        private_key: str,
        to_address: str,
        amount: str,
        token_address: str,
        decimals: int = 6
    ) -> dict:
        try:
            from tronpy import Tron
            from tronpy.keys import PrivateKey
            from tronpy.providers import HTTPProvider

            if TRONGRID_API_KEY:
                provider = HTTPProvider(NETWORKS['TRON']['rpc'], api_key=TRONGRID_API_KEY)
                client = Tron(provider=provider)
            else:
                client = Tron()
            priv_key = PrivateKey(bytes.fromhex(private_key))
            from_address = priv_key.public_key.to_base58check_address()

            amount_smallest = int(Decimal(amount) * Decimal(10 ** decimals))
            
            current_trx_balance = client.get_account_balance(from_address)
            current_trx_sun = int(Decimal(str(current_trx_balance)) * Decimal(10 ** 6))
            estimated_fee_sun = 15_000_000
            
            if current_trx_sun < estimated_fee_sun:
                fee_trx = Decimal(estimated_fee_sun) / Decimal(10 ** 6)
                balance_trx = Decimal(current_trx_sun) / Decimal(10 ** 6)
                return {
                    "success": False,
                    "error": (
                        f"Insufficient TRX for gas fees.\n\n"
                        f"Your TRX balance: {float(balance_trx):.6f} TRX\n"
                        f"Required for gas: ~{float(fee_trx):.6f} TRX\n\n"
                        f"Please deposit more TRX to cover the transaction fee."
                    )
                }

            contract = client.get_contract(token_address)
            txn = (
                contract.functions.transfer(to_address, amount_smallest)
                .with_owner(from_address)
                .fee_limit(15_000_000)
                .build()
                .sign(priv_key)
            )
            result = txn.broadcast()

            if result.get("result"):
                return {
                    "success": True,
                    "tx_hash": result["txid"],
                    "explorer_url": (
                        f"{NETWORKS['TRON']['explorer']}"
                        f"/#/transaction/{result['txid']}"
                    )
                }
            return {"success": False, "error": str(result)}
        except Exception as e:
            logger.error(f"Tron token withdrawal error: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    async def withdraw(
        network: str,
        private_key: str,
        to_address: str,
        amount: str,
        token_address: str = None
    ) -> dict:
        network = network.upper()
        network_info = NETWORKS.get(network)
        if not network_info:
            return {"success": False, "error": f"Unsupported network: {network}"}

        if network_info["type"] == "evm":
            return await WithdrawalHandler.withdraw_evm(
                network, private_key, to_address, amount, token_address
            )
        elif network_info["type"] == "solana":
            return await WithdrawalHandler.withdraw_solana(
                private_key, to_address, amount
            )
        elif network_info["type"] == "tron":
            return await WithdrawalHandler.withdraw_tron(
                private_key, to_address, amount
            )
        elif network_info["type"] == "ltc":
            return {"success": False, "error": "LTC withdrawals are not yet supported. Please contact support."}
        
        return {"success": False, "error": f"Unsupported network type: {network_info['type']}"}


db = WalletDatabase()

AUTHORIZED_USER_IDS = set()
AUTHORIZED_USERNAMES = set()


def load_authorized_users():
    """Load authorized users from the database into memory."""
    AUTHORIZED_USER_IDS.clear()
    AUTHORIZED_USERNAMES.clear()
    for entry in db.get_authorized_users():
        if entry.get("user_id"):
            AUTHORIZED_USER_IDS.add(entry["user_id"])
        if entry.get("username"):
            AUTHORIZED_USERNAMES.add(entry["username"].lower().lstrip("@"))


def is_authorized(user_id: int, username: str = None) -> bool:
    """Check if user is authorized to use the bot."""
    if user_id in USER_ACCESS:
        return True
    if user_id in AUTHORIZED_USER_IDS:
        return True
    if username:
        return username.lower().lstrip("@") in AUTHORIZED_USERNAMES
    return False


load_authorized_users()


async def check_callback_auth(update: Update) -> bool:
    query = update.callback_query
    user_id = query.from_user.id
    if not is_authorized(user_id):
        await query.answer(
            "You are not authorised to use the bot!",
            show_alert=True
        )
        return False
    return True


def is_admin(user_id: int) -> bool:
    """Check if user is the admin user."""
    return user_id == ALLOWED_USER_ID


async def admin_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a user to the bot's authorized users list (admin only)."""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text(
            "You are not authorized to use this command.",
            parse_mode="Markdown"
        )
        return

    target_user_id = None
    target_username = None

    if update.message.reply_to_message:
        target_user = update.message.reply_to_message.from_user
        target_user_id = target_user.id
        target_username = target_user.username
    else:
        args = context.args if context.args else update.message.text.split()[1:]
        if not args:
            await update.message.reply_text(
                "Usage: `+add @username`, `+add <user_id>`, or reply `+add` to a user's message.",
                parse_mode="Markdown"
            )
            return
        arg = args[0].strip()
        if arg.startswith("@"):
            target_username = arg[1:]
        elif arg.isdigit():
            target_user_id = int(arg)
            try:
                chat = await context.bot.get_chat(target_user_id)
                target_username = chat.username
            except Exception:
                pass
        else:
            await update.message.reply_text(
                "Usage: `+add @username`, `+add <user_id>`, or reply `+add` to a user's message.",
                parse_mode="Markdown"
            )
            return

    if not target_user_id and not target_username:
        await update.message.reply_text(
            "Could not determine the user to add. Please use @username, user ID, or reply to a message.",
            parse_mode="Markdown"
        )
        return

    db.add_authorized_user(user_id=target_user_id, username=target_username)
    load_authorized_users()

    display = f"`@{target_username}`" if target_username else f"ID `{target_user_id}`"
    await update.message.reply_text(
        f"User {display} has been authorized to use the bot.",
        parse_mode="Markdown"
    )


async def admin_list_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List authorized users (admin only)."""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text(
            "You are not authorized to use this command.",
            parse_mode="Markdown"
        )
        return

    users = db.get_authorized_users()
    lines = []
    for entry in users:
        uid = entry.get("user_id")
        uname = entry.get("username")
        if uid:
            lines.append(f"ID `{uid}`" + (f" (`@{uname}`)" if uname else ""))
        elif uname:
            lines.append(f"`@{uname}`")
    if not lines:
        await update.message.reply_text("No authorized users yet.")
        return
    await update.message.reply_text(
        "Authorized users:\n\n" + "\n".join(lines),
        parse_mode="Markdown"
    )


async def admin_remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a user from the authorized users list (admin only)."""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text(
            "You are not authorized to use this command.",
            parse_mode="Markdown"
        )
        return

    if update.message.reply_to_message:
        target_user = update.message.reply_to_message.from_user
        db.remove_authorized_user(user_id=target_user.id, username=target_user.username)
        load_authorized_users()
        display = f"`@{target_user.username}`" if target_user.username else f"ID `{target_user.id}`"
        await update.message.reply_text(
            f"User {display} has been removed.",
            parse_mode="Markdown"
        )
        return

    args = context.args if context.args else update.message.text.split()[1:]
    if not args:
        await update.message.reply_text(
            "Usage: `+remove @username`, `+remove <user_id>`, or reply `+remove` to a user's message.",
            parse_mode="Markdown"
        )
        return

    arg = args[0].strip()
    if arg.startswith("@"):
        db.remove_authorized_user(username=arg[1:])
    elif arg.isdigit():
        db.remove_authorized_user(user_id=int(arg))
    else:
        await update.message.reply_text(
            "Usage: `+remove @username`, `+remove <user_id>`, or reply `+remove` to a user's message.",
            parse_mode="Markdown"
        )
        return

    load_authorized_users()
    await update.message.reply_text(
        "User has been removed from authorized users.",
        parse_mode="Markdown"
    )


def get_friendly_error(error) -> str:
    error_str = str(error) if error else ""
    error_str_lower = error_str.lower()

    # If the error already contains detailed balance/fee info, return it as-is
    if "your balance:" in error_str_lower and "required" in error_str_lower:
        return error_str
    if "have" in error_str_lower and ("need" in error_str_lower or "required" in error_str_lower):
        return error_str

    if "insufficient funds" in error_str_lower or "balance 0" in error_str_lower:
        if "gas" in error_str_lower:
            return (
                "Insufficient native token for gas fees!\n\n"
                "For token withdrawals (USDT, USDC), you need the native "
                "coin (BNB on BSC, ETH on Ethereum, MATIC on Polygon) to pay "
                "for network fees.\n\n"
                "Please deposit some native tokens first."
            )
        return (
            "Insufficient balance! Your wallet doesn't have enough funds "
            "to cover the transaction and gas fees.\n\n"
            "Please deposit more funds first."
        )
    if "transfer amount exceeds balance" in error_str or "exceeds balance" in error_str:
        return (
            "Insufficient token balance! The amount you're trying to send "
            "exceeds your available balance.\n\n"
            "Please reduce the amount or deposit more tokens."
        )
    if "nonce too low" in error_str or "replacement transaction" in error_str:
        return (
            "Previous transaction still pending — try again shortly."
        )
    if "execution reverted" in error_str or "revert" in error_str:
        return (
            "Transaction was rejected by the network.\n\n"
            "Please double-check the token and network, then try again."
        )
    if "rate limit" in error_str or "429" in error_str or "too many requests" in error_str:
        return (
            "Network is currently busy.\n\n"
            "Please try again in a minute."
        )
    if "invalid address" in error_str or "checksum" in error_str:
        return (
            "Invalid wallet address!\n\n"
            "Please check the address format and try again."
        )
    if "timeout" in error_str or "timed out" in error_str:
        return (
            "Network request timed out.\n\n"
            "Please check your connection and try again."
        )
    if "connection" in error_str or "network" in error_str:
        return (
            "Unable to connect to the network.\n\n"
            "Please try again later."
        )

    return "Something went wrong. Please try again later."


def get_main_menu_keyboard():
    keyboard = [
        [
            ikb("Wallets", callback_data="menu_wallets", ui_name="wallet"),
            ikb("Deposit", callback_data="menu_deposit", ui_name="deposit")
        ],
        [
            ikb("Withdraw", callback_data="menu_withdraw", ui_name="withdraw"),
            ikb("Balances", callback_data="menu_balance", ui_name="balance")
        ],
        [
            ikb("Convert", callback_data="menu_convert", ui_name="convert"),
            ikb("New Wallet", callback_data="menu_generate", ui_name="card")
        ],
        [
            ikb("Help", callback_data="menu_help", ui_name="help")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_token_keyboard():
    keyboard = []
    for token_key, token_info in TOKENS.items():
        keyboard.append([
            ikb(
                f"{token_info['name']} ({token_info['symbol']})",
                callback_data=f"token_{token_key}",
                token=token_info['symbol'], emoji_fallback=token_info['icon']
            )
        ])
    keyboard.append([
        ikb("Main Menu", callback_data="main_menu", ui_name="home")
    ])
    return InlineKeyboardMarkup(keyboard)


def get_token_network_keyboard(token: str):
    token_info = TOKENS.get(token)
    if not token_info:
        return get_back_button("menu_tokens")

    keyboard = []
    for network in token_info["networks"].keys():
        network_info = NETWORKS.get(network)
        if network_info:
            keyboard.append([
                ikb(
                    network_info['name'],
                    callback_data=f"tokenbal_{token}_{network}",
                    emoji_id=net_emoji_id(network), emoji_fallback=network_info['icon']
                )
            ])
    keyboard.append([
        ikb("Back", callback_data="menu_tokens", emoji_fallback="\U0001F519")
    ])
    keyboard.append([
        ikb("Main Menu", callback_data="main_menu", ui_name="home")
    ])
    return InlineKeyboardMarkup(keyboard)


def get_network_keyboard(action: str, include_tokens: bool = True):
    keyboard = []
    row = []

    for token_key, token_info in TOKENS.items():
        for network_key in token_info.get("networks", {}).keys():
            network_short = network_key
            if network_key == "TRON":
                network_short = "TRX"
            elif network_key == "SOLANA":
                network_short = "SOL"

            btn_text = f"{token_key}[{network_short}]"
            callback = f"{action}_combo_{token_key}_{network_key}"

            btn = ikb(btn_text, callback_data=callback,
                      token=token_info['symbol'], emoji_fallback=token_info['icon'])
            row.append(btn)
            if len(row) == 2:
                keyboard.append(row)
                row = []

    if row:
        keyboard.append(row)

    keyboard.append([
        ikb("Main Menu", callback_data="main_menu", ui_name="home")
    ])
    return InlineKeyboardMarkup(keyboard)


def get_generate_network_keyboard():
    """Keyboard for generate wallet - shows only networks (not token/network combos)."""
    keyboard = []
    row = []

    for network_key, network_info in NETWORKS.items():
        btn_text = network_info['name']
        callback = f"gen_{network_key}"

        btn = ikb(btn_text, callback_data=callback,
                  emoji_id=net_emoji_id(network_key), emoji_fallback=network_info['icon'])
        row.append(btn)
        if len(row) == 2:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    keyboard.append([
        ikb("Main Menu", callback_data="main_menu", ui_name="home")
    ])
    return InlineKeyboardMarkup(keyboard)


def get_back_button(callback_data: str = "main_menu"):
    return InlineKeyboardMarkup([
        [ikb("Back", callback_data=callback_data, emoji_fallback="\U0001F519")]
    ])


def get_wallet_card_keyboard(network: str):
    keyboard = [
        [
            ikb("Refresh", callback_data=f"refresh_{network}", ui_name="refresh"),
            ikb("Deposit", callback_data=f"deposit_{network}", ui_name="deposit")
        ],
        [
            ikb("Withdraw", callback_data=f"withdraw_{network}", ui_name="withdraw"),
            ikb("Explorer", callback_data=f"explorer_{network}", ui_name="explorer")
        ],
        [
            ikb("Main Menu", callback_data="main_menu", ui_name="home")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def format_address(address: str) -> str:
    if len(address) > 20:
        return f"{address[:8]}...{address[-6:]}"
    return address


def build_main_menu_text(user_id: int) -> str:
    """Build main menu text with wallet count and balance.
    
    Uses internal ledger balance for fast response (no RPC calls).
    """
    wallets = db.get_all_wallets(user_id)
    wallet_count = len(wallets) if wallets else 0

    total_usdt_value = Decimal("0")
    balances = db.get_all_internal_balances(user_id)
    for asset, balance in (balances or {}).items():
        if asset in ["USDT", "USDC"]:
            try:
                total_usdt_value += Decimal(str(balance))
            except Exception:
                pass

    menu_text = (
        f"{h_html('portfolio', 'Your Portfolio')}\n\n"
        f"{ui('wallet')}  <b>Wallets</b>   <code>{wallet_count}</code>\n"
        f"{ui('cash')}  <b>Balance</b>   <code>${total_usdt_value:.2f}</code>"
    )
    return menu_text


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or "User"
    username = update.effective_user.username
    logger.info(f"User {user_name} ({user_id}) started bot")

    if not is_authorized(user_id, username):
        await update.message.reply_text(
            "*Access Denied*\nYou are not authorized to use this bot.",
            parse_mode="Markdown"
        )
        return

    if user_id not in AUTHORIZED_USER_IDS and username and username.lower().lstrip("@") in AUTHORIZED_USERNAMES:
        db.add_authorized_user(user_id=user_id, username=username)
        load_authorized_users()

    menu_text = build_main_menu_text(user_id)
    
    banner_path = get_banner_path("welcome")
    if os.path.exists(banner_path):
        with open(banner_path, "rb") as photo:
            await update.message.reply_photo(
                photo=photo,
                caption=menu_text,
                parse_mode="HTML",
                reply_markup=get_main_menu_keyboard()
            )
    else:
        await update.message.reply_text(
            menu_text,
            parse_mode="HTML",
            reply_markup=get_main_menu_keyboard()
        )


async def send_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text(
            "\U0001F6AB *You are not authorised to use the bot!*",
            parse_mode="Markdown"
        )
        return

    args = context.args

    # Owner-only messaging shortcut: /send <chat_id> <message>
    if is_admin(user_id) and args:
        chat_id_arg = args[0]
        if chat_id_arg.startswith("@") or chat_id_arg.lstrip("-").isdigit():
            text = update.message.text or ""
            match = re.match(r"^/send(?:@\w+)?\s+", text)
            rest = text[match.end():] if match else ""
            parts = rest.split(None, 1)
            if len(parts) < 2:
                await update.message.reply_text(
                    "Usage: `/send <chat_id> <message>`",
                    parse_mode="Markdown"
                )
                return
            chat_id_raw, message_text = parts[0], parts[1]
            if chat_id_raw.lstrip("-").isdigit():
                chat_id = int(chat_id_raw)
            else:
                chat_id = chat_id_raw

            def _utf16_len(s: str) -> int:
                return len(s.encode("utf-16-le")) // 2

            try:
                entities = update.message.entities or ()
                prefix = text[: len(text) - len(message_text)]
                prefix_len = _utf16_len(prefix)
                message_len = _utf16_len(message_text)
                new_entities = []
                for entity in entities:
                    if entity.offset < prefix_len:
                        continue
                    end = min(
                        entity.offset + entity.length,
                        prefix_len + message_len,
                    )
                    if end <= entity.offset:
                        continue
                    new_entities.append(
                        MessageEntity(
                            type=entity.type,
                            offset=entity.offset - prefix_len,
                            length=end - entity.offset,
                            url=entity.url,
                            user=entity.user,
                            language=entity.language,
                            custom_emoji_id=entity.custom_emoji_id,
                        )
                    )

                await context.bot.send_message(
                    chat_id=chat_id,
                    text=message_text,
                    entities=new_entities or None,
                )
                await update.message.reply_text(
                    f"Message sent to `{chat_id_raw}`.",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to send message to {chat_id_raw}: {e}")
                await update.message.reply_text(
                    f"Failed to send message to {chat_id_raw}:\n{e}",
                )
            return

    if not args:
        usage_text = (
            f"{h_html('deposit', 'Send Command')}\n\n"
            f"{ui('note')}  <b>Format:</b> <code>/send &lt;token&gt; [network]</code>\n\n"
            f"{ui('idea')}  <b>Examples:</b>\n"
            "    <code>/send USDT BSC</code>\n"
            "    <code>/send USDC ETH</code>\n"
            "    <code>/send ETH</code>\n"
            "    <code>/send BNB</code>\n"
            "    <code>/send SOL</code>\n"
            "    <code>/send TRX</code>\n"
            "    <code>/send LTC</code>\n\n"
            "<i>Network is auto-detected for single-network tokens</i>\n\n"
            f"{ui('coin')}  <b>Available Tokens:</b>\n"
            "    ETH, BNB, MATIC, SOL, TRX, LTC, USDT, USDC\n\n"
            f"{ui('explorer')}  <b>Available Networks:</b>\n"
            "    ETH, BSC, POLYGON, SOLANA, TRON, LTC"
        )
        await update.message.reply_text(usage_text, parse_mode="HTML")
        return

    token = detect_token_from_text(args[0])
    if not token:
        token = args[0].upper()

    if len(args) >= 2:
        network = detect_network_from_text(args[1])
        if not network:
            network = args[1].upper()
    else:
        available = get_available_networks_for_token(token)
        if len(available) == 1:
            network = available[0]
        else:
            networks_str = ", ".join(available) if available else "N/A"
            await update.message.reply_text(
                f"\U0001F4E4 *Send {token}*\n\n"
                f"Multiple networks available: `{networks_str}`\n\n"
                f"Please specify: `/send {token} <network>`",
                parse_mode="Markdown"
            )
            return

    if network not in NETWORKS:
        await update.message.reply_text(
            f"\u274C *Invalid Network*\n\n"
            f"Network `{network}` is not supported.\n\n"
            f"\U0001F310 *Available Networks:*\n"
            f"    ETH, BSC, POLYGON, SOLANA, TRON, LTC",
            parse_mode="Markdown"
        )
        return

    if token not in TOKENS:
        await update.message.reply_text(
            f"\u274C *Invalid Token*\n\n"
            f"Token `{token}` is not supported.\n\n"
            f"\U0001F4B5 *Available Tokens:*\n"
            f"    ETH, BNB, MATIC, SOL, TRX, LTC, USDT, USDC",
            parse_mode="Markdown"
        )
        return

    token_info = TOKENS[token]
    if network not in token_info.get("networks", {}):
        available_networks = list(token_info.get("networks", {}).keys())
        await update.message.reply_text(
            f"\u274C *Token Not Available on Network*\n\n"
            f"`{token}` is not available on `{network}`.\n\n"
            f"\U0001F310 *{token} is available on:*\n"
            f"    {', '.join(available_networks)}",
            parse_mode="Markdown"
        )
        return

    wallet = db.get_wallet(user_id, network)
    if not wallet:
        await update.message.reply_text(
            f"\u274C *No Wallet Found*\n\n"
            f"You don't have a wallet for `{network}` yet.\n\n"
            f"\U0001F4A1 Use the menu to generate a wallet first:\n"
            f"    /start \u2192 Generate Wallet \u2192 {NETWORKS[network]['name']}",
            parse_mode="Markdown"
        )
        return

    address = wallet["address"]
    network_info = NETWORKS[network]

    is_native = token_info.get("native", False)
    if is_native:
        balance_info = await BalanceChecker.get_balance(network, address)
    else:
        balance_info = await BalanceChecker.get_token_balance(token, network, address)
    balance_str = balance_info.get("balance", "0")

    response_text = (
        f"{h_html('deposit', 'Deposit Address')}\n\n"
        f"{tok(token)}  <b>Token:</b> {esc(token)}\n"
        f"{net_icon(network)}  <b>Network:</b> {esc(network_info['name'])}\n"
        f"{ui('balance')}  <b>Balance:</b> {esc(balance_str)} {esc(token)}\n\n"
        f"{ui('address')}  <b>Address:</b>\n"
        f"<code>{esc(address)}</code>\n\n"
        f"{ui('warning')}  <b>Important:</b> Only send {esc(token)} on {esc(network_info['name'])} network!"
    )

    keyboard = [
        [InlineKeyboardButton(
            "Refresh Balance",
            callback_data=f"refresh_send_{token}_{network}"
        )],
        [InlineKeyboardButton(
            "View on Explorer",
            url=f"{network_info['explorer']}/address/{address}"
        )],
        [InlineKeyboardButton("Main Menu", callback_data="main_menu")]
    ]

    banner_path = get_banner_path("deposit")
    if os.path.exists(banner_path):
        with open(banner_path, "rb") as photo:
            await update.message.reply_photo(
                photo=photo,
                caption=response_text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    else:
        await update.message.reply_text(
            response_text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def send_photo_with_banner(message, banner_name, text, reply_markup=None, parse_mode: str = "Markdown"):
    banner_path = get_banner_path(banner_name)
    if os.path.exists(banner_path):
        with open(banner_path, "rb") as photo:
            await message.reply_photo(
                photo=photo,
                caption=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup
            )
    else:
        await message.reply_text(
            text,
            parse_mode=parse_mode,
            reply_markup=reply_markup
        )


async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text(
            "*You are not authorised to use the bot!*",
            parse_mode="Markdown"
        )
        return

    chat_id = update.message.chat_id
    network_names = [f"{info['name']} ({key})" for key, info in NETWORKS.items()]
    networks_str = ", ".join(network_names)

    text = (
        "\U0001F4B0 *Check Balance*\n\n"
        "Which network?  _(or 'all')_\n\n"
        f"_{networks_str}_"
    )

    keyboard = [
        [InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]
    ]

    msg = await context.bot.send_photo(
        chat_id=chat_id,
        photo=open(get_banner_path("balance"), "rb"),
        caption=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["balance_msg_id"] = msg.message_id

    return BALANCE_NETWORK


async def wallets_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text(
            "*You are not authorised to use the bot!*",
            parse_mode="Markdown"
        )
        return

    wallets = db.get_all_wallets(user_id)

    if not wallets:
        text = "*Wallets*\n\nNo wallets found.\nCreate your first wallet."
        keyboard = [
            [InlineKeyboardButton("Create Wallet", callback_data="menu_generate")],
            [InlineKeyboardButton("Home", callback_data="main_menu")]
        ]
        await send_photo_with_banner(
            update.message, "wallets", text, InlineKeyboardMarkup(keyboard)
        )
        return

    text = f"*Wallets* ({len(wallets)})"
    keyboard = []

    for wallet in wallets:
        network = wallet["network"]
        info = NETWORKS[network]
        keyboard.append([
            InlineKeyboardButton(info['name'], callback_data=f"wallet_{network}")
        ])

    keyboard.append([InlineKeyboardButton("Add Wallet", callback_data="menu_generate")])
    keyboard.append([InlineKeyboardButton("Home", callback_data="main_menu")])

    await send_photo_with_banner(
        update.message, "wallets", text, InlineKeyboardMarkup(keyboard)
    )


async def deposit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text(
            "*You are not authorised to use the bot!*",
            parse_mode="Markdown"
        )
        return

    chat_id = update.message.chat_id

    text = (
        "\U0001F4E5 *Deposit*\n\n"
        "Which token?\n\n"
        "_e.g. USDT, ETH, SOL_\n\n"
        "_Tap the button below to see all supported tokens_"
    )

    keyboard = [
        [InlineKeyboardButton("\U0001F4CB View Supported Tokens", callback_data="show_tokens_list_deposit")],
        [InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]
    ]

    msg = await context.bot.send_photo(
        chat_id=chat_id,
        photo=open(get_banner_path("deposit"), "rb"),
        caption=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["deposit_msg_id"] = msg.message_id

    return DEPOSIT_TOKEN


async def withdraw_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text(
            "*You are not authorised to use the bot!*",
            parse_mode="Markdown"
        )
        return

    chat_id = update.message.chat_id
    clear_withdrawal_state(user_id, context)

    if context.args and len(context.args) >= 1:
        address = context.args[0].strip()
        detected = detect_network_from_address(address)
        
        if detected is None:
            text = (
                "\U0001F4E4 *Withdraw*\n\n"
                "\u274c Invalid address format.\n\n"
                "Please provide a valid wallet address.\n\n"
                "_Supported: EVM (0x...), Tron (T...), Solana, Litecoin, Bitcoin_"
            )
            keyboard = [[InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]]
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=open(get_banner_path("withdraw"), "rb"),
                caption=text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return ConversationHandler.END
        
        context.user_data["withdraw_address"] = address
        
        if isinstance(detected, list):
            context.user_data["withdraw_possible_networks"] = detected
            networks_str = " / ".join(detected)
            text = (
                "\U0001F4E4 *Quick Withdraw*\n\n"
                f"\U0001F4CD Address: `{address[:8]}...{address[-6:]}`\n\n"
                f"This is an EVM address. Which network?\n\n"
                f"Type: `{networks_str}`"
            )
            keyboard = [[InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]]
            msg = await context.bot.send_photo(
                chat_id=chat_id,
                photo=open(get_banner_path("withdraw"), "rb"),
                caption=text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            context.user_data["withdraw_msg_id"] = msg.message_id
            return WITHDRAW_QUICK_NETWORK
        else:
            context.user_data["withdraw_network"] = detected
            balances = db.get_all_internal_balances(user_id)
            usdt_balance = balances.get("USDT", Decimal("0"))
            usdc_balance = balances.get("USDC", Decimal("0"))
            
            network_info = NETWORKS.get(detected, {})
            network_name = network_info.get("name", detected)
            native_token = network_info.get("native_token", detected)
            native_balance = balances.get(native_token, Decimal("0"))
            
            if usdt_balance > 0:
                context.user_data["withdraw_token"] = "USDT"
                token_info = TOKENS.get("USDT", {})
                context.user_data["withdraw_contract"] = token_info.get("networks", {}).get(detected, {}).get("address")
                text = (
                    "\U0001F4E4 *Quick Withdraw USDT*\n\n"
                    f"\U0001F4CD To: `{address[:8]}...{address[-6:]}`\n"
                    f"\U0001F310 Network: *{network_name}*\n"
                    f"\U0001F4B0 Balance: `{usdt_balance:.4f} USDT`\n\n"
                    "Enter the amount to withdraw:"
                )
            elif usdc_balance > 0:
                context.user_data["withdraw_token"] = "USDC"
                token_info = TOKENS.get("USDC", {})
                context.user_data["withdraw_contract"] = token_info.get("networks", {}).get(detected, {}).get("address")
                text = (
                    "\U0001F4E4 *Quick Withdraw USDC*\n\n"
                    f"\U0001F4CD To: `{address[:8]}...{address[-6:]}`\n"
                    f"\U0001F310 Network: *{network_name}*\n"
                    f"\U0001F4B0 Balance: `{usdc_balance:.4f} USDC`\n\n"
                    "Enter the amount to withdraw:"
                )
            elif native_balance > 0:
                context.user_data["withdraw_token"] = native_token
                context.user_data["withdraw_contract"] = None
                text = (
                    f"\U0001F4E4 *Quick Withdraw {native_token}*\n\n"
                    f"\U0001F4CD To: `{address[:8]}...{address[-6:]}`\n"
                    f"\U0001F310 Network: *{network_name}*\n"
                    f"\U0001F4B0 Balance: `{native_balance:.6f} {native_token}`\n\n"
                    "Enter the amount to withdraw:"
                )
            else:
                text = (
                    "\U0001F4E4 *Quick Withdraw*\n\n"
                    f"\U0001F4CD To: `{address[:8]}...{address[-6:]}`\n"
                    f"\U0001F310 Network: *{network_name}*\n\n"
                    "\u274c No balance available on this network.\n"
                    "Please deposit funds first."
                )
                keyboard = [[InlineKeyboardButton("\U0001F3E0 Home", callback_data="main_menu")]]
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=open(get_banner_path("withdraw"), "rb"),
                    caption=text,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return ConversationHandler.END
            
            keyboard = [[InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]]
            msg = await context.bot.send_photo(
                chat_id=chat_id,
                photo=open(get_banner_path("withdraw"), "rb"),
                caption=text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            context.user_data["withdraw_msg_id"] = msg.message_id
            return WITHDRAW_AMOUNT

    text = (
        "\U0001F4E4 *Withdraw*\n\n"
        "Which token?\n\n"
        "_e.g. USDT, ETH, SOL_\n\n"
        "_Tip: Use `/withdraw <address>` for quick withdraw_"
    )

    keyboard = [
        [InlineKeyboardButton("\U0001F4CB View Supported Tokens", callback_data="show_tokens_list_withdraw")],
        [InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]
    ]

    msg = await context.bot.send_photo(
        chat_id=chat_id,
        photo=open(get_banner_path("withdraw"), "rb"),
        caption=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["withdraw_msg_id"] = msg.message_id

    return WITHDRAW_TOKEN


async def generate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text(
            "*You are not authorised to use the bot!*",
            parse_mode="Markdown"
        )
        return

    chat_id = update.message.chat_id
    network_names = [f"{info['name']} ({key})" for key, info in NETWORKS.items()]
    networks_str = ", ".join(network_names)

    text = (
        "\U0001F4B3 *Generate Wallet*\n\n"
        "Which network?\n\n"
        f"_{networks_str}_"
    )

    keyboard = [
        [InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]
    ]

    msg = await context.bot.send_photo(
        chat_id=chat_id,
        photo=open(get_banner_path("generate"), "rb"),
        caption=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["generate_msg_id"] = msg.message_id

    return GENERATE_NETWORK


async def convert_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text(
            "*You are not authorised to use the bot!*",
            parse_mode="Markdown"
        )
        return

    chat_id = update.message.chat_id
    balances = db.get_all_internal_balances(user_id)
    assets_with_balance = [
        asset for asset in CONVERTIBLE_ASSETS
        if balances.get(asset, Decimal("0")) > 0
    ]

    if not assets_with_balance:
        text = "*Convert*\n\nNo assets to convert.\nDeposit funds first."
        keyboard = [[InlineKeyboardButton("Home", callback_data="main_menu")]]
        await send_photo_with_banner(
            update.message, "convert", text, InlineKeyboardMarkup(keyboard)
        )
        return ConversationHandler.END

    balances_str = ", ".join([f"{a} ({balances.get(a, 0):.4f})" for a in assets_with_balance])

    text = (
        "\U0001F504 *Convert Assets*\n\n"
        "Convert from?\n\n"
        f"_{balances_str}_"
    )

    keyboard = [
        [InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]
    ]

    msg = await context.bot.send_photo(
        chat_id=chat_id,
        photo=open(get_banner_path("convert"), "rb"),
        caption=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["convert_msg_id"] = msg.message_id
    context.user_data["convert_balances"] = {a: str(balances.get(a, 0)) for a in assets_with_balance}

    return CONVERT_FROM_ASSET


async def tokens_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text(
            "*You are not authorised to use the bot!*",
            parse_mode="Markdown"
        )
        return

    text = "*Token Balances*\nSelect a token to check:"

    await send_photo_with_banner(
        update.message, "tokens", text, get_token_keyboard()
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text(
            "*You are not authorised to use the bot!*",
            parse_mode="Markdown"
        )
        return

    help_text = (
        f"{h_html('help', 'VM Depo Bot 2.0')}\n"
        "<i>Help Center</i>\n\n"
        f"{h_html('home', 'Navigation')}\n"
        "<code>/start</code> or <code>/menu</code>\n"
        "<i>Open the main menu dashboard</i>\n\n"
        f"{h_html('money', 'Balance & Wallets')}\n"
        "<code>/balance</code>  <i>Check balances across networks</i>\n"
        "<code>/wallets</code>  <i>Manage your wallet addresses</i>\n"
        "<code>/tokens</code>  <i>Token balances by network</i>\n\n"
        f"{h_html('deposit', 'Deposits')}\n"
        "<code>/deposit</code>  <i>Get a deposit address</i>\n"
        "<code>/send TOKEN NETWORK</code>  <i>Quick deposit (e.g. /send USDT BSC)</i>\n\n"
        f"{h_html('search', 'Check')}\n"
        "<code>/check &lt;address&gt;</code>  <i>USDT &amp; USDC balance</i>\n"
        "<code>/check &lt;tx hash|link&gt;</code>  <i>Transaction details</i>\n\n"
        f"{h_html('withdraw', 'Withdrawals')}\n"
        "<code>/withdraw</code>  <i>Send funds to an external wallet</i>\n\n"
        f"{h_html('convert', 'Convert')}\n"
        "<code>/convert</code>  <i>Swap between supported assets</i>\n\n"
        f"{h_html('card', 'Wallet Generation')}\n"
        "<code>/generate</code>  <i>Create a new wallet for any network</i>\n\n"
        f"{SOFT_DIVIDER}\n"
        "<i>Securely Made By Venom</i>"
    )

    await send_photo_with_banner(
        update.message, "help", help_text, get_back_button("main_menu"), parse_mode="HTML"
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel any active conversation and return to main menu."""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text(
            "*You are not authorised to use the bot!*",
            parse_mode="Markdown"
        )
        return ConversationHandler.END
    
    context.user_data.clear()
    
    if user_id in pending_withdrawals:
        del pending_withdrawals[user_id]
    
    await update.message.reply_text(
        "\u274c *Cancelled*",
        parse_mode="Markdown"
    )
    return ConversationHandler.END


async def _check_transaction(update: Update, networks, tx_hash: str):
    """Fetch and display transaction details for a tx hash/link."""
    candidates = [networks] if isinstance(networks, str) else list(networks)

    loading_msg = await update.message.reply_text(
        f"{ui('search')}  {fancy('Fetching Transaction')}\n\n"
        f"<code>{esc(tx_hash[:10])}...{esc(tx_hash[-8:])}</code>",
        parse_mode="HTML"
    )

    result = None
    used_network = None
    for net in candidates:
        try:
            info = await TransactionChecker.get_transaction(net, tx_hash)
        except Exception as e:
            logger.error(f"Error fetching tx on {net}: {e}")
            continue
        if info and not info.get("error"):
            result = info
            used_network = net
            break

    if not result:
        fallback_net = candidates[0]
        keyboard = [[InlineKeyboardButton(
            "\U0001F310 View on Explorer",
            url=tx_explorer_url(fallback_net, tx_hash)
        )]]
        await loading_msg.edit_text(
            f"{h_html('error', 'Transaction Not Found')}\n\n"
            f"Couldn't fetch details on: {esc(', '.join(candidates))}.\n"
            "It may be pending, very recent, or on a different network.\n\n"
            f"<code>{esc(tx_hash[:10])}...{esc(tx_hash[-8:])}</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    net_info = NETWORKS[used_network]
    sender = result.get("from") or "Unknown"
    receiver = result.get("to") or "Unknown"
    amount = result.get("amount")
    asset = result.get("asset") or ""
    status = result.get("status") or "Unknown"
    when = format_ist(result.get("timestamp"))
    h = result.get("hash", tx_hash)
    amount_str = f"{amount} {asset}".strip() if amount is not None else "Unknown"

    lines = [
        f"{ui('note')}  {fancy('Transaction Details')}\n\n",
        f"{net_icon(used_network)}  <b>Network:</b> {esc(net_info['name'])}\n",
        f"{ui('chart')}  <b>Status:</b> {esc(status)}\n",
        f"{tok(asset)}  <b>Amount:</b> <code>{esc(amount_str)}</code>\n",
        f"{ui('user')}  <b>From:</b> <code>{esc(sender)}</code>\n",
        f"{ui('deposit')}  <b>To:</b> <code>{esc(receiver)}</code>\n",
        f"{ui('time')}  <b>Date/Time:</b> {esc(when)}\n",
        f"{ui('link')}  <b>Tx Hash:</b> <code>{esc(h[:12])}...{esc(h[-10:])}</code>\n",
    ]
    keyboard = [[InlineKeyboardButton(
        "\U0001F310 View on Explorer",
        url=result.get("explorer_url") or tx_explorer_url(used_network, tx_hash)
    )]]
    await loading_msg.edit_text(
        "".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def _check_address(update: Update, address: str, detected, message=None):
    """Fetch and display USDT/USDC balances for an address, with explorer buttons."""
    if isinstance(detected, list):
        valid = is_valid_address(address, detected[0])
    else:
        valid = is_valid_address(address, detected)

    if not valid:
        if update.message:
            await update.message.reply_text(
                "\u274c *Invalid Address Format*\n\n"
                "The address format is not valid for the detected network.\n"
                "Please check the address and try again.",
                parse_mode="Markdown"
            )
        return

    loading_text = (
        f"{ui('search')}  {fancy('Checking Balances')}\n\n"
        f"<code>{esc(address[:8])}...{esc(address[-6:])}</code>"
    )
    if message is None:
        loading_msg = await update.message.reply_text(
            loading_text,
            parse_mode="HTML"
        )
    else:
        loading_msg = message
        await loading_msg.edit_text(loading_text, parse_mode="HTML")

    stablecoins = ["USDT", "USDC"]
    result_lines = [
        f"{ui('money')}  {fancy('USDT & USDC Balance')}\n",
        f"{ui('address')}  <code>{esc(address[:8])}...{esc(address[-6:])}</code>\n"
    ]
    explorer_buttons = []

    try:
        if isinstance(detected, list):
            result_lines.append(f"\n{ui('explorer')}  <b>EVM Networks</b>\n")
            for network in detected:
                network_name = NETWORKS[network]["name"]
                result_lines.append(f"\n{net_icon(network)}  <b>{esc(network_name)}</b>\n")
                for token in stablecoins:
                    try:
                        balance_info = await BalanceChecker.get_token_balance(token, network, address)
                        if balance_info.get("error"):
                            continue
                        balance = balance_info.get("balance", "0")
                        result_lines.append(f"{tok(token)}  <code>{esc(balance)}</code> {esc(token)}\n")
                    except Exception as e:
                        logger.error(f"Error checking {token} on {network}: {e}")
                        result_lines.append(f"{tok(token)}  {esc(token)}: Error\n")
                explorer_buttons.append(InlineKeyboardButton(
                    f"\U0001F310 {network_name}",
                    url=address_explorer_url(network, address)
                ))
        else:
            network = detected
            network_name = NETWORKS.get(network, {}).get("name", network)
            net_type = NETWORKS.get(network, {}).get("type", "")
            result_lines[0] = f"{ui('money')}  {fancy(network_name + ' Balance')}\n"
            result_lines.append(f"\n{net_icon(network)}  <b>Network:</b> {esc(network_name)}\n\n")

            if net_type in ("btc", "ltc"):
                try:
                    balance_info = await BalanceChecker.get_balance(network, address)
                    balance = balance_info.get("balance", "0")
                    symbol = balance_info.get("symbol", network)
                    result_lines.append(f"{tok(symbol)}  <b>{esc(symbol)} Balance:</b> <code>{esc(balance)}</code>\n")
                except Exception as e:
                    logger.error(f"Error checking {network} balance: {e}")
                    result_lines.append(f"{ui('error')}  Error checking balance\n")
            else:
                for token in stablecoins:
                    try:
                        balance_info = await BalanceChecker.get_token_balance(token, network, address)
                        if balance_info.get("error"):
                            continue
                        balance = balance_info.get("balance", "0")
                        result_lines.append(f"{tok(token)}  <b>{esc(token)} Balance:</b> <code>{esc(balance)}</code>\n")
                    except Exception as e:
                        logger.error(f"Error checking {token} on {network}: {e}")
                        result_lines.append(f"{ui('error')}  Error checking {esc(token)} balance\n")
            explorer_buttons.append(InlineKeyboardButton(
                "\U0001F310 View on Explorer",
                url=address_explorer_url(network, address)
            ))

        keyboard = [explorer_buttons[i:i + 2] for i in range(0, len(explorer_buttons), 2)]
        keyboard.append([InlineKeyboardButton("Refresh", callback_data=f"check_refresh_{address}")])
        await loading_msg.edit_text(
            "".join(result_lines),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Error in check_command (address): {e}")
        await loading_msg.edit_text(
            f"{h_html('error', 'Error')}\n\nFailed to check balance: {esc(str(e))}",
            parse_mode="HTML"
        )


async def refresh_check_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Re-fetch balances for an address from its existing /check message."""
    query = update.callback_query
    await query.answer("Refreshing...")

    prefix = "check_refresh_"
    if not query.data or not query.data.startswith(prefix):
        return

    address = query.data[len(prefix):]
    detected = detect_network_from_address(address)
    if not detected:
        await query.edit_message_text(
            "\u274c Could not detect network for refresh.",
            parse_mode="Markdown"
        )
        return

    await _check_address(update, address, detected, message=query.message)


async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check a wallet address (balances) or a transaction hash/link (details)."""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text(
            "*You are not authorised to use the bot!*",
            parse_mode="Markdown"
        )
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "\u26a0\ufe0f *Usage:* `/check <address | tx hash | tx link>`\n\n"
            "Examples:\n"
            "`/check 0x1234...` \u2013 Address balance (ETH/BSC/Polygon)\n"
            "`/check T1234...` \u2013 Tron address balance\n"
            "`/check 0xabc...<64 hex>` \u2013 Transaction details\n"
            "`/check https://bscscan.com/tx/0x...` \u2013 Transaction details",
            parse_mode="Markdown"
        )
        return

    query = args[0].strip()

    link = parse_explorer_link(query)
    if link:
        kind, link_network, value = link
        if kind == "tx":
            await _check_transaction(update, link_network, value)
            return
        detected = detect_network_from_address(value) or link_network
        await _check_address(update, value, detected)
        return

    tx_detected = detect_tx_hash(query)
    if tx_detected:
        networks, tx_hash = tx_detected
        await _check_transaction(update, networks, tx_hash)
        return

    detected = detect_network_from_address(query)
    if not detected:
        await update.message.reply_text(
            "\u274c *Invalid Input*\n\n"
            "Could not detect an address or transaction from your input.\n"
            "Supported:\n"
            "\u2022 Address \u2013 EVM (0x...), Tron (T...), Solana, Litecoin, Bitcoin\n"
            "\u2022 Transaction hash or explorer link",
            parse_mode="Markdown"
        )
        return

    await _check_address(update, query, detected)


async def notification_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle deposit notifications on/off."""
    global notifications_enabled
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text(
            "*You are not authorised to use the bot!*",
            parse_mode="Markdown"
        )
        return
    
    status = "\u2705 Enabled" if notifications_enabled else "\u274c Disabled"
    
    keyboard = []
    if notifications_enabled:
        keyboard.append([InlineKeyboardButton("\u23f8 Stop Notifications", callback_data="notif_stop")])
    else:
        keyboard.append([InlineKeyboardButton("\u25b6 Resume Notifications", callback_data="notif_resume")])
    keyboard.append([InlineKeyboardButton("\U0001F519 Back to Menu", callback_data="main_menu")])
    
    await update.message.reply_text(
        "\U0001F514 *Notification Settings*\n\n"
        f"*Current Status:* {status}\n\n"
        "Deposit notifications alert you when funds are received in your wallets.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def toggle_notifications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle notification toggle buttons."""
    global notifications_enabled
    if not await check_callback_auth(update):
        return
    query = update.callback_query
    await query.answer()
    
    action = query.data.split("_")[1]
    
    if action == "stop":
        notifications_enabled = False
        status = "\u274c Disabled"
        keyboard = [[InlineKeyboardButton("\u25b6 Resume Notifications", callback_data="notif_resume")]]
    else:
        notifications_enabled = True
        status = "\u2705 Enabled"
        keyboard = [[InlineKeyboardButton("\u23f8 Stop Notifications", callback_data="notif_stop")]]
    
    keyboard.append([InlineKeyboardButton("\U0001F519 Back to Menu", callback_data="main_menu")])
    
    await query.edit_message_text(
        "\U0001F514 *Notification Settings*\n\n"
        f"*Current Status:* {status}\n\n"
        "Deposit notifications alert you when funds are received in your wallets.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text(
            "*You are not authorised to use the bot!*",
            parse_mode="Markdown"
        )
        return

    # Delete the user's command message
    try:
        await update.message.delete()
    except Exception:
        pass

    # Send loading message and store reference to delete later
    loading_msg = await update.message.chat.send_message(
        "*Syncing balances…*",
        parse_mode="Markdown"
    )

    wallets = db.get_all_wallets(user_id)
    if not wallets:
        await update.message.reply_text(
            "*No wallets found.*\nGenerate a wallet first.",
            parse_mode="Markdown"
        )
        return

    synced = []
    
    # First, aggregate token balances across all networks
    # Tokens like USDT/USDC share a single ledger entry across networks
    token_totals = {"USDT": Decimal("0"), "USDC": Decimal("0")}
    token_details = {"USDT": [], "USDC": []}
    
    for wallet in wallets:
        network = wallet["network"]
        address = wallet["address"]

        # Sync native balances (these are per-network, so direct update is fine)
        try:
            balance_info = await BalanceChecker.get_balance(network, address)
            if balance_info.get("error"):
                logger.error(f"Error fetching balance for {network}: {balance_info.get('error')}")
                continue
            onchain_balance = Decimal(balance_info.get("balance", "0"))
            ledger_asset = get_ledger_asset(network)
            internal_balance = db.get_internal_balance(user_id, ledger_asset)
            diff = onchain_balance - internal_balance

            if diff != Decimal("0"):
                # Directly set internal balance to match on-chain balance
                db.update_internal_balance(user_id, ledger_asset, onchain_balance)
                db.log_ledger(user_id, ledger_asset, "sync", str(diff), network)
                if diff > Decimal("0"):
                    synced.append(f"{ledger_asset}: +{diff:.6f}")
                else:
                    synced.append(f"{ledger_asset}: {diff:.6f}")

        except Exception as e:
            logger.error(f"Error syncing {network}: {e}")

        # Collect token balances across all networks (don't update yet)
        for token_key in ["USDT", "USDC"]:
            token_info = TOKENS.get(token_key, {})
            if network not in token_info.get("networks", {}):
                continue
            network_token = token_info["networks"][network]
            if network_token.get("native"):
                continue

            try:
                token_balance_info = await BalanceChecker.get_token_balance(
                    token_key, network, address
                )
                if token_balance_info.get("error"):
                    continue

                onchain_token = Decimal(token_balance_info.get("balance", "0"))
                token_totals[token_key] += onchain_token
                if onchain_token > Decimal("0"):
                    token_details[token_key].append(f"{network}: {onchain_token:.6f}")

            except Exception as e:
                logger.error(f"Error fetching {token_key} on {network}: {e}")

    # Now update token balances with the aggregated totals
    for token_key in ["USDT", "USDC"]:
        total_onchain = token_totals[token_key]
        ledger_asset = get_ledger_asset(None, token_key)  # Just returns token_key
        internal_balance = db.get_internal_balance(user_id, ledger_asset)
        diff = total_onchain - internal_balance

        if diff != Decimal("0"):
            db.update_internal_balance(user_id, ledger_asset, total_onchain)
            db.log_ledger(user_id, ledger_asset, "sync", str(diff), "ALL")
            if diff > Decimal("0"):
                synced.append(f"{ledger_asset}: +{diff:.6f}")
            else:
                synced.append(f"{ledger_asset}: {diff:.6f}")
            # Add network breakdown if there are details
            if token_details[token_key]:
                synced.append(f"  ({', '.join(token_details[token_key])})")

    if synced:
        msg = "*Sync Complete!*\n\n*Updated:*\n" + "\n".join(synced)
    else:
        msg = "*Sync Complete!*\n\nAll balances already synced."

    # Delete the loading message before sending final result
    try:
        await loading_msg.delete()
    except Exception:
        pass

    await send_photo_with_banner(
        update.message, "balance", msg, get_back_button("main_menu")
    )


async def fix_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Scan and fix any errors in the bot's database and state."""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text(
            "*You are not authorised to use the bot!*",
            parse_mode="Markdown"
        )
        return

    # Delete the user's command message
    try:
        await update.message.delete()
    except Exception:
        pass

    # Send loading message and store reference to delete later
    loading_msg = await update.message.chat.send_message(
        "*Scanning for errors…*",
        parse_mode="Markdown"
    )

    fixes = []
    errors = []

    try:
        # 1. Re-initialize database tables to ensure schema is up to date
        db.init_db()
        fixes.append("Database schema verified")

        # 2. Clear stale balance cache
        global wallet_balances_cache
        cache_count = len(wallet_balances_cache)
        wallet_balances_cache = {}
        if cache_count > 0:
            fixes.append(f"Cleared {cache_count} cached balances")

        # 3. Check and fix internal balances for all wallets
        conn = sqlite3.connect(db.db_path)
        cursor = conn.cursor()

        # Get all unique user_ids from wallets table
        cursor.execute("SELECT DISTINCT user_id FROM wallets")
        wallet_users = [row[0] for row in cursor.fetchall()]

        # For each user, ensure they have internal balance entries
        for uid in wallet_users:
            # Get all wallets for this user
            cursor.execute(
                "SELECT network FROM wallets WHERE user_id = ?",
                (uid,)
            )
            user_wallets = cursor.fetchall()

            for (network,) in user_wallets:
                ledger_asset = get_ledger_asset(network)

                # Check if internal balance exists
                cursor.execute(
                    "SELECT balance FROM internal_balances WHERE user_id = ? AND asset = ?",
                    (uid, ledger_asset)
                )
                balance_row = cursor.fetchone()

                if not balance_row:
                    # Create missing internal balance entry
                    cursor.execute(
                        "INSERT INTO internal_balances (user_id, asset, balance) VALUES (?, ?, '0')",
                        (uid, ledger_asset)
                    )
                    fixes.append(f"Created missing balance entry for user {uid}: {ledger_asset}")

        # 4. Fix negative balances
        cursor.execute(
            "SELECT user_id, asset, balance FROM internal_balances WHERE CAST(balance AS REAL) < 0"
        )
        negative_balances = cursor.fetchall()
        for uid, asset, balance in negative_balances:
            cursor.execute(
                "UPDATE internal_balances SET balance = '0' WHERE user_id = ? AND asset = ?",
                (uid, asset)
            )
            fixes.append(f"Fixed negative balance for user {uid}: {asset} ({balance} -> 0)")

        # 5. Ensure user_settings exist for all users with wallets
        for uid in wallet_users:
            cursor.execute(
                "SELECT current_interface FROM user_settings WHERE user_id = ?",
                (uid,)
            )
            if not cursor.fetchone():
                cursor.execute(
                    "INSERT INTO user_settings (user_id, current_interface) VALUES (?, 1)",
                    (uid,)
                )
                fixes.append(f"Created missing user_settings for user {uid}")

        # 6. Check for orphaned ledger entries (optional cleanup)
        cursor.execute(
            "SELECT COUNT(*) FROM ledger WHERE status IS NULL OR status = ''"
        )
        orphaned_count = cursor.fetchone()[0]
        if orphaned_count > 0:
            cursor.execute(
                "UPDATE ledger SET status = 'completed' WHERE status IS NULL OR status = ''"
            )
            fixes.append(f"Fixed {orphaned_count} ledger entries with missing status")

        # 7. Verify wallet encryption keys are valid
        cursor.execute("SELECT user_id, network, encrypted_private_key FROM wallets LIMIT 10")
        sample_wallets = cursor.fetchall()
        decryption_errors = 0
        for uid, network, enc_key in sample_wallets:
            try:
                CryptoUtils.decrypt_private_key(enc_key)
            except Exception:
                decryption_errors += 1

        if decryption_errors > 0:
            errors.append(f"{decryption_errors} wallets have encryption issues")

        conn.commit()
        conn.close()

        # 8. Verify RPC connections for each network
        rpc_status = []
        for network_key, network_info in NETWORKS.items():
            try:
                if network_info.get("type") == "evm":
                    w3 = Web3(Web3.HTTPProvider(network_info["rpc"]))
                    if w3.is_connected():
                        rpc_status.append(f"{network_key}: OK")
                    else:
                        rpc_status.append(f"{network_key}: FAILED")
                        errors.append(f"{network_key} RPC not responding")
            except Exception as e:
                rpc_status.append(f"{network_key}: ERROR")
                errors.append(f"{network_key} RPC error: {str(e)[:30]}")

    except Exception as e:
        errors.append(f"Scan error: {str(e)}")
        logger.error(f"Fix command error: {e}")

    # Build response message
    msg_parts = ["*Fix Scan Complete!*\n"]

    if fixes:
        msg_parts.append("\n*Fixes Applied:*")
        for fix in fixes[:15]:  # Limit to 15 fixes to avoid message too long
            msg_parts.append(f"- {fix}")
        if len(fixes) > 15:
            msg_parts.append(f"... and {len(fixes) - 15} more fixes")

    if errors:
        msg_parts.append("\n*Errors Found:*")
        for error in errors[:10]:
            msg_parts.append(f"- {error}")

    if not fixes and not errors:
        msg_parts.append("\nNo issues found. Bot is healthy!")

    # Delete the loading message before sending final result
    try:
        await loading_msg.delete()
    except Exception:
        pass

    await send_photo_with_banner(
        update.message, "help", "\n".join(msg_parts), get_back_button("main_menu")
    )


async def refresh_send_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_callback_auth(update):
        return
    query = update.callback_query
    await query.answer("Refreshing balance...")

    parts = query.data.split("_")
    token = parts[2]
    network = parts[3]

    token_info = TOKENS.get(token)
    network_info = NETWORKS.get(network)
    user_id = query.from_user.id
    wallet = db.get_wallet(user_id, network)

    if not wallet:
        return

    address = wallet["address"]

    is_native = token_info.get("native", False)
    if is_native:
        balance_info = await BalanceChecker.get_balance(network, address)
    else:
        balance_info = await BalanceChecker.get_token_balance(token, network, address)
    balance_str = balance_info.get("balance", "0")

    response_text = (
        f"{h_html('deposit', 'Deposit Address')}\n\n"
        f"{tok(token)}  <b>Token:</b> {esc(token)}\n"
        f"{net_icon(network)}  <b>Network:</b> {esc(network_info['name'])}\n"
        f"{ui('balance')}  <b>Balance:</b> {esc(balance_str)} {esc(token)}\n\n"
        f"{ui('address')}  <b>Address:</b>\n"
        f"<code>{esc(address)}</code>\n\n"
        f"{ui('warning')}  <b>Important:</b> Only send {esc(token)} on {esc(network_info['name'])} network!"
    )

    keyboard = [
        [InlineKeyboardButton(
            "Refresh Balance",
            callback_data=f"refresh_send_{token}_{network}"
        )],
        [InlineKeyboardButton(
            "View on Explorer",
            url=f"{network_info['explorer']}/address/{address}"
        )],
        [InlineKeyboardButton("Main Menu", callback_data="main_menu")]
    ]

    await edit_message_with_banner(
        query, "deposit", response_text, InlineKeyboardMarkup(keyboard), parse_mode="HTML"
    )


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_callback_auth(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    
    if user_id in pending_withdrawals:
        del pending_withdrawals[user_id]
    context.user_data.clear()

    menu_text = build_main_menu_text(user_id)

    await send_new_message_with_banner(
        query, "welcome", menu_text, get_main_menu_keyboard(), parse_mode="HTML"
    )
    
    return ConversationHandler.END


async def show_wallets_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_callback_auth(update):
        return
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    wallets = db.get_all_wallets(user_id)

    if not wallets:
        text = "*Wallets*\n\nNo wallets found.\nCreate your first wallet."
        keyboard = [
            [InlineKeyboardButton("Create Wallet", callback_data="menu_generate")],
            [InlineKeyboardButton("Home", callback_data="main_menu")]
        ]
        await edit_message_with_banner(
            query, "wallets", text, InlineKeyboardMarkup(keyboard)
        )
        return

    text = f"*Wallets* ({len(wallets)})"
    keyboard = []

    for wallet in wallets:
        network = wallet["network"]
        info = NETWORKS[network]
        keyboard.append([
            InlineKeyboardButton(
                info['name'],
                callback_data=f"wallet_{network}"
            )
        ])

    keyboard.append([InlineKeyboardButton("Add Wallet", callback_data="menu_generate")])
    keyboard.append([InlineKeyboardButton("Home", callback_data="main_menu")])

    await edit_message_with_banner(
        query, "wallets", text, InlineKeyboardMarkup(keyboard)
    )


async def show_wallet_details(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not await check_callback_auth(update):
        return
    query = update.callback_query
    await query.answer()

    network = query.data.split("_")[1]
    user_id = query.from_user.id
    wallet = db.get_wallet(user_id, network)

    if not wallet:
        await edit_message_with_banner(
            query, "wallet",
            f"*Wallet Not Found*\n\nNo wallet exists for {NETWORKS[network]['name']}",
            get_back_button("menu_wallets")
        )
        return

    info = NETWORKS[network]
    text = (
        f"*{info['name']} Wallet*\n\n"
        f"*Address:*\n`{wallet['address']}`\n\n"
        f"*Balance:* Tap refresh to check\n\n"
        f"*Network:* {info['name']}"
    )
    await edit_message_with_banner(
        query, "wallet", text, get_wallet_card_keyboard(network)
    )


async def refresh_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_callback_auth(update):
        return
    query = update.callback_query
    await query.answer("Fetching balance...")

    network = query.data.split("_")[1]
    user_id = query.from_user.id
    wallet = db.get_wallet(user_id, network)

    if not wallet:
        await edit_message_with_banner(
            query, "wallet",
            f"*No Wallet*\n\nNo wallet found for {NETWORKS[network]['name']}",
            get_back_button("menu_wallets")
        )
        return

    info = NETWORKS[network]

    await edit_message_with_banner(
        query, "wallet",
        f"*{info['name']} Wallet*\n\n*Address:*\n`{wallet['address']}`\n\n*Fetching balance...*",
        None
    )

    balance_info = await BalanceChecker.get_balance(network, wallet["address"])
    balance_str = balance_info.get("balance", "Error")
    symbol = balance_info.get("symbol", info["symbol"])

    text = (
        f"*{info['name']} Wallet*\n\n"
        f"*Address:*\n`{wallet['address']}`\n\n"
        f"*Balance:* `{balance_str} {symbol}`\n\n"
        f"*Network:* {info['name']}"
    )
    await edit_message_with_banner(
        query, "wallet", text, get_wallet_card_keyboard(network)
    )


async def show_generate_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not await check_callback_auth(update):
        return
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    try:
        await query.message.delete()
    except Exception:
        pass

    network_names = [f"{info['name']} ({key})" for key, info in NETWORKS.items()]
    networks_str = ", ".join(network_names)

    text = (
        "\U0001F4B3 *Generate Wallet*\n\n"
        "Which network?\n\n"
        f"_{networks_str}_"
    )

    keyboard = [
        [InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]
    ]

    msg = await context.bot.send_photo(
        chat_id=chat_id,
        photo=open(get_banner_path("generate"), "rb"),
        caption=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["generate_msg_id"] = msg.message_id

    return GENERATE_NETWORK


async def receive_generate_network(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive and parse network from user's input for wallet generation."""
    text = update.message.text.strip()
    chat_id = update.message.chat_id
    user_id = update.effective_user.id

    try:
        await update.message.delete()
    except Exception:
        pass

    if "generate_msg_id" in context.user_data:
        try:
            await context.bot.delete_message(chat_id, context.user_data["generate_msg_id"])
        except Exception:
            pass

    network = detect_network_from_text(text)

    if not network:
        network_names = [f"{info['name']} ({key})" for key, info in NETWORKS.items()]
        networks_str = ", ".join(network_names)
        
        msg = await context.bot.send_photo(
            chat_id=chat_id,
            photo=open(get_banner_path("generate"), "rb"),
            caption=(
                "\u26a0\ufe0f *Network Not Recognized*\n\n"
                f"I couldn't understand which network you want.\n\n"
                f"_Available networks: {networks_str}_\n\n"
                "Please try again (e.g., 'Ethereum', 'BSC', 'Polygon')"
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]])
        )
        context.user_data["generate_msg_id"] = msg.message_id
        return GENERATE_NETWORK

    context.user_data["generate_network"] = network
    info = NETWORKS[network]

    existing = db.get_wallet(user_id, network)
    if existing:
        keyboard = [
            [
                InlineKeyboardButton("Yes, Replace", callback_data="confirm_generate_ai"),
                InlineKeyboardButton("Cancel", callback_data="main_menu")
            ]
        ]
        msg = await context.bot.send_photo(
            chat_id=chat_id,
            photo=open(get_banner_path("generate"), "rb"),
            caption=(
                f"\u26a0\ufe0f *Warning*\n\n"
                f"You already have a {info['name']} wallet.\n"
                f"Generating a new one will replace it.\n\n"
                f"Do you want to proceed?"
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data["generate_msg_id"] = msg.message_id
        return GENERATE_CONFIRM

    return await do_generate_wallet_ai(update, context, chat_id, network, user_id)


async def confirm_generate_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm wallet generation after warning."""
    query = update.callback_query
    await query.answer()
    
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    network = context.user_data.get("generate_network")
    
    if not network:
        await query.message.delete()
        return ConversationHandler.END
    
    try:
        await query.message.delete()
    except Exception:
        pass
    
    return await do_generate_wallet_ai(update, context, chat_id, network, user_id)


async def do_generate_wallet_ai(update, context, chat_id: int, network: str, user_id: int):
    """Generate wallet using AI conversational flow."""
    info = NETWORKS[network]
    logger.info(f"User {user_id} generating {network} wallet via AI flow")

    msg = await context.bot.send_photo(
        chat_id=chat_id,
        photo=open(get_banner_path("generate"), "rb"),
        caption=f"*Generating {info['name']} wallet...*",
        parse_mode="Markdown"
    )

    try:
        address, private_key = WalletGenerator.generate_wallet(network)
        encrypted_key = CryptoUtils.encrypt_private_key(private_key)
        db.save_wallet(user_id, network, address, encrypted_key)
        logger.info(f"User {user_id} generated {network} wallet: {address[:10]}...")

        keyboard = [
            [
                InlineKeyboardButton("Check Balance", callback_data=f"refresh_{network}"),
                InlineKeyboardButton("Deposit", callback_data=f"deposit_{network}")
            ],
            [InlineKeyboardButton("Generate Another", callback_data="menu_generate")],
            [InlineKeyboardButton("Home", callback_data="main_menu")]
        ]

        await msg.edit_caption(
            caption=(
                f"\u2705 *Wallet Generated!*\n\n"
                f"*Network:* {info['name']}\n"
                f"*Address:*\n`{address}`"
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Error generating wallet: {e}")
        await msg.edit_caption(
            caption=f"\u274c *Error*\n\n{str(e)}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back", callback_data="menu_generate")]])
        )

    return ConversationHandler.END


async def generate_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_callback_auth(update):
        return
    query = update.callback_query
    await query.answer()

    network = query.data.split("_")[1]
    user_id = query.from_user.id
    info = NETWORKS[network]

    existing = db.get_wallet(user_id, network)
    if existing:
        keyboard = [
            [
                InlineKeyboardButton(
                    "Yes, Replace",
                    callback_data=f"confirmgen_{network}"
                ),
                InlineKeyboardButton(
                    "Cancel",
                    callback_data="menu_generate"
                )
            ]
        ]
        text = (
            f"*Warning*\n\n"
            f"You already have a {info['name']} wallet.\n"
            f"Generating new will replace it."
        )
        await edit_message_with_banner(
            query, "generate", text, InlineKeyboardMarkup(keyboard)
        )
        return

    await do_generate_wallet(query, network, user_id)


async def confirm_generate_wallet(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not await check_callback_auth(update):
        return
    query = update.callback_query
    await query.answer()

    network = query.data.split("_")[1]
    user_id = query.from_user.id

    await do_generate_wallet(query, network, user_id)


async def do_generate_wallet(query, network: str, user_id: int):
    info = NETWORKS[network]
    logger.info(f"User {user_id} generating {network} wallet")

    await edit_message_with_banner(
        query, "generate", f"*Generating {info['name']} wallet...*", None
    )

    try:
        address, private_key = WalletGenerator.generate_wallet(network)
        encrypted_key = CryptoUtils.encrypt_private_key(private_key)
        db.save_wallet(user_id, network, address, encrypted_key)
        logger.info(f"User {user_id} generated {network} wallet: {address[:10]}...")

        keyboard = [
            [
                InlineKeyboardButton(
                    "Check Balance", callback_data=f"refresh_{network}"
                ),
                InlineKeyboardButton(
                    "Deposit", callback_data=f"deposit_{network}"
                )
            ],
            [InlineKeyboardButton("Generate Another", callback_data="menu_generate")],
            [InlineKeyboardButton("Home", callback_data="main_menu")]
        ]

        text = (
            f"*Wallet Generated!*\n\n"
            f"*Network:* {info['name']}\n"
            f"*Address:*\n`{address}`"
        )
        await edit_message_with_banner(
            query, "generate", text, InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Error generating wallet: {e}")
        await edit_message_with_banner(
            query, "generate", f"*Error*\n\n{str(e)}", get_back_button("menu_generate")
        )


async def show_deposit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_callback_auth(update):
        return
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    try:
        await query.message.delete()
    except Exception:
        pass

    text = (
        "\U0001F4E5 *Deposit*\n\n"
        "Which token?\n\n"
        "_e.g. USDT, ETH, SOL_\n\n"
        "_Tap the button below to see all supported tokens_"
    )

    keyboard = [
        [InlineKeyboardButton("\U0001F4CB View Supported Tokens", callback_data="show_tokens_list_deposit")],
        [InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]
    ]

    msg = await context.bot.send_photo(
        chat_id=chat_id,
        photo=open(get_banner_path("deposit"), "rb"),
        caption=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["deposit_msg_id"] = msg.message_id

    return DEPOSIT_TOKEN


async def receive_deposit_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive and parse token from user's natural language input for deposit."""
    text = update.message.text.strip()
    chat_id = update.message.chat_id
    user_id = update.effective_user.id

    try:
        await update.message.delete()
    except Exception:
        pass

    if "deposit_msg_id" in context.user_data:
        try:
            await context.bot.delete_message(chat_id, context.user_data["deposit_msg_id"])
        except Exception:
            pass

    token = detect_token_from_text(text)

    if not token:
        msg = await context.bot.send_photo(
            chat_id=chat_id,
            photo=open(get_banner_path("deposit"), "rb"),
            caption=(
                "\u26a0\ufe0f *Token Not Recognized*\n\n"
                f"I couldn't understand which token you want to deposit.\n\n"
                "Please try again (e.g., 'USDT', 'ETH', 'solana')\n\n"
                "_Tap the button below to see all supported tokens_"
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("\U0001F4CB View Supported Tokens", callback_data="show_tokens_list_deposit")],
                [InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]
            ])
        )
        context.user_data["deposit_msg_id"] = msg.message_id
        return DEPOSIT_TOKEN

    context.user_data["deposit_token"] = token
    available_networks = get_available_networks_for_token(token)

    if len(available_networks) == 1:
        context.user_data["deposit_network"] = available_networks[0]
        return await ask_deposit_confirmation(update, context, chat_id)

    network_names = [f"{NETWORKS.get(n, {}).get('name', n)} ({n})" for n in available_networks]
    networks_str = ", ".join(network_names)

    msg = await context.bot.send_photo(
        chat_id=chat_id,
        photo=open(get_banner_path("deposit"), "rb"),
        caption=(
            f"\U0001F4E5 *Deposit {token}*\n\n"
            f"Which network?\n\n"
            f"_{networks_str}_"
        ),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]])
    )
    context.user_data["deposit_msg_id"] = msg.message_id

    return DEPOSIT_NETWORK


async def receive_deposit_network(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive and parse network from user's natural language input for deposit."""
    text = update.message.text.strip()
    chat_id = update.message.chat_id

    try:
        await update.message.delete()
    except Exception:
        pass

    if "deposit_msg_id" in context.user_data:
        try:
            await context.bot.delete_message(chat_id, context.user_data["deposit_msg_id"])
        except Exception:
            pass

    token = context.user_data.get("deposit_token")
    available_networks = get_available_networks_for_token(token)

    network = detect_network_from_text(text)

    if not network or network not in available_networks:
        network_names = [f"{NETWORKS.get(n, {}).get('name', n)} ({n})" for n in available_networks]
        networks_str = ", ".join(network_names)
        msg = await context.bot.send_photo(
            chat_id=chat_id,
            photo=open(get_banner_path("deposit"), "rb"),
            caption=(
                f"\u26a0\ufe0f *Network Not Recognized*\n\n"
                f"I couldn't understand which network you want to use for {token}.\n\n"
                f"_Available networks: {networks_str}_\n\n"
                "Please try again (e.g., 'Polygon', 'BSC', 'Ethereum')"
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]])
        )
        context.user_data["deposit_msg_id"] = msg.message_id
        return DEPOSIT_NETWORK

    context.user_data["deposit_network"] = network
    return await ask_deposit_confirmation(update, context, chat_id)


async def ask_deposit_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Ask user to confirm their token and network selection for deposit."""
    token = context.user_data.get("deposit_token")
    network = context.user_data.get("deposit_network")

    token_info = TOKENS.get(token, {})
    network_info = NETWORKS.get(network, {})

    keyboard = [
        [
            InlineKeyboardButton("\u2705 Yes, proceed", callback_data="confirm_deposit_selection"),
            InlineKeyboardButton("\u274c No, restart", callback_data="menu_deposit")
        ]
    ]

    msg = await context.bot.send_photo(
        chat_id=chat_id,
        photo=open(get_banner_path("deposit"), "rb"),
        caption=(
            f"\U0001F4E5 *Confirm Deposit*\n\n"
            f"{token_info.get('icon', '')} *Token:* {token_info.get('name', token)} ({token})\n"
            f"{network_info.get('icon', '')} *Network:* {network_info.get('name', network)}\n\n"
            "Is this correct?"
        ),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["deposit_msg_id"] = msg.message_id

    return DEPOSIT_CONFIRM_SELECTION


async def confirm_deposit_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User confirmed their token/network selection, show deposit address."""
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    user_id = query.from_user.id

    try:
        await query.message.delete()
    except Exception:
        pass

    token = context.user_data.get("deposit_token")
    network = context.user_data.get("deposit_network")

    token_info = TOKENS.get(token, {})
    network_info = NETWORKS.get(network, {})

    wallet = db.get_wallet(user_id, network)
    if not wallet:
        keyboard = [
            [InlineKeyboardButton(f"\u2795 Generate {network_info['name']} Wallet", callback_data=f"gen_{network}")],
            [InlineKeyboardButton("\U0001F519 Back", callback_data="menu_deposit")]
        ]
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"\u26a0\ufe0f *No {network_info['name']} Wallet*\n\nGenerate a wallet first to deposit.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ConversationHandler.END

    address = wallet["address"]
    qr_buffer = generate_qr_code(address)

    if token in ["USDT", "USDC"]:
        token_balance_result = await BalanceChecker.get_token_balance(token, network, address)
        if "error" in token_balance_result:
            balance_display = f"`0` {token}"
        else:
            balance_display = f"`{token_balance_result.get('balance', '0')}` {token}"
    else:
        if network == "SOLANA":
            balance_result = await BalanceChecker.get_solana_balance(address)
        elif network == "TRON":
            balance_result = await BalanceChecker.get_tron_balance(address)
        elif network == "LTC":
            balance_result = await BalanceChecker.get_ltc_balance(address)
        else:
            balance_result = await BalanceChecker.get_evm_balance(network, address)
        
        if isinstance(balance_result, dict):
            if "error" in balance_result:
                balance_display = f"`0` {token}"
            else:
                balance_display = f"`{balance_result.get('balance', '0')}` {token}"
        else:
            balance_display = f"`{balance_result}` {token}"

    keyboard = [
        [InlineKeyboardButton("\U0001F504 Refresh Balance", callback_data=f"refresh_dep_{token}_{network}")],
        [InlineKeyboardButton("\U0001F3E0 Main Menu", callback_data="main_menu")]
    ]

    caption_text = (
        f"\U0001F4E5 *Deposit {token}*\n\n"
        f"{token_info.get('icon', '')} *Token:* {token}\n"
        f"{network_info.get('icon', '')} *Network:* {network_info.get('name', network)}\n\n"
        f"\U0001F4CD *Deposit Address:*\n`{address}`\n\n"
        f"\U0001F4B0 *Current Balance:* {balance_display}\n\n"
        f"\u26a0\ufe0f _Only send {token} on {network_info.get('name', network)} network!_"
    )

    if qr_buffer:
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=qr_buffer,
            caption=caption_text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=caption_text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    return ConversationHandler.END


async def show_deposit_address(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not await check_callback_auth(update):
        return
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    network = query.data.split("_")[1]
    user_id = query.from_user.id
    wallet = db.get_wallet(user_id, network)
    info = NETWORKS[network]

    if not wallet:
        keyboard = [
            [InlineKeyboardButton(
                f"Generate {info['name']} Wallet",
                callback_data=f"gen_{network}"
            )],
            [InlineKeyboardButton("Back", callback_data="menu_deposit")]
        ]
        await edit_message_with_banner(
            query, "deposit",
            f"*No Wallet Found*\n\nYou don't have a {info['name']} wallet yet.\n"
            f"Generate one first to get a deposit address.",
            InlineKeyboardMarkup(keyboard)
        )
        return

    if network == "SOLANA":
        explorer_url = f"{info['explorer']}/account/{wallet['address']}"
    elif network == "TRON":
        explorer_url = f"{info['explorer']}/#/address/{wallet['address']}"
    else:
        explorer_url = f"{info['explorer']}/address/{wallet['address']}"

    keyboard = [
        [InlineKeyboardButton(
            "\U0001F310 View on Explorer",
            url=explorer_url
        )],
        [
            InlineKeyboardButton(
                "\U0001F504 Refresh Balance",
                callback_data=f"refresh_{network}"
            ),
            InlineKeyboardButton(
                "\U0001F4E4 Withdraw",
                callback_data=f"withdraw_{network}"
            )
        ],
        [InlineKeyboardButton(
            "\U0001F519 Back",
            callback_data="menu_deposit"
        )],
        [InlineKeyboardButton(
            "\U0001F3E0 Main Menu",
            callback_data="main_menu"
        )]
    ]

    text = (
        f"*Deposit {info['symbol']}*\n\n"
        f"*Network:* {info['name']}\n\n"
        f"*Your Deposit Address:*\n`{wallet['address']}`\n\n"
        f"Tap the address to copy it.\n\n"
        f"*Important:* Only send {info['symbol']} and {info['name']} tokens to this address!"
    )

    # Try to edit the message first, fall back to delete/send for QR code
    try:
        if query.message.photo:
            await query.message.edit_caption(
                caption=text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
    except Exception:
        pass

    # Need to send new message with QR code
    try:
        await query.message.delete()
    except Exception:
        pass

    qr_image = generate_qr_code(wallet['address'])
    if qr_image:
        from telegram import InputFile
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=InputFile(qr_image, filename="deposit_qr.png"),
            caption=text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def show_token_deposit_networks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not await check_callback_auth(update):
        return
    query = update.callback_query
    await query.answer()

    token = query.data.split("_")[2]
    token_info = TOKENS.get(token)

    if not token_info:
        await edit_message_with_banner(
            query, "deposit", "*Token not found.*", get_back_button("menu_deposit")
        )
        return

    keyboard = []
    for network_key in token_info["networks"].keys():
        network_info = NETWORKS.get(network_key, {})
        btn = InlineKeyboardButton(
            f"{network_info.get('name', network_key)}",
            callback_data=f"tokendep_{token}_{network_key}"
        )
        keyboard.append([btn])

    keyboard.append([InlineKeyboardButton("Back", callback_data="menu_deposit")])

    text = f"*Deposit {token_info['symbol']}*\n\nSelect the network to deposit:"
    await edit_message_with_banner(
        query, "deposit", text, InlineKeyboardMarkup(keyboard)
    )


async def show_token_deposit_address(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not await check_callback_auth(update):
        return
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    parts = query.data.split("_")
    token = parts[1]
    network = parts[2]

    token_info = TOKENS.get(token)
    network_info = NETWORKS.get(network)
    user_id = query.from_user.id
    wallet = db.get_wallet(user_id, network)

    if not wallet:
        keyboard = [
            [InlineKeyboardButton(
                f"Generate {network_info['name']} Wallet",
                callback_data=f"gen_{network}"
            )],
            [InlineKeyboardButton("Back", callback_data=f"deposit_token_{token}")]
        ]
        await edit_message_with_banner(
            query, "deposit",
            f"*No Wallet Found*\n\nYou need a {network_info['name']} wallet to receive "
            f"{token_info['symbol']}.\nGenerate one first.",
            InlineKeyboardMarkup(keyboard)
        )
        return

    if network == "SOLANA":
        explorer_url = f"{network_info['explorer']}/account/{wallet['address']}"
    elif network == "TRON":
        explorer_url = f"{network_info['explorer']}/#/address/{wallet['address']}"
    else:
        explorer_url = f"{network_info['explorer']}/address/{wallet['address']}"

    keyboard = [
        [InlineKeyboardButton(
            "\U0001F310 View on Explorer",
            url=explorer_url
        )],
        [InlineKeyboardButton(
            "\U0001F519 Back",
            callback_data=f"deposit_token_{token}"
        )],
        [InlineKeyboardButton(
            "\U0001F3E0 Main Menu",
            callback_data="main_menu"
        )]
    ]

    text = (
        f"*Deposit {token_info['symbol']}*\n\n"
        f"*Network:* {network_info['name']}\n\n"
        f"*Your Deposit Address:*\n`{wallet['address']}`\n\n"
        f"Tap the address to copy it.\n\n"
        f"*Important:* Only send {token_info['symbol']} ({network_info['name']} network) "
        f"to this address!"
    )

    # Try to edit the message first, fall back to delete/send for QR code
    try:
        if query.message.photo:
            await query.message.edit_caption(
                caption=text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
    except Exception:
        pass

    # Need to send new message with QR code
    try:
        await query.message.delete()
    except Exception:
        pass

    qr_image = generate_qr_code(wallet['address'])
    if qr_image:
        from telegram import InputFile
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=InputFile(qr_image, filename="deposit_qr.png"),
            caption=text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def show_combo_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_callback_auth(update):
        return
    query = update.callback_query
    await query.answer("Loading...")
    chat_id = query.message.chat_id

    parts = query.data.split("_")
    token = parts[2]
    network = parts[3]

    token_info = TOKENS.get(token)
    network_info = NETWORKS.get(network)
    user_id = query.from_user.id
    wallet = db.get_wallet(user_id, network)

    network_short = network
    if network == "TRON":
        network_short = "TRX"
    elif network == "SOLANA":
        network_short = "SOL"

    if not wallet:
        keyboard = [
            [InlineKeyboardButton(
                f"Create {network_info['name']} Wallet",
                callback_data=f"gen_{network}"
            )],
            [InlineKeyboardButton("Back", callback_data="menu_deposit")]
        ]
        text = f"*No Wallet*\nCreate a {network_info['name']} wallet first."
        await edit_message_with_banner(
            query, "deposit", text, InlineKeyboardMarkup(keyboard)
        )
        return

    is_native = token_info.get("native", False)
    if is_native:
        balance_info = await BalanceChecker.get_balance(network, wallet["address"])
    else:
        balance_info = await BalanceChecker.get_token_balance(token, network, wallet["address"])
    balance_str = balance_info.get("balance", "0")

    if network == "SOLANA":
        explorer_url = f"{network_info['explorer']}/account/{wallet['address']}"
    elif network == "TRON":
        explorer_url = f"{network_info['explorer']}/#/address/{wallet['address']}"
    else:
        explorer_url = f"{network_info['explorer']}/address/{wallet['address']}"

    keyboard = [
        [InlineKeyboardButton("Refresh", callback_data=f"refresh_dep_{token}_{network}")],
        [InlineKeyboardButton("Explorer", url=explorer_url)],
        [InlineKeyboardButton("Back", callback_data="menu_deposit")],
        [InlineKeyboardButton("Home", callback_data="main_menu")]
    ]

    text = (
        f"*Deposit {token}[{network_short}]*\n\n"
        f"Balance: {balance_str} {token}\n\n"
        f"*Address:*\n`{wallet['address']}`"
    )

    # Try to edit the message first, fall back to delete/send for QR code
    try:
        if query.message.photo:
            await query.message.edit_caption(
                caption=text,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
    except Exception:
        pass

    # Need to send new message with QR code
    try:
        await query.message.delete()
    except Exception:
        pass

    qr_image = generate_qr_code(wallet['address'])
    if qr_image:
        from telegram import InputFile
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=InputFile(qr_image, filename="deposit_qr.png"),
            caption=text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def refresh_deposit_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_callback_auth(update):
        return
    query = update.callback_query
    await query.answer("Refreshing...")

    parts = query.data.split("_")
    token = parts[2]
    network = parts[3]

    token_info = TOKENS.get(token)
    network_info = NETWORKS.get(network)
    user_id = query.from_user.id
    wallet = db.get_wallet(user_id, network)

    network_short = network
    if network == "TRON":
        network_short = "TRX"
    elif network == "SOLANA":
        network_short = "SOL"

    if not wallet:
        return

    is_native = token_info.get("native", False)
    if is_native:
        balance_info = await BalanceChecker.get_balance(network, wallet["address"])
    else:
        balance_info = await BalanceChecker.get_token_balance(token, network, wallet["address"])
    balance_str = balance_info.get("balance", "0")

    if network == "SOLANA":
        explorer_url = f"{network_info['explorer']}/account/{wallet['address']}"
    elif network == "TRON":
        explorer_url = f"{network_info['explorer']}/#/address/{wallet['address']}"
    else:
        explorer_url = f"{network_info['explorer']}/address/{wallet['address']}"

    keyboard = [
        [InlineKeyboardButton("Refresh", callback_data=f"refresh_dep_{token}_{network}")],
        [InlineKeyboardButton("Explorer", url=explorer_url)],
        [InlineKeyboardButton("Back", callback_data="menu_deposit")],
        [InlineKeyboardButton("Home", callback_data="main_menu")]
    ]

    text = (
        f"*Deposit {token}[{network_short}]*\n\n"
        f"Balance: {balance_str} {token}\n\n"
        f"*Address:*\n`{wallet['address']}`"
    )
    await edit_message_with_banner(
        query, "deposit", text, InlineKeyboardMarkup(keyboard)
    )


async def show_combo_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_callback_auth(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer("Loading...")

    parts = query.data.split("_")
    token = parts[2]
    network = parts[3]

    token_info = TOKENS.get(token)
    network_info = NETWORKS.get(network)
    user_id = query.from_user.id
    clear_withdrawal_state(user_id, context)
    wallet = db.get_wallet(user_id, network)

    network_short = network
    if network == "TRON":
        network_short = "TRX"
    elif network == "SOLANA":
        network_short = "SOL"

    if not wallet:
        keyboard = [
            [InlineKeyboardButton(
                f"Create {network_info['name']} Wallet",
                callback_data=f"gen_{network}"
            )],
            [InlineKeyboardButton("Back", callback_data="menu_withdraw")]
        ]
        await edit_message_with_banner(
            query, "withdraw",
            f"*No Wallet*\nCreate a {network_info['name']} wallet first.",
            InlineKeyboardMarkup(keyboard)
        )
        return ConversationHandler.END

    # Use internal ledger balance instead of on-chain balance
    # This allows cross-network withdrawals
    is_native = token_info.get("native", False)
    if is_native:
        ledger_asset = get_ledger_asset(network)
    else:
        ledger_asset = get_ledger_asset(network, token)
    internal_balance = db.get_internal_balance(user_id, ledger_asset)
    balance_str = str(internal_balance)

    context.user_data["withdraw_network"] = network
    context.user_data["withdraw_token"] = token
    context.user_data["withdraw_balance"] = balance_str
    context.user_data["withdraw_is_native"] = is_native
    if not is_native and token_info and network in token_info.get("networks", {}):
        network_data = token_info["networks"][network]
        if isinstance(network_data, dict) and "address" in network_data:
            context.user_data["withdraw_token_address"] = network_data["address"]
        else:
            context.user_data["withdraw_token_address"] = None
    else:
        context.user_data["withdraw_token_address"] = None

    keyboard = [[InlineKeyboardButton("Cancel", callback_data="cancel_withdraw")]]

    text = (
        f"*Withdraw {token}[{network_short}]*\n\n"
        f"Network: {network_info['name']}\n"
        f"Available: {balance_str} {token}\n\n"
        f"Step 1/3: Enter amount\nReply with the amount (e.g., 0.1)"
    )
    await edit_message_with_banner(
        query, "withdraw", text, InlineKeyboardMarkup(keyboard)
    )

    return WITHDRAW_AMOUNT


async def show_token_withdraw_networks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not await check_callback_auth(update):
        return
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    clear_withdrawal_state(user_id, context)

    token = query.data.split("_")[2]
    token_info = TOKENS.get(token)

    if not token_info:
        await edit_message_with_banner(
            query, "withdraw", "*Token not found.*", get_back_button("menu_withdraw")
        )
        return

    keyboard = []
    for network_key in token_info["networks"].keys():
        network_info = NETWORKS.get(network_key, {})
        btn = InlineKeyboardButton(
            f"{network_info.get('name', network_key)}",
            callback_data=f"tokenwd_{token}_{network_key}"
        )
        keyboard.append([btn])

    keyboard.append([InlineKeyboardButton("Back", callback_data="menu_withdraw")])

    text = f"*Withdraw {token_info['symbol']}*\n\nSelect the network to withdraw:"
    await edit_message_with_banner(
        query, "withdraw", text, InlineKeyboardMarkup(keyboard)
    )


async def show_token_withdraw_info(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not await check_callback_auth(update):
        return
    query = update.callback_query
    await query.answer("Checking token balance...")

    parts = query.data.split("_")
    token = parts[1]
    network = parts[2]

    token_info = TOKENS.get(token)
    network_info = NETWORKS.get(network)
    user_id = query.from_user.id
    clear_withdrawal_state(user_id, context)
    wallet = db.get_wallet(user_id, network)

    if not wallet:
        keyboard = [
            [InlineKeyboardButton(
                f"Generate {network_info['name']} Wallet",
                callback_data=f"gen_{network}"
            )],
            [InlineKeyboardButton("Back", callback_data=f"withdraw_token_{token}")]
        ]
        await edit_message_with_banner(
            query, "withdraw",
            f"*No Wallet Found*\n\nYou need a {network_info['name']} wallet to withdraw "
            f"{token_info['symbol']}.",
            InlineKeyboardMarkup(keyboard)
        )
        return ConversationHandler.END

    # Use internal ledger balance instead of on-chain balance
    # This allows cross-network withdrawals (e.g., deposit on BSC, withdraw on TRC20)
    ledger_asset = get_ledger_asset(network, token)
    internal_balance = db.get_internal_balance(user_id, ledger_asset)
    balance_str = str(internal_balance)

    network_token = token_info["networks"][network]
    is_native = network_token.get("native", False)

    context.user_data["withdraw_network"] = network
    context.user_data["withdraw_balance"] = balance_str
    context.user_data["withdraw_token"] = token
    context.user_data["withdraw_token_address"] = None if is_native else network_token["address"]

    keyboard = [[InlineKeyboardButton("Cancel", callback_data="cancel_withdraw")]]

    text = (
        f"*Withdraw {token_info['symbol']}*\n\n"
        f"*Network:* {network_info['name']}\n"
        f"*Available:* `{balance_str} {token_info['symbol']}`\n\n"
        f"*Step 1/3:* Enter the amount to withdraw:\n\n"
        f"Reply with the amount (e.g., 10)"
    )
    await edit_message_with_banner(
        query, "withdraw", text, InlineKeyboardMarkup(keyboard)
    )

    return WITHDRAW_AMOUNT


async def show_balance_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_callback_auth(update):
        return
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    try:
        await query.message.delete()
    except Exception:
        pass

    network_names = [f"{info['name']} ({key})" for key, info in NETWORKS.items()]
    networks_str = ", ".join(network_names)

    text = (
        "\U0001F4B0 *Check Balance*\n\n"
        "Which network?  _(or 'all')_\n\n"
        f"_{networks_str}_"
    )

    keyboard = [
        [InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]
    ]

    msg = await context.bot.send_photo(
        chat_id=chat_id,
        photo=open(get_banner_path("balance"), "rb"),
        caption=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["balance_msg_id"] = msg.message_id

    return BALANCE_NETWORK


async def receive_balance_network(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive and parse network from user's input for balance check."""
    text = update.message.text.strip().lower()
    chat_id = update.message.chat_id
    user_id = update.effective_user.id

    try:
        await update.message.delete()
    except Exception:
        pass

    if "balance_msg_id" in context.user_data:
        try:
            await context.bot.delete_message(chat_id, context.user_data["balance_msg_id"])
        except Exception:
            pass

    if text == "all":
        return await do_check_balance_all(update, context, chat_id, user_id)

    network = detect_network_from_text(text)

    if not network:
        network_names = [f"{info['name']} ({key})" for key, info in NETWORKS.items()]
        networks_str = ", ".join(network_names)
        
        msg = await context.bot.send_photo(
            chat_id=chat_id,
            photo=open(get_banner_path("balance"), "rb"),
            caption=(
                "\u26a0\ufe0f *Network Not Recognized*\n\n"
                f"I couldn't understand which network you want.\n\n"
                f"_Available networks: {networks_str}_\n\n"
                "Type 'all' for all balances, or a network name (e.g., 'Ethereum', 'BSC')"
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]])
        )
        context.user_data["balance_msg_id"] = msg.message_id
        return BALANCE_NETWORK

    return await do_check_balance_network(update, context, chat_id, user_id, network)


async def do_check_balance_all(update, context, chat_id: int, user_id: int):
    """Check all balances using AI conversational flow."""
    wallets = db.get_all_wallets(user_id)
    if not wallets:
        keyboard = [[InlineKeyboardButton("Home", callback_data="main_menu")]]
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=open(get_banner_path("balance"), "rb"),
            caption="*No Wallets*\nGenerate a wallet first.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ConversationHandler.END

    msg = await context.bot.send_photo(
        chat_id=chat_id,
        photo=open(get_banner_path("balance"), "rb"),
        caption="*Fetching all balances...*",
        parse_mode="Markdown"
    )

    text = "*Your Balances*\n\n"
    
    text += "*On-Chain:*\n"
    for wallet in wallets:
        net = wallet["network"]
        info = NETWORKS[net]
        balance_info = await BalanceChecker.get_balance(net, wallet["address"])
        balance_str = balance_info.get("balance", "Error")
        symbol = balance_info.get("symbol", info["symbol"])
        text += f"{info['icon']} {info['name']}: `{balance_str} {symbol}`\n"
    
    internal_balances = db.get_all_internal_balances(user_id)
    if internal_balances:
        text += "\n*Internal Ledger:*\n"
        for asset, balance in internal_balances.items():
            if balance > Decimal("0"):
                text += f"\U0001F4B0 {asset}: `{balance:.6f}`\n"

    keyboard = [
        [InlineKeyboardButton("Check Another", callback_data="menu_balance")],
        [InlineKeyboardButton("Home", callback_data="main_menu")]
    ]

    await msg.edit_caption(
        caption=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ConversationHandler.END


async def do_check_balance_network(update, context, chat_id: int, user_id: int, network: str):
    """Check balance for a specific network using AI conversational flow."""
    wallet = db.get_wallet(user_id, network)
    info = NETWORKS[network]

    if not wallet:
        keyboard = [
            [InlineKeyboardButton(f"Generate {info['name']} Wallet", callback_data=f"gen_{network}")],
            [InlineKeyboardButton("Home", callback_data="main_menu")]
        ]
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=open(get_banner_path("balance"), "rb"),
            caption=f"*No {info['name']} Wallet*\nGenerate one first.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ConversationHandler.END

    msg = await context.bot.send_photo(
        chat_id=chat_id,
        photo=open(get_banner_path("balance"), "rb"),
        caption=f"*Fetching {info['name']} balances...*",
        parse_mode="Markdown"
    )

    balance_info = await BalanceChecker.get_balance(network, wallet["address"])
    native_balance = balance_info.get("balance", "Error")
    native_symbol = balance_info.get("symbol", info["symbol"])

    text = f"*{info['name']} Balances*\n\n"
    text += f"*On-Chain:*\n"
    text += f"{info['icon']} Native: `{native_balance} {native_symbol}`\n"

    for token_key, token_info in TOKENS.items():
        if network in token_info.get("networks", {}):
            net_info = token_info["networks"][network]
            if net_info.get("native"):
                continue
            try:
                result = await BalanceChecker.get_token_balance(
                    token_key, network, wallet["address"]
                )
                if result and "balance" in result and not result.get("error"):
                    bal_str = result["balance"]
                    text += f"\U0001F4B5 {token_info['symbol']}: `{bal_str}`\n"
            except Exception:
                pass

    ledger_asset = get_ledger_asset(network)
    internal_native = db.get_internal_balance(user_id, ledger_asset)
    
    text += f"\n*Internal Ledger:*\n"
    text += f"\U0001F4B0 {ledger_asset}: `{internal_native:.6f}`\n"
    
    for token_key in ["USDT", "USDC"]:
        token_info = TOKENS.get(token_key, {})
        if network in token_info.get("networks", {}):
            net_info = token_info["networks"][network]
            if not net_info.get("native"):
                token_ledger_asset = get_ledger_asset(network, token_key)
                internal_token = db.get_internal_balance(user_id, token_ledger_asset)
                text += f"\U0001F4B0 {token_ledger_asset}: `{internal_token:.6f}`\n"

    text += f"\n*Address:*\n`{wallet['address']}`"

    keyboard = [
        [
            InlineKeyboardButton("Check Another", callback_data="menu_balance"),
            InlineKeyboardButton("Withdraw", callback_data=f"withdraw_{network}")
        ],
        [InlineKeyboardButton("Home", callback_data="main_menu")]
    ]

    await msg.edit_caption(
        caption=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ConversationHandler.END


async def check_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_callback_auth(update):
        return
    query = update.callback_query
    await query.answer("Fetching...")

    network = query.data.split("_")[1]
    user_id = query.from_user.id

    if network == "all":
        wallets = db.get_all_wallets(user_id)
        if not wallets:
            await edit_message_with_banner(
                query, "balance", "*No Wallets*\nGenerate a wallet first.",
                get_back_button("menu_balance")
            )
            return

        await edit_message_with_banner(
            query, "balance", "*Fetching all balances...*", None
        )

        text = "*Your Balances*\n\n"
        
        # Show on-chain native balances
        text += "*On-Chain:*\n"
        for wallet in wallets:
            net = wallet["network"]
            info = NETWORKS[net]
            balance_info = await BalanceChecker.get_balance(net, wallet["address"])
            balance_str = balance_info.get("balance", "Error")
            symbol = balance_info.get("symbol", info["symbol"])
            text += f"{info['icon']} {info['name']}: `{balance_str} {symbol}`\n"
        
        # Show internal ledger balances (including USDT/USDC)
        internal_balances = db.get_all_internal_balances(user_id)
        if internal_balances:
            text += "\n*Internal Ledger:*\n"
            for asset, balance in internal_balances.items():
                if balance > Decimal("0"):
                    text += f"\U0001F4B0 {asset}: `{balance:.6f}`\n"

        keyboard = [
            [InlineKeyboardButton("Refresh", callback_data="balance_all")],
            [InlineKeyboardButton("Home", callback_data="main_menu")]
        ]

        await edit_message_with_banner(
            query, "balance", text, InlineKeyboardMarkup(keyboard)
        )
    else:
        wallet = db.get_wallet(user_id, network)
        info = NETWORKS[network]

        if not wallet:
            keyboard = [
                [InlineKeyboardButton(
                    f"Generate {info['name']} Wallet", callback_data=f"gen_{network}"
                )],
                [InlineKeyboardButton("Back", callback_data="menu_balance")]
            ]
            await edit_message_with_banner(
                query, "balance", f"*No {info['name']} Wallet*\nGenerate one first.",
                InlineKeyboardMarkup(keyboard)
            )
            return

        await edit_message_with_banner(
            query, "balance", f"*Fetching {info['name']} balances...*", None
        )

        balance_info = await BalanceChecker.get_balance(network, wallet["address"])
        native_balance = balance_info.get("balance", "Error")
        native_symbol = balance_info.get("symbol", info["symbol"])

        text = f"*{info['name']} Balances*\n\n"
        text += f"*On-Chain:*\n"
        text += f"{info['icon']} Native: `{native_balance} {native_symbol}`\n"

        for token_key, token_info in TOKENS.items():
            if network in token_info.get("networks", {}):
                net_info = token_info["networks"][network]
                if net_info.get("native"):
                    continue
                try:
                    result = await BalanceChecker.get_token_balance(
                        token_key, network, wallet["address"]
                    )
                    if result and "balance" in result and not result.get("error"):
                        bal_str = result["balance"]
                        text += f"\U0001F4B5 {token_info['symbol']}: `{bal_str}`\n"
                except Exception:
                    pass

        # Show internal ledger balances for this network
        ledger_asset = get_ledger_asset(network)
        internal_native = db.get_internal_balance(user_id, ledger_asset)
        
        text += f"\n*Internal Ledger:*\n"
        text += f"\U0001F4B0 {ledger_asset}: `{internal_native:.6f}`\n"
        
        # Check for USDT/USDC internal balances on this network
        for token_key in ["USDT", "USDC"]:
            token_info = TOKENS.get(token_key, {})
            if network in token_info.get("networks", {}):
                net_info = token_info["networks"][network]
                if not net_info.get("native"):
                    token_ledger_asset = get_ledger_asset(network, token_key)
                    internal_token = db.get_internal_balance(user_id, token_ledger_asset)
                    text += f"\U0001F4B0 {token_ledger_asset}: `{internal_token:.6f}`\n"

        text += f"\n*Address:*\n`{wallet['address']}`"

        keyboard = [
            [
                InlineKeyboardButton("Refresh", callback_data=f"balance_{network}"),
                InlineKeyboardButton("Withdraw", callback_data=f"withdraw_{network}")
            ],
            [InlineKeyboardButton("Back", callback_data="menu_balance")],
            [InlineKeyboardButton("Home", callback_data="main_menu")]
        ]

        await edit_message_with_banner(
            query, "balance", text, InlineKeyboardMarkup(keyboard)
        )


async def show_withdraw_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not await check_callback_auth(update):
        return
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    clear_withdrawal_state(user_id, context)

    try:
        await query.message.delete()
    except Exception:
        pass

    text = (
        "\U0001F4E4 *Withdraw*\n\n"
        "Which token?\n\n"
        "_e.g. USDT, ETH, SOL_\n\n"
        "_Tap the button below to see all supported tokens_"
    )

    keyboard = [
        [InlineKeyboardButton("\U0001F4CB View Supported Tokens", callback_data="show_tokens_list_withdraw")],
        [InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]
    ]

    msg = await context.bot.send_photo(
        chat_id=chat_id,
        photo=open(get_banner_path("withdraw"), "rb"),
        caption=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["withdraw_msg_id"] = msg.message_id

    return WITHDRAW_TOKEN


async def show_tokens_list_popup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show a popup with all supported tokens."""
    query = update.callback_query
    await query.answer()
    
    tokens_list = ", ".join([f"{info.get('icon', '')} {symbol}" for symbol, info in TOKENS.items()])
    popup_text = f"Supported: {tokens_list}"
    
    chat_id = query.message.chat_id
    action = query.data.split("_")[-1]
    
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=f"\U0001F4CB *Supported Tokens*\n\n{popup_text}\n\n_Type the token name to continue_",
        parse_mode="Markdown"
    )
    
    if action == "withdraw":
        return WITHDRAW_TOKEN
    elif action == "deposit":
        return DEPOSIT_TOKEN
    return None


async def receive_withdraw_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive and parse token from user's natural language input."""
    text = update.message.text.strip()
    chat_id = update.message.chat_id
    user_id = update.effective_user.id

    try:
        await update.message.delete()
    except Exception:
        pass

    if "withdraw_msg_id" in context.user_data:
        try:
            await context.bot.delete_message(chat_id, context.user_data["withdraw_msg_id"])
        except Exception:
            pass

    token = detect_token_from_text(text)

    if not token:
        available_tokens = ", ".join(TOKENS.keys())
        msg = await context.bot.send_photo(
            chat_id=chat_id,
            photo=open(get_banner_path("withdraw"), "rb"),
            caption=(
                "\u26a0\ufe0f *Token Not Recognized*\n\n"
                f"I couldn't understand which token you want to withdraw.\n\n"
                f"_Available tokens: {available_tokens}_\n\n"
                "Please try again (e.g., 'USDT', 'ETH', 'solana')"
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]])
        )
        context.user_data["withdraw_msg_id"] = msg.message_id
        return WITHDRAW_TOKEN

    context.user_data["withdraw_token"] = token
    token_info = TOKENS.get(token, {})
    available_networks = get_available_networks_for_token(token)

    if len(available_networks) == 1:
        context.user_data["withdraw_network"] = available_networks[0]
        return await ask_withdraw_confirmation(update, context, chat_id)

    network_names = [f"{NETWORKS.get(n, {}).get('name', n)} ({n})" for n in available_networks]
    networks_str = ", ".join(network_names)

    msg = await context.bot.send_photo(
        chat_id=chat_id,
        photo=open(get_banner_path("withdraw"), "rb"),
        caption=(
            f"\U0001F4E4 *Withdraw {token}*\n\n"
            f"Which network?\n\n"
            f"_{networks_str}_"
        ),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]])
    )
    context.user_data["withdraw_msg_id"] = msg.message_id

    return WITHDRAW_NETWORK


async def receive_withdraw_network(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive and parse network from user's natural language input."""
    text = update.message.text.strip()
    chat_id = update.message.chat_id

    try:
        await update.message.delete()
    except Exception:
        pass

    if "withdraw_msg_id" in context.user_data:
        try:
            await context.bot.delete_message(chat_id, context.user_data["withdraw_msg_id"])
        except Exception:
            pass

    token = context.user_data.get("withdraw_token")
    available_networks = get_available_networks_for_token(token)

    network = detect_network_from_text(text)

    if not network or network not in available_networks:
        network_names = [f"{NETWORKS.get(n, {}).get('name', n)} ({n})" for n in available_networks]
        networks_str = ", ".join(network_names)
        msg = await context.bot.send_photo(
            chat_id=chat_id,
            photo=open(get_banner_path("withdraw"), "rb"),
            caption=(
                f"\u26a0\ufe0f *Network Not Recognized*\n\n"
                f"I couldn't understand which network you want to use for {token}.\n\n"
                f"_Available networks: {networks_str}_\n\n"
                "Please try again (e.g., 'Polygon', 'BSC', 'Ethereum')"
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]])
        )
        context.user_data["withdraw_msg_id"] = msg.message_id
        return WITHDRAW_NETWORK

    context.user_data["withdraw_network"] = network
    return await ask_withdraw_confirmation(update, context, chat_id)


async def ask_withdraw_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Ask user to confirm their token and network selection."""
    token = context.user_data.get("withdraw_token")
    network = context.user_data.get("withdraw_network")

    token_info = TOKENS.get(token, {})
    network_info = NETWORKS.get(network, {})

    keyboard = [
        [
            InlineKeyboardButton("\u2705 Yes, proceed", callback_data="confirm_withdraw_selection"),
            InlineKeyboardButton("\u274c No, restart", callback_data="menu_withdraw")
        ]
    ]

    msg = await context.bot.send_photo(
        chat_id=chat_id,
        photo=open(get_banner_path("withdraw"), "rb"),
        caption=(
            f"\U0001F4E4 *Confirm Withdrawal*\n\n"
            f"{token_info.get('icon', '')} *Token:* {token_info.get('name', token)} ({token})\n"
            f"{network_info.get('icon', '')} *Network:* {network_info.get('name', network)}\n\n"
            "Is this correct?"
        ),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["withdraw_msg_id"] = msg.message_id

    return WITHDRAW_CONFIRM_SELECTION


async def confirm_withdraw_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User confirmed their token/network selection, proceed to amount entry."""
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    user_id = query.from_user.id

    try:
        await query.message.delete()
    except Exception:
        pass

    token = context.user_data.get("withdraw_token")
    network = context.user_data.get("withdraw_network")

    token_info = TOKENS.get(token, {})
    network_info = NETWORKS.get(network, {})
    
    # Set the token contract address for non-native tokens
    token_network_info = token_info.get("networks", {}).get(network, {})
    if not token_info.get("native") and token_network_info.get("address"):
        context.user_data["withdraw_token_address"] = token_network_info["address"]
    else:
        context.user_data["withdraw_token_address"] = None

    wallet = db.get_wallet(user_id, network)
    if not wallet:
        keyboard = [
            [InlineKeyboardButton(f"\u2795 Generate {network_info['name']} Wallet", callback_data=f"gen_{network}")],
            [InlineKeyboardButton("\U0001F519 Back", callback_data="menu_withdraw")]
        ]
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"\u26a0\ufe0f *No {network_info['name']} Wallet*\n\nGenerate a wallet first to withdraw.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ConversationHandler.END

    ledger_asset = token if token in ["USDT", "USDC"] else get_ledger_asset(network)
    internal_balance = db.get_internal_balance(user_id, ledger_asset)
    balance_str = str(internal_balance)

    context.user_data["withdraw_balance"] = balance_str

    keyboard = [[InlineKeyboardButton("\u274c Cancel", callback_data="cancel_withdraw")]]

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"\U0001F4E4 *Withdraw {token}*\n\n"
            f"{token_info.get('icon', '')} *Token:* {token}\n"
            f"{network_info.get('icon', '')} *Network:* {network_info.get('name', network)}\n"
            f"\U0001F4B0 *Available:* `{balance_str} {token}`\n\n"
            f"\U0001F4DD *Step 1/3:* Enter the amount to withdraw:\n\n"
            f"_Reply with the amount (e.g., 0.1)_"
        ),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["withdraw_msg_id"] = msg.message_id

    return WITHDRAW_AMOUNT


async def start_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_callback_auth(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    # Delete the old message first
    try:
        await query.message.delete()
    except Exception:
        pass

    network = query.data.split("_")[1]
    user_id = query.from_user.id
    clear_withdrawal_state(user_id, context)
    wallet = db.get_wallet(user_id, network)
    info = NETWORKS[network]

    if not wallet:
        keyboard = [
            [InlineKeyboardButton(
                f"\u2795 Generate {info['name']} Wallet",
                callback_data=f"gen_{network}"
            )],
            [InlineKeyboardButton(
                "\U0001F519 Back",
                callback_data="menu_withdraw"
            )]
        ]
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=f"\u26a0\ufe0f *No {info['name']} Wallet*\n\n"
            f"Generate a wallet first to withdraw.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ConversationHandler.END

    # Use internal ledger balance instead of on-chain balance
    # This allows cross-network withdrawals
    ledger_asset = get_ledger_asset(network)
    internal_balance = db.get_internal_balance(user_id, ledger_asset)
    balance_str = str(internal_balance)

    context.user_data["withdraw_network"] = network
    context.user_data["withdraw_balance"] = balance_str

    keyboard = [
        [InlineKeyboardButton(
            "\u274c Cancel",
            callback_data="cancel_withdraw"
        )]
    ]

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=f"\U0001F4E4 *Withdraw {info['symbol']}*\n\n"
        f"{info['icon']} *Network:* {info['name']}\n"
        f"\U0001F4B0 *Available:* `{balance_str} {info['symbol']}`\n\n"
        f"\U0001F4DD *Step 1/3:* Enter the amount to withdraw:\n\n"
        f"_Reply with the amount (e.g., 0.1)_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    # Store the new message ID so we can delete it later
    context.user_data["withdraw_msg_id"] = msg.message_id

    return WITHDRAW_AMOUNT


async def receive_withdraw_quick_network(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    """Handle network selection for quick withdraw with EVM address."""
    user_input = update.message.text.strip().upper()
    chat_id = update.message.chat_id
    user_id = update.effective_user.id
    
    try:
        await update.message.delete()
    except Exception:
        pass
    
    prev_msg_id = context.user_data.get("withdraw_msg_id")
    if prev_msg_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=prev_msg_id)
        except Exception:
            pass
    
    possible_networks = context.user_data.get("withdraw_possible_networks", [])
    address = context.user_data.get("withdraw_address")
    
    network = detect_network_from_text(user_input)
    if not network or network not in possible_networks:
        networks_str = " / ".join(possible_networks)
        text = (
            "\U0001F4E4 *Quick Withdraw*\n\n"
            f"\u274c Invalid network. Please type one of:\n`{networks_str}`"
        )
        keyboard = [[InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]]
        msg = await context.bot.send_photo(
            chat_id=chat_id,
            photo=open(get_banner_path("withdraw"), "rb"),
            caption=text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data["withdraw_msg_id"] = msg.message_id
        return WITHDRAW_QUICK_NETWORK
    
    context.user_data["withdraw_network"] = network
    balances = db.get_all_internal_balances(user_id)
    usdt_balance = balances.get("USDT", Decimal("0"))
    usdc_balance = balances.get("USDC", Decimal("0"))
    
    network_info = NETWORKS.get(network, {})
    network_name = network_info.get("name", network)
    native_token = network_info.get("native_token", network)
    native_balance = balances.get(native_token, Decimal("0"))
    
    if usdt_balance > 0:
        context.user_data["withdraw_token"] = "USDT"
        token_info = TOKENS.get("USDT", {})
        context.user_data["withdraw_contract"] = token_info.get("networks", {}).get(network, {}).get("address")
        text = (
            "\U0001F4E4 *Quick Withdraw USDT*\n\n"
            f"\U0001F4CD To: `{address[:8]}...{address[-6:]}`\n"
            f"\U0001F310 Network: *{network_name}*\n"
            f"\U0001F4B0 Balance: `{usdt_balance:.4f} USDT`\n\n"
            "Enter the amount to withdraw:"
        )
    elif usdc_balance > 0:
        context.user_data["withdraw_token"] = "USDC"
        token_info = TOKENS.get("USDC", {})
        context.user_data["withdraw_contract"] = token_info.get("networks", {}).get(network, {}).get("address")
        text = (
            "\U0001F4E4 *Quick Withdraw USDC*\n\n"
            f"\U0001F4CD To: `{address[:8]}...{address[-6:]}`\n"
            f"\U0001F310 Network: *{network_name}*\n"
            f"\U0001F4B0 Balance: `{usdc_balance:.4f} USDC`\n\n"
            "Enter the amount to withdraw:"
        )
    elif native_balance > 0:
        context.user_data["withdraw_token"] = native_token
        context.user_data["withdraw_contract"] = None
        text = (
            f"\U0001F4E4 *Quick Withdraw {native_token}*\n\n"
            f"\U0001F4CD To: `{address[:8]}...{address[-6:]}`\n"
            f"\U0001F310 Network: *{network_name}*\n"
            f"\U0001F4B0 Balance: `{native_balance:.6f} {native_token}`\n\n"
            "Enter the amount to withdraw:"
        )
    else:
        text = (
            "\U0001F4E4 *Quick Withdraw*\n\n"
            f"\U0001F4CD To: `{address[:8]}...{address[-6:]}`\n"
            f"\U0001F310 Network: *{network_name}*\n\n"
            "\u274c No balance available on this network.\n"
            "Please deposit funds first."
        )
        keyboard = [[InlineKeyboardButton("\U0001F3E0 Home", callback_data="main_menu")]]
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=open(get_banner_path("withdraw"), "rb"),
            caption=text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ConversationHandler.END
    
    keyboard = [[InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]]
    msg = await context.bot.send_photo(
        chat_id=chat_id,
        photo=open(get_banner_path("withdraw"), "rb"),
        caption=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["withdraw_msg_id"] = msg.message_id
    return WITHDRAW_AMOUNT


async def receive_withdraw_amount(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    amount = update.message.text.strip()
    chat_id = update.message.chat_id  # Get chat_id before any deletions

    # Delete the user's input message
    try:
        await update.message.delete()
    except Exception:
        pass

    # Delete the previous bot message if stored
    prev_msg_id = context.user_data.get("withdraw_msg_id")
    if prev_msg_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=prev_msg_id)
        except Exception:
            pass

    try:
        amount_decimal = Decimal(amount)
        if amount_decimal <= 0:
            raise ValueError("Amount must be positive")
    except Exception:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text="\u274c *Invalid Amount*\n\n"
            "Please enter a valid positive number.\n"
            "Example: `0.1` or `100`",
            parse_mode="Markdown"
        )
        context.user_data["withdraw_msg_id"] = msg.message_id
        return WITHDRAW_AMOUNT

    context.user_data["withdraw_amount"] = amount
    network = context.user_data.get("withdraw_network")
    token = context.user_data.get("withdraw_token")
    address = context.user_data.get("withdraw_address")
    token_address = context.user_data.get("withdraw_contract") or context.user_data.get("withdraw_token_address")
    info = NETWORKS[network]

    if token:
        token_info = TOKENS[token]
        symbol = token_info["symbol"]
        icon = token_info["icon"]
    else:
        symbol = info["symbol"]
        icon = info["icon"]

    if address:
        user_id = update.effective_user.id
        pending_withdrawals[user_id] = {
            "network": network,
            "amount": amount,
            "address": address,
            "token": token,
            "token_address": token_address
        }
        
        keyboard = [
            [
                InlineKeyboardButton(
                    "\u2705 Confirm Withdrawal",
                    callback_data="exec_withdraw"
                )
            ],
            [
                InlineKeyboardButton(
                    "\u274c Cancel",
                    callback_data="cancel_withdraw"
                )
            ]
        ]
        
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=f"\U0001F4E4 *Withdrawal Confirmation*\n\n"
            f"Please review and confirm:\n\n"
            f"{'='*30}\n"
            f"{info['icon']} *Network:* {info['name']}\n"
            f"{icon} *Asset:* {symbol}\n"
            f"\U0001F4B0 *Amount:* `{amount} {symbol}`\n"
            f"\U0001F4CD *To:*\n`{address}`\n"
            f"{'='*30}\n\n"
            f"\u26a0\ufe0f *Warning:* This action cannot be undone!\n"
            f"Please verify the address carefully.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        context.user_data["withdraw_msg_id"] = msg.message_id
        return WITHDRAW_CONFIRM

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=f"{icon} *Withdraw {symbol}*\n\n"
        f"\U0001F4B0 *Amount:* `{amount} {symbol}`\n\n"
        f"\U0001F4DD *Step 2/3:* Enter the destination address:\n\n"
        f"_Reply with the {info['name']} address_",
        parse_mode="Markdown"
    )
    context.user_data["withdraw_msg_id"] = msg.message_id

    return WITHDRAW_ADDRESS


async def receive_withdraw_address(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    address = update.message.text.strip()
    chat_id = update.message.chat_id  # Get chat_id before any deletions
    network = context.user_data.get("withdraw_network")
    amount = context.user_data.get("withdraw_amount")
    token = context.user_data.get("withdraw_token")
    token_address = context.user_data.get("withdraw_token_address")
    info = NETWORKS[network]

    # Delete the user's input message
    try:
        await update.message.delete()
    except Exception:
        pass

    # Delete the previous bot message if stored
    prev_msg_id = context.user_data.get("withdraw_msg_id")
    if prev_msg_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=prev_msg_id)
        except Exception:
            pass

    if info["type"] == "evm":
        if not address.startswith("0x") or len(address) != 42:
            msg = await context.bot.send_message(
                chat_id=chat_id,
                text="\u274c *Invalid Address*\n\n"
                "Please enter a valid EVM address starting with `0x`",
                parse_mode="Markdown"
            )
            context.user_data["withdraw_msg_id"] = msg.message_id
            return WITHDRAW_ADDRESS
    elif info["type"] == "solana":
        if len(address) < 32 or len(address) > 44:
            msg = await context.bot.send_message(
                chat_id=chat_id,
                text="\u274c *Invalid Address*\n\n"
                "Please enter a valid Solana address",
                parse_mode="Markdown"
            )
            context.user_data["withdraw_msg_id"] = msg.message_id
            return WITHDRAW_ADDRESS
    elif info["type"] == "tron":
        if not address.startswith("T") or len(address) != 34:
            msg = await context.bot.send_message(
                chat_id=chat_id,
                text="\u274c *Invalid Address*\n\n"
                "Please enter a valid Tron address starting with `T`",
                parse_mode="Markdown"
            )
            context.user_data["withdraw_msg_id"] = msg.message_id
            return WITHDRAW_ADDRESS

    context.user_data["withdraw_address"] = address

    user_id = update.effective_user.id
    pending_withdrawals[user_id] = {
        "network": network,
        "amount": amount,
        "address": address,
        "token": token,
        "token_address": token_address
    }

    if token:
        token_info = TOKENS[token]
        symbol = token_info["symbol"]
        icon = token_info["icon"]
    else:
        symbol = info["symbol"]
        icon = info["icon"]

    keyboard = [
        [
            InlineKeyboardButton(
                "\u2705 Confirm Withdrawal",
                callback_data="exec_withdraw"
            )
        ],
        [
            InlineKeyboardButton(
                "\u274c Cancel",
                callback_data="cancel_withdraw"
            )
        ]
    ]

    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=f"\U0001F4E4 *Withdrawal Confirmation*\n\n"
        f"\U0001F4DD *Step 3/3:* Please review and confirm:\n\n"
        f"{'='*30}\n"
        f"{info['icon']} *Network:* {info['name']}\n"
        f"{icon} *Asset:* {symbol}\n"
        f"\U0001F4B0 *Amount:* `{amount} {symbol}`\n"
        f"\U0001F4CD *To:*\n`{address}`\n"
        f"{'='*30}\n\n"
        f"\u26a0\ufe0f *Warning:* This action cannot be undone!\n"
        f"Please verify the address carefully.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["withdraw_msg_id"] = msg.message_id

    return WITHDRAW_CONFIRM


async def execute_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    withdrawal = pending_withdrawals.get(user_id)
    if withdrawal:
        logger.info(f"User {user_id} withdrawing {withdrawal.get('amount')} {withdrawal.get('token') or withdrawal.get('network')} to {withdrawal.get('address')[:10]}...")

    if not withdrawal:
        await query.edit_message_text(
            "\u274c *Withdrawal Expired*\n\n"
            "Please start a new withdrawal.",
            parse_mode="Markdown",
            reply_markup=get_back_button("menu_withdraw")
        )
        return ConversationHandler.END

    network = withdrawal["network"]
    amount = withdrawal["amount"]
    address = withdrawal["address"]
    token = withdrawal.get("token")
    token_address = withdrawal.get("token_address")
    info = NETWORKS[network]

    if token:
        token_info = TOKENS[token]
        symbol = token_info["symbol"]
        icon = token_info["icon"]
    else:
        symbol = info["symbol"]
        icon = info["icon"]

    wallet = db.get_wallet(user_id, network)
    if not wallet:
        await query.edit_message_text(
            "\u274c *Wallet Not Found*",
            parse_mode="Markdown",
            reply_markup=get_back_button("main_menu")
        )
        return ConversationHandler.END

    await query.edit_message_text(
        f"{h_html('time', 'Processing')}",
        parse_mode="HTML"
    )

    try:
        private_key = CryptoUtils.decrypt_private_key(
            wallet["encrypted_private_key"]
        )

        if token_address and info["type"] == "evm":
            result = await WithdrawalHandler.withdraw_evm(
                network, private_key, address, amount, token_address
            )
        elif token_address and info["type"] == "solana":
            result = await WithdrawalHandler.withdraw_solana_token(
                private_key, address, amount, token_address, decimals=6
            )
        elif token_address and info["type"] == "tron":
            result = await WithdrawalHandler.withdraw_tron_token(
                private_key, address, amount, token_address, decimals=6
            )
        else:
            result = await WithdrawalHandler.withdraw(
                network, private_key, address, amount
            )

        if result.get("success"):
            tx_hash = result.get("tx_hash")
            explorer_url = result.get("explorer_url")
            
            # Show transaction submitted message with pending status
            pending_keyboard = [
                [ikb("View on Explorer", ui_name="explorer", url=explorer_url)]
            ]
            
            max_seconds = 60
            
            await query.edit_message_text(
                f"{h_html('withdraw', 'Sent')}\n\n"
                f"{tok(symbol)}  <b>{esc(amount)} {esc(symbol)}</b>  "
                f"[{esc(info['name'])}]\n"
                f"{ui('address')}  <code>{esc(format_address(address))}</code>\n"
                f"{ui('time')}  <b>Confirming\u2026</b>  <code>0/{max_seconds}s</code>\n\n"
                f"<code>{esc(tx_hash)}</code>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(pending_keyboard)
            )
            
            confirmed = False
            max_attempts = 30
            attempt = 0
            
            while not confirmed and attempt < max_attempts:
                attempt += 1
                await asyncio.sleep(2)
                
                try:
                    if info["type"] == "evm":
                        w3 = get_web3_with_retry(network)
                        receipt = w3.eth.get_transaction_receipt(tx_hash)
                        if receipt:
                            confirmed = receipt.status == 1
                            if not confirmed:
                                # Transaction failed on chain
                                await query.edit_message_text(
                                    f"{h_html('error', 'Failed')}\n\n"
                                    f"<i>Rejected by the blockchain.</i>\n\n"
                                    f"<code>{esc(tx_hash)}</code>",
                                    parse_mode="HTML",
                                    reply_markup=InlineKeyboardMarkup(pending_keyboard)
                                )
                                return ConversationHandler.END
                    elif info["type"] == "solana":
                        import aiohttp
                        async with aiohttp.ClientSession() as session:
                            payload = {
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "getSignatureStatuses",
                                "params": [[tx_hash], {"searchTransactionHistory": True}]
                            }
                            async with session.post(NETWORKS["SOLANA"]["rpc"][0], json=payload) as resp:
                                data = await resp.json()
                                if data.get("result", {}).get("value", [None])[0]:
                                    status = data["result"]["value"][0]
                                    if status.get("confirmationStatus") in ["confirmed", "finalized"]:
                                        confirmed = True
                    elif info["type"] == "tron":
                        from tronpy import Tron
                        client = Tron(network="mainnet")
                        tx_info = client.get_transaction_info(tx_hash)
                        if tx_info and tx_info.get("receipt"):
                            confirmed = True
                    else:
                        # For other networks, assume confirmed after submission
                        confirmed = True
                        
                    elapsed_seconds = attempt * 2
                    if not confirmed and attempt % 3 == 0:
                        await query.edit_message_text(
                            f"{h_html('withdraw', 'Sent')}\n\n"
                            f"{tok(symbol)}  <b>{esc(amount)} {esc(symbol)}</b>  "
                            f"[{esc(info['name'])}]\n"
                            f"{ui('address')}  <code>{esc(format_address(address))}</code>\n"
                            f"{ui('time')}  <b>Confirming\u2026</b>  "
                            f"<code>{elapsed_seconds}/{max_seconds}s</code>\n\n"
                            f"<code>{esc(tx_hash)}</code>",
                            parse_mode="HTML",
                            reply_markup=InlineKeyboardMarkup(pending_keyboard)
                        )
                except Exception as e:
                    logger.warning(f"Error checking tx confirmation: {e}")
                    continue
            
            # Log transaction and debit balance
            db.log_transaction(
                user_id, network, "withdraw", amount,
                tx_hash, address, None, "completed"
            )
            
            # Debit the internal balance after successful withdrawal
            if token:
                ledger_asset = token
            else:
                ledger_asset = get_ledger_asset(network)
            withdraw_amount = Decimal(amount)
            db.debit_balance(user_id, ledger_asset, withdraw_amount, "withdraw", network, tx_hash)
            remaining_balance = db.get_internal_balance(user_id, ledger_asset)

            keyboard = [
                [ikb("View on Explorer", ui_name="explorer", url=explorer_url)],
                [ikb("Balance", ui_name="chart", callback_data=f"balance_{network}")],
                [ikb("Main Menu", ui_name="home", callback_data="main_menu")]
            ]

            if confirmed:
                await query.edit_message_text(
                    f"{h_html('success', 'Sent')}\n\n"
                    f"{tok(symbol)}  <b>{esc(amount)} {esc(symbol)}</b>  "
                    f"[{esc(info['name'])}]\n"
                    f"{ui('address')}  <code>{esc(format_address(address))}</code>\n"
                    f"{ui('check')}  <b>Confirmed</b>\n"
                    f"{ui('money')}  <b>Balance:</b>  <code>{esc(remaining_balance)} {esc(ledger_asset)}</code>\n\n"
                    f"<code>{esc(tx_hash)}</code>",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                # Timeout - show success but note confirmation pending
                await query.edit_message_text(
                    f"{h_html('withdraw', 'Sent')}\n\n"
                    f"{tok(symbol)}  <b>{esc(amount)} {esc(symbol)}</b>  "
                    f"[{esc(info['name'])}]\n"
                    f"{ui('address')}  <code>{esc(format_address(address))}</code>\n"
                    f"{ui('time')}  <b>Confirmation pending</b>\n"
                    f"{ui('money')}  <b>Balance:</b>  <code>{esc(remaining_balance)} {esc(ledger_asset)}</code>\n\n"
                    f"<code>{esc(tx_hash)}</code>",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
        else:
            db.log_transaction(
                user_id, network, "withdraw", amount,
                None, address, None, "failed"
            )

            raw_error = result.get('error', 'Unknown error')
            logger.error(f"Withdrawal failed: {raw_error}")
            friendly_msg = get_friendly_error(raw_error)
            await query.edit_message_text(
                f"{h_html('error', 'Failed')}\n\n{esc(friendly_msg)}",
                parse_mode="HTML",
                reply_markup=get_back_button("menu_withdraw")
            )
    except Exception as e:
        logger.error(f"Withdrawal error: {e}")
        friendly_msg = get_friendly_error(e)
        await query.edit_message_text(
            f"{h_html('error', 'Error')}\n\n{esc(friendly_msg)}",
            parse_mode="HTML",
            reply_markup=get_back_button("menu_withdraw")
        )

    clear_withdrawal_state(user_id, context)
    return ConversationHandler.END


async def cancel_withdraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_callback_auth(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if user_id in pending_withdrawals:
        del pending_withdrawals[user_id]

    context.user_data.clear()

    await edit_message_with_banner(
        query, "withdraw", "*Withdrawal Cancelled*", get_back_button("main_menu")
    )

    return ConversationHandler.END


async def show_explorer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_callback_auth(update):
        return
    query = update.callback_query
    await query.answer()

    network = query.data.split("_")[1]
    user_id = query.from_user.id
    wallet = db.get_wallet(user_id, network)
    info = NETWORKS[network]

    if not wallet:
        await edit_message_with_banner(
            query, "wallet",
            f"*No wallet found for {info['name']}*",
            get_back_button("menu_wallets")
        )
        return

    if network == "SOLANA":
        explorer_url = f"{info['explorer']}/account/{wallet['address']}"
    elif network == "TRON":
        explorer_url = f"{info['explorer']}/#/address/{wallet['address']}"
    else:
        explorer_url = f"{info['explorer']}/address/{wallet['address']}"

    keyboard = [
        [InlineKeyboardButton("Open Explorer", url=explorer_url)],
        [InlineKeyboardButton("Back", callback_data=f"wallet_{network}")]
    ]

    text = (
        f"*{info['name']} Explorer*\n\n"
        f"*Address:*\n`{wallet['address']}`\n\n"
        f"Tap the button below to view on explorer:"
    )
    await edit_message_with_banner(
        query, "wallet", text, InlineKeyboardMarkup(keyboard)
    )


async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_callback_auth(update):
        return
    query = update.callback_query
    await query.answer()

    help_text = (
        f"{h_html('help', 'VM Depo Bot 2.0')}\n"
        "<i>Help Center</i>\n\n"
        f"{h_html('home', 'Navigation')}\n"
        "<code>/start</code> or <code>/menu</code>\n"
        "<i>Open the main menu dashboard</i>\n\n"
        f"{h_html('money', 'Balance & Wallets')}\n"
        "<code>/balance</code>  <i>Check balances across networks</i>\n"
        "<code>/wallets</code>  <i>Manage your wallet addresses</i>\n"
        "<code>/tokens</code>  <i>Token balances by network</i>\n\n"
        f"{h_html('deposit', 'Deposits')}\n"
        "<code>/deposit</code>  <i>Get a deposit address</i>\n"
        "<code>/send TOKEN NETWORK</code>  <i>Quick deposit (e.g. /send USDT BSC)</i>\n\n"
        f"{h_html('search', 'Check')}\n"
        "<code>/check &lt;address&gt;</code>  <i>USDT &amp; USDC balance</i>\n"
        "<code>/check &lt;tx hash|link&gt;</code>  <i>Transaction details</i>\n\n"
        f"{h_html('withdraw', 'Withdrawals')}\n"
        "<code>/withdraw</code>  <i>Send funds to an external wallet</i>\n\n"
        f"{h_html('convert', 'Convert')}\n"
        "<code>/convert</code>  <i>Swap between supported assets</i>\n\n"
        f"{h_html('card', 'Wallet Generation')}\n"
        "<code>/generate</code>  <i>Create a new wallet for any network</i>\n\n"
        f"{SOFT_DIVIDER}\n"
        "<i>Securely Made By Venom</i>"
    )

    await edit_message_with_banner(
        query, "help", help_text, get_back_button("main_menu"), parse_mode="HTML"
    )


CONVERTIBLE_ASSETS = ["ETH", "BNB", "MATIC", "SOL", "TRX", "LTC", "USDT", "USDC"]


CONVERT_AMOUNT = 10


async def show_convert_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_callback_auth(update):
        return
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id

    try:
        await query.message.delete()
    except Exception:
        pass

    user_id = query.from_user.id
    balances = db.get_all_internal_balances(user_id)

    assets_with_balance = [a for a in CONVERTIBLE_ASSETS if balances.get(a, Decimal("0")) > 0]

    if not assets_with_balance:
        keyboard = [
            [InlineKeyboardButton("Deposit", callback_data="menu_deposit")],
            [InlineKeyboardButton("Home", callback_data="main_menu")]
        ]
        text = "*Convert*\n\nNo assets to convert.\nDeposit funds first."
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=open(get_banner_path("convert"), "rb"),
            caption=text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ConversationHandler.END

    balances_str = ", ".join([f"{a} ({balances.get(a, 0):.4f})" for a in assets_with_balance])

    text = (
        "\U0001F504 *Convert Assets*\n\n"
        "Convert from?\n\n"
        f"_{balances_str}_"
    )

    keyboard = [
        [InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]
    ]

    msg = await context.bot.send_photo(
        chat_id=chat_id,
        photo=open(get_banner_path("convert"), "rb"),
        caption=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["convert_msg_id"] = msg.message_id
    context.user_data["convert_balances"] = {a: str(balances.get(a, 0)) for a in assets_with_balance}

    return CONVERT_FROM_ASSET


async def receive_convert_from_asset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive and parse the 'from' asset from user's input for conversion."""
    text = update.message.text.strip().upper()
    chat_id = update.message.chat_id
    user_id = update.effective_user.id

    try:
        await update.message.delete()
    except Exception:
        pass

    if "convert_msg_id" in context.user_data:
        try:
            await context.bot.delete_message(chat_id, context.user_data["convert_msg_id"])
        except Exception:
            pass

    balances = context.user_data.get("convert_balances", {})
    
    from_asset = None
    if text in CONVERTIBLE_ASSETS:
        from_asset = text
    else:
        text_lower = text.lower()
        if text_lower in TOKEN_ALIASES:
            potential = TOKEN_ALIASES[text_lower]
            if potential in CONVERTIBLE_ASSETS:
                from_asset = potential

    if not from_asset or from_asset not in balances:
        balances_str = ", ".join([f"{a} ({balances.get(a, '0')})" for a in balances.keys()])
        
        msg = await context.bot.send_photo(
            chat_id=chat_id,
            photo=open(get_banner_path("convert"), "rb"),
            caption=(
                "\u26a0\ufe0f *Asset Not Recognized*\n\n"
                f"I couldn't understand which asset you want to convert.\n\n"
                f"_Your balances: {balances_str}_\n\n"
                "Please type a valid asset name (e.g., 'ETH', 'USDT', 'BNB')"
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]])
        )
        context.user_data["convert_msg_id"] = msg.message_id
        return CONVERT_FROM_ASSET

    context.user_data["convert_from"] = from_asset
    balance = Decimal(balances.get(from_asset, "0"))

    to_assets = [a for a in CONVERTIBLE_ASSETS if a != from_asset]
    to_assets_str = ", ".join(to_assets)

    text = (
        f"\U0001F504 *Convert {from_asset}*\n\n"
        f"Available: {balance:.6f} {from_asset}\n\n"
        f"Which asset would you like to convert TO?\n\n"
        f"_Available: {to_assets_str}_\n\n"
        "Just type the asset name (e.g., 'USDT', 'ETH', 'BNB')"
    )

    keyboard = [
        [InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]
    ]

    msg = await context.bot.send_photo(
        chat_id=chat_id,
        photo=open(get_banner_path("convert"), "rb"),
        caption=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["convert_msg_id"] = msg.message_id

    return CONVERT_TO_ASSET


async def receive_convert_to_asset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive and parse the 'to' asset from user's input for conversion."""
    text = update.message.text.strip().upper()
    chat_id = update.message.chat_id
    user_id = update.effective_user.id

    try:
        await update.message.delete()
    except Exception:
        pass

    if "convert_msg_id" in context.user_data:
        try:
            await context.bot.delete_message(chat_id, context.user_data["convert_msg_id"])
        except Exception:
            pass

    from_asset = context.user_data.get("convert_from")
    
    to_asset = None
    if text in CONVERTIBLE_ASSETS and text != from_asset:
        to_asset = text
    else:
        text_lower = text.lower()
        if text_lower in TOKEN_ALIASES:
            potential = TOKEN_ALIASES[text_lower]
            if potential in CONVERTIBLE_ASSETS and potential != from_asset:
                to_asset = potential

    if not to_asset:
        to_assets = [a for a in CONVERTIBLE_ASSETS if a != from_asset]
        to_assets_str = ", ".join(to_assets)
        
        msg = await context.bot.send_photo(
            chat_id=chat_id,
            photo=open(get_banner_path("convert"), "rb"),
            caption=(
                "\u26a0\ufe0f *Asset Not Recognized*\n\n"
                f"I couldn't understand which asset you want to convert to.\n\n"
                f"_Available: {to_assets_str}_\n\n"
                "Please type a valid asset name (e.g., 'USDT', 'ETH', 'BNB')"
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]])
        )
        context.user_data["convert_msg_id"] = msg.message_id
        return CONVERT_TO_ASSET

    context.user_data["convert_to"] = to_asset
    
    balances = context.user_data.get("convert_balances", {})
    balance = Decimal(balances.get(from_asset, "0"))
    context.user_data["convert_balance"] = str(balance)

    rate = await PriceFetcher.get_conversion_rate(from_asset, to_asset)

    text = (
        f"\U0001F504 *Convert {from_asset} to {to_asset}*\n\n"
        f"Available: {balance:.6f} {from_asset}\n"
        f"Rate: 1 {from_asset} = {rate:.6f} {to_asset}\n\n"
        f"Enter the amount of {from_asset} to convert:"
    )

    keyboard = [
        [InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]
    ]

    msg = await context.bot.send_photo(
        chat_id=chat_id,
        photo=open(get_banner_path("convert"), "rb"),
        caption=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["convert_msg_id"] = msg.message_id

    return CONVERT_AMOUNT_AI


async def receive_convert_amount_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive and process the conversion amount from user's input."""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return ConversationHandler.END

    chat_id = update.message.chat_id
    amount_str = update.message.text.strip()

    try:
        await update.message.delete()
    except Exception:
        pass

    if "convert_msg_id" in context.user_data:
        try:
            await context.bot.delete_message(chat_id, context.user_data["convert_msg_id"])
        except Exception:
            pass

    try:
        amount = Decimal(amount_str)
        if amount <= 0:
            raise ValueError("Amount must be positive")
    except Exception:
        from_asset = context.user_data.get("convert_from")
        to_asset = context.user_data.get("convert_to")
        balance_str = context.user_data.get("convert_balance", "0")
        
        msg = await context.bot.send_photo(
            chat_id=chat_id,
            photo=open(get_banner_path("convert"), "rb"),
            caption=(
                f"\u26a0\ufe0f *Invalid Amount*\n\n"
                f"Please enter a valid number.\n\n"
                f"Available: {balance_str} {from_asset}"
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]])
        )
        context.user_data["convert_msg_id"] = msg.message_id
        return CONVERT_AMOUNT_AI

    balance_str = context.user_data.get("convert_balance", "0")
    try:
        balance = Decimal(balance_str)
    except Exception:
        balance = Decimal("0")

    from_asset = context.user_data.get("convert_from")
    to_asset = context.user_data.get("convert_to")

    if amount > balance:
        msg = await context.bot.send_photo(
            chat_id=chat_id,
            photo=open(get_banner_path("convert"), "rb"),
            caption=(
                f"\u26a0\ufe0f *Insufficient Balance*\n\n"
                f"You have {balance_str} {from_asset}.\n\n"
                f"Please enter a smaller amount."
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\u274c Cancel", callback_data="main_menu")]])
        )
        context.user_data["convert_msg_id"] = msg.message_id
        return CONVERT_AMOUNT_AI

    context.user_data["convert_amount"] = str(amount)

    rate = await PriceFetcher.get_conversion_rate(from_asset, to_asset)
    to_amount = amount * rate

    keyboard = [
        [
            InlineKeyboardButton("Confirm", callback_data="confirm_convert_ai"),
            InlineKeyboardButton("Cancel", callback_data="main_menu")
        ]
    ]

    msg = await context.bot.send_photo(
        chat_id=chat_id,
        photo=open(get_banner_path("convert"), "rb"),
        caption=(
            f"\U0001F504 *Confirm Conversion*\n\n"
            f"*From:* {amount:.6f} {from_asset}\n"
            f"*To:* {to_amount:.6f} {to_asset}\n"
            f"*Rate:* 1 {from_asset} = {rate:.6f} {to_asset}\n\n"
            f"Confirm this conversion?"
        ),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["convert_msg_id"] = msg.message_id

    return ConversationHandler.END


async def confirm_convert_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Execute the conversion after confirmation."""
    if not await check_callback_auth(update):
        return
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    user_id = query.from_user.id

    try:
        await query.message.delete()
    except Exception:
        pass

    from_asset = context.user_data.get("convert_from")
    to_asset = context.user_data.get("convert_to")
    amount_str = context.user_data.get("convert_amount", "0")
    amount = Decimal(amount_str)

    msg = await context.bot.send_photo(
        chat_id=chat_id,
        photo=open(get_banner_path("convert"), "rb"),
        caption=f"*Processing conversion...*",
        parse_mode="Markdown"
    )

    try:
        rate = await PriceFetcher.get_conversion_rate(from_asset, to_asset)
        to_amount = amount * rate

        success = db.convert_balance(user_id, from_asset, to_asset, amount, to_amount)

        if success:
            keyboard = [
                [InlineKeyboardButton("Convert More", callback_data="menu_convert")],
                [InlineKeyboardButton("Home", callback_data="main_menu")]
            ]
            await msg.edit_caption(
                caption=(
                    f"\u2705 *Conversion Successful!*\n\n"
                    f"*Converted:* {amount:.6f} {from_asset}\n"
                    f"*Received:* {to_amount:.6f} {to_asset}\n"
                    f"*Rate:* 1 {from_asset} = {rate:.6f} {to_asset}"
                ),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            keyboard = [[InlineKeyboardButton("Home", callback_data="main_menu")]]
            await msg.edit_caption(
                caption=f"\u274c *Conversion Failed*\n\nInsufficient balance.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    except Exception as e:
        logger.error(f"Conversion error: {e}")
        keyboard = [[InlineKeyboardButton("Home", callback_data="main_menu")]]
        await msg.edit_caption(
            caption=f"\u274c *Conversion Failed*\n\n{str(e)}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def cancel_convert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_callback_auth(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()

    context.user_data.clear()

    await query.edit_message_text(
        "*Conversion Cancelled*",
        parse_mode="Markdown",
        reply_markup=get_back_button("main_menu")
    )
    
    return ConversationHandler.END


async def show_tokens_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_callback_auth(update):
        return
    query = update.callback_query
    await query.answer()

    text = "*Token Balances*\nSelect a token to check:"

    await edit_message_with_banner(
        query, "tokens", text, get_token_keyboard()
    )


async def show_token_networks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_callback_auth(update):
        return
    query = update.callback_query
    await query.answer()

    token = query.data.split("_")[1]
    token_info = TOKENS.get(token)

    if not token_info:
        await edit_message_with_banner(
            query, "tokens", "*Token not found*", get_back_button("menu_tokens")
        )
        return

    text = (
        f"*{token_info['name']}*\n\n"
        f"Select a network to check your {token_info['symbol']} balance:"
    )

    await edit_message_with_banner(
        query, "tokens", text, get_token_network_keyboard(token)
    )


async def show_token_balance_networks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not await check_callback_auth(update):
        return
    query = update.callback_query
    await query.answer()

    token = query.data.split("_")[2]
    token_info = TOKENS.get(token)

    if not token_info:
        await edit_message_with_banner(
            query, "balance", "*Token not found*", get_back_button("menu_balance")
        )
        return

    keyboard = []
    for network in token_info.get("networks", {}).keys():
        net_info = NETWORKS.get(network)
        if net_info:
            keyboard.append([
                InlineKeyboardButton(
                    f"{net_info['name']}",
                    callback_data=f"tokenbal_{token}_{network}"
                )
            ])

    keyboard.append([InlineKeyboardButton("Back", callback_data="menu_balance")])

    text = (
        f"*{token_info['name']}*\n\n"
        f"Select a network to check your {token_info['symbol']} balance:"
    )

    await edit_message_with_banner(
        query, "balance", text, InlineKeyboardMarkup(keyboard)
    )


async def check_token_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_callback_auth(update):
        return
    query = update.callback_query
    await query.answer("Fetching token balance...")

    parts = query.data.split("_")
    token = parts[1]
    network = parts[2]

    user_id = query.from_user.id
    token_info = TOKENS.get(token)
    network_info = NETWORKS.get(network)

    if not token_info or not network_info:
        await edit_message_with_banner(
            query, "tokens", "*Invalid token or network*", get_back_button("menu_tokens")
        )
        return

    wallet = db.get_wallet(user_id, network)

    if not wallet:
        keyboard = [
            [InlineKeyboardButton(
                f"Generate {network_info['name']} Wallet",
                callback_data=f"gen_{network}"
            )],
            [InlineKeyboardButton("Back", callback_data=f"token_{token}")]
        ]
        await edit_message_with_banner(
            query, "tokens",
            f"*No {network_info['name']} Wallet*\n\n"
            f"You need a {network_info['name']} wallet to check "
            f"{token_info['symbol']} balance.",
            InlineKeyboardMarkup(keyboard)
        )
        return

    await edit_message_with_banner(
        query, "tokens",
        f"*Fetching {token_info['symbol']} balance on {network_info['name']}...*",
        None
    )

    balance_info = await BalanceChecker.get_token_balance(
        token, network, wallet["address"]
    )
    balance_str = balance_info.get("balance", "0")

    keyboard = [
        [InlineKeyboardButton("Refresh", callback_data=f"tokenbal_{token}_{network}")],
        [InlineKeyboardButton("Back", callback_data=f"token_{token}")],
        [InlineKeyboardButton("Main Menu", callback_data="main_menu")]
    ]

    text = (
        f"*{token_info['symbol']} Balance*\n\n"
        f"*Network:* {network_info['name']}\n"
        f"*Balance:* `{balance_str} {token_info['symbol']}`\n\n"
        f"*Wallet:*\n`{wallet['address']}`"
    )
    await edit_message_with_banner(
        query, "tokens", text, InlineKeyboardMarkup(keyboard)
    )


async def handle_text_commands(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if update.message is None:
        return

    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return

    text = update.message.text.lower().strip()

    if text in ["/menu", "/start", "menu", "home"]:
        await start(update, context)


def get_ledger_asset(network: str, token_key: str = None) -> str:
    if token_key:
        return token_key
    network_to_asset = {
        "ETH": "ETH",
        "BSC": "BNB",
        "POLYGON": "MATIC",
        "SOLANA": "SOL",
        "TRON": "TRX",
        "LTC": "LTC"
    }
    return network_to_asset.get(network, network)


async def check_wallet_transactions(application):
    global wallet_balances_cache, wallet_cache_initialized, notification_cooldowns
    import time

    wallets = db.get_all_wallets(ALLOWED_USER_ID)
    if not wallets:
        return

    # On first run, just initialize the cache without sending notifications
    # This prevents spam notifications for existing balances when bot starts
    is_first_run = not wallet_cache_initialized

    async def send_balance_notification(
        network, address, symbol, old_balance, new_balance, token_name=None
    ):
        old_val = Decimal(old_balance) if old_balance else Decimal("0")
        new_val = Decimal(new_balance) if new_balance else Decimal("0")

        if new_val == old_val:
            return

        diff = new_val - old_val
        network_info = NETWORKS.get(network, {})

        network_short = network
        if network == "TRON":
            network_short = "TRX"
        elif network == "SOLANA":
            network_short = "SOL"
        elif network == "POLYGON":
            network_short = "MATIC"

        # Filter out small balance changes (noise from RPC inconsistencies)
        # Use higher threshold for stablecoins due to Polygon RPC fluctuations
        is_native = token_name is None
        min_threshold = Decimal("0.0001") if is_native else Decimal("0.5")
        if abs(diff) < min_threshold:
            return

        # Only send notifications for deposits, not withdrawals
        if diff <= 0:
            return
        
        # Check if notifications are enabled
        if not notifications_enabled:
            return
        
        # Cooldown: Don't send notification for same token/network within 5 minutes
        cooldown_key = f"{network}:{token_name or 'NATIVE'}"
        current_time = time.time()
        last_notification = notification_cooldowns.get(cooldown_key, 0)
        if current_time - last_notification < 300:  # 5 minute cooldown
            return
        notification_cooldowns[cooldown_key] = current_time

        msg = (
            f"{h_html('deposit', 'Deposit Received')}\n\n"
            f"{tok(symbol)}  <b>+{esc(diff)} {esc(symbol)}</b>  "
            f"[{esc(network_info.get('name', network_short))}]\n"
            f"{ui('money')}  <b>Balance:</b>  <code>{esc(new_balance)} {esc(symbol)}</code>"
        )

        keyboard = [[
            ikb(
                "View on Explorer", ui_name="explorer",
                url=f"{network_info.get('explorer', '')}/address/{address}"
            )
        ]]

        try:
            await application.bot.send_message(
                chat_id=ALLOWED_USER_ID,
                text=msg,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            if ALLOWED_CHAT_ID:
                await application.bot.send_message(
                    chat_id=ALLOWED_CHAT_ID,
                    text=msg,
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
        except Exception as e:
            logger.error(f"Error sending transaction notification: {e}")

    for wallet in wallets:
        network = wallet["network"]
        address = wallet["address"]

        try:
            balance_info = await BalanceChecker.get_balance(network, address)
            current_balance = balance_info.get("balance", "0")
            symbol = balance_info.get("symbol", network)
            cache_key = f"{network}:{address}:NATIVE"

            if cache_key in wallet_balances_cache and not is_first_run:
                old_balance = wallet_balances_cache[cache_key]
                old_val = Decimal(old_balance) if old_balance else Decimal("0")
                new_val = Decimal(current_balance) if current_balance else Decimal("0")
                diff = new_val - old_val

                if diff > Decimal("0.0001"):
                    ledger_asset = get_ledger_asset(network)
                    db.credit_balance(
                        ALLOWED_USER_ID, ledger_asset, diff, "deposit", network
                    )
                    logger.info(f"Credited {diff} {ledger_asset} to internal balance")

                await send_balance_notification(
                    network, address, symbol, old_balance, current_balance
                )

            wallet_balances_cache[cache_key] = current_balance

        except Exception as e:
            logger.error(f"Error checking native balance for {network}: {e}")

        # Add delay between RPC calls to avoid rate limiting
        await asyncio.sleep(3)

        for token_key in ["USDT", "USDC"]:
            token_info = TOKENS.get(token_key, {})
            if network not in token_info.get("networks", {}):
                continue

            network_token = token_info["networks"][network]
            if network_token.get("native"):
                continue

            try:
                token_balance_info = await BalanceChecker.get_token_balance(
                    token_key, network, address
                )

                if token_balance_info.get("error"):
                    continue

                current_token_balance = token_balance_info.get("balance", "0")
                token_symbol = token_balance_info.get("symbol", token_key)
                token_cache_key = f"{network}:{address}:{token_key}"

                if token_cache_key in wallet_balances_cache and not is_first_run:
                    old_token_balance = wallet_balances_cache[token_cache_key]
                    old_val = Decimal(old_token_balance) if old_token_balance else Decimal("0")
                    new_val = (
                        Decimal(current_token_balance) if current_token_balance
                        else Decimal("0")
                    )
                    diff = new_val - old_val

                    if diff > Decimal("0.0001"):
                        ledger_asset = get_ledger_asset(network, token_key)
                        db.credit_balance(
                            ALLOWED_USER_ID, ledger_asset, diff, "deposit", network
                        )
                        logger.info(f"Credited {diff} {ledger_asset} to internal balance")

                    await send_balance_notification(
                        network, address, token_symbol,
                        old_token_balance, current_token_balance, token_key
                    )

                wallet_balances_cache[token_cache_key] = current_token_balance

            except Exception as e:
                logger.error(f"Error checking {token_key} balance on {network}: {e}")
            
            # Add delay between token balance checks to avoid rate limiting
            await asyncio.sleep(3)

    # Mark cache as initialized after first run completes
    if is_first_run:
        wallet_cache_initialized = True
        logger.info("Wallet balance cache initialized - notifications will start from next check")


async def transaction_monitor_loop(application):
    import asyncio
    logger.info("Transaction monitor started - checking every 180 seconds")
    await asyncio.sleep(10)
    while True:
        try:
            await check_wallet_transactions(application)
        except Exception as e:
            logger.error(f"Transaction monitor error: {e}")
        await asyncio.sleep(180)  # 3 minutes between checks to reduce RPC calls


async def background_sync_balances():
    """Background task to sync balances every 300 seconds (5 minutes)."""
    import asyncio
    logger.info("Background balance sync started - syncing every 300 seconds")
    await asyncio.sleep(60)  # Offset from transaction monitor
    
    while True:
        try:
            # Sync balances for all authorized users
            for user_id in list(set(USER_ACCESS.keys()) | AUTHORIZED_USER_IDS):
                wallets = db.get_all_wallets(user_id)
                if not wallets:
                    continue
                
                # Aggregate token balances across all networks
                token_totals = {"USDT": Decimal("0"), "USDC": Decimal("0")}
                
                for wallet in wallets:
                    network = wallet["network"]
                    address = wallet["address"]
                    
                    # Sync native balances
                    try:
                        balance_info = await BalanceChecker.get_balance(network, address)
                        if not balance_info.get("error"):
                            onchain_balance = Decimal(balance_info.get("balance", "0"))
                            ledger_asset = get_ledger_asset(network)
                            internal_balance = db.get_internal_balance(user_id, ledger_asset)
                            
                            # Only log significant changes (> 0.01)
                            diff = abs(onchain_balance - internal_balance)
                            if diff > Decimal("0.01"):
                                db.update_internal_balance(user_id, ledger_asset, onchain_balance)
                                logger.info(f"Background sync: {user_id} {ledger_asset} {internal_balance} -> {onchain_balance}")
                            elif onchain_balance != internal_balance:
                                # Still update but don't log small changes
                                db.update_internal_balance(user_id, ledger_asset, onchain_balance)
                    except Exception as e:
                        logger.debug(f"Background sync error for {network}: {e}")
                    
                    # Add delay between RPC calls to avoid rate limiting
                    await asyncio.sleep(2)
                    
                    # Collect token balances
                    for token_key in ["USDT", "USDC"]:
                        token_info = TOKENS.get(token_key, {})
                        if network not in token_info.get("networks", {}):
                            continue
                        network_token = token_info["networks"][network]
                        if network_token.get("native"):
                            continue
                        
                        try:
                            token_balance_info = await BalanceChecker.get_token_balance(
                                token_key, network, address
                            )
                            if not token_balance_info.get("error"):
                                onchain_token = Decimal(token_balance_info.get("balance", "0"))
                                token_totals[token_key] += onchain_token
                        except Exception as e:
                            logger.debug(f"Background sync error for {token_key} on {network}: {e}")
                        
                        # Add delay between RPC calls to avoid rate limiting
                        await asyncio.sleep(2)
                
                # Update aggregated token balances
                for token_key in ["USDT", "USDC"]:
                    total_onchain = token_totals[token_key]
                    ledger_asset = get_ledger_asset(None, token_key)
                    internal_balance = db.get_internal_balance(user_id, ledger_asset)
                    
                    # Only log significant changes (> 0.01)
                    diff = abs(total_onchain - internal_balance)
                    if diff > Decimal("0.01"):
                        db.update_internal_balance(user_id, ledger_asset, total_onchain)
                        logger.info(f"Background sync: {user_id} {ledger_asset} {internal_balance} -> {total_onchain}")
                    elif total_onchain != internal_balance:
                        # Still update but don't log small changes
                        db.update_internal_balance(user_id, ledger_asset, total_onchain)
                
        except Exception as e:
            logger.error(f"Background sync error: {e}")
        
        await asyncio.sleep(300)  # 5 minutes between syncs to reduce RPC calls


async def start_transaction_monitor(application):
    import asyncio
    asyncio.create_task(transaction_monitor_loop(application))
    logger.info("Transaction monitor background task created")
    asyncio.create_task(background_sync_balances())
    logger.info("Background balance sync task created")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler to prevent bot from dying on exceptions."""
    logger.error(f"Exception while handling an update: {context.error}")
    logger.error(f"Traceback: {traceback.format_exc()}")
    
    try:
        if update and hasattr(update, 'effective_chat') and update.effective_chat:
            chat_id = update.effective_chat.id
            await context.bot.send_message(
                chat_id=chat_id,
                text="An error occurred. Please try again or use /start to restart."
            )
    except Exception as e:
        logger.error(f"Failed to send error message to user: {e}")


def main():
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN environment variable not set!")
        return

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(start_transaction_monitor)
        .build()
    )

    deposit_handler = ConversationHandler(
        entry_points=[
            CommandHandler("deposit", deposit_command),
            CallbackQueryHandler(
                show_deposit_menu,
                pattern=r"^menu_deposit$"
            )
        ],
        states={
            DEPOSIT_TOKEN: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_deposit_token
                ),
                CallbackQueryHandler(show_tokens_list_popup, pattern="^show_tokens_list_deposit$")
            ],
            DEPOSIT_NETWORK: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_deposit_network
                )
            ],
            DEPOSIT_CONFIRM_SELECTION: [
                CallbackQueryHandler(confirm_deposit_selection, pattern="^confirm_deposit_selection$"),
                CallbackQueryHandler(show_deposit_menu, pattern="^menu_deposit$")
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cancel_command),
            CallbackQueryHandler(show_main_menu, pattern="^main_menu$"),
            CallbackQueryHandler(show_deposit_menu, pattern="^menu_deposit$")
        ],
        per_message=False
    )

    withdraw_handler = ConversationHandler(
        entry_points=[
            CommandHandler("withdraw", withdraw_command),
            CallbackQueryHandler(
                show_withdraw_menu,
                pattern=r"^menu_withdraw$"
            ),
            CallbackQueryHandler(
                start_withdraw,
                pattern=r"^withdraw_[A-Z]+$"
            ),
            CallbackQueryHandler(
                show_token_withdraw_info,
                pattern=r"^tokenwd_[A-Z]+_[A-Z]+$"
            ),
            CallbackQueryHandler(
                show_combo_withdraw,
                pattern=r"^withdraw_combo_[A-Z]+_[A-Z]+$"
            ),
            CallbackQueryHandler(
                show_token_withdraw_networks,
                pattern=r"^withdraw_token_[A-Z]+$"
            )
        ],
        states={
            WITHDRAW_TOKEN: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_withdraw_token
                ),
                CallbackQueryHandler(show_tokens_list_popup, pattern="^show_tokens_list_withdraw$")
            ],
            WITHDRAW_NETWORK: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_withdraw_network
                )
            ],
            WITHDRAW_CONFIRM_SELECTION: [
                CallbackQueryHandler(confirm_withdraw_selection, pattern="^confirm_withdraw_selection$"),
                CallbackQueryHandler(show_withdraw_menu, pattern="^menu_withdraw$")
            ],
            WITHDRAW_AMOUNT: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_withdraw_amount
                )
            ],
            WITHDRAW_QUICK_NETWORK: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_withdraw_quick_network
                )
            ],
            WITHDRAW_ADDRESS: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_withdraw_address
                )
            ],
            WITHDRAW_CONFIRM: [
                CallbackQueryHandler(execute_withdraw, pattern="^exec_withdraw$")
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cancel_command),
            CallbackQueryHandler(cancel_withdraw, pattern="^cancel_withdraw$"),
            CallbackQueryHandler(show_main_menu, pattern="^main_menu$"),
            CallbackQueryHandler(show_withdraw_menu, pattern="^menu_withdraw$")
        ],
        per_message=False
    )

    # Generate wallet conversation handler
    generate_handler = ConversationHandler(
        entry_points=[
            CommandHandler("generate", generate_command),
            CallbackQueryHandler(
                show_generate_menu,
                pattern=r"^menu_generate$"
            )
        ],
        states={
            GENERATE_NETWORK: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_generate_network
                )
            ],
            GENERATE_CONFIRM: [
                CallbackQueryHandler(confirm_generate_ai, pattern="^confirm_generate_ai$")
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cancel_command),
            CallbackQueryHandler(show_main_menu, pattern="^main_menu$")
        ],
        per_message=False
    )

    # Balance check conversation handler
    balance_handler = ConversationHandler(
        entry_points=[
            CommandHandler("balance", balance_command),
            CallbackQueryHandler(
                show_balance_menu,
                pattern=r"^menu_balance$"
            )
        ],
        states={
            BALANCE_NETWORK: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_balance_network
                )
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cancel_command),
            CallbackQueryHandler(show_main_menu, pattern="^main_menu$")
        ],
        per_message=False
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", start))
    application.add_handler(CommandHandler("send", send_command))
    application.add_handler(CommandHandler("wallets", wallets_command))
    application.add_handler(CommandHandler("tokens", tokens_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CommandHandler("check", check_command))
    application.add_handler(CommandHandler("notification", notification_command))
    application.add_handler(CommandHandler("notifications", notification_command))
    application.add_handler(CommandHandler("sync", sync_command))
    application.add_handler(CommandHandler("fix", fix_command))

    application.add_handler(CommandHandler("add", admin_add_command))
    application.add_handler(CommandHandler("list", admin_list_users_command))
    application.add_handler(CommandHandler("remove", admin_remove_command))

    application.add_handler(
        MessageHandler(
            filters.Regex(r"^\+add(?:\s|$)") & ~filters.UpdateType.EDITED_MESSAGE,
            admin_add_command
        )
    )
    application.add_handler(
        MessageHandler(
            filters.Regex(r"^\+list(?:\s|$)") & ~filters.UpdateType.EDITED_MESSAGE,
            admin_list_users_command
        )
    )
    application.add_handler(
        MessageHandler(
            filters.Regex(r"^\+remove(?:\s|$)") & ~filters.UpdateType.EDITED_MESSAGE,
            admin_remove_command
        )
    )

    application.add_handler(deposit_handler)
    application.add_handler(withdraw_handler)
    application.add_handler(generate_handler)
    application.add_handler(balance_handler)

    # Convert conversation handler (AI flow)
    convert_handler_ai = ConversationHandler(
        entry_points=[
            CommandHandler("convert", convert_command),
            CallbackQueryHandler(
                show_convert_menu,
                pattern=r"^menu_convert$"
            )
        ],
        states={
            CONVERT_FROM_ASSET: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_convert_from_asset
                )
            ],
            CONVERT_TO_ASSET: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_convert_to_asset
                )
            ],
            CONVERT_AMOUNT_AI: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_convert_amount_ai
                )
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cancel_command),
            CallbackQueryHandler(cancel_convert, pattern="^cancel_convert$"),
            CallbackQueryHandler(show_main_menu, pattern="^main_menu$")
        ],
        per_message=False
    )
    application.add_handler(convert_handler_ai)
    
    application.add_handler(
        CallbackQueryHandler(confirm_convert_ai, pattern="^confirm_convert_ai$")
    )

    application.add_handler(
        CallbackQueryHandler(show_main_menu, pattern="^main_menu$")
    )
    application.add_handler(
        CallbackQueryHandler(show_wallets_menu, pattern="^menu_wallets$")
    )
    application.add_handler(
        CallbackQueryHandler(show_wallet_details, pattern=r"^wallet_[A-Z]+$")
    )
    application.add_handler(
        CallbackQueryHandler(refresh_balance, pattern=r"^refresh_[A-Z]+$")
    )
    application.add_handler(
        CallbackQueryHandler(generate_wallet, pattern=r"^gen_[A-Z]+$")
    )
    application.add_handler(
        CallbackQueryHandler(
            confirm_generate_wallet,
            pattern=r"^confirmgen_[A-Z]+$"
        )
    )
    application.add_handler(
        CallbackQueryHandler(show_deposit_menu, pattern="^menu_deposit$")
    )
    application.add_handler(
        CallbackQueryHandler(show_deposit_address, pattern=r"^deposit_[A-Z]+$")
    )
    application.add_handler(
        CallbackQueryHandler(
            show_token_deposit_networks,
            pattern=r"^deposit_token_[A-Z]+$"
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            show_token_deposit_address,
            pattern=r"^tokendep_[A-Z]+_[A-Z]+$"
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            show_combo_deposit,
            pattern=r"^deposit_combo_[A-Z]+_[A-Z]+$"
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            refresh_deposit_balance,
            pattern=r"^refresh_dep_[A-Z]+_[A-Z]+$"
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            refresh_send_balance,
            pattern=r"^refresh_send_[A-Z]+_[A-Z]+$"
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            show_token_withdraw_networks,
            pattern=r"^withdraw_token_[A-Z]+$"
        )
    )
    application.add_handler(
        CallbackQueryHandler(lambda u, c: u.callback_query.answer(), pattern="^noop$")
    )
    application.add_handler(
        CallbackQueryHandler(lambda u, c: u.callback_query.answer(), pattern="^ignore$")
    )
    application.add_handler(
        CallbackQueryHandler(check_balance, pattern=r"^balance_")
    )
    application.add_handler(
        CallbackQueryHandler(show_withdraw_menu, pattern="^menu_withdraw$")
    )
    application.add_handler(
        CallbackQueryHandler(show_explorer, pattern=r"^explorer_[A-Z]+$")
    )
    application.add_handler(
        CallbackQueryHandler(refresh_check_address, pattern=r"^check_refresh_.*")
    )
    application.add_handler(
        CallbackQueryHandler(show_help, pattern="^menu_help$")
    )
    application.add_handler(
        CallbackQueryHandler(show_tokens_menu, pattern="^menu_tokens$")
    )
    application.add_handler(
        CallbackQueryHandler(show_token_networks, pattern=r"^token_[A-Z]+$")
    )
    application.add_handler(
        CallbackQueryHandler(
            show_token_balance_networks,
            pattern=r"^token_balance_[A-Z]+$"
        )
    )
    application.add_handler(
        CallbackQueryHandler(check_token_balance, pattern=r"^tokenbal_[A-Z]+_[A-Z]+$")
    )
    application.add_handler(
        CallbackQueryHandler(toggle_notifications, pattern=r"^notif_(stop|resume)$")
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & ~filters.UpdateType.EDITED_MESSAGE,
            handle_text_commands
        )
    )

    # Add global error handler to prevent bot from dying
    application.add_error_handler(error_handler)

    logger.info("Starting Depo Bot with enhanced UI...")
    application.run_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
