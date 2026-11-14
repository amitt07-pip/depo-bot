import os
import io
import json
import html
import sqlite3
import logging
import hashlib
import base64
import asyncio
import time
import aiohttp
import requests
import re
import inspect
import contextvars
from typing import Optional
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

try:
    import qrcode
    QR_AVAILABLE = True
except ImportError:
    QR_AVAILABLE = False

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, MessageEntity,
    CopyTextButton, Message
)
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    Defaults,
    MessageHandler,
    TypeHandler,
    filters,
)


class InlineKeyboardMarkup(InlineKeyboardMarkup):
    """Drop any inline row whose label is a main-menu button."""

    _MAIN_MENU_LABELS = {"main menu", "home", "back to menu"}

    def __init__(self, inline_keyboard, *args, **kwargs):
        cleaned = []
        emoji_re = re.compile(r"^[\U0001F300-\U0001FAFF\s]+")
        for row in inline_keyboard:
            new_row = []
            for button in row:
                text = (button.text or "").strip()
                label = emoji_re.sub("", text).strip().lower()
                if label in self._MAIN_MENU_LABELS and getattr(button, "callback_data", None) == "main_menu":
                    continue
                new_row.append(button)
            if new_row:
                cleaned.append(new_row)
        super().__init__(inline_keyboard=cleaned, *args, **kwargs)


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

from address_card import render_address_card, render_dashboard_card

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
    # No clipboard icon in the pack; reuses the note icon. Override with COPY_EMOJI_ID.
    "copy": (os.getenv("COPY_EMOJI_ID") or "5886330010054168711", "\U0001F4CB"),  # 📋
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
    "OPBNB": "BNB",
    "POLYGON": "MATIC",
    "SOLANA": "SOL",
    "TRON": "TRX",
    "LTC": "LTC",
    "BTC": "BTC",
    "TON": "TON",
}

# Tracks owner /send reply-prompt messages so we can forward replies.
REPLY_PROMPTS = {}


class ReplyToOwnerFilter(filters.MessageFilter):
    """Match private replies to an active owner /send prompt."""

    def filter(self, message):
        if not message or not message.reply_to_message:
            return False
        if message.chat.type != "private":
            return False
        reply_to = message.reply_to_message
        key = (reply_to.chat_id, reply_to.message_id)
        if key not in REPLY_PROMPTS:
            return False
        prompt_text = reply_to.text or ""
        return "Now reply to this message with your text" in prompt_text


REPLY_TO_OWNER_FILTER = ReplyToOwnerFilter()


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


# Per-user ownership of interactive bot messages: (chat_id, message_id) -> user_id.
# Only the user who triggered a menu may press its buttons.
CURRENT_USER_ID = contextvars.ContextVar("current_user_id", default=None)
MESSAGE_OWNERS = {}
_MESSAGE_OWNERS_MAX = 5000


def _remember_owner(message, owner_id):
    if owner_id is None or message is None or not hasattr(message, "message_id"):
        return
    if len(MESSAGE_OWNERS) >= _MESSAGE_OWNERS_MAX:
        for key in list(MESSAGE_OWNERS)[: _MESSAGE_OWNERS_MAX // 5]:
            MESSAGE_OWNERS.pop(key, None)
    MESSAGE_OWNERS[(message.chat_id, message.message_id)] = owner_id


def _install_message_owner_tracking():
    """Record which user each keyboard-bearing bot message belongs to."""
    from telegram import Bot

    def _wrap(method_name):
        orig = getattr(Bot, method_name)

        async def wrapper(self, *args, **kwargs):
            result = await orig(self, *args, **kwargs)
            if kwargs.get("reply_markup") is not None:
                _remember_owner(result, CURRENT_USER_ID.get())
            return result

        wrapper.__name__ = getattr(orig, "__name__", method_name)
        wrapper.__doc__ = getattr(orig, "__doc__", None)
        setattr(Bot, method_name, wrapper)

    for name in ("send_message", "send_photo", "send_document", "send_animation",
                 "edit_message_text", "edit_message_caption", "edit_message_media",
                 "edit_message_reply_markup"):
        _wrap(name)


_install_message_owner_tracking()


def _install_safe_callback_answer():
    """Expired callback queries must not abort the handler that answers them."""
    from telegram import CallbackQuery
    from telegram.error import BadRequest

    orig = CallbackQuery.answer

    async def answer(self, *args, **kwargs):
        try:
            return await orig(self, *args, **kwargs)
        except BadRequest as e:
            msg = str(e).lower()
            if "query is too old" in msg or "query id is invalid" in msg:
                logger.debug(f"Ignoring expired callback query: {e}")
                return False
            raise

    answer.__doc__ = orig.__doc__
    CallbackQuery.answer = answer


_install_safe_callback_answer()


# ---------------------------------------------------------------------------
# Coloured buttons (InlineKeyboardButton.style) and rich messages
# (sendRichMessage: buttons inside the text, collapsible blocks).
# ---------------------------------------------------------------------------
_IKB_SUPPORTS_STYLE = "style" in inspect.signature(InlineKeyboardButton.__init__).parameters
_BASE_MARKUP = InlineKeyboardMarkup.__mro__[1]  # telegram.InlineKeyboardMarkup

_STOP_RE = re.compile(r"\b(cancel|delete|remove|revoke|reject|stop)\b", re.I)
_CONFIRM_RE = re.compile(r"\b(confirm|yes|approve|resume)\b", re.I)
_OUT_RE = re.compile(r"\b(withdraw|send|rescue)\b", re.I)
_IN_RE = re.compile(r"\b(deposit|refresh|generate|create|add wallet|copy)\b", re.I)
_PRIMARY_RE = re.compile(r"\b(convert|swap|explorer|balance|wallets?|tokens|portfolio)\b", re.I)


def auto_button_style(text: str, data=None):
    """Colour a button by what it does. Red: cancel/stop and money out (withdraw,
    send). Green: confirm and money in / positive (deposit, refresh, create, copy).
    Blue: convert, explorer, balance, wallets."""
    label = text or ""
    data = data if isinstance(data, str) else ""
    if data.startswith(("cancel", "rescue:no:", "notif_stop")) or _STOP_RE.search(label):
        return "danger"
    if data.startswith(("confirm", "exec_withdraw", "rescue:ok:", "notif_resume")) or _CONFIRM_RE.search(label):
        return "success"
    if data.startswith(("menu_withdraw", "withdraw_")) or _OUT_RE.search(label):
        return "danger"
    if data.startswith(("menu_deposit", "menu_generate", "gen_", "refresh_")) or _IN_RE.search(label):
        return "success"
    if data.startswith(("menu_convert", "menu_balance", "menu_wallets", "menu_tokens")) \
            or _PRIMARY_RE.search(label):
        return "primary"
    return None


def style_keyboard(markup):
    if not _IKB_SUPPORTS_STYLE or not isinstance(markup, _BASE_MARKUP):
        return markup
    rows, changed = [], False
    for row in markup.inline_keyboard:
        new_row = []
        for btn in row:
            style = None if btn.style else auto_button_style(btn.text, btn.callback_data)
            if style:
                data = btn.to_dict()
                data["style"] = style
                btn = InlineKeyboardButton.de_json(data, None)
                changed = True
            new_row.append(btn)
        rows.append(new_row)
    return InlineKeyboardMarkup(rows) if changed else markup


def copy_address_button(address: str):
    return ikb("Copy Address", ui_name="copy", copy_text=CopyTextButton(address), style="success")


def _install_button_styles():
    from telegram import Bot

    def _wrap(method_name):
        orig = getattr(Bot, method_name)

        async def wrapper(self, *args, **kwargs):
            if "reply_markup" in kwargs:
                kwargs["reply_markup"] = style_keyboard(kwargs["reply_markup"])
            return await orig(self, *args, **kwargs)

        wrapper.__name__ = getattr(orig, "__name__", method_name)
        wrapper.__doc__ = getattr(orig, "__doc__", None)
        setattr(Bot, method_name, wrapper)

    for name in ("send_message", "send_photo", "send_document", "send_animation",
                 "edit_message_text", "edit_message_caption", "edit_message_media",
                 "edit_message_reply_markup"):
        _wrap(name)


_install_button_styles()


RICH_MESSAGES_ENABLED = os.getenv("RICH_MESSAGES", "1") != "0"
_RICH_STATE = {"retry_after": 0.0}
RICH_MESSAGE_IDS = set()     # (chat_id, message_id) sent via sendRichMessage
RICH_TEXT_TARGETS = set()    # captionless messages: caption edits become text edits
RICH_REPLACED = {}           # rich message that had to be re-sent -> replacement id
_RICH_BANNER_FILE_IDS = {}
_EMOJI_ID_FALLBACK = {eid: fb for eid, fb in UI_EMOJI.values() if eid}


def _rich_attr(value) -> str:
    return html.escape(str(value), quote=True)


def _rich_button(btn):
    label = html.escape(btn.text, quote=False)
    if getattr(btn, "icon_custom_emoji_id", None):
        fallback = _EMOJI_ID_FALLBACK.get(btn.icon_custom_emoji_id, "\U0001FA99")
        label = f"{ce(btn.icon_custom_emoji_id, fallback)} {label}"
    style = getattr(btn, "style", None) or auto_button_style(btn.text, btn.callback_data)
    style_attr = f' style="{style}"' if style else ""
    if btn.copy_text:
        return f'<tg-button type="copy_text" text="{_rich_attr(btn.copy_text.text)}"{style_attr}>{label}</tg-button>'
    if btn.url:
        style_attr = "" if style == "link" else style_attr
        return f'<tg-button type="url" url="{_rich_attr(btn.url)}"{style_attr}>{label}</tg-button>'
    if isinstance(btn.callback_data, str):
        return f'<tg-button type="callback_data" data="{_rich_attr(btn.callback_data)}"{style_attr}>{label}</tg-button>'
    return None


def _rich_paragraphs(caption_html: str) -> str:
    if re.search(r"<(blockquote|pre)\b", caption_html):
        return f"<p>{caption_html.strip().replace(chr(10), '<br>')}</p>"
    paras = [p.strip("\n") for p in re.split(r"\n{2,}", caption_html.strip()) if p.strip()]
    return "".join(f"<p>{p.replace(chr(10), '<br>')}</p>" for p in paras)


def build_rich_html(caption_html: str, keyboard, banner: bool = False, qr: bool = False):
    """Rich HTML: optional banner, text, optional collapsible QR, then in-text button rows."""
    rows = keyboard.inline_keyboard if isinstance(keyboard, _BASE_MARKUP) else (keyboard or [])
    parts = ['<img src="tg://photo?id=banner"/>'] if banner else []
    parts.append(_rich_paragraphs(caption_html))
    if qr:
        parts.append(
            '<details><summary>View QR code</summary>'
            '<img src="tg://photo?id=qr"/></details>'
        )
    for row in rows:
        buttons = [_rich_button(b) for b in row]
        if any(b is None for b in buttons):
            return None
        parts.append(f"<tg-button-row>{' '.join(buttons)}</tg-button-row>")
    return "".join(parts)


def _photo_file_ids(obj, found):
    if isinstance(obj, dict):
        if "file_id" in obj and "width" in obj:
            found.append(obj)
        for v in obj.values():
            _photo_file_ids(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _photo_file_ids(v, found)


def address_card_spec(symbol, network_name, address, balance=None, title="DEPOSIT"):
    """Card image that replaces the banner and the address details on rich screens."""
    if balance is not None:
        balance = (str(balance).replace("`", "").split() or ["0"])[0]
    return {"symbol": symbol, "network": network_name, "address": address,
            "balance": balance, "title": title,
            "caption": f"{ui('deposit')}  <b>{esc(title.title())} {esc(symbol)}</b> \u00b7 {esc(network_name)}"}


async def send_rich_screen(bot, chat_id, banner_name, caption, keyboard,
                           parse_mode: str = "HTML", qr: str = None, card: dict = None,
                           image: bytes = None):
    """Send a screen as a rich message. Returns the Message, or None so the
    caller falls back to the classic photo + inline keyboard."""
    if not RICH_MESSAGES_ENABLED or time.time() < _RICH_STATE["retry_after"]:
        return None
    caption_html = tg_html(caption) if parse_mode == "Markdown" else caption
    banner_path = get_banner_path(banner_name) if banner_name else None
    card_png = image
    if card and not card_png:
        try:
            brand = f"@{bot.username}" if getattr(bot, "username", None) else None
        except Exception:
            brand = None
        try:
            card_png = await asyncio.to_thread(
                render_address_card, card["symbol"], card["network"], card["address"],
                card.get("balance"), brand, card.get("title", "DEPOSIT")
            )
            caption_html = card["caption"]
        except Exception as e:
            logger.warning(f"Address card render failed: {e}")
    use_banner = bool(card_png) or bool(banner_path and os.path.exists(banner_path))
    qr_png = None
    if qr:
        buf = generate_qr_code(qr, box_size=QR_RICH_BOX_SIZE, strip=False)
        qr_png = buf.getvalue() if buf else None
    body = build_rich_html(caption_html, keyboard, banner=use_banner, qr=bool(qr_png))
    if body is None:
        return None

    form = aiohttp.FormData()
    media = []
    if card_png:
        media.append({"id": "banner", "media": {"type": "photo", "media": "attach://banner"}})
        form.add_field("banner", card_png, filename="card.png", content_type="image/png")
    elif use_banner:
        cached = _RICH_BANNER_FILE_IDS.get(banner_name)
        media.append({"id": "banner", "media": {"type": "photo", "media": cached or "attach://banner"}})
        if not cached:
            with open(banner_path, "rb") as f:
                form.add_field("banner", f.read(), filename=f"{banner_name}.png", content_type="image/png")
    if qr_png:
        media.append({"id": "qr", "media": {"type": "photo", "media": "attach://qr"}})
        form.add_field("qr", qr_png, filename="deposit_qr.png", content_type="image/png")
    rich = {"html": body}
    if media:
        rich["media"] = media
    form.add_field("chat_id", str(chat_id))
    form.add_field("rich_message", json.dumps(rich))
    form.add_field("protect_content", "true")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{bot.base_url}/sendRichMessage", data=form, timeout=30) as resp:
                data = await resp.json(content_type=None)
    except Exception as e:
        logger.warning(f"sendRichMessage failed, using classic layout: {e}")
        return None
    if not data.get("ok"):
        desc = str(data.get("description", data))
        logger.warning(f"sendRichMessage rejected, using classic layout: {desc}")
        if data.get("error_code") == 404 or "not found" in desc.lower():
            _RICH_STATE["retry_after"] = time.time() + 600
        return None

    result = data["result"]
    if use_banner and not card_png and not qr_png and banner_name not in _RICH_BANNER_FILE_IDS:
        photos = []
        _photo_file_ids(result, photos)
        if photos:
            _RICH_BANNER_FILE_IDS[banner_name] = max(photos, key=lambda p: p.get("width", 0))["file_id"]
    msg = Message.de_json(result, bot)
    key = (msg.chat_id, msg.message_id)
    RICH_MESSAGE_IDS.add(key)
    RICH_TEXT_TARGETS.add(key)
    _remember_owner(msg, CURRENT_USER_ID.get())
    return msg


async def replace_with_rich(query, banner_name, caption, keyboard,
                            parse_mode: str = "HTML", qr: str = None, card: dict = None,
                            image: bytes = None):
    """Send the screen as a new rich message, then remove the old one."""
    msg = await send_rich_screen(
        query.get_bot(), query.message.chat_id, banner_name, caption, keyboard, parse_mode, qr, card,
        image
    )
    if msg:
        try:
            await query.message.delete()
        except Exception:
            pass
    return msg


def _install_rich_edit_redirects():
    """Rich messages have no caption and may not accept every edit: route caption
    edits to text edits and re-send the message when Telegram refuses an edit."""
    from telegram import Bot
    from telegram.error import BadRequest

    orig_text = Bot.edit_message_text
    orig_caption = Bot.edit_message_caption
    orig_markup = Bot.edit_message_reply_markup
    orig_media = Bot.edit_message_media
    orig_delete = Bot.delete_message

    def _remap(kwargs):
        key = (kwargs.get("chat_id"), kwargs.get("message_id"))
        seen = 0
        while key in RICH_REPLACED and seen < 10:
            key = (key[0], RICH_REPLACED[key])
            seen += 1
        if seen:
            kwargs["message_id"] = key[1]
        return key

    async def _replace(self, key, send):
        try:
            await orig_delete(self, key[0], key[1])
        except Exception:
            pass
        new = await send()
        RICH_REPLACED[key] = new.message_id
        RICH_TEXT_TARGETS.add((new.chat_id, new.message_id))
        return new

    async def edit_message_text(self, *args, **kwargs):
        key = _remap(kwargs)
        if key not in RICH_MESSAGE_IDS:
            return await orig_text(self, *args, **kwargs)
        try:
            return await orig_text(self, *args, **kwargs)
        except BadRequest as e:
            if "not modified" in str(e).lower():
                raise
            text = kwargs.get("text", args[0] if args else "")
            return await _replace(self, key, lambda: self.send_message(
                chat_id=key[0], text=text, parse_mode=kwargs.get("parse_mode"),
                reply_markup=kwargs.get("reply_markup")
            ))

    async def edit_message_caption(self, *args, **kwargs):
        key = _remap(kwargs)
        if key in RICH_TEXT_TARGETS:
            return await self.edit_message_text(
                text=kwargs.get("caption") or "\u200b", chat_id=key[0], message_id=key[1],
                parse_mode=kwargs.get("parse_mode"), reply_markup=kwargs.get("reply_markup")
            )
        return await orig_caption(self, *args, **kwargs)

    async def edit_message_reply_markup(self, *args, **kwargs):
        _remap(kwargs)
        return await orig_markup(self, *args, **kwargs)

    async def edit_message_media(self, *args, **kwargs):
        key = _remap(kwargs)
        if key not in RICH_TEXT_TARGETS:
            return await orig_media(self, *args, **kwargs)
        try:
            return await orig_media(self, *args, **kwargs)
        except BadRequest as e:
            if "not modified" in str(e).lower():
                raise
            media = kwargs.get("media")
            return await _replace(self, key, lambda: self.send_photo(
                chat_id=key[0], photo=media.media, caption=media.caption,
                parse_mode=media.parse_mode, reply_markup=kwargs.get("reply_markup")
            ))

    async def delete_message(self, chat_id, message_id, *args, **kwargs):
        key = (chat_id, message_id)
        while key in RICH_REPLACED:
            key = (chat_id, RICH_REPLACED[key])
        return await orig_delete(self, key[0], key[1], *args, **kwargs)

    for fn in (edit_message_text, edit_message_caption, edit_message_reply_markup,
               edit_message_media, delete_message):
        orig = getattr(Bot, fn.__name__)
        fn.__doc__ = orig.__doc__
        setattr(Bot, fn.__name__, fn)


_install_rich_edit_redirects()


_KNOWN_BOT_CHATS = {}


def _remember_bot_chat(update: Update):
    """Keep a persistent list of groups/channels the bot is in (for /get_link)."""
    chat = update.effective_chat
    if not chat or chat.type not in ("group", "supergroup", "channel"):
        return
    try:
        member_update = update.my_chat_member
        if member_update and member_update.new_chat_member.status in ("left", "kicked"):
            _KNOWN_BOT_CHATS.pop(chat.id, None)
            db.remove_bot_chat(chat.id)
            return
        title = chat.title or str(chat.id)
        if _KNOWN_BOT_CHATS.get(chat.id) != title or member_update:
            _KNOWN_BOT_CHATS[chat.id] = title
            db.save_bot_chat(chat.id, title, chat.type)
    except Exception as e:
        logger.debug(f"Could not record chat {chat.id}: {e}")


async def track_update_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Runs before every handler: bind the acting user and reject button
    presses on menus that belong to somebody else."""
    user = update.effective_user
    CURRENT_USER_ID.set(user.id if user else None)

    _remember_bot_chat(update)

    query = update.callback_query
    if not query or not query.message or not user:
        return
    if (query.data or "").startswith("reply_to_owner:"):
        return
    owner = MESSAGE_OWNERS.get((query.message.chat_id, query.message.message_id))
    if owner is not None and owner != user.id:
        try:
            await query.answer("This menu belongs to another user.", show_alert=True)
        except Exception:
            pass
        raise ApplicationHandlerStop


def get_banner_path(name: str) -> str:
    """Get the path to a banner image."""
    return os.path.join(ASSETS_DIR, f"{name}.png")


QR_STRIP_COLOR = (23, 33, 43)  # Telegram dark theme background
QR_RICH_BOX_SIZE = int(os.getenv("QR_RICH_BOX_SIZE", "7"))  # px per QR square in rich messages


def generate_qr_code(data: str, box_size: int = 10, strip: bool = True) -> io.BytesIO:
    """Generate a QR code image for the given data and return as BytesIO."""
    if not QR_AVAILABLE:
        return None
    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=box_size,
            border=1,  # thinnest edge that still scans on Telegram's dark theme
        )
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        if strip:
            # Photo messages are stretched to the bubble width: centre the QR on a
            # wide dark strip so it renders small.
            from PIL import Image
            pad = img.height // 12
            canvas = Image.new("RGB", (int(img.width * 2.6), img.height + 2 * pad), QR_STRIP_COLOR)
            canvas.paste(img, ((canvas.width - img.width) // 2, pad))
            img = canvas
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


async def send_new_message_with_banner(query, banner_name: str, caption: str, reply_markup,
                                       parse_mode: str = "Markdown", rich: bool = False):
    """Delete current message and send new one with banner image. For changing sections or returning to main menu."""
    if rich and await replace_with_rich(query, banner_name, caption, reply_markup, parse_mode):
        return
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
    query, banner_name: str, caption: str, reply_markup, parse_mode: str = "Markdown",
    rich: bool = False
):
    """Edit the message caption and keyboard. Falls back to delete/send if needed."""
    if rich and await replace_with_rich(query, banner_name, caption, reply_markup, parse_mode):
        return
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

# SideShift.ai instant exchange (used by /convert for real on-chain swaps)
SIDESHIFT_API = "https://sideshift.ai/api/v2"
SIDESHIFT_AFFILIATE_ID = os.getenv("SIDESHIFT_AFFILIATE_ID", "")
SIDESHIFT_SECRET = os.getenv("SIDESHIFT_SECRET", "")

# LI.FI DEX/bridge aggregator (EVM chains incl. opBNB; API key optional)
LIFI_API = "https://li.quest/v1"
LIFI_API_KEY = os.getenv("LIFI_API_KEY", "")

wallet_balances_cache = {}
wallet_cache_initialized = False  # Flag to track if cache has been initialized on first run
notification_cooldowns = {}  # Track last notification time per user/token to prevent spam
last_notified_balances = {}  # Track highest balance already notified per wallet/token

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
            "https://bsc-rpc.publicnode.com"
        ],
        "chain_id": 56,
        "symbol": "BNB",
        "explorer": "https://bscscan.com",
        "type": "evm",
        "icon": "\U0001F7E1"
    },
    "OPBNB": {
        "name": "opBNB",
        "rpc": "https://opbnb-mainnet-rpc.bnbchain.org",
        "rpc_fallbacks": [
            "https://opbnb.drpc.org",
            "https://opbnb-rpc.publicnode.com",
            "https://1rpc.io/opbnb"
        ],
        "chain_id": 204,
        "symbol": "BNB",
        "explorer": "https://opbnb.bscscan.com",
        "type": "evm",
        "icon": "\U0001F7E1"
    },
    "POLYGON": {
        "name": "Polygon",
        "rpc": "https://polygon.drpc.org",
        "rpc_fallbacks": [
            "https://1rpc.io/matic",
            "https://polygon-bor-rpc.publicnode.com",
            "https://polygon.publicnode.com"
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
            "https://api.mainnet.solana.com",
            "https://solana-rpc.publicnode.com",
            "https://solana.publicnode.com"
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
    },
    "TON": {
        "name": "TON",
        "rpc": "https://toncenter.com/api/v2",
        "rpc_fallbacks": [],
        "symbol": "TON",
        "explorer": "https://tonviewer.com",
        "type": "ton",
        "icon": "\U0001F48E"
    }
}

_WEB3_CLIENTS = {}
_WEB3_PREFERRED = {}
_RPC_COOLDOWN = {}
_RPC_COOLDOWN_SECONDS = 300


def _web3_for(rpc: str) -> Web3:
    w3 = _WEB3_CLIENTS.get(rpc)
    if w3 is None:
        w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 8}))
        _WEB3_CLIENTS[rpc] = w3
    return w3


def _mark_rpc_failed(network: str, rpc: str, error: Exception):
    if rpc not in _RPC_COOLDOWN:
        logger.warning(f"RPC {rpc} failed ({error}); skipping it for {_RPC_COOLDOWN_SECONDS // 60} min")
    _RPC_COOLDOWN[rpc] = time.time() + _RPC_COOLDOWN_SECONDS
    if _WEB3_PREFERRED.get(network) == rpc:
        _WEB3_PREFERRED.pop(network, None)


def evm_rpc_order(network: str) -> list:
    """RPC URLs for a network: last known-good first, cooling-down ones last."""
    network_info = NETWORKS.get(network) or {}
    rpcs = [network_info.get("rpc")] + network_info.get("rpc_fallbacks", [])
    rpcs = [r for r in rpcs if r]
    now = time.time()
    for rpc in [r for r, until in _RPC_COOLDOWN.items() if until <= now]:
        _RPC_COOLDOWN.pop(rpc, None)
    healthy = [r for r in rpcs if r not in _RPC_COOLDOWN]
    cooling = [r for r in rpcs if r in _RPC_COOLDOWN]
    preferred = _WEB3_PREFERRED.get(network)
    if preferred in healthy:
        healthy.remove(preferred)
        healthy.insert(0, preferred)
    return healthy + cooling


def evm_call(network: str, fn):
    """Run fn(w3) against each RPC until one succeeds (blocking)."""
    last_error = None
    for rpc in evm_rpc_order(network):
        try:
            result = fn(_web3_for(rpc))
            _WEB3_PREFERRED[network] = rpc
            _RPC_COOLDOWN.pop(rpc, None)
            return result
        except Exception as e:
            last_error = e
            _mark_rpc_failed(network, rpc, e)
    raise last_error or RuntimeError(f"No RPC available for {network}")


def evm_send_raw(network: str, raw_tx, tx_hash) -> str:
    """Broadcast a signed tx, trying each RPC until one accepts it."""
    tx_hash = Web3.to_hex(tx_hash)
    last_error = None
    for rpc in evm_rpc_order(network):
        try:
            _web3_for(rpc).eth.send_raw_transaction(raw_tx)
            _WEB3_PREFERRED[network] = rpc
            return tx_hash
        except Exception as e:
            message = str(e).lower()
            if "already known" in message or "known transaction" in message:
                return tx_hash
            last_error = e
            if not isinstance(e, (requests.exceptions.RequestException, OSError)):
                raise
            _mark_rpc_failed(network, rpc, e)
    raise last_error or RuntimeError(f"No RPC available for {network}")


def get_web3_with_retry(network: str, max_retries: int = 3):
    """Get a Web3 instance for a network, preferring the last working RPC."""
    if network not in NETWORKS:
        return None
    for rpc in evm_rpc_order(network):
        w3 = _web3_for(rpc)
        try:
            w3.eth.block_number
            _WEB3_PREFERRED[network] = rpc
            _RPC_COOLDOWN.pop(rpc, None)
            return w3
        except Exception as e:
            _mark_rpc_failed(network, rpc, e)
    return _web3_for(NETWORKS[network]["rpc"])

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
            "BSC": {"native": True, "decimals": 18},
            "OPBNB": {"native": True, "decimals": 18}
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
            "OPBNB": {
                "address": "0x9e5AAC1Ba1a2e6aEd6b32689DFcF62A509Ca96f3",
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
    },
    "TON": {
        "name": "Toncoin",
        "symbol": "TON",
        "icon": "\U0001F48E",
        "native": True,
        "networks": {
            "TON": {"native": True, "decimals": 9}
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
CONVERT_PROVIDER = 16
CONVERT_DEST, CONVERT_DEST_ADDRESS = range(17, 19)

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
    "opbnb": "OPBNB", "op bnb": "OPBNB", "opbnb chain": "OPBNB", "op-bnb": "OPBNB",
    "polygon": "POLYGON", "matic": "POLYGON", "poly": "POLYGON", "pol": "POLYGON",
    "matic network": "POLYGON", "polygon mainnet": "POLYGON", "polyg": "POLYGON",
    "solana": "SOLANA", "sol": "SOLANA", "solan": "SOLANA",
    "tron": "TRON", "trx": "TRON", "trc20": "TRON", "trc-20": "TRON", "tronix": "TRON", "trn": "TRON",
    "ltc": "LTC", "litecoin": "LTC", "lite": "LTC", "lite coin": "LTC",
    "btc": "BTC", "bitcoin": "BTC", "bit coin": "BTC", "bit": "BTC",
    "ton": "TON", "toncoin": "TON", "gram": "TON", "the open network": "TON",
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
        - List of possible networks (e.g., ['ETH', 'BSC', 'OPBNB', 'POLYGON']) for EVM addresses
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
    
    if re.match(r'^(EQ|UQ|kQ|0Q)[A-Za-z0-9_-]{46}$', address):
        return 'TON'
    if re.match(r'^-?\d:[0-9a-fA-F]{64}$', address):
        return 'TON'

    if address.startswith('0x') and len(address) == 42:
        return ['ETH', 'BSC', 'OPBNB', 'POLYGON']
    
    return None

def is_valid_address(address: str, network: str) -> bool:
    """Validate if address format is correct for the given network."""
    address = address.strip()
    
    if network in ['ETH', 'BSC', 'OPBNB', 'POLYGON']:
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
    elif network == 'TON':
        return (re.match(r'^(EQ|UQ|kQ|0Q)[A-Za-z0-9_-]{46}$', address) is not None or
                re.match(r'^-?\d:[0-9a-fA-F]{64}$', address) is not None)

    return False


EXPLORER_DOMAIN_NETWORK = {
    "etherscan.io": "ETH",
    "opbnb.bscscan.com": "OPBNB",
    "opbnbscan.com": "OPBNB",
    "bscscan.com": "BSC",
    "polygonscan.com": "POLYGON",
    "solscan.io": "SOLANA",
    "tronscan.org": "TRON",
    "blockchair.com": None,  # resolved by path: /bitcoin or /litecoin
    "blockcypher.com": "BTC",
    "blockchain.com": "BTC",
    "mempool.space": "BTC",
    "tonviewer.com": "TON",
    "tonscan.org": "TON",
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

    # Some explorers (e.g. tonviewer.com) put the address directly in the path root
    if network == "TON":
        for seg in segments:
            if re.match(r'^(EQ|UQ|kQ|0Q)[A-Za-z0-9_-]{46}$', seg) or re.match(r'^-?\d:[0-9a-fA-F]{64}$', seg):
                return ("address", network, seg)

    return None


def detect_tx_hash(text: str):
    """Detect a raw transaction hash/signature.

    Returns (network_or_list, normalized_hash) or None.
    """
    t = text.strip()

    if re.fullmatch(r"0x[0-9a-fA-F]{64}", t):
        return (["ETH", "BSC", "OPBNB", "POLYGON"], t)
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
    if network == "TON":
        return f"{explorer}/transaction/{tx_hash}"
    return f"{explorer}/tx/{tx_hash}"


def address_explorer_url(network: str, address: str) -> str:
    """Build the explorer URL for an address on a given network."""
    explorer = NETWORKS.get(network, {}).get("explorer", "")
    if network == "TRON":
        return f"{explorer}/#/address/{address}"
    if network == "LTC":
        return f"{explorer}/address/{address}"
    if network == "TON":
        return f"{explorer}/{address}"
    return f"{explorer}/address/{address}"


COINGECKO_IDS = {
    "ETH": "ethereum",
    "BNB": "binancecoin",
    "MATIC": "matic-network",
    "SOL": "solana",
    "TRX": "tron",
    "LTC": "litecoin",
    "BTC": "bitcoin",
    "TON": "the-open-network",
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
        "BTC": Decimal("95000"),
        "TON": Decimal("5"),
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
    async def get_prices(assets) -> dict:
        """USD prices for several assets with one CoinGecko request (cached)."""
        now = time.time()
        prices, missing = {}, []
        for asset in set(assets):
            cached = PriceFetcher._price_cache.get(asset)
            if asset in ("USDT", "USDC"):
                prices[asset] = Decimal("1.0")
            elif cached and now - cached[1] < PriceFetcher._cache_ttl:
                prices[asset] = cached[0]
            elif COINGECKO_IDS.get(asset):
                missing.append(asset)
            else:
                prices[asset] = PriceFetcher.FALLBACK_PRICES.get(asset, Decimal("0"))
        if missing:
            ids = ",".join(COINGECKO_IDS[a] for a in missing)
            data = {}
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        "https://api.coingecko.com/api/v3/simple/price",
                        params={"ids": ids, "vs_currencies": "usd"}, timeout=aiohttp.ClientTimeout(total=6)
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
            except Exception as e:
                logger.warning(f"CoinGecko batch price error: {e}")
            for asset in missing:
                price = data.get(COINGECKO_IDS[asset], {}).get("usd") or 0
                if price > 0:
                    prices[asset] = Decimal(str(price))
                    PriceFetcher._price_cache[asset] = (prices[asset], now)
                else:
                    prices[asset] = PriceFetcher.FALLBACK_PRICES.get(asset, Decimal("0"))
        return prices

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
                notifications_enabled INTEGER NOT NULL DEFAULT 1,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("PRAGMA table_info(wallets)")
        columns = [col[1] for col in cursor.fetchall()]
        if "interface_id" not in columns:
            cursor.execute("ALTER TABLE wallets ADD COLUMN interface_id INTEGER NOT NULL DEFAULT 1")
        cursor.execute("PRAGMA table_info(user_settings)")
        user_columns = [col[1] for col in cursor.fetchall()]
        if "notifications_enabled" not in user_columns:
            cursor.execute("ALTER TABLE user_settings ADD COLUMN notifications_enabled INTEGER NOT NULL DEFAULT 1")
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
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bot_chats (
                chat_id INTEGER PRIMARY KEY,
                title TEXT,
                chat_type TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS swaps (
                shift_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                from_asset TEXT NOT NULL,
                from_network TEXT NOT NULL,
                to_asset TEXT NOT NULL,
                to_network TEXT NOT NULL,
                amount TEXT NOT NULL,
                deposit_address TEXT,
                deposit_tx_hash TEXT,
                status TEXT DEFAULT 'waiting',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._link_opbnb_wallets(cursor)
        conn.commit()
        conn.close()

    @staticmethod
    def _link_opbnb_wallets(cursor, user_id: int = None):
        """opBNB uses the same EVM key format: reuse the user's BSC (or ETH/Polygon) wallet."""
        for source in ("BSC", "ETH", "POLYGON"):
            cursor.execute(
                "INSERT OR IGNORE INTO wallets "
                "(user_id, network, address, encrypted_private_key, interface_id) "
                "SELECT user_id, 'OPBNB', address, encrypted_private_key, interface_id "
                "FROM wallets w WHERE w.network = ? AND (? IS NULL OR w.user_id = ?) "
                "AND NOT EXISTS (SELECT 1 FROM wallets o "
                "WHERE o.user_id = w.user_id AND o.network = 'OPBNB')",
                (source, user_id, user_id)
            )

    def save_swap(self, shift_id: str, user_id: int, from_asset: str,
                  from_network: str, to_asset: str, to_network: str,
                  amount: str, deposit_address: str, deposit_tx_hash: str):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO swaps (shift_id, user_id, from_asset, "
            "from_network, to_asset, to_network, amount, deposit_address, "
            "deposit_tx_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (shift_id, user_id, from_asset, from_network, to_asset,
             to_network, amount, deposit_address, deposit_tx_hash)
        )
        conn.commit()
        conn.close()

    def get_swap(self, shift_id: str) -> Optional[dict]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT shift_id, user_id, from_asset, from_network, to_asset, "
            "to_network, amount, deposit_address, deposit_tx_hash, status "
            "FROM swaps WHERE shift_id = ?",
            (shift_id,)
        )
        row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        keys = ["shift_id", "user_id", "from_asset", "from_network",
                "to_asset", "to_network", "amount", "deposit_address",
                "deposit_tx_hash", "status"]
        return dict(zip(keys, row))

    def update_swap_status(self, shift_id: str, status: str):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE swaps SET status = ? WHERE shift_id = ?",
            (status, shift_id)
        )
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
        if network in ("BSC", "ETH", "POLYGON"):
            self._link_opbnb_wallets(cursor, user_id)
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

    def get_all_wallets_with_users(self) -> list:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, network, address FROM wallets"
        )
        rows = cursor.fetchall()
        conn.close()
        return [{"user_id": row[0], "network": row[1], "address": row[2]} for row in rows]

    def get_user_notification_setting(self, user_id: int) -> bool:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO user_settings (user_id, notifications_enabled) VALUES (?, 1)",
            (user_id,)
        )
        cursor.execute(
            "SELECT notifications_enabled FROM user_settings WHERE user_id = ?",
            (user_id,)
        )
        row = cursor.fetchone()
        conn.commit()
        conn.close()
        return bool(row[0]) if row else True

    def set_user_notification_setting(self, user_id: int, enabled: bool):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO user_settings (user_id, current_interface, notifications_enabled) "
            "VALUES (?, COALESCE((SELECT current_interface FROM user_settings WHERE user_id = ?), 1), ?)",
            (user_id, user_id, 1 if enabled else 0)
        )
        conn.commit()
        conn.close()

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

    def save_bot_chat(self, chat_id: int, title: str, chat_type: str):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT OR REPLACE INTO bot_chats (chat_id, title, chat_type, updated_at) "
            "VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (chat_id, title, chat_type)
        )
        conn.commit()
        conn.close()

    def remove_bot_chat(self, chat_id: int):
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM bot_chats WHERE chat_id = ?", (chat_id,))
        conn.commit()
        conn.close()

    def get_bot_chats(self) -> list:
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT chat_id, title, chat_type FROM bot_chats ORDER BY title"
        ).fetchall()
        conn.close()
        return [{"chat_id": r[0], "title": r[1], "chat_type": r[2]} for r in rows]

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


TONCENTER_API_KEY = os.getenv("TONCENTER_API_KEY", "")
TON_FEE_RESERVE = Decimal("0.01")
_toncenter_lock = asyncio.Lock()
_toncenter_last_call = 0.0


async def toncenter_call(method: str, params: dict = None, payload: dict = None):
    """Call the Toncenter v2 API, spacing requests to stay under its rate limit."""
    global _toncenter_last_call
    url = f"{NETWORKS['TON']['rpc']}/{method}"
    headers = {"X-API-Key": TONCENTER_API_KEY} if TONCENTER_API_KEY else {}
    min_gap = 0.15 if TONCENTER_API_KEY else 1.1
    last_error = "unknown error"
    for attempt in range(4):
        async with _toncenter_lock:
            wait = min_gap - (time.monotonic() - _toncenter_last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            _toncenter_last_call = time.monotonic()
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                if payload is None:
                    req = session.get(url, params=params, headers=headers)
                else:
                    req = session.post(url, json=payload, headers=headers)
                async with req as resp:
                    status = resp.status
                    data = await resp.json(content_type=None)
            if isinstance(data, dict) and data.get("ok"):
                return data.get("result")
            last_error = (data or {}).get("error") if isinstance(data, dict) else None
            last_error = last_error or f"HTTP {status}"
            if status == 429 or (isinstance(data, dict) and data.get("code") == 429):
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            break
        except Exception as e:
            last_error = str(e)
            await asyncio.sleep(1)
    raise RuntimeError(f"Toncenter {method} failed: {last_error}")


def ton_raw_address(address: str) -> str:
    """Normalize any TON address form to lowercase raw ``wc:hex``."""
    from tonsdk.utils import Address
    return Address(address).to_string(False).lower()


def ton_wallet_from_mnemonic(mnemonic: str):
    from tonsdk.contract.wallet import Wallets, WalletVersionEnum
    _m, _pub, _priv, wallet = Wallets.from_mnemonics(
        mnemonic.split(), WalletVersionEnum.v4r2, 0
    )
    return wallet


def ton_hash_to_hex(value: str) -> str:
    """Toncenter returns hashes as base64; explorers use hex."""
    if not value:
        return ""
    if re.fullmatch(r"[0-9a-fA-F]{64}", value):
        return value.lower()
    padded = value + "=" * (-len(value) % 4)
    try:
        return base64.b64decode(padded).hex()
    except Exception:
        return base64.urlsafe_b64decode(padded).hex()


class WalletGenerator:
    @staticmethod
    def _uncompressed_public_key(private_key_hex: str) -> bytes:
        from ecdsa import SigningKey, SECP256k1
        sk = SigningKey.from_string(bytes.fromhex(private_key_hex), curve=SECP256k1)
        vk = sk.get_verifying_key()
        return b'\x04' + vk.to_string()

    @staticmethod
    def _p2pkh_address(public_key: bytes, version_byte: bytes) -> str:
        import hashlib
        from Crypto.Hash import RIPEMD160
        sha256_hash = hashlib.sha256(public_key).digest()
        ripemd160 = RIPEMD160.new()
        ripemd160.update(sha256_hash)
        pubkey_hash = ripemd160.digest()
        versioned_payload = version_byte + pubkey_hash
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
        return address

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
        import secrets
        private_key = secrets.token_bytes(32)
        public_key = WalletGenerator._uncompressed_public_key(private_key.hex())
        address = WalletGenerator._p2pkh_address(public_key, b'\x30')
        return address, private_key.hex()

    @staticmethod
    def generate_btc_wallet() -> tuple:
        import secrets
        private_key = secrets.token_bytes(32)
        public_key = WalletGenerator._uncompressed_public_key(private_key.hex())
        address = WalletGenerator._p2pkh_address(public_key, b'\x00')
        return address, private_key.hex()

    @staticmethod
    def generate_ton_wallet() -> tuple:
        from tonsdk.crypto import mnemonic_new
        mnemonic = " ".join(mnemonic_new())
        wallet = ton_wallet_from_mnemonic(mnemonic)
        address = wallet.address.to_string(True, True, False)
        return address, mnemonic

    @staticmethod
    def ltc_address_from_private_key(private_key_hex: str) -> str:
        public_key = WalletGenerator._uncompressed_public_key(private_key_hex)
        return WalletGenerator._p2pkh_address(public_key, b'\x30')

    @staticmethod
    def btc_address_from_private_key(private_key_hex: str) -> str:
        public_key = WalletGenerator._uncompressed_public_key(private_key_hex)
        return WalletGenerator._p2pkh_address(public_key, b'\x00')

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
        elif network_info["type"] == "btc":
            return WalletGenerator.generate_btc_wallet()
        elif network_info["type"] == "ton":
            return WalletGenerator.generate_ton_wallet()
        else:
            raise ValueError(f"Unknown network type: {network_info['type']}")


_TOKEN_META_CACHE = {}


SOL_TX_FEE_LAMPORTS = 5000
SOL_RENT_EXEMPT_LAMPORTS = 890880
SOL_TOKEN_ACCOUNT_RENT_LAMPORTS = 2039280
SPL_TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
SPL_TOKEN_2022_PROGRAM_ID = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
SPL_ASSOCIATED_TOKEN_PROGRAM_ID = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
_SOL_TOKEN_META_CACHE = {}


def _solana_rpcs() -> list:
    rpcs = [os.getenv("SOLANA_RPC_URL", ""), NETWORKS["SOLANA"]["rpc"]]
    rpcs += NETWORKS["SOLANA"].get("rpc_fallbacks", [])
    seen = []
    for rpc in rpcs:
        if rpc and rpc not in seen:
            seen.append(rpc)
    return seen


async def solana_rpc(method: str, params: list) -> dict:
    """Call Solana JSON-RPC, retrying rate limits and failing over to the next RPC.

    Returns the raw JSON-RPC response (with "result" or "error").
    """
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    first_rpc_error = None
    transport_error = None
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for rpc in _solana_rpcs():
            for attempt in range(3):
                try:
                    async with session.post(rpc, json=payload) as resp:
                        status = resp.status
                        try:
                            data = await resp.json(content_type=None)
                        except Exception:
                            data = None
                except Exception as e:
                    transport_error = {"error": {"message": f"{type(e).__name__} from {rpc}: {e}"}}
                    break
                err = data.get("error") if isinstance(data, dict) else None
                code = err.get("code") if isinstance(err, dict) else None
                if status == 429 or code == 429:
                    transport_error = {"error": {"message": f"Rate limited by {rpc}"}}
                    await asyncio.sleep(0.7 * (attempt + 1))
                    continue
                if not isinstance(data, dict) or status >= 500:
                    transport_error = {"error": {"message": f"HTTP {status} from {rpc}"}}
                    break
                if "result" in data:
                    return data
                # Simulation/preflight errors are deterministic; other RPCs would say the same.
                if code == -32002:
                    return data
                if first_rpc_error is None and status < 400:
                    first_rpc_error = data
                break
    return first_rpc_error or transport_error or {"error": {"message": "No Solana RPC available"}}


def solana_error_text(data) -> str:
    """Turn a Solana RPC error (including simulation logs) into a readable message."""
    err = data.get("error") if isinstance(data, dict) else data
    if not isinstance(err, dict):
        return str(err)
    msg = str(err.get("message", err))
    details = err.get("data") or {}
    logs = details.get("logs") or [] if isinstance(details, dict) else []
    inner = details.get("err") if isinstance(details, dict) else None
    haystack = (msg + " " + json.dumps(inner) + " " + " ".join(logs)).lower()
    if "insufficientfundsforrent" in haystack or "insufficient funds for rent" in haystack:
        return (
            "Not enough SOL left for Solana's rent minimum. A Solana wallet must keep at "
            f"least {Decimal(SOL_RENT_EXEMPT_LAMPORTS) / Decimal(10 ** 9)} SOL, or be emptied fully. "
            "Send a smaller amount, or use 'all'."
        )
    if "accountnotfound" in haystack or "no record of a prior credit" in haystack:
        return "This wallet has no SOL to pay the network fee. Deposit a little SOL first."
    if "insufficient lamports" in haystack:
        return "Not enough SOL to cover the amount plus network fee / token account rent."
    if "insufficient funds" in haystack:
        return "Not enough token balance for this amount."
    if "blockhash not found" in haystack:
        return "Solana network is busy (blockhash expired). Please try again."
    tail = next((l for l in reversed(logs) if "failed" in l.lower() or "error" in l.lower()), "")
    return f"{msg}\n{tail}".strip() if tail else msg


async def solana_token_accounts(owner: str, mint: str = None) -> dict:
    """List SPL token accounts (Token and Token-2022) owned by ``owner``.

    Returns {"accounts": [...]} or {"error": str}. Each account has
    pubkey, mint, amount (raw int), decimals and program.
    """
    filters = [{"mint": mint}] if mint else [
        {"programId": SPL_TOKEN_PROGRAM_ID},
        {"programId": SPL_TOKEN_2022_PROGRAM_ID},
    ]
    accounts = []
    for flt in filters:
        data = await solana_rpc(
            "getTokenAccountsByOwner",
            [owner, flt, {"encoding": "jsonParsed", "commitment": "confirmed"}],
        )
        if "result" not in data:
            return {"error": solana_error_text(data)}
        for item in data["result"].get("value", []):
            try:
                info = item["account"]["data"]["parsed"]["info"]
                accounts.append({
                    "pubkey": item["pubkey"],
                    "mint": info["mint"],
                    "amount": int(info["tokenAmount"]["amount"]),
                    "decimals": int(info["tokenAmount"]["decimals"]),
                    "program": item["account"].get("owner", SPL_TOKEN_PROGRAM_ID),
                })
            except (KeyError, TypeError, ValueError):
                continue
    return {"accounts": accounts}


def solana_known_symbol(mint: str) -> Optional[str]:
    for symbol, info in TOKENS.items():
        if info.get("networks", {}).get("SOLANA", {}).get("address") == mint:
            return symbol
    return None


async def solana_tokens_meta(mints: list) -> dict:
    """Symbol and USD price for SPL mints (one batched Jupiter call, cached 10 min)."""
    now = time.time()
    result = {}
    missing = []
    for mint in mints:
        cached = _SOL_TOKEN_META_CACHE.get(mint)
        if cached and now - cached["ts"] < 600:
            result[mint] = cached
        else:
            result[mint] = {
                "symbol": solana_known_symbol(mint) or f"{mint[:4]}…{mint[-4:]}",
                "price": None, "ts": now,
            }
            missing.append(mint)
    if missing:
        try:
            timeout = aiohttp.ClientTimeout(total=8)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    "https://lite-api.jup.ag/tokens/v2/search", params={"query": ",".join(missing)}
                ) as resp:
                    data = await resp.json(content_type=None)
            for item in data if isinstance(data, list) else []:
                meta = result.get(item.get("id"))
                if not meta:
                    continue
                if not solana_known_symbol(item["id"]) and item.get("symbol"):
                    meta["symbol"] = item["symbol"]
                if item.get("usdPrice") is not None:
                    meta["price"] = Decimal(str(item["usdPrice"]))
            for mint in missing:
                _SOL_TOKEN_META_CACHE[mint] = result[mint]
        except Exception as e:
            logger.debug(f"Jupiter token lookup failed: {e}")
    return result

def _lamports_to_sol(lamports: int) -> str:
    return f"{(Decimal(lamports) / Decimal(10 ** 9)).normalize():f}"


async def solana_send_transaction(tx) -> dict:
    tx_base64 = base64.b64encode(bytes(tx)).decode()
    data = await solana_rpc(
        "sendTransaction",
        [tx_base64, {"encoding": "base64", "preflightCommitment": "confirmed", "maxRetries": 5}],
    )
    if "result" in data:
        return {
            "success": True,
            "tx_hash": data["result"],
            "explorer_url": f"{NETWORKS['SOLANA']['explorer']}/tx/{data['result']}",
        }
    logger.error(f"Solana sendTransaction failed: {json.dumps(data.get('error', data), default=str)[:3000]}")
    return {"success": False, "error": f"Solana: {solana_error_text(data)}"}


async def solana_extra_holdings(address: str) -> list:
    """Non-zero SPL holdings other than the bot's built-in tokens (USDT/USDC)."""
    held = await solana_token_accounts(address)
    if "error" in held:
        return []
    totals = {}
    for acct in held["accounts"]:
        if acct["amount"] <= 0 or solana_known_symbol(acct["mint"]):
            continue
        raw, dec = totals.get(acct["mint"], (0, acct["decimals"]))
        totals[acct["mint"]] = (raw + acct["amount"], dec)
    # Cap lookups so wallets holding thousands of spam tokens stay fast.
    mints = list(totals.items())[:25]
    metas = await solana_tokens_meta([mint for mint, _ in mints])
    holdings = []
    for mint, (raw, dec) in mints:
        meta = metas[mint]
        balance = Decimal(raw) / Decimal(10 ** dec)
        holdings.append({
            "mint": mint,
            "symbol": meta["symbol"],
            "balance": balance,
            "usd_value": balance * meta["price"] if meta["price"] is not None else Decimal("0"),
        })
    holdings.sort(key=lambda h: h["usd_value"], reverse=True)
    return holdings


async def solana_extra_tokens_md(address: str) -> str:
    try:
        holdings = await solana_extra_holdings(address)
    except Exception as e:
        logger.warning(f"Solana token list failed for {address}: {e}")
        return ""
    symbol_safe = lambda v: re.sub(r"[`*_\[\]]", "", str(v))
    return "".join(
        f"\U0001FA99 {symbol_safe(h['symbol'])}: `{h['balance'].normalize():f}`\n" for h in holdings
    )


class BalanceChecker:
    @staticmethod
    async def get_evm_balance(
        network: str,
        address: str,
        token_address: str = None
    ) -> dict:
        network_info = NETWORKS[network]
        owner = Web3.to_checksum_address(address)

        if token_address:
            token = Web3.to_checksum_address(token_address)
            cache_key = (network, token)

            def fetch(w3):
                contract = w3.eth.contract(address=token, abi=ERC20_ABI)
                raw = contract.functions.balanceOf(owner).call()
                meta = _TOKEN_META_CACHE.get(cache_key)
                if meta is None:
                    meta = (
                        contract.functions.decimals().call(),
                        contract.functions.symbol().call(),
                    )
                    _TOKEN_META_CACHE[cache_key] = meta
                return raw, meta

            try:
                raw, (decimals, symbol) = await asyncio.to_thread(evm_call, network, fetch)
                return {
                    "balance": str(Decimal(raw) / Decimal(10 ** decimals)),
                    "symbol": symbol,
                    "raw_balance": raw
                }
            except Exception as e:
                logger.error(f"Error getting token balance on {network}: {e}")
                return {"balance": "0", "symbol": "TOKEN", "error": str(e)}

        try:
            raw = await asyncio.to_thread(
                evm_call, network, lambda w3: w3.eth.get_balance(owner)
            )
        except Exception as e:
            logger.error(f"Error getting {network} native balance: {e}")
            return {"balance": "0", "symbol": network_info["symbol"], "error": str(e)}
        return {
            "balance": str(Web3.from_wei(raw, "ether")),
            "symbol": network_info["symbol"],
            "raw_balance": raw
        }

    @staticmethod
    async def get_solana_balance(address: str) -> dict:
        data = await solana_rpc("getBalance", [address, {"commitment": "confirmed"}])
        if "result" in data:
            lamports = data["result"]["value"]
            return {
                "balance": str(Decimal(lamports) / Decimal(10 ** 9)),
                "symbol": "SOL",
                "raw_balance": lamports
            }
        logger.warning(f"Solana balance lookup failed for {address}: {solana_error_text(data)}")
        return {"balance": "0", "symbol": "SOL", "error": solana_error_text(data)}

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
        endpoints = [
            f"https://litecoinspace.org/api/address/{address}",
            f"https://api.blockcypher.com/v1/ltc/main/addrs/{address}/balance",
        ]
        try:
            async with aiohttp.ClientSession() as session:
                for url in endpoints:
                    try:
                        async with session.get(
                            url,
                            timeout=aiohttp.ClientTimeout(total=15)
                        ) as resp:
                            if resp.status != 200:
                                continue
                            data = await resp.json()
                            if isinstance(data, dict) and "chain_stats" in data:
                                chain = data.get("chain_stats", {})
                                balance_sat = chain.get("funded_txo_sum", 0) - chain.get("spent_txo_sum", 0)
                                return {
                                    "balance": str(Decimal(balance_sat) / Decimal(10 ** 8)),
                                    "symbol": "LTC",
                                    "raw_balance": balance_sat
                                }
                            if isinstance(data, dict) and "balance" in data:
                                balance_satoshi = data.get("balance", 0)
                                return {
                                    "balance": str(Decimal(balance_satoshi) / Decimal(10 ** 8)),
                                    "symbol": "LTC",
                                    "raw_balance": balance_satoshi
                                }
                    except Exception as e:
                        logger.warning(f"LTC balance endpoint {url} failed: {e}")
                        continue
            return {"balance": "0", "symbol": "LTC", "error": "All LTC balance endpoints failed"}
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
    async def get_ton_balance(address: str) -> dict:
        try:
            result = await toncenter_call("getAddressBalance", {"address": address})
            balance = int(result or 0)
            return {
                "balance": str(Decimal(balance) / Decimal(10 ** 9)),
                "symbol": "TON",
                "raw_balance": balance
            }
        except Exception as e:
            logger.error(f"Error getting TON balance: {e}")
            return {"balance": "0", "symbol": "TON", "error": str(e)}

    @staticmethod
    async def get_solana_token_balance(address: str, token_mint: str) -> dict:
        symbol = solana_known_symbol(token_mint) or "TOKEN"
        try:
            result = await solana_token_accounts(address, token_mint)
            if "error" in result:
                logger.warning(f"Solana {symbol} balance lookup failed for {address}: {result['error']}")
                return {"balance": "0", "symbol": symbol, "error": result["error"]}
            accounts = result["accounts"]
            raw = sum(a["amount"] for a in accounts)
            decimals = accounts[0]["decimals"] if accounts else (
                TOKENS.get(symbol, {}).get("networks", {}).get("SOLANA", {}).get("decimals", 6)
            )
            return {
                "balance": str(Decimal(raw) / Decimal(10 ** decimals)),
                "symbol": symbol,
                "raw_balance": raw
            }
        except Exception as e:
            logger.error(f"Error getting Solana token balance: {e}")
            return {"balance": "0", "symbol": symbol, "error": str(e)}

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
            elif network_info["type"] == "ton":
                return await BalanceChecker.get_ton_balance(address)
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
        data = await solana_rpc(payload["method"], payload["params"])

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
            if program in ("spl-token", "spl-token-2022") and itype in ("transfer", "transferChecked"):
                sender = info.get("authority") or info.get("source")
                receiver = info.get("destination")
                dest_owner = next(
                    (b.get("owner") for b in result.get("meta", {}).get("postTokenBalances", [])
                     if b.get("owner") and message.get("accountKeys", [{}])[b.get("accountIndex", 0)].get("pubkey") == receiver),
                    None
                ) if isinstance(message.get("accountKeys", [None])[0], dict) else None
                receiver = dest_owner or receiver
                mint = info.get("mint") or next(
                    (b.get("mint") for b in result.get("meta", {}).get("postTokenBalances", [])), None
                )
                token_amount = info.get("tokenAmount")
                if token_amount:
                    amount = token_amount.get("uiAmountString") or str(
                        token_amount.get("uiAmount", "")
                    )
                elif "amount" in info:
                    decimals = next(
                        (b.get("uiTokenAmount", {}).get("decimals") for b in result.get("meta", {}).get("postTokenBalances", [])
                         if b.get("mint") == mint), 6
                    )
                    amount = str(Decimal(info["amount"]) / Decimal(10 ** int(decimals)))
                asset = (solana_known_symbol(mint) if mint else None) or "SPL Token"
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
        import aiohttp
        url = f"https://litecoinspace.org/api/tx/{tx_hash}"
        timeout = aiohttp.ClientTimeout(total=15)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return {"error": "not_found"}
                    tx_data = await resp.json()
        except Exception as e:
            logger.error(f"Error getting LTC tx: {e}")
            return {"error": "not_found"}

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
        if vout:
            primary = max(vout, key=lambda o: o.get("value", 0))
            receiver = primary.get("scriptpubkey_address")
            amount = str(Decimal(primary.get("value", 0)) / Decimal(10 ** 8))

        status = tx_data.get("status", {})
        timestamp = status.get("block_time")

        return {
            "network": "LTC",
            "hash": tx_hash,
            "from": sender,
            "to": receiver,
            "amount": amount,
            "asset": "LTC",
            "timestamp": timestamp,
            "status": "Success" if status.get("confirmed") else "Pending",
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

    @staticmethod
    async def find_deposit_tx(network: str, address: str, token: str = None) -> dict:
        """Try to find the most recent incoming transaction for an address.

        Returns a dict with ``tx_hash``, ``explorer_url``, ``amount`` (Decimal) and
        ``timestamp`` (unix seconds, None if unconfirmed) keys, or an empty dict
        when no transaction can be located.
        """
        network_info = NETWORKS.get(network)
        if not network_info or not address:
            return {}

        token_address = None
        token_decimals = 18
        if token and token in TOKENS and network in TOKENS[token].get("networks", {}):
            token_data = TOKENS[token]["networks"][network]
            if not token_data.get("native"):
                token_address = token_data.get("address")
                token_decimals = token_data.get("decimals", 18)

        try:
            net_type = network_info["type"]
            if net_type == "evm":
                return await TransactionChecker._find_evm_deposit_tx(
                    network, address, token_address, token_decimals
                )
            if net_type == "solana":
                return await TransactionChecker._find_solana_deposit_tx(address, token_address)
            if net_type == "tron":
                return await TransactionChecker._find_tron_deposit_tx(address, token_address)
            if net_type == "btc":
                return await TransactionChecker._find_btc_deposit_tx(address)
            if net_type == "ltc":
                return await TransactionChecker._find_ltc_deposit_tx(address)
            if net_type == "ton":
                return await TransactionChecker._find_ton_deposit_tx(address)
        except Exception as e:
            logger.error(f"Error finding deposit tx for {network} {address}: {e}")

        return {}

    @staticmethod
    async def _find_evm_deposit_tx(
        network: str, address: str, token_address: str = None, token_decimals: int = 18
    ) -> dict:
        def _hex(h) -> str:
            h = h.hex() if hasattr(h, "hex") else str(h)
            return h if h.startswith("0x") else "0x" + h

        def _token(w3):
            latest = w3.eth.block_number
            addr_topic = "0x" + address[2:].lower().rjust(64, "0")
            logs = w3.eth.get_logs({
                "fromBlock": max(0, latest - 200),
                "toBlock": "latest",
                "address": Web3.to_checksum_address(token_address),
                "topics": [TransactionChecker.TRANSFER_TOPIC, None, [addr_topic]],
            })
            def _value(lg):
                data = lg.data
                return int.from_bytes(bytes(data), "big") if not isinstance(data, str) else int(data, 16)

            # Ignore zero-value "address poisoning" transfers
            logs = [lg for lg in logs if _value(lg) > 0]
            if not logs:
                return {}
            log = max(logs, key=lambda lg: (lg.blockNumber, lg.logIndex))
            tx_hash = _hex(log.transactionHash)
            block = w3.provider.make_request("eth_getBlockByNumber", [hex(log.blockNumber), False])
            return {
                "tx_hash": tx_hash,
                "explorer_url": tx_explorer_url(network, tx_hash),
                "amount": Decimal(_value(log)) / Decimal(10 ** token_decimals),
                "timestamp": int((block.get("result") or {}).get("timestamp", "0x0"), 16) or None,
            }

        def _native(w3):
            # Raw JSON-RPC: web3's block formatter rejects POA chains (BSC, opBNB, Polygon)
            latest = w3.eth.block_number
            target = address.lower()
            for block_num in range(latest, max(-1, latest - 50), -1):
                resp = w3.provider.make_request("eth_getBlockByNumber", [hex(block_num), True])
                if resp.get("error"):
                    raise RuntimeError(resp["error"])
                block = resp.get("result") or {}
                for tx in block.get("transactions") or []:
                    value = int(tx.get("value") or "0x0", 16)
                    if value and (tx.get("to") or "").lower() == target:
                        tx_hash = _hex(tx["hash"])
                        return {
                            "tx_hash": tx_hash,
                            "explorer_url": tx_explorer_url(network, tx_hash),
                            "amount": Decimal(value) / Decimal(10 ** 18),
                            "timestamp": int(block.get("timestamp", "0x0"), 16) or None,
                        }
            return {}

        def _search():
            # Log/block scans hit stricter RPC limits than balance reads, so a failure
            # here must not put the RPC into the shared failover cooldown.
            last_error = None
            for rpc in evm_rpc_order(network):
                try:
                    return (_token if token_address else _native)(_web3_for(rpc))
                except Exception as e:
                    last_error = e
            raise last_error or RuntimeError(f"No RPC available for {network}")

        try:
            return await asyncio.to_thread(_search)
        except Exception as e:
            logger.error(f"Error finding EVM deposit tx on {network}: {e}")
            return {}

    @staticmethod
    async def _find_solana_deposit_tx(address: str, token_mint: str = None) -> dict:
        async def _rpc(method: str, params: list):
            return (await solana_rpc(method, params)).get("result")

        search_addresses = [address]
        if token_mint:
            held = await solana_token_accounts(address, token_mint)
            if held.get("accounts"):
                search_addresses = [a["pubkey"] for a in held["accounts"]]

        candidates = []
        for search_address in search_addresses[:3]:
            sigs = await _rpc(
                "getSignaturesForAddress",
                [search_address, {"limit": 10, "commitment": "confirmed"}],
            ) or []
            candidates.extend(sigs)
        candidates.sort(key=lambda x: x.get("blockTime") or 0, reverse=True)

        seen = set()
        for sig_info in candidates:
            signature = sig_info.get("signature")
            if not signature or signature in seen or sig_info.get("err"):
                continue
            seen.add(signature)
            tx_result = await _rpc(
                "getTransaction",
                [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}],
            )
            if not tx_result:
                continue

            meta = tx_result.get("meta") or {}
            message = tx_result.get("transaction", {}).get("message", {})
            account_keys = [
                k.get("pubkey") if isinstance(k, dict) else k
                for k in message.get("accountKeys", [])
            ]

            if token_mint:
                def _owned(balances):
                    total = Decimal("0")
                    for b in balances or []:
                        if b.get("mint") == token_mint and b.get("owner") == address:
                            total += Decimal(str(b.get("uiTokenAmount", {}).get("uiAmountString") or "0"))
                    return total
                pre_amount = _owned(meta.get("preTokenBalances"))
                post_amount = _owned(meta.get("postTokenBalances"))
                if post_amount > pre_amount:
                    return {
                        "tx_hash": signature,
                        "explorer_url": tx_explorer_url("SOLANA", signature),
                        "amount": post_amount - pre_amount,
                        "timestamp": sig_info.get("blockTime"),
                    }
            else:
                if address not in account_keys:
                    continue
                idx = account_keys.index(address)
                pre_balances = meta.get("preBalances", [])
                post_balances = meta.get("postBalances", [])
                if idx < len(pre_balances) and idx < len(post_balances):
                    pre_sol = Decimal(pre_balances[idx]) / Decimal(10 ** 9)
                    post_sol = Decimal(post_balances[idx]) / Decimal(10 ** 9)
                    if post_sol > pre_sol:
                        return {
                            "tx_hash": signature,
                            "explorer_url": tx_explorer_url("SOLANA", signature),
                            "amount": post_sol - pre_sol,
                            "timestamp": sig_info.get("blockTime"),
                        }

        return {}

        for sig_info in signatures:
            signature = sig_info.get("signature")
            if not signature:
                continue
            tx_result = await _rpc(
                "getTransaction",
                [signature, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"}],
            )
            if not tx_result:
                continue

            meta = tx_result.get("meta", {})
            message = tx_result.get("transaction", {}).get("message", {})
            account_keys = message.get("accountKeys", [])

            try:
                idx = account_keys.index(search_address)
            except ValueError:
                continue

            if token_mint:
                pre = {
                    b.get("accountIndex"): b
                    for b in meta.get("preTokenBalances", [])
                    if b.get("mint") == token_mint
                }
                post = {
                    b.get("accountIndex"): b
                    for b in meta.get("postTokenBalances", [])
                    if b.get("mint") == token_mint
                }
                pre_amount = Decimal(str(pre.get(idx, {}).get("uiTokenAmount", {}).get("uiAmount", 0) or 0))
                post_amount = Decimal(str(post.get(idx, {}).get("uiTokenAmount", {}).get("uiAmount", 0) or 0))
                if post_amount > pre_amount:
                    return {
                        "tx_hash": signature,
                        "explorer_url": tx_explorer_url("SOLANA", signature),
                        "amount": post_amount - pre_amount,
                        "timestamp": sig_info.get("blockTime"),
                    }
            else:
                pre_balances = meta.get("preBalances", [])
                post_balances = meta.get("postBalances", [])
                if idx < len(pre_balances) and idx < len(post_balances):
                    pre_sol = Decimal(pre_balances[idx]) / Decimal(10 ** 9)
                    post_sol = Decimal(post_balances[idx]) / Decimal(10 ** 9)
                    if post_sol > pre_sol:
                        return {
                            "tx_hash": signature,
                            "explorer_url": tx_explorer_url("SOLANA", signature),
                            "amount": post_sol - pre_sol,
                            "timestamp": sig_info.get("blockTime"),
                        }

        return {}

    @staticmethod
    async def _find_tron_deposit_tx(address: str, token_address: str = None) -> dict:
        base = NETWORKS["TRON"]["rpc"]
        headers = {}
        if TRONGRID_API_KEY:
            headers["TRON-PRO-API-KEY"] = TRONGRID_API_KEY

        async def _get(endpoint: str):
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for attempt in range(3):
                    try:
                        async with session.get(f"{base}{endpoint}", headers=headers) as resp:
                            if resp.status == 200:
                                return await resp.json()
                            if resp.status != 429:
                                break
                    except Exception as e:
                        logger.error(f"Tron tx search error: {e}")
                        break
                    await asyncio.sleep(1.5 * (attempt + 1))
            return {}

        if token_address:
            data = await _get(f"/v1/accounts/{address}/transactions/trc20?limit=20&order=blockNum,desc")
            for tr in data.get("data", []):
                contract = tr.get("token_address") or (tr.get("token_info") or {}).get("address")
                if tr.get("to") == address and contract == token_address and int(tr.get("value") or 0) > 0:
                    tx_id = tr.get("transaction_id")
                    if tx_id:
                        decimals = int((tr.get("token_info") or {}).get("decimals") or 6)
                        return {
                            "tx_hash": tx_id,
                            "explorer_url": tx_explorer_url("TRON", tx_id),
                            "amount": Decimal(str(tr.get("value") or 0)) / Decimal(10 ** decimals),
                            "timestamp": int(tr.get("block_timestamp") or 0) // 1000 or None,
                        }
        else:
            data = await _get(f"/v1/accounts/{address}/transactions?limit=20&order=blockNum,desc")
            for tx in data.get("data", []):
                raw = tx.get("raw_data", {})
                contract = raw.get("contract", [{}])[0]
                value = contract.get("parameter", {}).get("value", {})
                to_hex = value.get("to_address")
                amount = value.get("amount", 0)
                if to_hex and amount and int(amount) >= 10000:  # skip dust spam
                    try:
                        from tronpy.keys import to_base58check_address
                        to_addr = to_base58check_address(to_hex)
                        if to_addr == address:
                            tx_id = tx.get("txID")
                            if tx_id:
                                return {
                                    "tx_hash": tx_id,
                                    "explorer_url": tx_explorer_url("TRON", tx_id),
                                    "amount": Decimal(amount) / Decimal(10 ** 6),
                                    "timestamp": int(tx.get("block_timestamp") or 0) // 1000 or None,
                                }
                    except Exception:
                        continue

        return {}

    @staticmethod
    def _esplora_deposit(network: str, address: str, txs: list) -> dict:
        for tx in txs:
            if any((vin.get("prevout") or {}).get("scriptpubkey_address") == address
                   for vin in tx.get("vin", [])):
                continue  # our own send (change output)
            sats = sum(out.get("value", 0) for out in tx.get("vout", [])
                       if out.get("scriptpubkey_address") == address)
            if sats > 0:
                tx_hash = tx.get("txid")
                status = tx.get("status") or {}
                return {
                    "tx_hash": tx_hash,
                    "explorer_url": tx_explorer_url(network, tx_hash),
                    "amount": Decimal(sats) / Decimal(10 ** 8),
                    "timestamp": status.get("block_time") if status.get("confirmed") else None,
                }
        return {}

    @staticmethod
    async def _find_btc_deposit_tx(address: str) -> dict:
        url = f"https://mempool.space/api/address/{address}/txs"
        timeout = aiohttp.ClientTimeout(total=10)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        return TransactionChecker._esplora_deposit("BTC", address, await resp.json())
        except Exception as e:
            logger.error(f"BTC tx search error: {e}")
        return {}

    @staticmethod
    async def _find_ltc_deposit_tx(address: str) -> dict:
        endpoints = [
            f"https://litecoinspace.org/api/address/{address}/txs",
            f"https://api.blockcypher.com/v1/ltc/main/addrs/{address}/full?limit=5",
        ]
        timeout = aiohttp.ClientTimeout(total=15)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for url in endpoints:
                    try:
                        async with session.get(url) as resp:
                            if resp.status != 200:
                                continue
                            data = await resp.json()
                        if isinstance(data, list):
                            return TransactionChecker._esplora_deposit("LTC", address, data)
                        for tx in (data or {}).get("txs", []):
                            if any(address in (i.get("addresses") or []) for i in tx.get("inputs", [])):
                                continue
                            sats = sum(o.get("value", 0) for o in tx.get("outputs", [])
                                       if address in (o.get("addresses") or []))
                            if sats > 0:
                                tx_hash = tx.get("hash")
                                confirmed = tx.get("confirmed")
                                ts = None
                                if confirmed:
                                    try:
                                        ts = int(datetime.fromisoformat(confirmed.replace("Z", "+00:00")).timestamp())
                                    except Exception:
                                        ts = None
                                return {
                                    "tx_hash": tx_hash,
                                    "explorer_url": tx_explorer_url("LTC", tx_hash),
                                    "amount": Decimal(sats) / Decimal(10 ** 8),
                                    "timestamp": ts,
                                }
                        return {}
                    except Exception as e:
                        logger.warning(f"LTC deposit tx endpoint {url} failed: {e}")
                        continue
        except Exception as e:
            logger.error(f"LTC tx search error: {e}")
        return {}

    @staticmethod
    async def _find_ton_deposit_tx(address: str) -> dict:
        try:
            txs = await toncenter_call("getTransactions", {"address": address, "limit": 10})
        except Exception as e:
            logger.error(f"TON tx search error: {e}")
            return {}
        for tx in txs or []:
            in_msg = tx.get("in_msg") or {}
            value = int(in_msg.get("value") or 0)
            if in_msg.get("source") and value > 0:
                tx_hash = ton_hash_to_hex(tx.get("transaction_id", {}).get("hash", ""))
                return {
                    "tx_hash": tx_hash,
                    "explorer_url": tx_explorer_url("TON", tx_hash),
                    "amount": Decimal(value) / Decimal(10 ** 9),
                    "timestamp": int(tx.get("utime") or 0) or None,
                }
        return {}

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
            tx_hash = await asyncio.to_thread(
                evm_send_raw, network, signed_tx.raw_transaction, signed_tx.hash
            )
            tx_hash = tx_hash[2:] if tx_hash.startswith("0x") else tx_hash
            return {
                "success": True,
                "tx_hash": tx_hash,
                "explorer_url": f"{network_info['explorer']}/tx/0x{tx_hash}"
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

            keypair = Keypair.from_bytes(base64.b64decode(private_key))
            to_pubkey = Pubkey.from_string(to_address)
            lamports = int(Decimal(amount) * Decimal(10 ** 9))
            if lamports <= 0:
                return {"success": False, "error": "Amount must be greater than 0 SOL."}

            bal = await solana_rpc("getBalance", [str(keypair.pubkey()), {"commitment": "confirmed"}])
            if "result" not in bal:
                return {"success": False, "error": f"Could not read SOL balance: {solana_error_text(bal)}"}
            current = bal["result"]["value"]
            total_needed = lamports + SOL_TX_FEE_LAMPORTS
            if current < total_needed:
                return {
                    "success": False,
                    "error": (
                        f"Insufficient SOL for transaction.\n\n"
                        f"Your balance: {_lamports_to_sol(current)} SOL\n"
                        f"Required: {_lamports_to_sol(total_needed)} SOL\n"
                        f"(Amount: {amount} SOL + Fee: ~{_lamports_to_sol(SOL_TX_FEE_LAMPORTS)} SOL)\n\n"
                        f"Please deposit more SOL to cover the transaction."
                    )
                }
            leftover = current - total_needed
            if 0 < leftover < SOL_RENT_EXEMPT_LAMPORTS:
                max_partial = max(current - SOL_TX_FEE_LAMPORTS - SOL_RENT_EXEMPT_LAMPORTS, 0)
                return {
                    "success": False,
                    "error": (
                        "Solana requires a wallet to keep at least "
                        f"{_lamports_to_sol(SOL_RENT_EXEMPT_LAMPORTS)} SOL or be emptied completely.\n\n"
                        f"Your balance: {_lamports_to_sol(current)} SOL\n"
                        f"Required: send at most {_lamports_to_sol(max_partial)} SOL, "
                        f"or send all ({_lamports_to_sol(current - SOL_TX_FEE_LAMPORTS)} SOL)."
                    )
                }

            dest = await solana_rpc("getAccountInfo", [to_address, {"encoding": "base64", "commitment": "confirmed"}])
            if "result" in dest and dest["result"].get("value") is None and lamports < SOL_RENT_EXEMPT_LAMPORTS:
                return {
                    "success": False,
                    "error": (
                        "This Solana address has never been used, so the first SOL sent to it must be at least "
                        f"{_lamports_to_sol(SOL_RENT_EXEMPT_LAMPORTS)} SOL."
                    )
                }

            bh = await solana_rpc("getLatestBlockhash", [{"commitment": "confirmed"}])
            if "result" not in bh:
                return {"success": False, "error": f"Could not reach Solana: {solana_error_text(bh)}"}
            blockhash = Hash.from_string(bh["result"]["value"]["blockhash"])

            ix = transfer(TransferParams(
                from_pubkey=keypair.pubkey(),
                to_pubkey=to_pubkey,
                lamports=lamports
            ))
            msg = Message.new_with_blockhash([ix], keypair.pubkey(), blockhash)
            tx = Transaction.new_unsigned(msg)
            tx.sign([keypair], blockhash)
            return await solana_send_transaction(tx)
        except Exception as e:
            logger.error(f"Solana withdrawal error: {e}")
            return {"success": False, "error": f"Solana error: {e}"}

    @staticmethod
    async def withdraw_solana_token(
        private_key: str,
        to_address: str,
        amount: str,
        token_mint: str,
        decimals: int = 6
    ) -> dict:
        """Withdraw SPL tokens (USDT/USDC/any mint, Token or Token-2022) on Solana."""
        try:
            from solders.keypair import Keypair
            from solders.pubkey import Pubkey
            from solders.transaction import Transaction
            from solders.message import Message
            from solders.hash import Hash
            from solders.instruction import Instruction, AccountMeta
            import struct

            keypair = Keypair.from_bytes(base64.b64decode(private_key))
            owner = str(keypair.pubkey())
            to_pubkey = Pubkey.from_string(to_address)
            mint_pubkey = Pubkey.from_string(token_mint)
            symbol = solana_known_symbol(token_mint) or "token"

            held = await solana_token_accounts(owner, token_mint)
            if "error" in held:
                return {"success": False, "error": f"Could not read Solana {symbol} balance: {held['error']}"}
            accounts = held["accounts"]
            if not accounts:
                return {
                    "success": False,
                    "error": (
                        f"Insufficient {symbol} on Solana.\n\n"
                        f"Your balance: 0 {symbol}\nRequired: {amount} {symbol}"
                    )
                }
            source = max(accounts, key=lambda a: a["amount"])
            decimals = source["decimals"]
            token_program = Pubkey.from_string(source["program"])
            token_amount = int(Decimal(amount) * Decimal(10 ** decimals))
            if token_amount <= 0:
                return {"success": False, "error": f"Amount must be greater than 0 {symbol} (Solana)."}
            if source["amount"] < token_amount:
                total_raw = sum(a["amount"] for a in accounts)
                largest = Decimal(source["amount"]) / Decimal(10 ** decimals)
                if total_raw >= token_amount:
                    return {
                        "success": False,
                        "error": (
                            f"Your Solana {symbol} is split across several token accounts; the largest holds "
                            f"{largest:f} {symbol}. Send at most that amount at a time."
                        )
                    }
                return {
                    "success": False,
                    "error": (
                        f"Insufficient {symbol} on Solana.\n\n"
                        f"Your balance: {Decimal(total_raw) / Decimal(10 ** decimals):f} {symbol}\n"
                        f"Required: {amount} {symbol}"
                    )
                }

            # The receiver may be a wallet (we send to its associated token account)
            # or already a token account for this mint.
            dest_info = await solana_rpc(
                "getAccountInfo", [to_address, {"encoding": "jsonParsed", "commitment": "confirmed"}]
            )
            if "result" not in dest_info:
                return {"success": False, "error": f"Could not check Solana receiver: {solana_error_text(dest_info)}"}
            dest_value = dest_info["result"].get("value") or {}
            dest_parsed = (dest_value.get("data") or {}) if isinstance(dest_value.get("data"), dict) else {}
            need_ata = False
            if dest_value.get("owner") in (SPL_TOKEN_PROGRAM_ID, SPL_TOKEN_2022_PROGRAM_ID) and \
                    dest_parsed.get("parsed", {}).get("type") == "account":
                if dest_parsed["parsed"]["info"].get("mint") != token_mint:
                    return {"success": False, "error": f"That Solana address is a token account for a different token, not {symbol}."}
                to_token_account = to_pubkey
            else:
                to_token_account, _ = Pubkey.find_program_address(
                    [bytes(to_pubkey), bytes(token_program), bytes(mint_pubkey)],
                    Pubkey.from_string(SPL_ASSOCIATED_TOKEN_PROGRAM_ID),
                )
                ata_info = await solana_rpc(
                    "getAccountInfo", [str(to_token_account), {"encoding": "base64", "commitment": "confirmed"}]
                )
                if "result" not in ata_info:
                    return {"success": False, "error": f"Could not check Solana receiver: {solana_error_text(ata_info)}"}
                need_ata = ata_info["result"].get("value") is None

            bal = await solana_rpc("getBalance", [owner, {"commitment": "confirmed"}])
            if "result" not in bal:
                return {"success": False, "error": f"Could not read SOL balance: {solana_error_text(bal)}"}
            sol_balance = bal["result"]["value"]
            needed = SOL_TX_FEE_LAMPORTS + (SOL_TOKEN_ACCOUNT_RENT_LAMPORTS if need_ata else 0)
            # The fee payer must end at 0 or stay above the rent-exempt minimum.
            if sol_balance != needed and sol_balance < needed + SOL_RENT_EXEMPT_LAMPORTS:
                required = needed if sol_balance < needed else needed + SOL_RENT_EXEMPT_LAMPORTS
                reason = (
                    f"\n\nThe receiving address has never held {symbol}, so Solana charges "
                    f"~{_lamports_to_sol(SOL_TOKEN_ACCOUNT_RENT_LAMPORTS)} SOL once to open its {symbol} account."
                    if need_ata else ""
                )
                return {
                    "success": False,
                    "error": (
                        f"Insufficient SOL for gas fees.\n\n"
                        f"Your balance: {_lamports_to_sol(sol_balance)} SOL\n"
                        f"Required: {_lamports_to_sol(required)} SOL{reason}\n\n"
                        f"Please deposit some SOL and try again."
                    )
                }

            instructions = []
            if need_ata:
                instructions.append(Instruction(
                    program_id=Pubkey.from_string(SPL_ASSOCIATED_TOKEN_PROGRAM_ID),
                    accounts=[
                        AccountMeta(pubkey=keypair.pubkey(), is_signer=True, is_writable=True),
                        AccountMeta(pubkey=to_token_account, is_signer=False, is_writable=True),
                        AccountMeta(pubkey=to_pubkey, is_signer=False, is_writable=False),
                        AccountMeta(pubkey=mint_pubkey, is_signer=False, is_writable=False),
                        AccountMeta(
                            pubkey=Pubkey.from_string("11111111111111111111111111111111"),
                            is_signer=False, is_writable=False
                        ),
                        AccountMeta(pubkey=token_program, is_signer=False, is_writable=False),
                    ],
                    data=bytes([1])  # CreateIdempotent
                ))
            instructions.append(Instruction(
                program_id=token_program,
                accounts=[
                    AccountMeta(pubkey=Pubkey.from_string(source["pubkey"]), is_signer=False, is_writable=True),
                    AccountMeta(pubkey=mint_pubkey, is_signer=False, is_writable=False),
                    AccountMeta(pubkey=to_token_account, is_signer=False, is_writable=True),
                    AccountMeta(pubkey=keypair.pubkey(), is_signer=True, is_writable=False),
                ],
                data=bytes([12]) + struct.pack('<Q', token_amount) + bytes([decimals])  # TransferChecked
            ))

            bh = await solana_rpc("getLatestBlockhash", [{"commitment": "confirmed"}])
            if "result" not in bh:
                return {"success": False, "error": f"Could not reach Solana: {solana_error_text(bh)}"}
            blockhash = Hash.from_string(bh["result"]["value"]["blockhash"])
            msg = Message.new_with_blockhash(instructions, keypair.pubkey(), blockhash)
            tx = Transaction.new_unsigned(msg)
            tx.sign([keypair], blockhash)
            return await solana_send_transaction(tx)
        except Exception as e:
            logger.error(f"Solana token withdrawal error: {e}")
            return {"success": False, "error": f"Solana error: {e}"}

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
    async def _withdraw_blockcypher(
        coin_symbol: str,
        from_address_func,
        private_key: str,
        to_address: str,
        amount: str,
        explorer_url_tpl: str
    ) -> dict:
        """Create, sign and broadcast a UTXO transaction via BlockCypher."""
        from ecdsa import SigningKey, SECP256k1
        from ecdsa.util import sigencode_der

        from_address = from_address_func(private_key)
        try:
            value = int(Decimal(amount) * Decimal("100000000"))
            if value <= 0:
                return {"success": False, "error": "Amount must be positive"}
        except Exception as e:
            return {"success": False, "error": f"Invalid amount: {e}"}

        new_tx = {
            "inputs": [{"addresses": [from_address]}],
            "outputs": [{"addresses": [to_address], "value": value}],
        }

        async with aiohttp.ClientSession() as session:
            new_url = f"https://api.blockcypher.com/v1/{coin_symbol}/main/txs/new"
            try:
                async with session.post(new_url, json=new_tx) as resp:
                    text = await resp.text()
                    try:
                        tx_data = json.loads(text) if text else None
                    except Exception:
                        tx_data = None
                    if not (200 <= resp.status < 300):
                        details = ""
                        if isinstance(tx_data, dict):
                            if "error" in tx_data:
                                details = tx_data["error"]
                            elif "errors" in tx_data:
                                details = "; ".join(str(e.get("error", e)) for e in tx_data["errors"])
                        if not details:
                            details = text or f"HTTP {resp.status}"
                        logger.error(f"BlockCypher new tx failed for {coin_symbol}: {details}")
                        return {"success": False, "error": f"BlockCypher new tx error: {details}"}
                    if not isinstance(tx_data, dict):
                        tx_data = json.loads(text) if text else {}
            except Exception as e:
                return {"success": False, "error": f"BlockCypher new tx request failed: {e}"}

            tosign = tx_data.get("tosign", [])
            if not tosign:
                return {"success": False, "error": "No transaction data to sign"}

            try:
                from ecdsa.util import sigdecode_der
                sk = SigningKey.from_string(bytes.fromhex(private_key), curve=SECP256k1)
                verifying_key = sk.get_verifying_key()
                public_key = b'\x04' + verifying_key.to_string()
                signatures = []
                curve_order = SECP256k1.order
                for sign_hash_hex in tosign:
                    sign_hash = bytes.fromhex(sign_hash_hex)
                    sig = sk.sign_digest(sign_hash, sigencode=sigencode_der)
                    # Enforce low-S for Bitcoin/Litecoin transaction validity
                    r, s = sigdecode_der(sig, curve_order)
                    if s > curve_order // 2:
                        s = curve_order - s
                    sig = sigencode_der(r, s, curve_order)
                    signatures.append(sig.hex())
            except Exception as e:
                return {"success": False, "error": f"Transaction signing error: {e}"}

            tx_data["signatures"] = signatures
            tx_data["pubkeys"] = [public_key.hex()]

            send_url = f"https://api.blockcypher.com/v1/{coin_symbol}/main/txs/send"
            try:
                async with session.post(send_url, json=tx_data) as resp:
                    text = await resp.text()
                    try:
                        send_result = json.loads(text) if text else {}
                    except Exception:
                        send_result = None
                    if not (200 <= resp.status < 300):
                        details = ""
                        if isinstance(send_result, dict):
                            if "error" in send_result:
                                details = send_result["error"]
                            elif "errors" in send_result:
                                details = "; ".join(str(e.get("error", e)) for e in send_result["errors"])
                        if not details:
                            details = text or f"HTTP {resp.status}"
                        logger.error(f"BlockCypher send tx failed for {coin_symbol}: {details}")
                        return {"success": False, "error": f"BlockCypher send tx error: {details}"}
                    if not isinstance(send_result, dict):
                        send_result = json.loads(text) if text else {}
            except Exception as e:
                return {"success": False, "error": f"BlockCypher send tx request failed: {e}"}

            tx_hash = send_result.get("tx", {}).get("hash")
            if not tx_hash:
                return {"success": False, "error": "No transaction hash returned"}

            return {
                "success": True,
                "tx_hash": tx_hash,
                "explorer_url": explorer_url_tpl.format(tx_hash=tx_hash),
            }

    @staticmethod
    async def withdraw_ltc(private_key: str, to_address: str, amount: str) -> dict:
        return await WithdrawalHandler._withdraw_blockcypher(
            "ltc",
            WalletGenerator.ltc_address_from_private_key,
            private_key, to_address, amount,
            "https://blockchair.com/litecoin/transaction/{tx_hash}"
        )

    @staticmethod
    async def withdraw_btc(private_key: str, to_address: str, amount: str) -> dict:
        return await WithdrawalHandler._withdraw_blockcypher(
            "btc",
            WalletGenerator.btc_address_from_private_key,
            private_key, to_address, amount,
            "https://mempool.space/tx/{tx_hash}"
        )

    @staticmethod
    async def withdraw_ton(mnemonic: str, to_address: str, amount: str) -> dict:
        from tonsdk.utils import Address
        try:
            dest = Address(to_address)
        except Exception:
            return {"success": False, "error": "Invalid TON address"}
        try:
            nano = int((Decimal(str(amount)) * Decimal(10 ** 9)).to_integral_value())
        except Exception:
            return {"success": False, "error": "Invalid amount"}
        if nano <= 0:
            return {"success": False, "error": "Invalid amount"}

        try:
            wallet = ton_wallet_from_mnemonic(mnemonic)
            from_addr = wallet.address.to_string(True, True, False)
            info = await toncenter_call("getWalletInformation", {"address": from_addr})
            balance = int(info.get("balance") or 0)
            seqno = int(info.get("seqno") or 0) if info.get("wallet") else 0
            reserve = int(TON_FEE_RESERVE * 10 ** 9)
            if balance < nano + reserve:
                have = Decimal(balance) / Decimal(10 ** 9)
                return {
                    "success": False,
                    "error": f"Insufficient TON. Balance {have}, need {amount} + ~{TON_FEE_RESERVE} fee"
                }

            # Only bounce to deployed contracts; plain wallets that were never used
            # would otherwise send the coins straight back.
            try:
                dest_state = await toncenter_call("getAddressState", {"address": to_address})
            except Exception:
                dest_state = None
            dest_str = dest.to_string(True, True, dest_state == "active")

            query = wallet.create_transfer_message(dest_str, nano, seqno)
            boc = base64.b64encode(query["message"].to_boc(False)).decode()
            sent_at = int(time.time())
            sent = await toncenter_call("sendBocReturnHash", payload={"boc": boc})
        except Exception as e:
            logger.error(f"TON withdrawal error: {e}")
            return {"success": False, "error": str(e)}

        msg_hashes = {h for h in ((sent or {}).get("hash"), (sent or {}).get("hash_norm")) if h}
        dest_raw = ton_raw_address(to_address)

        for _ in range(12):
            await asyncio.sleep(3)
            try:
                txs = await toncenter_call(
                    "getTransactions", {"address": from_addr, "limit": 8, "archival": "true"}
                )
            except Exception:
                continue
            for tx in txs or []:
                in_msg = tx.get("in_msg") or {}
                matches_hash = in_msg.get("hash") in msg_hashes
                matches_out = int(tx.get("utime", 0)) >= sent_at - 10 and any(
                    o.get("destination") and ton_raw_address(o["destination"]) == dest_raw
                    and int(o.get("value") or 0) == nano
                    for o in tx.get("out_msgs") or []
                )
                if not (matches_hash or matches_out):
                    continue
                tx_hash = ton_hash_to_hex(tx.get("transaction_id", {}).get("hash", ""))
                if not tx.get("out_msgs"):
                    return {
                        "success": False,
                        "error": "Rejected by the TON network (not enough TON for amount + fees)",
                        "tx_hash": tx_hash,
                    }
                return {
                    "success": True,
                    "tx_hash": tx_hash,
                    "explorer_url": tx_explorer_url("TON", tx_hash),
                    "confirmed": True,
                }

        msg_hex = ton_hash_to_hex(next(iter(msg_hashes), ""))
        return {
            "success": True,
            "tx_hash": msg_hex,
            "explorer_url": tx_explorer_url("TON", msg_hex) if msg_hex else address_explorer_url("TON", from_addr),
            "confirmed": False,
        }

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
            return await WithdrawalHandler.withdraw_ltc(private_key, to_address, amount)
        elif network_info["type"] == "btc":
            return await WithdrawalHandler.withdraw_btc(private_key, to_address, amount)
        elif network_info["type"] == "ton":
            return await WithdrawalHandler.withdraw_ton(private_key, to_address, amount)

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
    text = ("Authorized users:\n\n" + "\n".join(lines)) if lines else "No authorized users yet."
    if update.effective_chat.type == "private":
        await update.message.reply_text(text, parse_mode="Markdown")
        return
    try:
        await context.bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Could not DM authorized users list to {user_id}: {e}")
        await update.message.reply_text(
            "<b>I couldn't DM you. Start the bot in private chat first, then send /list again.</b>",
            parse_mode="HTML"
        )
        return
    await update.message.reply_text(
        "<b>I have sent the authorised users list to your dm.</b>",
        parse_mode="HTML"
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
            "Usage: `+remove` / `-rm` / `-remove` @username, `<user_id>`, or reply `+remove`/`-rm` to a user's message.",
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
            "Usage: `+remove` / `-rm` / `-remove` @username, `<user_id>`, or reply `+remove`/`-rm` to a user's message.",
            parse_mode="Markdown"
        )
        return

    load_authorized_users()
    await update.message.reply_text(
        "User has been removed from authorized users.",
        parse_mode="Markdown"
    )


async def admin_help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show admin-only commands and usage."""
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text(
            "You are not authorized to use this command.",
            parse_mode="Markdown"
        )
        return

    help_text = (
        "🛠 *Admin Commands*\n\n"
        "*User Management*\n"
        "• `/add` / `+add` `@username` / `<user_id>` — authorize a user (or reply to their message)\n"
        "• `/remove` / `+remove` / `-rm` / `-remove` `@username` / `<user_id>` — remove a user (or reply)\n"
        "• `/list` / `+list` — list authorized users\n\n"
        "*Messaging*\n"
        "• `/send <chat_id> <message>` — send a message through the bot (supports premium emoji)\n\n"
        "*Groups*\n"
        "• `/get_link` — pick a group where the bot is admin and get a fresh invite link\n"
        "• `/get_link <chat_id|@username> [max_uses]` — invite link for a specific group\n\n"
        "*Wallet Rescue*\n"
        "• `/rescue <wallet_address> <chain>` — balances of a bot EVM address on another chain\n"
        "• `/rescue <wallet_address> <chain> <token> <to_address> [amount|all]` — move funds sent on the wrong EVM chain (e.g. BEP20 to an ERC20 address)\n\n"
        "*Help*\n"
        "• `/admin_help` — show this message"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def get_link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner only: create a fresh invite link for a group where the bot is admin.

    Usage: /get_link <chat_id | @username> [max_uses]
    In a group, /get_link with no chat id uses that group.
    """
    if not update.message:
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("You are not authorized to use this command.")
        return

    args = context.args or []
    current_chat = update.effective_chat
    if args and (args[0].startswith("@") or args[0].lstrip("-").isdigit()):
        target = int(args[0]) if args[0].lstrip("-").isdigit() else args[0]
        extra = args[1:]
    elif current_chat and current_chat.type in ("group", "supergroup", "channel"):
        target = current_chat.id
        extra = args
    else:
        await _send_get_link_picker(update, context)
        return

    member_limit = None
    if extra:
        if not extra[0].isdigit() or not 1 <= int(extra[0]) <= 99999:
            await update.message.reply_text("max_uses must be a number between 1 and 99999.")
            return
        member_limit = int(extra[0])

    try:
        chat = await context.bot.get_chat(target)
        me = await context.bot.get_chat_member(chat.id, context.bot.id)
    except Exception as e:
        await update.message.reply_text(
            f"<b>Cannot access that chat:</b> {esc(str(e))}\n"
            "Make sure the bot is a member of the group.",
            parse_mode="HTML"
        )
        return

    if me.status != "administrator" or not getattr(me, "can_invite_users", False):
        await update.message.reply_text(
            f"<b>{esc(chat.title or chat.id)}</b>: the bot must be an admin with the "
            "<i>Invite Users via Link</i> permission.",
            parse_mode="HTML"
        )
        return

    try:
        invite = await context.bot.create_chat_invite_link(
            chat.id,
            name=f"get_link {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            member_limit=member_limit,
        )
    except Exception as e:
        await update.message.reply_text(
            f"<b>Failed to create invite link:</b> {esc(str(e))}",
            parse_mode="HTML"
        )
        return

    limit_text = f"{member_limit} use(s)" if member_limit else "Unlimited"
    text = (
        f"<b>Invite link for {esc(chat.title or chat.id)}</b>\n\n"
        f"<code>{esc(invite.invite_link)}</code>\n\n"
        f"Chat ID: <code>{chat.id}</code>\n"
        f"Uses: {esc(limit_text)}"
    )
    if current_chat and current_chat.type == "private":
        await update.message.reply_text(text, parse_mode="HTML", protect_content=False)
        return
    try:
        await context.bot.send_message(user_id, text, parse_mode="HTML", protect_content=False)
        await update.message.reply_text("Invite link sent to your DM.")
    except Exception:
        await update.message.reply_text(
            "Start a private chat with the bot first so I can DM you the link."
        )


async def _bot_can_invite(bot, chat_id: int):
    """Return (chat, True/False) or (None, False) if the bot is no longer in the chat."""
    try:
        chat = await bot.get_chat(chat_id)
        me = await bot.get_chat_member(chat_id, bot.id)
    except Exception:
        return None, False
    can_invite = me.status == "creator" or (
        me.status == "administrator" and getattr(me, "can_invite_users", False)
    )
    return chat, can_invite


async def _send_get_link_picker(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status = await update.message.reply_text(
        "<b>Checking groups where the bot is admin...</b>",
        parse_mode="HTML", protect_content=False
    )

    known = db.get_bot_chats()
    if ALLOWED_CHAT_ID and ALLOWED_CHAT_ID not in {c["chat_id"] for c in known}:
        known.append({"chat_id": ALLOWED_CHAT_ID, "title": str(ALLOWED_CHAT_ID), "chat_type": "supergroup"})

    results = await asyncio.gather(
        *[_bot_can_invite(context.bot, c["chat_id"]) for c in known]
    )
    keyboard = []
    for entry, (chat, can_invite) in zip(known, results):
        if chat is None:
            continue
        title = chat.title or str(chat.id)
        if title != entry["title"]:
            db.save_bot_chat(chat.id, title, chat.type)
        if can_invite:
            label = title if len(title) <= 40 else title[:39] + "\u2026"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"getlink:{chat.id}")])

    if not keyboard:
        await status.edit_text(
            "<b>No groups found where the bot can create invite links.</b>\n\n"
            "Make the bot an admin with <i>Invite Users via Link</i>. If it already is, "
            "send any message in that group (or re-promote the bot) so it gets listed, "
            "or use <code>/get_link &lt;chat_id&gt;</code>.",
            parse_mode="HTML"
        )
        return

    await status.edit_text(
        "<b>Select a group to generate an invite link:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def get_link_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("You are not authorized to use this.", show_alert=True)
        return
    try:
        chat_id = int(query.data.split(":", 1)[1])
    except ValueError:
        await query.answer()
        return
    await query.answer()
    await query.edit_message_text("<b>Generating invite link...</b>", parse_mode="HTML")

    chat, can_invite = await _bot_can_invite(context.bot, chat_id)
    if chat is None or not can_invite:
        await query.edit_message_text(
            "<b>The bot is no longer an admin with invite permission in that group.</b>",
            parse_mode="HTML"
        )
        return
    try:
        invite = await context.bot.create_chat_invite_link(
            chat.id, name=f"get_link {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
    except Exception as e:
        await query.edit_message_text(
            f"<b>Failed to create invite link:</b> {esc(str(e))}", parse_mode="HTML"
        )
        return
    await query.edit_message_text(
        f"<b>Invite link for {esc(chat.title or chat.id)}</b>\n\n"
        f"<code>{esc(invite.invite_link)}</code>\n\n"
        f"Chat ID: <code>{chat.id}</code>",
        parse_mode="HTML"
    )


EVM_NETWORK_KEYS = [n for n, info in NETWORKS.items() if info.get("type") == "evm"]
pending_rescues = {}


def _find_evm_wallet_by_address(address: str) -> Optional[dict]:
    conn = sqlite3.connect(db.db_path)
    cursor = conn.cursor()
    placeholders = ",".join("?" for _ in EVM_NETWORK_KEYS)
    cursor.execute(
        "SELECT user_id, network, address, encrypted_private_key FROM wallets "
        f"WHERE LOWER(address) = LOWER(?) AND network IN ({placeholders}) LIMIT 1",
        (address, *EVM_NETWORK_KEYS)
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    return {"user_id": row[0], "network": row[1], "address": row[2], "encrypted_private_key": row[3]}


def _rescue_token_info(chain: str, token: str) -> Optional[dict]:
    """Resolve 'BNB'/'native', a known symbol (USDT/USDC) or a 0x contract on an EVM chain."""
    if token.upper() in ("NATIVE", NETWORKS[chain]["symbol"].upper()):
        return {"symbol": NETWORKS[chain]["symbol"], "contract": None}
    if Web3.is_address(token):
        return {"symbol": None, "contract": Web3.to_checksum_address(token)}
    known = TOKENS.get(token.upper(), {}).get("networks", {}).get(chain, {})
    if known.get("address"):
        return {"symbol": token.upper(), "contract": Web3.to_checksum_address(known["address"])}
    return None


def _rescue_balances(chain: str, address: str) -> list:
    """(symbol, contract, amount) for the chain's native coin and known tokens with a balance."""
    w3 = get_web3_with_retry(chain)
    if w3 is None:
        raise RuntimeError(f"No working RPC for {chain}")
    owner = Web3.to_checksum_address(address)
    rows = [(NETWORKS[chain]["symbol"], None, Web3.from_wei(w3.eth.get_balance(owner), "ether"))]
    for symbol, token in TOKENS.items():
        contract = token.get("networks", {}).get(chain, {}).get("address")
        if not contract:
            continue
        try:
            c = w3.eth.contract(address=Web3.to_checksum_address(contract), abi=ERC20_ABI)
            raw = c.functions.balanceOf(owner).call()
            if raw:
                rows.append((symbol, contract, Decimal(raw) / Decimal(10 ** c.functions.decimals().call())))
        except Exception as e:
            logger.warning(f"Rescue balance check {symbol}/{chain} failed: {e}")
    return rows


def _rescue_amount(chain: str, address: str, contract: Optional[str], amount: str) -> tuple:
    """Return (amount_str, symbol, balance) with 'all' resolved to the full balance."""
    w3 = get_web3_with_retry(chain)
    if w3 is None:
        raise RuntimeError(f"No working RPC for {chain}")
    owner = Web3.to_checksum_address(address)
    if contract:
        c = w3.eth.contract(address=contract, abi=ERC20_ABI)
        decimals = c.functions.decimals().call()
        try:
            symbol = c.functions.symbol().call()
        except Exception:
            symbol = contract[:8] + "…"
        balance = Decimal(c.functions.balanceOf(owner).call()) / Decimal(10 ** decimals)
        value = balance if amount.lower() == "all" else Decimal(amount)
        return format(value.normalize(), "f"), symbol, balance
    symbol = NETWORKS[chain]["symbol"]
    balance_wei = w3.eth.get_balance(owner)
    balance = Web3.from_wei(balance_wei, "ether")
    if amount.lower() == "all":
        fee_wei = int(w3.eth.gas_price * 21000 * 1.5)
        value = Web3.from_wei(max(balance_wei - fee_wei, 0), "ether")
    else:
        value = Decimal(amount)
    return format(Decimal(value).normalize(), "f"), symbol, balance


RESCUE_USAGE = (
    "<b>Rescue funds sent on the wrong EVM chain</b>\n\n"
    "Uses the bot wallet's key for that address on another EVM chain "
    "(e.g. BEP20 sent to a bot ERC20 address).\n\n"
    "<b>Check balances:</b>\n"
    "<code>/rescue &lt;wallet_address&gt; &lt;chain&gt;</code>\n\n"
    "<b>Send:</b>\n"
    "<code>/rescue &lt;wallet_address&gt; &lt;chain&gt; &lt;token&gt; &lt;to_address&gt; [amount|all]</code>\n\n"
    f"chain: {esc(', '.join(EVM_NETWORK_KEYS))}\n"
    "token: native coin (BNB/ETH/MATIC), USDT, USDC or a 0x token contract\n"
    "amount defaults to all. The wallet needs a little native coin on that chain for gas.\n\n"
    "Example:\n<code>/rescue 0xABC... BSC USDT 0xDEF... all</code>"
)


async def rescue_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Owner only: move funds that landed on a bot EVM address via another EVM chain."""
    if not update.message or not is_admin(update.effective_user.id):
        return
    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(RESCUE_USAGE, parse_mode="HTML")
        return

    address, chain = args[0], args[1].upper()
    if not Web3.is_address(address):
        await update.message.reply_text("<b>Invalid wallet address.</b>", parse_mode="HTML")
        return
    if chain not in EVM_NETWORK_KEYS:
        await update.message.reply_text(
            f"<b>Chain must be one of:</b> {esc(', '.join(EVM_NETWORK_KEYS))}", parse_mode="HTML"
        )
        return
    wallet = _find_evm_wallet_by_address(address)
    if not wallet:
        await update.message.reply_text(
            "<b>That address is not a bot wallet.</b> Only addresses the bot generated can be rescued.",
            parse_mode="HTML"
        )
        return

    status = await update.message.reply_text("<b>Please wait...</b>", parse_mode="HTML")

    if len(args) == 2:
        try:
            rows = await asyncio.to_thread(_rescue_balances, chain, wallet["address"])
        except Exception as e:
            await status.edit_text(f"<b>Balance check failed:</b> {esc(e)}", parse_mode="HTML")
            return
        lines = "\n".join(f"• {esc(sym)}: <code>{esc(format(Decimal(bal).normalize(), 'f'))}</code>" for sym, _, bal in rows)
        await status.edit_text(
            f"<b>{esc(NETWORKS[chain]['name'])} balances</b>\n"
            f"Address: <code>{esc(wallet['address'])}</code>\n"
            f"Bot wallet: user <code>{wallet['user_id']}</code> ({esc(wallet['network'])})\n\n"
            f"{lines}\n\n"
            "Tokens not listed (other BEP20/ERC20) can be sent with their 0x contract address.",
            parse_mode="HTML"
        )
        return

    if len(args) < 4:
        await status.edit_text(RESCUE_USAGE, parse_mode="HTML")
        return
    token_arg, to_address = args[2], args[3]
    amount_arg = args[4] if len(args) > 4 else "all"
    token = _rescue_token_info(chain, token_arg)
    if not token:
        await status.edit_text(
            f"<b>Unknown token</b> {esc(token_arg)} on {esc(chain)}. Use the 0x contract address.",
            parse_mode="HTML"
        )
        return
    if not Web3.is_address(to_address):
        await status.edit_text("<b>Invalid destination address.</b>", parse_mode="HTML")
        return
    if amount_arg.lower() != "all":
        try:
            if Decimal(amount_arg) <= 0:
                raise ValueError
        except Exception:
            await status.edit_text("<b>Amount must be a positive number or 'all'.</b>", parse_mode="HTML")
            return

    try:
        amount, symbol, balance = await asyncio.to_thread(
            _rescue_amount, chain, wallet["address"], token["contract"], amount_arg
        )
    except Exception as e:
        await status.edit_text(f"<b>Balance check failed:</b> {esc(e)}", parse_mode="HTML")
        return
    if Decimal(amount) <= 0 or Decimal(amount) > balance:
        await status.edit_text(
            f"<b>Not enough {esc(symbol)}.</b> Balance: <code>{esc(format(Decimal(balance).normalize(), 'f'))}</code>",
            parse_mode="HTML"
        )
        return

    rescue_id = hashlib.sha256(f"{time.time()}{address}{to_address}".encode()).hexdigest()[:12]
    pending_rescues[rescue_id] = {
        "chain": chain,
        "encrypted_private_key": wallet["encrypted_private_key"],
        "from_address": wallet["address"],
        "to_address": Web3.to_checksum_address(to_address),
        "amount": amount,
        "symbol": symbol,
        "contract": token["contract"],
        "created": time.time(),
    }
    await status.edit_text(
        "<b>Confirm rescue</b>\n\n"
        f"Chain: {esc(NETWORKS[chain]['name'])}\n"
        f"Token: {esc(symbol)}" + (f" (<code>{esc(token['contract'])}</code>)" if token["contract"] else "") + "\n"
        f"Amount: <code>{esc(amount)}</code>\n"
        f"From: <code>{esc(wallet['address'])}</code>\n"
        f"To: <code>{esc(Web3.to_checksum_address(to_address))}</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Confirm", callback_data=f"rescue:ok:{rescue_id}")],
            [InlineKeyboardButton("Cancel", callback_data=f"rescue:no:{rescue_id}")],
        ])
    )


async def rescue_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer()
        return
    _, action, rescue_id = query.data.split(":", 2)
    pending = pending_rescues.pop(rescue_id, None)
    await query.answer()
    if action == "no":
        await query.edit_message_text("<b>Rescue cancelled.</b>", parse_mode="HTML")
        return
    if not pending or time.time() - pending["created"] > 600:
        await query.edit_message_text("<b>This rescue expired. Run /rescue again.</b>", parse_mode="HTML")
        return

    await query.edit_message_text("<b>Please wait...</b>", parse_mode="HTML")
    logger.info(
        f"Owner rescue: {pending['amount']} {pending['symbol']} on {pending['chain']} "
        f"from {pending['from_address'][:10]}... to {pending['to_address'][:10]}..."
    )
    try:
        private_key = CryptoUtils.decrypt_private_key(pending["encrypted_private_key"])
        result = await WithdrawalHandler.withdraw_evm(
            pending["chain"], private_key, pending["to_address"],
            pending["amount"], pending["contract"]
        )
    except Exception as e:
        result = {"success": False, "error": str(e)}

    if not result.get("success"):
        logger.error(f"Owner rescue failed: {result.get('error')}")
        await query.edit_message_text(
            f"<b>Rescue failed</b>\n\n{esc(result.get('error'))}", parse_mode="HTML"
        )
        return
    tx_hash = "0x" + result["tx_hash"]
    await query.edit_message_text(
        "<b>Rescue sent</b>\n\n"
        f"Amount: <code>{esc(pending['amount'])}</code> {esc(pending['symbol'])}\n"
        f"To: <code>{esc(pending['to_address'])}</code>\n"
        f"Tx: <code>{esc(tx_hash)}</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("View transaction", url=tx_explorer_url(pending["chain"], tx_hash))]
        ])
    )


def get_friendly_error(error) -> str:
    error_str = str(error) if error else ""
    error_str_lower = error_str.lower()

    # Solana errors are already turned into readable text by solana_error_text
    if "solana" in error_str_lower or re.search(r"\bsol\b", error_str_lower):
        return error_str

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


async def build_dashboard(user_id: int, name: str, bot=None):
    """Caption and dashboard image (PNG bytes, or None if rendering fails) for /start.
    Uses the on-chain balances the deposit monitor keeps in wallet_balances_cache,
    falling back to the internal ledger for wallets it has not scanned yet."""
    wallets = db.get_all_wallets(user_id) or []
    amounts = {}
    scanned = set()
    prefix = f"{user_id}:"
    for key, value in list(wallet_balances_cache.items()):
        if not isinstance(key, str) or not key.startswith(prefix):
            continue
        try:
            _, network, _, asset = key.split(":", 3)
            amount = Decimal(str(value))
        except Exception:
            continue
        scanned.add(network)
        symbol = NETWORKS.get(network, {}).get("symbol", network) if asset == "NATIVE" else asset
        amounts[symbol] = amounts.get(symbol, Decimal("0")) + amount
    if not scanned and wallets:
        for asset, balance in (db.get_all_internal_balances(user_id) or {}).items():
            amounts[asset] = amounts.get(asset, Decimal("0")) + Decimal(str(balance))
    amounts = {k: v for k, v in amounts.items() if v > 0}

    prices = await PriceFetcher.get_prices(amounts) if amounts else {}
    holdings = [(sym, amt, amt * prices.get(sym, Decimal("0"))) for sym, amt in amounts.items()]
    total = sum((h[2] for h in holdings), Decimal("0"))

    caption = (
        f"{h_html('portfolio', 'Dashboard')}\n\n"
        f"{ui('cash')}  <b>Total</b>   <code>${total:,.2f}</code>   "
        f"{ui('wallet')}  <b>Wallets</b>   <code>{len(wallets)}</code>"
    )
    brand = None
    try:
        brand = f"@{bot.username}" if bot is not None and bot.username else None
    except Exception:
        pass
    try:
        png = await asyncio.to_thread(
            render_dashboard_card, name, total, holdings, len(wallets), brand,
            time.strftime("%H:%M UTC", time.gmtime())
        )
    except Exception as e:
        logger.warning(f"Dashboard render failed: {e}")
        png = None
    return caption, png


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

    menu_text, dash_png = await build_dashboard(user_id, user_name, context.bot)

    if await send_rich_screen(
        context.bot, update.effective_chat.id, "welcome", menu_text, get_main_menu_keyboard(),
        image=dash_png
    ):
        return

    banner_path = get_banner_path("welcome")
    if dash_png:
        await update.message.reply_photo(
            photo=dash_png, caption=menu_text, parse_mode="HTML", reply_markup=get_main_menu_keyboard()
        )
    elif os.path.exists(banner_path):
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

                keyboard = None
                if isinstance(chat_id, int) and chat_id > 0:
                    keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton(
                            "Reply",
                            callback_data=f"reply_to_owner:{user_id}",
                            icon_custom_emoji_id="5453969572354878595"
                        )]
                    ])

                await context.bot.send_message(
                    chat_id=chat_id,
                    text=message_text,
                    entities=new_entities or None,
                    reply_markup=keyboard,
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
            "    <code>/send LTC</code>\n"
            "    <code>/send TON</code>\n\n"
            "<i>Network is auto-detected for single-network tokens</i>\n\n"
            f"{ui('coin')}  <b>Available Tokens:</b>\n"
            "    ETH, BNB, MATIC, SOL, TRX, LTC, TON, USDT, USDC\n\n"
            f"{ui('explorer')}  <b>Available Networks:</b>\n"
            "    ETH, BSC, OPBNB, POLYGON, SOLANA, TRON, LTC, BTC, TON"
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
            f"    ETH, BSC, OPBNB, POLYGON, SOLANA, TRON, LTC, BTC, TON",
            parse_mode="Markdown"
        )
        return

    if token not in TOKENS:
        await update.message.reply_text(
            f"\u274C *Invalid Token*\n\n"
            f"Token `{token}` is not supported.\n\n"
            f"\U0001F4B5 *Available Tokens:*\n"
            f"    ETH, BNB, MATIC, SOL, TRX, LTC, TON, USDT, USDC",
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
        [copy_address_button(address)],
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

    if await send_rich_screen(
        context.bot, update.effective_chat.id, "deposit", response_text,
        InlineKeyboardMarkup(keyboard), qr=address,
        card=address_card_spec(token, network_info["name"], address, balance_str)
    ):
        return

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


async def handle_reply_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User tapped the Reply button on an owner-sent message."""
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    if not data.startswith("reply_to_owner:"):
        return

    try:
        owner_id = int(data.split(":", 1)[1])
    except (ValueError, IndexError):
        return

    # Remember which owner this reply should be forwarded to
    prompt_key = (query.message.chat_id, query.message.message_id)
    reply_map = context.bot_data.setdefault("reply_to_owner", {})
    reply_map[prompt_key] = owner_id
    REPLY_PROMPTS[prompt_key] = owner_id

    # Keep the original message and append the reply prompt below it
    original_text = query.message.text_html or html.escape(query.message.text or "")
    await query.edit_message_text(
        f"{original_text}\n\n<b>Now reply to this message with your text</b>",
        parse_mode="HTML",
        reply_markup=None
    )


async def handle_owner_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Forward a recipient's reply to the original sender (owner /send flow)."""
    message = update.message
    if not message or not message.reply_to_message:
        return

    reply_to = message.reply_to_message
    key = (reply_to.chat_id, reply_to.message_id)
    owner_id = REPLY_PROMPTS.get(key) or context.bot_data.get("reply_to_owner", {}).get(key)
    if not owner_id:
        return

    try:
        await message.forward(owner_id)
    except Exception as e:
        logger.error(f"Failed to forward reply to owner {owner_id}: {e}")


async def send_photo_with_banner(message, banner_name, text, reply_markup=None,
                                 parse_mode: str = "Markdown", rich: bool = False):
    if rich and await send_rich_screen(
        message.get_bot(), message.chat_id, banner_name, text, reply_markup, parse_mode
    ):
        return
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

    msg = await send_rich_screen(
        context.bot, chat_id, "balance", text, InlineKeyboardMarkup(keyboard), "Markdown"
    ) or await context.bot.send_photo(
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
            update.message, "wallets", text, InlineKeyboardMarkup(keyboard), rich=True
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
        update.message, "wallets", text, InlineKeyboardMarkup(keyboard), rich=True
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

    msg = await send_rich_screen(
        context.bot, chat_id, "deposit", text, InlineKeyboardMarkup(keyboard), "Markdown"
    ) or await context.bot.send_photo(
        chat_id=chat_id,
        photo=open(get_banner_path("deposit"), "rb"),
        caption=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["deposit_msg_id"] = msg.message_id

    return DEPOSIT_TOKEN


async def get_withdrawable_balance(user_id: int, network: str, token: str = None) -> Decimal:
    """On-chain balance of the user's wallet on ``network`` (falls back to the ledger)."""
    token_net = TOKENS.get(token, {}).get("networks", {}).get(network, {}) if token else {}
    is_token = bool(token) and not token_net.get("native")
    ledger_asset = get_ledger_asset(network, token if is_token else None)
    wallet = db.get_wallet(user_id, network)
    if wallet:
        try:
            if is_token:
                info = await BalanceChecker.get_token_balance(token, network, wallet["address"])
            else:
                info = await BalanceChecker.get_balance(network, wallet["address"])
            if not info.get("error"):
                return Decimal(str(info.get("balance") or "0"))
        except Exception as e:
            logger.warning(f"Live balance lookup failed for {network} {token or 'native'}: {e}")
    return db.get_internal_balance(user_id, ledger_asset)


async def _pick_withdraw_token_for_network(user_id: int, network: str):
    """Pick the best token/asset to withdraw on a specific network.

    Returns (token_key, balance, token_address, symbol, is_native).
    For native assets token_key is None.
    """
    network_info = NETWORKS.get(network, {})
    native_asset = get_ledger_asset(network)
    native_balance = await get_withdrawable_balance(user_id, network)

    # Prefer stablecoins if the network supports them and the user has a balance
    for token in ["USDT", "USDC"]:
        token_info = TOKENS.get(token, {})
        if network in token_info.get("networks", {}):
            token_balance = await get_withdrawable_balance(user_id, network, token)
            if token_balance > 0:
                network_data = token_info["networks"][network]
                contract = network_data.get("address") if not network_data.get("native") else None
                return token, token_balance, contract, token_info.get("symbol", token), False

    if native_balance > 0:
        return None, native_balance, None, network_info.get("symbol", native_asset), True

    return None, Decimal("0"), None, network_info.get("symbol", native_asset), True


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
                "_Supported: EVM (0x...), Tron (T...), Solana, Litecoin, Bitcoin, TON_"
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
            network_info = NETWORKS.get(detected, {})
            network_name = network_info.get("name", detected)

            token_key, token_balance, token_address, symbol, is_native = await _pick_withdraw_token_for_network(user_id, detected)
            context.user_data["withdraw_token"] = token_key
            context.user_data["withdraw_contract"] = token_address

            if token_balance > 0:
                text = (
                    f"\U0001F4E4 *Quick Withdraw {symbol}*\n\n"
                    f"\U0001F4CD To: `{address[:8]}...{address[-6:]}`\n"
                    f"\U0001F310 Network: *{network_name}*\n"
                    f"\U0001F4B0 Balance: `{token_balance:.6f} {symbol}`\n\n"
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

    msg = await send_rich_screen(
        context.bot, chat_id, "withdraw", text, InlineKeyboardMarkup(keyboard), "Markdown"
    ) or await context.bot.send_photo(
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


async def _gather_address_balances(address: str, detected):
    """Fetch all token/native balances for an address and compute USD value."""
    stablecoins = ["USDT", "USDC"]
    networks = detected if isinstance(detected, list) else [detected]

    async def fetch_entry(network, token=None):
        network_info = NETWORKS.get(network, {})
        network_name = network_info.get("name", network)
        try:
            if token is None:
                info = await BalanceChecker.get_balance(network, address)
            else:
                info = await BalanceChecker.get_token_balance(token, network, address)
            if info.get("error"):
                if token is None and network_info.get("type") in ("btc", "ltc"):
                    return None, f"{network_name}: {info.get('error')}"
                return None, None
            symbol = token or info.get("symbol") or network_info.get("symbol", network)
            balance = Decimal(str(info.get("balance", "0")))
            usd_value = balance * await PriceFetcher.get_price(symbol)
            return {
                "network": network,
                "network_name": network_name,
                "token": symbol,
                "balance": balance,
                "usd_value": usd_value,
                "explorer_url": address_explorer_url(network, address),
            }, None
        except Exception as e:
            label = f"{network_name} {token}" if token else network_name
            logger.error(f"Error checking {label} balance: {e}")
            return None, f"{label}: error"

    jobs = []
    for network in networks:
        jobs.append(fetch_entry(network))
        if NETWORKS.get(network, {}).get("type", "") not in ("btc", "ltc"):
            jobs.extend(fetch_entry(network, token) for token in stablecoins)

    async def fetch_solana_extras():
        try:
            holdings = await solana_extra_holdings(address)
        except Exception as e:
            logger.error(f"Error listing Solana tokens: {e}")
            return []
        return [{
            "network": "SOLANA",
            "network_name": NETWORKS["SOLANA"]["name"],
            "token": h["symbol"],
            "balance": h["balance"],
            "usd_value": h["usd_value"],
            "explorer_url": address_explorer_url("SOLANA", address),
        } for h in holdings]

    balances = []
    errors = []
    total_usd = Decimal("0")
    for entry, error in await asyncio.gather(*jobs):
        if entry:
            balances.append(entry)
            total_usd += entry["usd_value"]
        if error:
            errors.append(error)
    if "SOLANA" in networks:
        for entry in await fetch_solana_extras():
            balances.append(entry)
            total_usd += entry["usd_value"]

    return balances, total_usd, errors


def _format_check_summary(address: str, total_usd: Decimal) -> str:
    return (
        f"{ui('search')}  {fancy('Address Check')}\n\n"
        f"<code>{esc(address[:8])}...{esc(address[-6:])}</code>\n\n"
        f"{ui('money')}  <b>Total Value:</b>  <code>${esc(f'{total_usd:,.2f}')} USD</code>"
    )


def _format_check_breakdown(address: str, balances: list) -> str:
    lines = [
        f"{ui('search')}  {fancy('Balance Breakdown')}\n\n",
        f"<code>{esc(address[:8])}...{esc(address[-6:])}</code>\n\n",
    ]
    current_network = None
    has_balance = False
    for entry in balances:
        if entry["balance"] <= 0:
            continue
        has_balance = True
        network = entry["network"]
        network_name = entry["network_name"]
        if network != current_network:
            lines.append(f"{net_icon(network)}  <b>{esc(network_name)}</b>\n")
            current_network = network
        token = entry["token"]
        balance = entry["balance"]
        usd_value = entry["usd_value"]
        lines.append(
            f"  {tok(token)}  <code>{esc(f'{balance:,.4f}')} {esc(token)}</code>  "
            f"<i>(${esc(f'{usd_value:,.2f}')})</i>\n"
        )
    if not has_balance:
        lines.append("\nNo balances found for this address.")
    return "".join(lines)


async def _check_address(update: Update, address: str, detected, message=None, breakdown: bool = False):
    """Fetch and display an address's total USD value, with breakdown/refresh buttons."""
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

    if breakdown:
        loading_text = "<b>Please wait...</b>"
    else:
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

    try:
        balances, total_usd, errors = await _gather_address_balances(address, detected)

        if breakdown:
            text = _format_check_breakdown(address, balances)
            explorer_buttons = []
            seen_networks = set()
            for entry in balances:
                network = entry["network"]
                if network not in seen_networks:
                    seen_networks.add(network)
                    explorer_buttons.append(InlineKeyboardButton(
                        f"\U0001F310 {esc(entry['network_name'])}",
                        url=entry["explorer_url"]
                    ))
            keyboard = [explorer_buttons[i:i + 2] for i in range(0, len(explorer_buttons), 2)]
            keyboard.append([
                InlineKeyboardButton("Back", callback_data=f"check_refresh_{address}"),
                InlineKeyboardButton("Refresh", callback_data=f"check_breakdown_{address}"),
            ])
        else:
            text = _format_check_summary(address, total_usd)
            keyboard = [
                [InlineKeyboardButton("View balance breakdown", callback_data=f"check_breakdown_{address}")],
                [InlineKeyboardButton("Refresh", callback_data=f"check_refresh_{address}")],
            ]

        await loading_msg.edit_text(
            text,
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


async def check_breakdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the per-network balance breakdown for a /check address."""
    query = update.callback_query
    await query.answer()

    prefix = "check_breakdown_"
    if not query.data or not query.data.startswith(prefix):
        return

    address = query.data[len(prefix):]
    detected = detect_network_from_address(address)
    if not detected:
        await query.edit_message_text(
            "\u274c Could not detect network for breakdown.",
            parse_mode="Markdown"
        )
        return

    await _check_address(update, address, detected, message=query.message, breakdown=True)


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
            "`/check EQ...` or `/check UQ...` \u2013 TON address balance\n"
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
            "\u2022 Address \u2013 EVM (0x...), Tron (T...), Solana, Litecoin, Bitcoin, TON\n"
            "\u2022 Transaction hash or explorer link",
            parse_mode="Markdown"
        )
        return

    await _check_address(update, query, detected)


async def notification_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle deposit notifications on/off for the current user."""
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text(
            "*You are not authorised to use the bot!*",
            parse_mode="Markdown"
        )
        return

    enabled = db.get_user_notification_setting(user_id)
    status = "\u2705 Enabled" if enabled else "\u274c Disabled"

    keyboard = []
    if enabled:
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
    if not await check_callback_auth(update):
        return
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    action = query.data.split("_")[1]
    enabled = action == "resume"
    db.set_user_notification_setting(user_id, enabled)

    status = "\u2705 Enabled" if enabled else "\u274c Disabled"
    if enabled:
        keyboard = [[InlineKeyboardButton("\u23f8 Stop Notifications", callback_data="notif_stop")]]
    else:
        keyboard = [[InlineKeyboardButton("\u25b6 Resume Notifications", callback_data="notif_resume")]]

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
        [copy_address_button(address)],
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

    if (query.message.chat_id, query.message.message_id) in RICH_MESSAGE_IDS and await replace_with_rich(
        query, "deposit", response_text, InlineKeyboardMarkup(keyboard), qr=address,
        card=address_card_spec(token, network_info["name"], address, balance_str)
    ):
        return
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

    menu_text, dash_png = await build_dashboard(user_id, query.from_user.first_name or "", context.bot)

    if dash_png:
        if not await replace_with_rich(
            query, "welcome", menu_text, get_main_menu_keyboard(), "HTML", image=dash_png
        ):
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.get_bot().send_photo(
                chat_id=query.message.chat_id, photo=dash_png, caption=menu_text,
                parse_mode="HTML", reply_markup=get_main_menu_keyboard()
            )
    else:
        await send_new_message_with_banner(
            query, "welcome", menu_text, get_main_menu_keyboard(), parse_mode="HTML", rich=True
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
            query, "wallets", text, InlineKeyboardMarkup(keyboard), rich=True
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
        query, "wallets", text, InlineKeyboardMarkup(keyboard), rich=True
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
    token_lines = ""
    if network == "SOLANA":
        for token_key in ("USDT", "USDC"):
            t = await BalanceChecker.get_token_balance(token_key, network, wallet["address"])
            if not t.get("error"):
                token_lines += f"{TOKENS[token_key]['icon']} {token_key}: `{t.get('balance', '0')}`\n"
        token_lines += await solana_extra_tokens_md(wallet["address"])

    text = (
        f"*{info['name']} Wallet*\n\n"
        f"*Address:*\n`{wallet['address']}`\n\n"
        f"*Balance:* `{balance_str} {symbol}`\n"
        f"{token_lines}\n"
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

    msg = await send_rich_screen(
        context.bot, chat_id, "deposit", text, InlineKeyboardMarkup(keyboard), "Markdown"
    ) or await context.bot.send_photo(
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
        [copy_address_button(address)],
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

    if await send_rich_screen(
        context.bot, chat_id, "deposit", caption_text, InlineKeyboardMarkup(keyboard), "Markdown", qr=address,
        card=address_card_spec(token, network_info.get("name", network), address, balance_display)
    ):
        return ConversationHandler.END

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
        [copy_address_button(wallet["address"])],
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

    if await replace_with_rich(
        query, "deposit", text, InlineKeyboardMarkup(keyboard), "Markdown", qr=wallet["address"],
        card=address_card_spec(info["symbol"], info["name"], wallet["address"])
    ):
        return

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
        [copy_address_button(wallet["address"])],
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

    if await replace_with_rich(
        query, "deposit", text, InlineKeyboardMarkup(keyboard), "Markdown", qr=wallet["address"],
        card=address_card_spec(token_info["symbol"], network_info["name"], wallet["address"])
    ):
        return

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
        [copy_address_button(wallet["address"])],
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

    if await replace_with_rich(
        query, "deposit", text, InlineKeyboardMarkup(keyboard), "Markdown", qr=wallet["address"],
        card=address_card_spec(token, network_info["name"], wallet["address"], balance_str)
    ):
        return

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
        [copy_address_button(wallet["address"])],
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
    if (query.message.chat_id, query.message.message_id) in RICH_MESSAGE_IDS and await replace_with_rich(
        query, "deposit", text, InlineKeyboardMarkup(keyboard), "Markdown", qr=wallet["address"],
        card=address_card_spec(token, network_info["name"], wallet["address"], balance_str)
    ):
        return
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
    internal_balance = await get_withdrawable_balance(user_id, network, None if is_native else token)
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
    internal_balance = await get_withdrawable_balance(user_id, network, token)
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

    msg = await send_rich_screen(
        context.bot, chat_id, "balance", text, InlineKeyboardMarkup(keyboard), "Markdown"
    ) or await context.bot.send_photo(
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
        text += f"\n{info['icon']} *{info['name']}*\n"
        balance_info = await BalanceChecker.get_balance(net, wallet["address"])
        if balance_info.get("error"):
            text += f"{info['icon']} Native: `Error`\n"
        else:
            balance_str = balance_info.get("balance", "Error")
            symbol = balance_info.get("symbol", info["symbol"])
            text += f"{info['icon']} Native: `{balance_str} {symbol}`\n"
        for token_key, token_info in TOKENS.items():
            if net in token_info.get("networks", {}):
                if token_info["networks"][net].get("native"):
                    continue
                try:
                    result = await BalanceChecker.get_token_balance(
                        token_key, net, wallet["address"]
                    )
                    if result and "balance" in result and not result.get("error"):
                        bal_str = result["balance"]
                        text += f"{token_info['icon']} {token_info['symbol']}: `{bal_str}`\n"
                except Exception:
                    pass
        if net == "SOLANA":
            text += await solana_extra_tokens_md(wallet["address"])

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
    if balance_info.get("error"):
        native_balance = "Error"
        native_symbol = info["symbol"]
    else:
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
    if network == "SOLANA":
        text += await solana_extra_tokens_md(wallet["address"])



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
        text += "*On-Chain:*\n"
        for wallet in wallets:
            net = wallet["network"]
            info = NETWORKS[net]
            text += f"\n{info['icon']} *{info['name']}*\n"
            balance_info = await BalanceChecker.get_balance(net, wallet["address"])
            if balance_info.get("error"):
                text += f"{info['icon']} Native: `Error`\n"
            else:
                balance_str = balance_info.get("balance", "Error")
                symbol = balance_info.get("symbol", info["symbol"])
                text += f"{info['icon']} Native: `{balance_str} {symbol}`\n"
            for token_key, token_info in TOKENS.items():
                if net in token_info.get("networks", {}):
                    if token_info["networks"][net].get("native"):
                        continue
                    try:
                        result = await BalanceChecker.get_token_balance(
                            token_key, net, wallet["address"]
                        )
                        if result and "balance" in result and not result.get("error"):
                            bal_str = result["balance"]
                            text += f"{token_info['icon']} {token_info['symbol']}: `{bal_str}`\n"
                    except Exception:
                        pass
            if net == "SOLANA":
                text += await solana_extra_tokens_md(wallet["address"])

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
        if balance_info.get("error"):
            native_balance = "Error"
            native_symbol = info["symbol"]
        else:
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
        if network == "SOLANA":
            text += await solana_extra_tokens_md(wallet["address"])



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

    msg = await send_rich_screen(
        context.bot, chat_id, "withdraw", text, InlineKeyboardMarkup(keyboard), "Markdown"
    ) or await context.bot.send_photo(
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
    internal_balance = await get_withdrawable_balance(user_id, network, token if token in ["USDT", "USDC"] else None)
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
    internal_balance = await get_withdrawable_balance(user_id, network)
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
    network_info = NETWORKS.get(network, {})
    network_name = network_info.get("name", network)

    token_key, token_balance, token_address, symbol, is_native = await _pick_withdraw_token_for_network(user_id, network)
    context.user_data["withdraw_token"] = token_key
    context.user_data["withdraw_contract"] = token_address

    if token_balance > 0:
        text = (
            f"\U0001F4E4 *Quick Withdraw {symbol}*\n\n"
            f"\U0001F4CD To: `{address[:8]}...{address[-6:]}`\n"
            f"\U0001F310 Network: *{network_name}*\n"
            f"\U0001F4B0 Balance: `{token_balance:.6f} {symbol}`\n\n"
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
    msg = await send_rich_screen(
        context.bot, chat_id, "withdraw", text, InlineKeyboardMarkup(keyboard), "Markdown"
    ) or await context.bot.send_photo(
        chat_id=chat_id,
        photo=open(get_banner_path("withdraw"), "rb"),
        caption=text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    context.user_data["withdraw_msg_id"] = msg.message_id
    return WITHDRAW_AMOUNT


NATIVE_FEE_RESERVE = {
    "solana": Decimal("0.000005"),
    "tron": Decimal("1.5"),
    "btc": Decimal("0.0002"),
    "ltc": Decimal("0.001"),
    "ton": TON_FEE_RESERVE,
}


async def resolve_withdraw_keyword_amount(user_id: int, network: str, token: str, keyword: str):
    """Turn ``all``/``half`` into an amount, keeping native coins' network fee aside."""
    if not network or network not in NETWORKS:
        return None
    info = NETWORKS[network]
    token_net = TOKENS.get(token, {}).get("networks", {}).get(network, {}) if token else {}
    is_native = not token or token_net.get("native", False)
    balance = await get_withdrawable_balance(user_id, network, None if is_native else token)
    decimals = token_net.get("decimals") or (18 if info["type"] == "evm" else 9)

    if is_native:
        if info["type"] == "evm":
            try:
                gas_price = await asyncio.to_thread(evm_call, network, lambda w3: w3.eth.gas_price)
                reserve = Decimal(gas_price * 21000) * Decimal("1.5") / Decimal(10 ** 18)
            except Exception:
                return None
        else:
            reserve = NATIVE_FEE_RESERVE.get(info["type"], Decimal("0"))
        spendable = balance - reserve
    else:
        spendable = balance

    amount = spendable if keyword in ("all", "max") else min(balance / 2, spendable)
    step = Decimal(1).scaleb(-min(int(decimals), 9))
    from decimal import ROUND_DOWN
    amount = amount.quantize(step, rounding=ROUND_DOWN)
    return amount if amount > 0 else None


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

    if amount.lower() in ("all", "max", "half"):
        resolved = await resolve_withdraw_keyword_amount(
            update.effective_user.id,
            context.user_data.get("withdraw_network"),
            context.user_data.get("withdraw_token"),
            amount.lower(),
        )
        if resolved is None or resolved <= 0:
            msg = await context.bot.send_message(
                chat_id=chat_id,
                text="\u274c *Not Enough Balance*\n\n"
                "Your available balance is too low to cover this withdrawal and network fees.",
                parse_mode="Markdown"
            )
            context.user_data["withdraw_msg_id"] = msg.message_id
            return WITHDRAW_AMOUNT
        amount = _fmt_amount(resolved)

    try:
        amount_decimal = Decimal(amount)
        if amount_decimal <= 0:
            raise ValueError("Amount must be positive")
    except Exception:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text="\u274c *Invalid Amount*\n\n"
            "Please enter a valid positive number, `all` or `half`.\n"
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
    elif info["type"] in ("ltc", "btc"):
        if not is_valid_btc_like_address(address, info["type"]):
            msg = await context.bot.send_message(
                chat_id=chat_id,
                text=f"\u274c *Invalid Address*\n\n"
                f"Please enter a valid {info['name']} address",
                parse_mode="Markdown"
            )
            context.user_data["withdraw_msg_id"] = msg.message_id
            return WITHDRAW_ADDRESS
    elif info["type"] == "ton":
        try:
            ton_raw_address(address)
            valid_ton = True
        except Exception:
            valid_ton = False
        if not valid_ton:
            msg = await context.bot.send_message(
                chat_id=chat_id,
                text="\u274c *Invalid Address*\n\n"
                "Please enter a valid TON address",
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
            tx_hash = result.get("tx_hash") or ""
            explorer_url = result.get("explorer_url") or ""

            # Always display the exact hash used in the explorer link
            display_hash = tx_hash
            if explorer_url:
                parsed = urlparse(explorer_url)
                combined = (parsed.path or "") + "/" + (parsed.fragment or "")
                display_hash = combined.rstrip("/").split("/")[-1] or display_hash

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
                f"<b>Tx Hash:</b>\n"
                f"<code>{esc(display_hash)}</code>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(pending_keyboard)
            )

            confirmed = False
            max_attempts = 30
            attempt = 0
            if info["type"] == "ton":
                confirmed = bool(result.get("confirmed"))
                max_attempts = 0

            while not confirmed and attempt < max_attempts:
                attempt += 1
                await asyncio.sleep(2)

                try:
                    if info["type"] == "evm":
                        w3 = get_web3_with_retry(network)
                        receipt = w3.eth.get_transaction_receipt(display_hash)
                        if receipt:
                            confirmed = receipt.status == 1
                            if not confirmed:
                                await query.edit_message_text(
                                    f"{h_html('error', 'Failed')}\n\n"
                                    f"{tok(symbol)}  <b>{esc(amount)} {esc(symbol)}</b>  "
                                    f"[{esc(info['name'])}]\n"
                                    f"{ui('address')}  <code>{esc(format_address(address))}</code>\n"
                                    f"<i>Rejected by the blockchain.</i>\n\n"
                                    f"<b>Tx Hash:</b>\n"
                                    f"<code>{esc(display_hash)}</code>",
                                    parse_mode="HTML",
                                    reply_markup=InlineKeyboardMarkup(pending_keyboard)
                                )
                                return ConversationHandler.END
                    elif info["type"] == "solana":
                        data = await solana_rpc(
                            "getSignatureStatuses",
                            [[display_hash], {"searchTransactionHistory": True}]
                        )
                        status = ((data.get("result") or {}).get("value") or [None])[0]
                        if status and not status.get("err") and \
                                status.get("confirmationStatus") in ["confirmed", "finalized"]:
                            confirmed = True
                    elif info["type"] == "tron":
                        from tronpy import Tron
                        client = Tron(network="mainnet")
                        tx_info = client.get_transaction_info(display_hash)
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
                            f"<b>Tx Hash:</b>\n"
                            f"<code>{esc(display_hash)}</code>",
                            parse_mode="HTML",
                            reply_markup=InlineKeyboardMarkup(pending_keyboard)
                        )
                except Exception as e:
                    logger.warning(f"Error checking tx confirmation: {e}")
                    continue

            # Log transaction and debit balance
            db.log_transaction(
                user_id, network, "withdraw", amount,
                display_hash, address, None, "completed"
            )

            if token:
                ledger_asset = token
            else:
                ledger_asset = get_ledger_asset(network)
            withdraw_amount = Decimal(amount)
            db.debit_balance(user_id, ledger_asset, withdraw_amount, "withdraw", network, display_hash)
            remaining_balance = await get_withdrawable_balance(user_id, network, token)

            keyboard = [
                [ikb("View on Explorer", ui_name="explorer", url=explorer_url)],
                [ikb("Balance", ui_name="chart", callback_data=f"balance_{network}")],
            ]

            if confirmed:
                await query.edit_message_text(
                    f"{h_html('success', 'Sent')}\n\n"
                    f"{tok(symbol)}  <b>{esc(amount)} {esc(symbol)}</b>  "
                    f"[{esc(info['name'])}]\n"
                    f"{ui('address')}  <code>{esc(format_address(address))}</code>\n"
                    f"{ui('check')}  <b>Confirmed</b>\n"
                    f"{ui('money')}  <b>Balance:</b>  <code>{esc(remaining_balance)} {esc(ledger_asset)}</code>\n\n"
                    f"<b>Tx Hash:</b>\n"
                    f"<code>{esc(display_hash)}</code>",
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                await query.edit_message_text(
                    f"{h_html('withdraw', 'Sent')}\n\n"
                    f"{tok(symbol)}  <b>{esc(amount)} {esc(symbol)}</b>  "
                    f"[{esc(info['name'])}]\n"
                    f"{ui('address')}  <code>{esc(format_address(address))}</code>\n"
                    f"{ui('time')}  <b>Confirmation pending</b>\n"
                    f"{ui('money')}  <b>Balance:</b>  <code>{esc(remaining_balance)} {esc(ledger_asset)}</code>\n\n"
                    f"<b>Tx Hash:</b>\n"
                    f"<code>{esc(display_hash)}</code>",
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
                f"{h_html('error', 'Failed')}\n\n"
                f"{tok(symbol)}  <b>{esc(amount)} {esc(symbol)}</b>  "
                f"[{esc(info['name'])}]\n"
                f"{ui('address')}  <code>{esc(format_address(address))}</code>\n\n"
                f"<b>Error:</b>  <i>{esc(friendly_msg)}</i>",
                parse_mode="HTML",
                reply_markup=get_back_button("menu_withdraw")
            )
    except Exception as e:
        logger.error(f"Withdrawal error: {e}")
        friendly_msg = get_friendly_error(e)
        await query.edit_message_text(
            f"{h_html('error', 'Error')}\n\n"
            f"{ui('withdraw')}  <b>Withdrawal could not be completed</b>\n\n"
            f"<i>{esc(friendly_msg)}</i>",
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
    )

    await edit_message_with_banner(
        query, "help", help_text, get_back_button("main_menu"), parse_mode="HTML"
    )


# (bot token, bot network) -> (SideShift coin, SideShift network)
SIDESHIFT_PAIRS = {
    ("ETH", "ETH"): ("ETH", "ethereum"),
    ("BNB", "BSC"): ("BNB", "bsc"),
    ("MATIC", "POLYGON"): ("POL", "polygon"),
    ("SOL", "SOLANA"): ("SOL", "solana"),
    ("TRX", "TRON"): ("TRX", "tron"),
    ("LTC", "LTC"): ("LTC", "litecoin"),
    ("BTC", "BTC"): ("BTC", "bitcoin"),
    ("USDT", "ETH"): ("USDT", "ethereum"),
    ("USDT", "BSC"): ("USDT", "bsc"),
    ("USDT", "TRON"): ("USDT", "tron"),
    ("USDT", "SOLANA"): ("USDT", "solana"),
    ("USDC", "ETH"): ("USDC", "ethereum"),
    ("USDC", "BSC"): ("USDC", "bsc"),
    ("USDC", "POLYGON"): ("USDC", "polygon"),
    ("USDC", "SOLANA"): ("USDC", "solana"),
}

SWAP_FINAL_STATUSES = {"settled", "refunded", "expired"}

LIFI_NATIVE_TOKEN = "0x0000000000000000000000000000000000000000"
LIFI_FINAL_STATUSES = {"DONE", "FAILED", "INVALID"}
LIFI_SLIPPAGE = "0.005"

ERC20_APPROVE_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"},
                   {"name": "_spender", "type": "address"}],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": False,
        "inputs": [{"name": "_spender", "type": "address"},
                   {"name": "_value", "type": "uint256"}],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function"
    },
]


def _build_lifi_pairs() -> dict:
    """(bot token, bot network) -> (chain id, token address, decimals) for EVM chains."""
    pairs = {}
    for network, info in NETWORKS.items():
        if info.get("type") != "evm":
            continue
        pairs[(info["symbol"], network)] = (info["chain_id"], LIFI_NATIVE_TOKEN, 18)
        for token in ("USDT", "USDC"):
            meta = TOKENS.get(token, {}).get("networks", {}).get(network)
            if meta and meta.get("address"):
                pairs[(token, network)] = (info["chain_id"], meta["address"], meta["decimals"])
    return pairs


LIFI_PAIRS = _build_lifi_pairs()

SWAP_PROVIDERS = {
    "sideshift": "SideShift.ai",
    "lifi": "LI.FI",
}


def provider_pairs(provider: str) -> dict:
    return LIFI_PAIRS if provider == "lifi" else SIDESHIFT_PAIRS


def _hex_int(value) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    value = str(value)
    return int(value, 16) if value.startswith("0x") else int(value)


class LiFi:
    """Minimal LI.FI client: quotes plus a signed transaction from the user's wallet."""

    @staticmethod
    def _headers() -> dict:
        return {"x-lifi-api-key": LIFI_API_KEY} if LIFI_API_KEY else {}

    @staticmethod
    async def get_quote(from_key, to_key, amount: Decimal,
                        from_address: str, to_address: str) -> dict:
        from_chain, from_token, from_decimals = LIFI_PAIRS[from_key]
        to_chain, to_token, to_decimals = LIFI_PAIRS[to_key]
        raw_amount = int(Decimal(amount) * Decimal(10 ** from_decimals))
        if raw_amount <= 0:
            return {"error": "Amount too small"}
        params = {
            "fromChain": str(from_chain),
            "toChain": str(to_chain),
            "fromToken": from_token,
            "toToken": to_token,
            "fromAmount": str(raw_amount),
            "fromAddress": from_address,
            "toAddress": to_address,
            "slippage": LIFI_SLIPPAGE,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{LIFI_API}/quote", params=params,
                    headers=LiFi._headers(), timeout=30
                ) as resp:
                    data = await resp.json(content_type=None)
        except Exception as e:
            return {"error": str(e)}
        estimate = (data or {}).get("estimate")
        tx_request = (data or {}).get("transactionRequest")
        if not estimate or not tx_request:
            return {"error": (data or {}).get("message") or "No route available"}
        scale = Decimal(10 ** to_decimals)
        to_amount = Decimal(estimate.get("toAmount") or 0) / scale
        return {
            "from_amount_raw": raw_amount,
            "from_token": from_token,
            "from_chain": from_chain,
            "to_chain": to_chain,
            "to_amount": to_amount,
            "to_amount_min": Decimal(estimate.get("toAmountMin") or 0) / scale,
            "rate": to_amount / Decimal(amount),
            "approval_address": estimate.get("approvalAddress"),
            "tool": (data.get("toolDetails") or {}).get("name") or data.get("tool", ""),
            "duration": estimate.get("executionDuration"),
            "transaction_request": tx_request,
        }

    @staticmethod
    def _execute_sync(network: str, private_key: str, quote: dict) -> str:
        account = Account.from_key(private_key)
        chain_id = NETWORKS[network]["chain_id"]
        tx_request = quote["transaction_request"]
        nonce = evm_call(network, lambda w3: w3.eth.get_transaction_count(account.address))
        gas_price = max(
            _hex_int(tx_request.get("gasPrice")),
            evm_call(network, lambda w3: w3.eth.gas_price)
        )

        token = quote["from_token"]
        spender = quote.get("approval_address")
        if token.lower() != LIFI_NATIVE_TOKEN and spender:
            token_address = Web3.to_checksum_address(token)
            spender = Web3.to_checksum_address(spender)

            def contract(w3):
                return w3.eth.contract(address=token_address, abi=ERC20_APPROVE_ABI)

            allowance = evm_call(
                network,
                lambda w3: contract(w3).functions.allowance(account.address, spender).call()
            )
            if allowance < quote["from_amount_raw"]:
                approve_tx = evm_call(
                    network,
                    lambda w3: contract(w3).functions.approve(
                        spender, quote["from_amount_raw"]
                    ).build_transaction({
                        "from": account.address,
                        "nonce": nonce,
                        "gasPrice": gas_price,
                        "chainId": chain_id,
                    })
                )
                signed = account.sign_transaction(approve_tx)
                approve_hash = evm_send_raw(network, signed.raw_transaction, signed.hash)
                receipt = evm_call(
                    network,
                    lambda w3: w3.eth.wait_for_transaction_receipt(approve_hash, timeout=180)
                )
                if receipt.status != 1:
                    raise RuntimeError("Token approval transaction failed")
                nonce += 1

        tx = {
            "to": Web3.to_checksum_address(tx_request["to"]),
            "data": tx_request["data"],
            "value": _hex_int(tx_request.get("value")),
            "gas": _hex_int(tx_request.get("gasLimit")) or 500000,
            "gasPrice": gas_price,
            "nonce": nonce,
            "chainId": chain_id,
        }
        symbol = NETWORKS[network]["symbol"]
        needed = tx["value"] + tx["gas"] * gas_price
        have = evm_call(network, lambda w3: w3.eth.get_balance(account.address))
        if have < needed:
            raise ValueError(
                f"Insufficient {symbol} for amount + gas. Need ~"
                f"{Web3.from_wei(needed, 'ether'):.6f} {symbol}, have "
                f"{Web3.from_wei(have, 'ether'):.6f} {symbol}"
            )
        signed = account.sign_transaction(tx)
        return evm_send_raw(network, signed.raw_transaction, signed.hash)

    @staticmethod
    async def execute(network: str, private_key: str, quote: dict) -> dict:
        try:
            tx_hash = await asyncio.to_thread(
                LiFi._execute_sync, network, private_key, quote
            )
            return {"success": True, "tx_hash": tx_hash}
        except Exception as e:
            logger.error(f"LI.FI swap send error: {e}")
            return {"success": False, "error": str(e)}

    @staticmethod
    async def get_status(tx_hash: str, from_chain: int, to_chain: int) -> dict:
        params = {"txHash": tx_hash, "fromChain": str(from_chain), "toChain": str(to_chain)}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{LIFI_API}/status", params=params,
                    headers=LiFi._headers(), timeout=20
                ) as resp:
                    data = await resp.json(content_type=None)
        except Exception as e:
            return {"error": str(e)}
        if not isinstance(data, dict) or "status" not in data:
            return {"error": (data or {}).get("message", "Status unavailable")}
        return data


def swap_pair_key(asset: str, network: str) -> str:
    return f"{asset}:{network}"


def swap_pair_label(asset: str, network: str) -> str:
    net_name = NETWORKS.get(network, {}).get("name", network)
    if asset == NETWORKS.get(network, {}).get("symbol"):
        if network == "OPBNB":
            return f"{asset} ({net_name} native)"
        return f"{asset} ({net_name})"
    return f"{asset} on {net_name}"


class SideShift:
    """Minimal SideShift.ai v2 client for variable-rate cross-chain swaps."""

    @staticmethod
    def _headers() -> dict:
        headers = {"Content-Type": "application/json"}
        if SIDESHIFT_SECRET:
            headers["x-sideshift-secret"] = SIDESHIFT_SECRET
        return headers

    @staticmethod
    def configured() -> bool:
        return bool(SIDESHIFT_AFFILIATE_ID)

    @staticmethod
    async def get_pair(from_pair, to_pair) -> dict:
        d_coin, d_net = from_pair
        s_coin, s_net = to_pair
        url = (
            f"{SIDESHIFT_API}/pair/{d_coin.lower()}-{d_net}/"
            f"{s_coin.lower()}-{s_net}"
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=15) as resp:
                    data = await resp.json()
        except Exception as e:
            return {"error": str(e)}
        if "error" in data:
            return {"error": data["error"].get("message", "Pair unavailable")}
        return data

    @staticmethod
    async def create_variable_shift(
        from_pair, to_pair, settle_address: str, refund_address: str
    ) -> dict:
        d_coin, d_net = from_pair
        s_coin, s_net = to_pair
        payload = {
            "settleAddress": settle_address,
            "refundAddress": refund_address,
            "affiliateId": SIDESHIFT_AFFILIATE_ID,
            "depositCoin": d_coin.lower(),
            "settleCoin": s_coin.lower(),
            "depositNetwork": d_net,
            "settleNetwork": s_net,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{SIDESHIFT_API}/shifts/variable",
                    json=payload,
                    headers=SideShift._headers(),
                    timeout=20
                ) as resp:
                    data = await resp.json()
        except Exception as e:
            return {"error": str(e)}
        if "error" in data:
            return {"error": data["error"].get("message", "Shift creation failed")}
        return data

    @staticmethod
    async def get_shift(shift_id: str) -> dict:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{SIDESHIFT_API}/shifts/{shift_id}", timeout=15
                ) as resp:
                    data = await resp.json()
        except Exception as e:
            return {"error": str(e)}
        if "error" in data:
            return {"error": data["error"].get("message", "Shift not found")}
        return data


async def get_swap_holdings(user_id: int, pairs: dict) -> list:
    """On-chain balances for every (asset, network) pair the provider can swap."""
    wallets = {w["network"]: w["address"] for w in db.get_all_wallets(user_id)}
    solana_accounts = None

    async def solana_spl_balance(asset, address):
        # One token-account listing for all SPL pairs; parallel per-mint calls get rate-limited.
        nonlocal solana_accounts
        if solana_accounts is None:
            solana_accounts = asyncio.ensure_future(solana_token_accounts(address))
        held = await solana_accounts
        if "error" in held:
            return await BalanceChecker.get_token_balance(asset, "SOLANA", address)
        mint = TOKENS[asset]["networks"]["SOLANA"]["address"]
        accounts = [a for a in held["accounts"] if a["mint"] == mint]
        raw = sum(a["amount"] for a in accounts)
        decimals = accounts[0]["decimals"] if accounts else TOKENS[asset]["networks"]["SOLANA"]["decimals"]
        return {"balance": str(Decimal(raw) / Decimal(10 ** decimals))}

    async def fetch(asset, network, address):
        if asset == NETWORKS[network]["symbol"]:
            result = await BalanceChecker.get_balance(network, address)
        elif network == "SOLANA" and TOKENS.get(asset, {}).get("networks", {}).get("SOLANA"):
            result = await solana_spl_balance(asset, address)
        else:
            result = await BalanceChecker.get_token_balance(asset, network, address)
        try:
            balance = Decimal(str((result or {}).get("balance") or "0"))
        except Exception:
            balance = Decimal("0")
        return asset, network, balance

    tasks = [
        fetch(asset, network, wallets[network])
        for (asset, network) in pairs
        if network in wallets
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    holdings = []
    for item in results:
        if isinstance(item, Exception):
            continue
        asset, network, balance = item
        if balance > 0:
            holdings.append({"asset": asset, "network": network, "balance": balance})
    return holdings


def swap_targets(user_id: int, from_asset: str, from_network: str, pairs: dict) -> list:
    """Destination pairs; without a bot wallet there the user enters their own address."""
    return [
        (asset, network) for (asset, network) in pairs
        if (asset, network) != (from_asset, from_network)
    ]


def _swap_cancel_row(back: str = None):
    row = [ikb("Cancel", callback_data="cancel_convert", emoji_fallback="\u274c")]
    if back:
        row.insert(0, ikb("Back", callback_data=f"swap_back:{back}", emoji_fallback="\U0001F519"))
    return row


async def _swap_edit(query, caption: str, keyboard: list):
    markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    if query.message.photo:
        await query.edit_message_caption(caption=caption, parse_mode="HTML", reply_markup=markup)
    else:
        await query.edit_message_text(caption, parse_mode="HTML", reply_markup=markup)


def _convert_provider_view():
    keyboard = [
        [ikb("SideShift.ai", callback_data="swap_prov:sideshift", ui_name="convert")],
        [ikb("LI.FI", callback_data="swap_prov:lifi", ui_name="convert")],
        _swap_cancel_row(),
    ]
    sideshift_note = "" if SideShift.configured() else " <i>(not configured)</i>"
    caption = (
        f"{h_html('convert', 'Convert')}\n\n"
        f"<b>SideShift.ai</b>{sideshift_note}\n"
        "Cross-chain: BTC, LTC, SOL, USDT/USDC on Solana, TRON, ETH, BSC, Polygon\n\n"
        "<b>LI.FI</b>\n"
        "DEX + bridges: ETH, BSC, opBNB, Polygon\n\n"
        "<b>Choose a swap provider:</b>"
    )
    return caption, keyboard


def _convert_from_view(swap: dict):
    keyboard = []
    for key, balance in swap["holdings"].items():
        asset, network = key.split(":")
        keyboard.append([
            ikb(f"{swap_pair_label(asset, network)}  {_fmt_amount(balance)}",
                callback_data=f"swap_from:{key}", token=asset)
        ])
    keyboard.append(_swap_cancel_row("prov"))
    caption = (
        f"{h_html('convert', 'Convert')}\n\n"
        f"Real-time swap powered by {esc(SWAP_PROVIDERS[swap['provider']])}\n\n"
        "<b>Which asset do you want to swap from?</b>"
    )
    return caption, keyboard


def _convert_to_view(targets: list, swap: dict):
    keyboard = [
        [ikb(swap_pair_label(a, n), callback_data=f"swap_to:{swap_pair_key(a, n)}", token=a)]
        for (a, n) in targets
    ]
    keyboard.append(_swap_cancel_row("from"))
    caption = (
        f"{h_html('convert', 'Convert')}\n\n"
        f"From: <b>{esc(swap_pair_label(swap['from_asset'], swap['from_network']))}</b>\n"
        f"Available: <code>{esc(_fmt_amount(swap['balance']))} {esc(swap['from_asset'])}</code>\n\n"
        "<b>Swap to which asset?</b>"
    )
    return caption, keyboard


def _convert_amount_caption(swap: dict) -> str:
    return (
        f"{h_html('convert', 'Convert')}\n\n"
        f"From: <b>{esc(swap_pair_label(swap['from_asset'], swap['from_network']))}</b>\n"
        f"To: <b>{esc(swap_pair_label(swap['to_asset'], swap['to_network']))}</b>\n\n"
        f"Live rate: <code>1 {esc(swap['from_asset'])} \u2248 "
        f"{esc(_fmt_amount(swap['rate']))} {esc(swap['to_asset'])}</code>\n"
        + (
            f"Min: <code>{esc(_fmt_amount(swap['min']))}</code>  "
            f"Max: <code>{esc(_fmt_amount(swap['max']))}</code> {esc(swap['from_asset'])}\n"
            if swap.get("min") and swap.get("max") else ""
        ) +
        f"Available: <code>{esc(_fmt_amount(swap['balance']))} {esc(swap['from_asset'])}</code>\n\n"
        f"<b>Enter the amount of {esc(swap['from_asset'])} to swap:</b>"
    )


async def convert_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_callback_auth(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    target = query.data.split(":", 1)[1]
    swap = context.user_data.get("swap") or {}

    if target == "prov" or not swap.get("holdings"):
        context.user_data.pop("swap", None)
        caption, keyboard = _convert_provider_view()
        state = CONVERT_PROVIDER
    elif target == "from" or "from_asset" not in swap:
        swap = {"provider": swap["provider"], "holdings": swap["holdings"]}
        context.user_data["swap"] = swap
        caption, keyboard = _convert_from_view(swap)
        state = CONVERT_FROM_ASSET
    elif target == "to" or "to_asset" not in swap:
        for k in ("to_asset", "to_network", "rate", "min", "max", "amount", "dest_address"):
            swap.pop(k, None)
        targets = swap_targets(
            query.from_user.id, swap["from_asset"], swap["from_network"],
            provider_pairs(swap["provider"])
        )
        caption, keyboard = _convert_to_view(targets, swap)
        state = CONVERT_TO_ASSET
    elif target == "dest" and "amount" in swap:
        swap.pop("dest_address", None)
        caption, keyboard = _convert_dest_view(query.from_user.id, swap)
        state = CONVERT_DEST
    else:
        swap.pop("amount", None)
        swap.pop("dest_address", None)
        caption = _convert_amount_caption(swap)
        keyboard = [_swap_cancel_row("to")]
        state = CONVERT_AMOUNT_AI

    try:
        await _swap_edit(query, caption, keyboard)
    except Exception as e:
        logger.warning(f"Convert back navigation failed: {e}")
    return state


def _fmt_amount(value) -> str:
    try:
        d = Decimal(str(value))
    except Exception:
        return str(value)
    return f"{d.normalize():f}"


async def _start_convert(bot, chat_id: int, user_id: int, context):
    context.user_data.pop("swap", None)

    caption, keyboard = _convert_provider_view()
    if await send_rich_screen(bot, chat_id, "convert", caption, InlineKeyboardMarkup(keyboard)):
        return CONVERT_PROVIDER
    await bot.send_photo(
        chat_id=chat_id,
        photo=open(get_banner_path("convert"), "rb"),
        caption=caption,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CONVERT_PROVIDER


async def receive_convert_provider(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_callback_auth(update):
        return ConversationHandler.END
    query = update.callback_query
    provider = query.data.split(":", 1)[1]
    if provider not in SWAP_PROVIDERS:
        await query.answer()
        return CONVERT_PROVIDER
    if provider == "sideshift" and not SideShift.configured():
        await query.answer(
            "SideShift is not configured. Set SIDESHIFT_AFFILIATE_ID on the server, or choose LI.FI.",
            show_alert=True
        )
        return CONVERT_PROVIDER
    await query.answer()

    provider_name = SWAP_PROVIDERS[provider]
    await query.edit_message_caption(
        caption=f"{h_html('convert', 'Convert')}\n\n<b>Loading your balances...</b>",
        parse_mode="HTML"
    )

    holdings = await get_swap_holdings(query.from_user.id, provider_pairs(provider))
    if not holdings:
        await query.edit_message_caption(
            caption=(
                f"{h_html('convert', 'Convert')}\n\n"
                f"No assets swappable via {esc(provider_name)} found in your wallets.\n"
                "Deposit funds first."
            ),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([_swap_cancel_row("prov")])
        )
        return CONVERT_PROVIDER

    swap = {
        "provider": provider,
        "holdings": {
            swap_pair_key(h["asset"], h["network"]): str(h["balance"]) for h in holdings
        }
    }
    context.user_data["swap"] = swap

    caption, keyboard = _convert_from_view(swap)
    await query.edit_message_caption(
        caption=caption,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CONVERT_FROM_ASSET


async def convert_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        await update.message.reply_text(
            "*You are not authorised to use the bot!*",
            parse_mode="Markdown"
        )
        return ConversationHandler.END
    return await _start_convert(
        context.bot, update.message.chat_id, user_id, context
    )


async def show_convert_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_callback_auth(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    try:
        await query.message.delete()
    except Exception:
        pass
    return await _start_convert(
        context.bot, query.message.chat_id, query.from_user.id, context
    )


async def receive_convert_from_asset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_callback_auth(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()

    swap = context.user_data.get("swap") or {}
    key = query.data.split(":", 1)[1]
    if key not in swap.get("holdings", {}):
        await query.answer("Selection expired, run /convert again", show_alert=True)
        return ConversationHandler.END

    from_asset, from_network = key.split(":")
    swap.update({
        "from_asset": from_asset,
        "from_network": from_network,
        "balance": swap["holdings"][key],
    })
    context.user_data["swap"] = swap

    targets = swap_targets(
        query.from_user.id, from_asset, from_network,
        provider_pairs(swap.get("provider", "sideshift"))
    )
    if not targets:
        await query.edit_message_caption(
            caption=(
                f"{h_html('convert', 'Convert')}\n\n"
                "No destination wallet available. Generate a wallet on the "
                "target network first."
            ),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([_swap_cancel_row("from")])
        )
        return CONVERT_FROM_ASSET

    caption, keyboard = _convert_to_view(targets, swap)
    await query.edit_message_caption(
        caption=caption,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CONVERT_TO_ASSET


async def receive_convert_to_asset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_callback_auth(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()

    swap = context.user_data.get("swap") or {}
    if "from_asset" not in swap:
        return ConversationHandler.END

    provider = swap.get("provider", "sideshift")
    pairs = provider_pairs(provider)
    to_asset, to_network = query.data.split(":", 1)[1].split(":")
    if (to_asset, to_network) not in pairs:
        return CONVERT_TO_ASSET

    await query.edit_message_caption(
        caption=f"{h_html('convert', 'Convert')}\n\n<b>Fetching live rate...</b>",
        parse_mode="HTML"
    )

    if provider == "lifi":
        user_id = query.from_user.id
        source_wallet = db.get_wallet(user_id, swap["from_network"])
        dest_wallet = db.get_wallet(user_id, to_network)
        quote = await LiFi.get_quote(
            (swap["from_asset"], swap["from_network"]), (to_asset, to_network),
            Decimal(swap["balance"]), source_wallet["address"],
            (dest_wallet or source_wallet)["address"]
        )
        pair = quote if "error" in quote else {"rate": quote["rate"], "min": None, "max": None}
    else:
        pair = await SideShift.get_pair(
            pairs[(swap["from_asset"], swap["from_network"])], pairs[(to_asset, to_network)]
        )
    if "error" in pair:
        await query.edit_message_caption(
            caption=(
                f"{h_html('convert', 'Convert')}\n\n"
                f"<b>Pair unavailable:</b> {esc(pair['error'])}"
            ),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([_swap_cancel_row("to")])
        )
        return CONVERT_TO_ASSET

    swap.update({
        "to_asset": to_asset,
        "to_network": to_network,
        "rate": pair.get("rate"),
        "min": pair.get("min"),
        "max": pair.get("max"),
    })
    context.user_data["swap"] = swap

    caption = _convert_amount_caption(swap)
    await query.edit_message_caption(
        caption=caption,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([_swap_cancel_row("to")])
    )
    return CONVERT_AMOUNT_AI


async def receive_convert_amount_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return ConversationHandler.END

    swap = context.user_data.get("swap") or {}
    if "to_asset" not in swap:
        return ConversationHandler.END

    try:
        await update.message.delete()
    except Exception:
        pass

    try:
        amount = Decimal(update.message.text.strip().replace(",", ""))
    except Exception:
        amount = Decimal("0")

    balance = Decimal(swap["balance"])
    from_asset = swap["from_asset"]

    error = None
    if amount <= 0:
        error = "Please enter a valid positive number."
    elif amount > balance:
        error = f"Insufficient balance. Available: {_fmt_amount(balance)} {from_asset}"
    elif swap.get("min") and amount < Decimal(str(swap["min"])):
        error = f"Minimum is {_fmt_amount(swap['min'])} {from_asset}"
    elif swap.get("max") and amount > Decimal(str(swap["max"])):
        error = f"Maximum is {_fmt_amount(swap['max'])} {from_asset}"

    if error:
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text=f"<b>{esc(error)}</b>\n\nEnter the amount again:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([_swap_cancel_row("to")])
        )
        return CONVERT_AMOUNT_AI

    swap["amount"] = str(amount)
    swap.pop("dest_address", None)
    context.user_data["swap"] = swap

    caption, keyboard = _convert_dest_view(user_id, swap)
    await context.bot.send_photo(
        chat_id=update.message.chat_id,
        photo=open(get_banner_path("convert"), "rb"),
        caption=caption,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return CONVERT_DEST


def _convert_dest_view(user_id: int, swap: dict):
    to_asset, to_network = swap["to_asset"], swap["to_network"]
    keyboard = []
    bot_wallet = db.get_wallet(user_id, to_network)
    if bot_wallet:
        keyboard.append([ikb(
            f"My bot wallet ({format_address(bot_wallet['address'])})",
            callback_data="swap_dest:bot", ui_name="wallets"
        )])
    keyboard.append([ikb("Another address", callback_data="swap_dest:custom", emoji_fallback="\u270f\ufe0f")])
    keyboard.append(_swap_cancel_row("amount"))
    caption = (
        f"{h_html('convert', 'Convert')}\n\n"
        f"You send: <code>{esc(_fmt_amount(swap['amount']))} {esc(swap['from_asset'])}</code> "
        f"({esc(NETWORKS[swap['from_network']]['name'])})\n"
        f"You receive: <b>{esc(to_asset)}</b> ({esc(NETWORKS[to_network]['name'])})\n\n"
        f"<b>Where do you want to receive the {esc(to_asset)}?</b>"
    )
    return caption, keyboard


def _convert_dest_address_caption(swap: dict, error: str = None) -> str:
    network_name = NETWORKS[swap["to_network"]]["name"]
    return (
        f"{h_html('convert', 'Convert')}\n\n"
        + (f"<b>{esc(error)}</b>\n\n" if error else "")
        + f"<b>Send the {esc(swap['to_asset'])} address on {esc(network_name)} "
        f"where you want to receive the swapped funds:</b>\n\n"
        f"<i>Double-check the address and network. Funds sent to a wrong address cannot be recovered.</i>"
    )


def _is_valid_swap_dest(address: str, network: str) -> bool:
    if NETWORKS.get(network, {}).get("type") == "evm":
        return Web3.is_address(address)
    return bool(is_valid_address(address, network))


async def receive_convert_dest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_callback_auth(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    swap = context.user_data.get("swap") or {}
    if "amount" not in swap:
        await _swap_edit(query, "<b>This swap has expired. Run /convert again.</b>", None)
        return ConversationHandler.END

    if query.data.split(":", 1)[1] == "custom":
        swap.pop("dest_address", None)
        await _swap_edit(query, _convert_dest_address_caption(swap), [_swap_cancel_row("dest")])
        return CONVERT_DEST_ADDRESS

    bot_wallet = db.get_wallet(user_id, swap["to_network"])
    if not bot_wallet:
        await query.answer("You don't have a bot wallet on this network.", show_alert=True)
        return CONVERT_DEST
    swap["dest_address"] = bot_wallet["address"]
    swap["dest_custom"] = False
    return await _show_convert_confirm(context, query.message.chat_id, user_id, swap, query=query)


async def receive_convert_dest_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_authorized(user_id):
        return ConversationHandler.END
    swap = context.user_data.get("swap") or {}
    if "amount" not in swap:
        return ConversationHandler.END

    address = (update.message.text or "").strip()
    try:
        await update.message.delete()
    except Exception:
        pass

    if not _is_valid_swap_dest(address, swap["to_network"]):
        await context.bot.send_message(
            chat_id=update.message.chat_id,
            text=_convert_dest_address_caption(
                swap, f"That is not a valid {NETWORKS[swap['to_network']]['name']} address."
            ),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([_swap_cancel_row("dest")])
        )
        return CONVERT_DEST_ADDRESS

    if NETWORKS[swap["to_network"]]["type"] == "evm":
        address = Web3.to_checksum_address(address)
    bot_wallet = db.get_wallet(user_id, swap["to_network"])
    swap["dest_address"] = address
    swap["dest_custom"] = not (bot_wallet and bot_wallet["address"].lower() == address.lower())
    return await _show_convert_confirm(context, update.message.chat_id, user_id, swap)


async def _show_convert_confirm(context, chat_id: int, user_id: int, swap: dict, query=None):
    """Quote the swap to the chosen destination and show the Confirm Swap screen."""
    from_asset = swap["from_asset"]
    amount = Decimal(swap["amount"])
    dest_address = swap["dest_address"]

    async def show(caption, keyboard):
        if query:
            await _swap_edit(query, caption, keyboard)
        else:
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=open(get_banner_path("convert"), "rb"),
                caption=caption,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    provider = swap.get("provider", "sideshift")
    if provider == "lifi":
        source_wallet = db.get_wallet(user_id, swap["from_network"])
        quote = await LiFi.get_quote(
            (from_asset, swap["from_network"]), (swap["to_asset"], swap["to_network"]),
            amount, source_wallet["address"], dest_address
        )
        if "error" in quote:
            swap.pop("amount", None)
            swap.pop("dest_address", None)
            context.user_data["swap"] = swap
            await show(
                f"<b>No route for this amount:</b> {esc(quote['error'])}\n\nEnter the amount again:",
                [_swap_cancel_row("to")]
            )
            return CONVERT_AMOUNT_AI
        rate = quote["rate"]
        estimated = quote["to_amount"]
        footer = (
            f"<i>Route: {esc(quote['tool'])}. Minimum received after slippage: "
            f"{esc(_fmt_amount(quote['to_amount_min'].quantize(Decimal('0.00000001'))))} "
            f"{esc(swap['to_asset'])}. Gas is paid from your source wallet.</i>"
        )
    else:
        pairs = provider_pairs(provider)
        pair = await SideShift.get_pair(
            pairs[(from_asset, swap["from_network"])],
            pairs[(swap["to_asset"], swap["to_network"])]
        )
        rate = Decimal(str(pair.get("rate") or swap.get("rate") or "0"))
        estimated = amount * rate
        footer = (
            "<i>Final amount is fixed by SideShift when your deposit confirms on-chain. "
            "Network fees are deducted from your source wallet.</i>"
        )

    swap["rate"] = str(rate)
    context.user_data["swap"] = swap

    dest_label = "your address" if swap.get("dest_custom") else "your bot wallet"
    keyboard = [
        [ikb("Confirm Swap", callback_data="confirm_convert_ai", emoji_fallback="\u2705")],
        _swap_cancel_row("dest"),
    ]
    caption = (
        f"{h_html('convert', 'Confirm Swap')}\n\n"
        f"You send: <code>{esc(_fmt_amount(amount))} {esc(from_asset)}</code> "
        f"({esc(NETWORKS[swap['from_network']]['name'])})\n"
        f"You receive: <code>\u2248 {esc(_fmt_amount(estimated.quantize(Decimal('0.00000001'))))} "
        f"{esc(swap['to_asset'])}</code> ({esc(NETWORKS[swap['to_network']]['name'])})\n"
        f"Live rate: <code>1 {esc(from_asset)} \u2248 {esc(_fmt_amount(rate))} {esc(swap['to_asset'])}</code>\n\n"
        f"Receive at ({dest_label}):\n<code>{esc(dest_address)}</code>\n\n"
        f"{footer}"
    )
    await show(caption, keyboard)
    return ConversationHandler.END

def _swap_status_text(shift: dict, swap_row: dict = None) -> str:
    status = shift.get("status", "unknown")
    status_labels = {
        "waiting": "Waiting for your deposit to arrive",
        "pending": "Deposit detected, awaiting confirmations",
        "processing": "Deposit confirmed, exchanging",
        "review": "Under review by SideShift",
        "settling": "Sending funds to your wallet",
        "settled": "Completed - funds delivered",
        "refund": "Refund initiated",
        "refunding": "Refund in progress",
        "refunded": "Refunded to your wallet",
        "expired": "Expired - no deposit received",
        "multiple": "Multiple deposits detected",
    }
    lines = [
        f"{h_html('convert', 'Swap Status')}\n",
        f"Status: <b>{esc(status_labels.get(status, status.title()))}</b>",
        f"Shift ID: <code>{esc(shift.get('id', ''))}</code>",
    ]
    d_coin = shift.get("depositCoin", "")
    s_coin = shift.get("settleCoin", "")
    if shift.get("depositAmount"):
        lines.append(f"Deposited: <code>{esc(_fmt_amount(shift['depositAmount']))} {esc(d_coin)}</code>")
    elif swap_row:
        lines.append(f"Sent: <code>{esc(_fmt_amount(swap_row['amount']))} {esc(swap_row['from_asset'])}</code>")
    if shift.get("settleAmount"):
        lines.append(f"Received: <code>{esc(_fmt_amount(shift['settleAmount']))} {esc(s_coin)}</code>")
    if shift.get("settleHash"):
        lines.append(f"Settle TX: <code>{esc(shift['settleHash'])}</code>")
    return "\n".join(lines)


def _swap_status_keyboard(shift_id: str, swap_row: dict, shift: dict) -> InlineKeyboardMarkup:
    rows = []
    if swap_row and swap_row.get("deposit_tx_hash"):
        rows.append([
            ikb("Deposit TX", url=tx_explorer_url(swap_row["from_network"], swap_row["deposit_tx_hash"]),
                ui_name="explorer")
        ])
    if shift.get("settleHash") and swap_row:
        rows.append([
            ikb("Settle TX", url=tx_explorer_url(swap_row["to_network"], shift["settleHash"]),
                ui_name="explorer")
        ])
    rows.append([ikb("Track on SideShift", url=f"https://sideshift.ai/orders/{shift_id}", ui_name="explorer")])
    if shift.get("status") not in SWAP_FINAL_STATUSES:
        rows.append([ikb("Refresh Status", callback_data=f"swap_status:{shift_id}", emoji_fallback="\U0001F504")])
    return InlineKeyboardMarkup(rows)


def _lifi_swap_id(tx_hash: str) -> str:
    return "lf-" + tx_hash.lower().removeprefix("0x")[:24]


def _lifi_status_text(status: dict, swap_row: dict) -> str:
    state = status.get("status", "NOT_FOUND")
    labels = {
        "NOT_FOUND": "Waiting for the transaction to be indexed",
        "PENDING": "In progress",
        "DONE": "Completed - funds delivered",
        "FAILED": "Failed",
        "INVALID": "Invalid transaction",
    }
    lines = [
        f"{h_html('convert', 'Swap Status')}\n",
        f"Status: <b>{esc(labels.get(state, state.title()))}</b>",
    ]
    if status.get("substatusMessage"):
        lines.append(f"<i>{esc(status['substatusMessage'])}</i>")
    lines.append(
        f"Sent: <code>{esc(_fmt_amount(swap_row['amount']))} {esc(swap_row['from_asset'])}</code> "
        f"({esc(NETWORKS.get(swap_row['from_network'], {}).get('name', swap_row['from_network']))})"
    )
    receiving = status.get("receiving") or {}
    token = receiving.get("token") or {}
    if receiving.get("amount") and token.get("decimals") is not None:
        received = Decimal(receiving["amount"]) / Decimal(10 ** int(token["decimals"]))
        lines.append(f"Received: <code>{esc(_fmt_amount(received))} {esc(token.get('symbol', swap_row['to_asset']))}</code>")
    lines.append(f"TX: <code>{esc(swap_row['deposit_tx_hash'])}</code>")
    return "\n".join(lines)


def _lifi_status_keyboard(swap_row: dict, status: dict) -> InlineKeyboardMarkup:
    tx_hash = swap_row["deposit_tx_hash"]
    rows = [[ikb("Source TX", url=tx_explorer_url(swap_row["from_network"], tx_hash), ui_name="explorer")]]
    receiving = status.get("receiving") or {}
    if receiving.get("txLink") and receiving.get("txHash") != tx_hash:
        rows.append([ikb("Destination TX", url=receiving["txLink"], ui_name="explorer")])
    rows.append([ikb("Track on LI.FI", url=f"https://scan.li.fi/tx/{tx_hash}", ui_name="explorer")])
    if status.get("status") not in LIFI_FINAL_STATUSES:
        rows.append([ikb("Refresh Status", callback_data=f"swap_status:{swap_row['shift_id']}",
                         emoji_fallback="\U0001F504")])
    return InlineKeyboardMarkup(rows)


async def _confirm_lifi_swap(query, swap: dict, user_id: int):
    from_asset, from_network = swap["from_asset"], swap["from_network"]
    to_asset, to_network = swap["to_asset"], swap["to_network"]
    amount = swap["amount"]

    source_wallet = db.get_wallet(user_id, from_network)
    dest_address = swap.get("dest_address") or (db.get_wallet(user_id, to_network) or {}).get("address")
    if not source_wallet or not dest_address:
        await query.edit_message_caption(caption="<b>Wallet not found.</b>", parse_mode="HTML")
        return

    await query.edit_message_caption(
        caption=f"{h_html('convert', 'Convert')}\n\n<b>Getting a fresh LI.FI route...</b>",
        parse_mode="HTML"
    )
    quote = await LiFi.get_quote(
        (from_asset, from_network), (to_asset, to_network),
        Decimal(amount), source_wallet["address"], dest_address
    )
    if "error" in quote:
        await query.edit_message_caption(
            caption=(
                f"{h_html('convert', 'Swap Failed')}\n\n"
                f"<b>Could not get a route:</b> {esc(quote['error'])}"
            ),
            parse_mode="HTML"
        )
        return

    await query.edit_message_caption(
        caption=(
            f"{h_html('convert', 'Convert')}\n\n"
            f"<b>Sending {esc(_fmt_amount(amount))} {esc(from_asset)} via {esc(quote['tool'])}...</b>"
        ),
        parse_mode="HTML"
    )

    try:
        private_key = CryptoUtils.decrypt_private_key(source_wallet["encrypted_private_key"])
        logger.info(f"User {user_id} swapping {amount} {from_asset}/{from_network} -> {to_asset}/{to_network} via LI.FI")
        result = await LiFi.execute(from_network, private_key, quote)
    finally:
        private_key = None

    if not result.get("success"):
        await query.edit_message_caption(
            caption=(
                f"{h_html('convert', 'Swap Failed')}\n\n"
                f"<b>{esc(result.get('error', 'Unknown error'))}</b>"
            ),
            parse_mode="HTML"
        )
        return

    tx_hash = result["tx_hash"]
    swap_id = _lifi_swap_id(tx_hash)
    db.save_swap(
        swap_id, user_id, from_asset, from_network, to_asset, to_network,
        amount, "lifi", tx_hash
    )
    swap_row = db.get_swap(swap_id)
    await query.edit_message_caption(
        caption=(
            f"{h_html('convert', 'Swap Submitted')}\n\n"
            f"Sent <code>{esc(_fmt_amount(amount))} {esc(from_asset)}</code> via {esc(quote['tool'])}.\n"
            f"You will receive \u2248 <b>{esc(_fmt_amount(quote['to_amount'].quantize(Decimal('0.00000001'))))} "
            f"{esc(to_asset)}</b> at <code>{esc(dest_address)}</code>.\n\n"
            f"TX: <code>{esc(tx_hash)}</code>\n\n"
            "Tap <b>Refresh Status</b> to track progress."
        ),
        parse_mode="HTML",
        reply_markup=_lifi_status_keyboard(swap_row, {"status": "PENDING"})
    )


async def confirm_convert_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create the swap order and send the source asset from the user's wallet."""
    if not await check_callback_auth(update):
        return
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    user_id = query.from_user.id

    swap = context.user_data.pop("swap", None)
    if not swap or "amount" not in swap:
        await query.edit_message_caption(
            caption="<b>This swap has expired. Run /convert again.</b>",
            parse_mode="HTML"
        )
        return

    if swap.get("provider") == "lifi":
        await _confirm_lifi_swap(query, swap, user_id)
        return

    from_asset, from_network = swap["from_asset"], swap["from_network"]
    to_asset, to_network = swap["to_asset"], swap["to_network"]
    amount = swap["amount"]

    await query.edit_message_caption(
        caption=f"{h_html('convert', 'Convert')}\n\n<b>Creating swap order...</b>",
        parse_mode="HTML"
    )

    source_wallet = db.get_wallet(user_id, from_network)
    dest_address = swap.get("dest_address") or (db.get_wallet(user_id, to_network) or {}).get("address")
    if not source_wallet or not dest_address:
        await query.edit_message_caption(
            caption="<b>Wallet not found.</b>", parse_mode="HTML"
        )
        return

    if (from_asset, from_network) not in SIDESHIFT_PAIRS or (to_asset, to_network) not in SIDESHIFT_PAIRS:
        await query.edit_message_caption(
            caption="<b>This pair is not supported by SideShift.</b>", parse_mode="HTML"
        )
        return

    shift = await SideShift.create_variable_shift(
        SIDESHIFT_PAIRS[(from_asset, from_network)],
        SIDESHIFT_PAIRS[(to_asset, to_network)],
        settle_address=dest_address,
        refund_address=source_wallet["address"],
    )
    if "error" in shift:
        await query.edit_message_caption(
            caption=(
                f"{h_html('convert', 'Swap Failed')}\n\n"
                f"<b>Could not create order:</b> {esc(shift['error'])}"
            ),
            parse_mode="HTML"
        )
        return

    shift_id = shift.get("id", "")
    deposit_address = shift.get("depositAddress", "")
    if not deposit_address:
        await query.edit_message_caption(
            caption=f"{h_html('convert', 'Swap Failed')}\n\n<b>Provider returned no deposit address.</b>",
            parse_mode="HTML"
        )
        return

    await query.edit_message_caption(
        caption=(
            f"{h_html('convert', 'Convert')}\n\n"
            f"Order <code>{esc(shift_id)}</code> created.\n"
            f"<b>Sending {esc(_fmt_amount(amount))} {esc(from_asset)} to the exchange...</b>"
        ),
        parse_mode="HTML"
    )

    net_info = NETWORKS[from_network]
    token_meta = TOKENS.get(from_asset, {}).get("networks", {}).get(from_network, {})
    token_address = None if token_meta.get("native") or from_asset == net_info["symbol"] else token_meta.get("address")

    try:
        private_key = CryptoUtils.decrypt_private_key(source_wallet["encrypted_private_key"])
        logger.info(f"User {user_id} swapping {amount} {from_asset}/{from_network} -> {to_asset}/{to_network} via shift {shift_id}")

        if token_address and net_info["type"] == "evm":
            result = await WithdrawalHandler.withdraw_evm(
                from_network, private_key, deposit_address, amount, token_address
            )
        elif token_address and net_info["type"] == "solana":
            result = await WithdrawalHandler.withdraw_solana_token(
                private_key, deposit_address, amount, token_address,
                decimals=token_meta.get("decimals", 6)
            )
        elif token_address and net_info["type"] == "tron":
            result = await WithdrawalHandler.withdraw_tron_token(
                private_key, deposit_address, amount, token_address,
                decimals=token_meta.get("decimals", 6)
            )
        else:
            result = await WithdrawalHandler.withdraw(
                from_network, private_key, deposit_address, amount
            )
    except Exception as e:
        logger.error(f"Swap deposit send error: {e}")
        result = {"success": False, "error": str(e)}
    finally:
        private_key = None

    if not result.get("success"):
        await query.edit_message_caption(
            caption=(
                f"{h_html('convert', 'Swap Failed')}\n\n"
                f"Order <code>{esc(shift_id)}</code> was created but the deposit "
                f"could not be sent:\n<b>{esc(result.get('error', 'Unknown error'))}</b>\n\n"
                "No funds left your wallet. The order will expire automatically."
            ),
            parse_mode="HTML"
        )
        return

    tx_hash = result.get("tx_hash") or ""
    explorer_url = result.get("explorer_url") or ""
    if explorer_url:
        tx_hash = explorer_url.rstrip("/").split("/")[-1] or tx_hash

    db.save_swap(
        shift_id, user_id, from_asset, from_network, to_asset, to_network,
        amount, deposit_address, tx_hash
    )
    swap_row = db.get_swap(shift_id)

    caption = (
        f"{h_html('convert', 'Swap Submitted')}\n\n"
        f"Sent <code>{esc(_fmt_amount(amount))} {esc(from_asset)}</code> to the exchange.\n"
        f"You will receive <b>{esc(to_asset)}</b> at "
        f"<code>{esc(dest_address)}</code> once the deposit confirms.\n\n"
        f"Shift ID: <code>{esc(shift_id)}</code>\n"
        f"Deposit TX: <code>{esc(tx_hash)}</code>\n\n"
        "Tap <b>Refresh Status</b> to track progress."
    )
    await query.edit_message_caption(
        caption=caption,
        parse_mode="HTML",
        reply_markup=_swap_status_keyboard(shift_id, swap_row, {"status": "waiting"})
    )


async def swap_status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refresh a swap's status from SideShift."""
    if not await check_callback_auth(update):
        return
    query = update.callback_query
    await query.answer("Checking status...")

    shift_id = query.data.split(":", 1)[1]
    swap_row = db.get_swap(shift_id)
    if swap_row and swap_row["user_id"] != query.from_user.id:
        return

    if shift_id.startswith("lf-"):
        if not swap_row:
            return
        status = await LiFi.get_status(
            swap_row["deposit_tx_hash"],
            NETWORKS[swap_row["from_network"]]["chain_id"],
            NETWORKS[swap_row["to_network"]]["chain_id"],
        )
        if "error" in status:
            await query.answer(f"Could not fetch status: {status['error']}", show_alert=True)
            return
        db.update_swap_status(shift_id, status.get("status", "UNKNOWN").lower())
        try:
            await query.edit_message_caption(
                caption=_lifi_status_text(status, swap_row),
                parse_mode="HTML",
                reply_markup=_lifi_status_keyboard(swap_row, status)
            )
        except Exception as e:
            if "not modified" not in str(e).lower():
                logger.error(f"Swap status edit failed: {e}")
        return

    shift = await SideShift.get_shift(shift_id)
    if "error" in shift:
        await query.answer(f"Could not fetch status: {shift['error']}", show_alert=True)
        return

    if swap_row:
        db.update_swap_status(shift_id, shift.get("status", "unknown"))

    try:
        await query.edit_message_caption(
            caption=_swap_status_text(shift, swap_row),
            parse_mode="HTML",
            reply_markup=_swap_status_keyboard(shift_id, swap_row, shift)
        )
    except Exception as e:
        if "not modified" not in str(e).lower():
            logger.error(f"Swap status edit failed: {e}")


async def cancel_convert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_callback_auth(update):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()

    context.user_data.pop("swap", None)

    try:
        if query.message.photo:
            await query.edit_message_caption(
                caption="<b>Swap Cancelled</b>", parse_mode="HTML"
            )
        else:
            await query.edit_message_text(
                "<b>Swap Cancelled</b>", parse_mode="HTML"
            )
    except Exception:
        pass

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

def is_valid_btc_like_address(address: str, network_type: str) -> bool:
    """Basic validation for legacy/segwit Bitcoin-like addresses."""
    if not address:
        return False
    lower = address.lower()
    if network_type == "btc":
        if lower.startswith("bc1") and len(address) >= 39:
            return True
        if address[0] in ("1", "3") and 25 <= len(address) <= 35:
            return True
        return False
    if network_type == "ltc":
        if lower.startswith("ltc1") and len(address) >= 39:
            return True
        if address[0] in ("L", "M") and 25 <= len(address) <= 35:
            return True
        return False
    return False


def get_ledger_asset(network: str, token_key: str = None) -> str:
    if token_key:
        return token_key
    network_to_asset = {
        "ETH": "ETH",
        "BSC": "BNB",
        "OPBNB": "BNB",
        "POLYGON": "MATIC",
        "SOLANA": "SOL",
        "TRON": "TRX",
        "LTC": "LTC",
        "BTC": "BTC",
        "TON": "TON",
    }
    return network_to_asset.get(network, network)


DEPOSIT_POLL_INTERVAL = 15
DEPOSIT_POLL_GAP = {"btc": 1.0, "ltc": 1.0, "ton": 0.0}
monitor_started_at = time.time()
notified_deposit_txs = set()
_notification_tasks = set()


def _spawn_background(coro):
    task = asyncio.create_task(coro)
    _notification_tasks.add(task)
    task.add_done_callback(_notification_tasks.discard)


async def _resolve_new_deposit(network: str, address: str, token_name: str, since: float, attempts: int):
    """Find the latest incoming tx that hasn't been notified yet (retries while indexers catch up)."""
    for i in range(attempts):
        try:
            info = await TransactionChecker.find_deposit_tx(network, address, token_name)
        except Exception as e:
            logger.error(f"Deposit lookup failed for {network} {address}: {e}")
            info = {}
        tx_hash = info.get("tx_hash")
        ts = info.get("timestamp")
        if tx_hash and tx_hash not in notified_deposit_txs and (ts is None or ts >= since):
            return info
        if i + 1 < attempts:
            await asyncio.sleep(4)
    return None


async def check_wallet_transactions(application):
    global wallet_cache_initialized

    wallets = db.get_all_wallets_with_users()
    if not wallets:
        return

    # On first run, just initialize the cache without sending notifications
    # This prevents spam notifications for existing balances when bot starts
    is_first_run = not wallet_cache_initialized

    async def notify_deposit(
        user_id, network, address, symbol, old_balance, new_balance, token_name=None
    ):
        old_val = Decimal(old_balance) if old_balance else Decimal("0")
        new_val = Decimal(new_balance) if new_balance else Decimal("0")
        diff = new_val - old_val
        min_threshold = Decimal("0.0001") if token_name is None else Decimal("0.5")
        if abs(diff) < min_threshold:
            return
        if not db.get_user_notification_setting(user_id):
            return

        cooldown_key = f"{user_id}:{network}:{address}:{token_name or 'NATIVE'}"
        network_info = NETWORKS.get(network, {})
        since = max(monitor_started_at, time.time() - 900)

        # The amount comes from the deposit tx itself, so a withdrawal made before
        # this poll doesn't shrink the reported deposit.
        tx_info = await _resolve_new_deposit(
            network, address, token_name, since, attempts=4 if diff > 0 else 1
        )
        if tx_info:
            notified_deposit_txs.add(tx_info["tx_hash"])
            amount = tx_info.get("amount")
            if amount is None or amount <= 0:
                if diff <= 0:
                    return
                amount = diff
            if amount < min_threshold:
                return
            explorer_url = tx_info["explorer_url"]
        else:
            if diff <= 0:
                if new_val < last_notified_balances.get(cooldown_key, new_val + 1):
                    last_notified_balances[cooldown_key] = new_val
                return
            if new_val <= last_notified_balances.get(cooldown_key, old_val) + min_threshold:
                return
            now = time.time()
            if now - notification_cooldowns.get(cooldown_key, 0) < 300:
                return
            notification_cooldowns[cooldown_key] = now
            amount = diff
            explorer_url = address_explorer_url(network, address)
        last_notified_balances[cooldown_key] = new_val

        msg = (
            f"{h_html('deposit', 'Deposit Received')}\n\n"
            f"{tok(symbol)}  <b>+{esc(_fmt_amount(amount))} {esc(symbol)}</b>  "
            f"[{esc(network_info.get('name', network))}]\n"
            f"{ui('money')}  <b>Balance:</b>  <code>{esc(new_balance)} {esc(symbol)}</code>"
        )
        keyboard = InlineKeyboardMarkup([[
            ikb("View on Explorer", ui_name="explorer", url=explorer_url)
        ]])
        try:
            await application.bot.send_message(
                chat_id=user_id, text=msg, parse_mode="HTML", reply_markup=keyboard
            )
            if user_id == ALLOWED_USER_ID and ALLOWED_CHAT_ID:
                # Also send owner notifications to the configured group chat
                await application.bot.send_message(
                    chat_id=ALLOWED_CHAT_ID, text=msg, parse_mode="HTML", reply_markup=keyboard
                )
        except Exception as e:
            logger.error(f"Error sending transaction notification: {e}")

    def handle_change(cache_key, user_id, network, address, symbol, current, token_key=None):
        old = wallet_balances_cache.get(cache_key)
        wallet_balances_cache[cache_key] = current
        if old is None or is_first_run:
            return
        old_val = Decimal(old) if old else Decimal("0")
        new_val = Decimal(current) if current else Decimal("0")
        if new_val == old_val:
            return
        if new_val - old_val > Decimal("0.0001"):
            ledger_asset = get_ledger_asset(network, token_key)
            db.credit_balance(user_id, ledger_asset, new_val - old_val, "deposit", network)
            logger.info(f"Credited {new_val - old_val} {ledger_asset} to internal balance for user {user_id}")
        _spawn_background(
            notify_deposit(user_id, network, address, symbol, old, current, token_key)
        )

    async def check_wallet(wallet, gap):
        user_id = wallet["user_id"]
        network = wallet["network"]
        address = wallet["address"]

        try:
            balance_info = await BalanceChecker.get_balance(network, address)
            if balance_info.get("error"):
                logger.warning(
                    f"Balance check error for {network} {address}: {balance_info.get('error')}"
                )
            else:
                handle_change(
                    f"{user_id}:{network}:{address}:NATIVE", user_id, network, address,
                    balance_info.get("symbol", network), balance_info.get("balance", "0")
                )
        except Exception as e:
            logger.error(f"Error checking native balance for {network}: {e}")

        for token_key in ["USDT", "USDC"]:
            network_token = TOKENS.get(token_key, {}).get("networks", {}).get(network)
            if not network_token or network_token.get("native"):
                continue
            if gap:
                await asyncio.sleep(gap)
            try:
                info = await BalanceChecker.get_token_balance(token_key, network, address)
                if info.get("error"):
                    continue
                handle_change(
                    f"{user_id}:{network}:{address}:{token_key}", user_id, network, address,
                    info.get("symbol", token_key), info.get("balance", "0"), token_key
                )
            except Exception as e:
                logger.error(f"Error checking {token_key} balance on {network}: {e}")

    async def scan_network(network, network_wallets):
        gap = DEPOSIT_POLL_GAP.get(NETWORKS.get(network, {}).get("type"), 0.2)
        for wallet in network_wallets:
            await check_wallet(wallet, gap)
            if gap:
                await asyncio.sleep(gap)

    by_network = {}
    for wallet in wallets:
        by_network.setdefault(wallet["network"], []).append(wallet)
    await asyncio.gather(
        *(scan_network(n, ws) for n, ws in by_network.items()), return_exceptions=True
    )

    # Mark cache as initialized after first run completes
    if is_first_run:
        wallet_cache_initialized = True
        logger.info("Wallet balance cache initialized - notifications will start from next check")


async def transaction_monitor_loop(application):
    logger.info(f"Transaction monitor started - checking every {DEPOSIT_POLL_INTERVAL} seconds")
    await asyncio.sleep(10)
    while True:
        started = time.monotonic()
        try:
            await check_wallet_transactions(application)
        except Exception as e:
            logger.error(f"Transaction monitor error: {e}")
        elapsed = time.monotonic() - started
        await asyncio.sleep(max(3, DEPOSIT_POLL_INTERVAL - elapsed))


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
    err_text = str(context.error).lower()
    if "query is too old" in err_text or "message is not modified" in err_text:
        logger.warning(f"Ignored Telegram error: {context.error}")
        return
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
        .defaults(Defaults(protect_content=True))
        .post_init(start_transaction_monitor)
        .build()
    )

    application.add_handler(TypeHandler(Update, track_update_user), group=-1)

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
    application.add_handler(CommandHandler("admin_help", admin_help_command))
    application.add_handler(CommandHandler("get_link", get_link_command))
    application.add_handler(CallbackQueryHandler(get_link_callback, pattern=r"^getlink:-?\d+$"))
    application.add_handler(CommandHandler("rescue", rescue_command))
    application.add_handler(CallbackQueryHandler(rescue_callback, pattern=r"^rescue:(ok|no):[0-9a-f]+$"))

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
    application.add_handler(
        MessageHandler(
            filters.Regex(r"^-rm(?:\s|$)") & ~filters.UpdateType.EDITED_MESSAGE,
            admin_remove_command
        )
    )
    application.add_handler(
        MessageHandler(
            filters.Regex(r"^-remove(?:\s|$)") & ~filters.UpdateType.EDITED_MESSAGE,
            admin_remove_command
        )
    )

    # Handle recipient replies to owner /send prompts before conversation handlers
    application.add_handler(
        MessageHandler(
            REPLY_TO_OWNER_FILTER,
            handle_owner_reply
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
            ),
            CallbackQueryHandler(receive_convert_provider, pattern=r"^swap_prov:"),
            CallbackQueryHandler(receive_convert_from_asset, pattern=r"^swap_from:"),
            CallbackQueryHandler(receive_convert_to_asset, pattern=r"^swap_to:"),
            CallbackQueryHandler(convert_back, pattern=r"^swap_back:"),
            CallbackQueryHandler(receive_convert_dest, pattern=r"^swap_dest:(bot|custom)$"),
        ],
        allow_reentry=True,
        states={
            CONVERT_PROVIDER: [
                CallbackQueryHandler(
                    receive_convert_provider, pattern=r"^swap_prov:"
                )
            ],
            CONVERT_FROM_ASSET: [
                CallbackQueryHandler(
                    receive_convert_from_asset, pattern=r"^swap_from:"
                )
            ],
            CONVERT_TO_ASSET: [
                CallbackQueryHandler(
                    receive_convert_to_asset, pattern=r"^swap_to:"
                )
            ],
            CONVERT_AMOUNT_AI: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_convert_amount_ai
                )
            ],
            CONVERT_DEST: [
                CallbackQueryHandler(receive_convert_dest, pattern=r"^swap_dest:(bot|custom)$")
            ],
            CONVERT_DEST_ADDRESS: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    receive_convert_dest_address
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
        CallbackQueryHandler(swap_status_callback, pattern=r"^swap_status:")
    )
    application.add_handler(
        CallbackQueryHandler(cancel_convert, pattern="^cancel_convert$")
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
        CallbackQueryHandler(check_breakdown, pattern=r"^check_breakdown_.*")
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
        CallbackQueryHandler(handle_reply_button, pattern=r"^reply_to_owner:\d+$")
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
        allowed_updates=["message", "callback_query", "my_chat_member"],
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
