"""
notifier.py — Send deal alerts via Telegram Bot API (plain requests, no library).
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

CONDITION_LABELS = {
    "heavily_used": "Heavily Used",
    "used": "Used",
    "good": "Good",
    "very_good": "Very Good",
    "like_new": "Like New / New",
    "unknown": "Unknown",
}


def _build_message(
    display_name: str,
    title: str,
    price_pln: float,
    discount_pct: float,
    condition: str,
    url: str,
) -> str:
    condition_label = CONDITION_LABELS.get(condition, "Unknown")
    price_fmt = f"{price_pln:,.0f}".replace(",", " ")
    return (
        "🎯 <b>Deal Alert!</b>\n\n"
        f"📦 Product: {display_name}\n"
        f"📝 Title: {title}\n"
        f"💰 Price: {price_fmt} zł (≈ {discount_pct:.0f}% off new)\n"
        f"📊 Condition: {condition_label}\n"
        f"🔗 {url}"
    )


def send_deal_notification(
    display_name: str,
    title: str,
    price_pln: float,
    discount_pct: float,
    condition: str,
    url: str,
    bot_token: str = None,
    chat_id: str = None,
) -> bool:
    """
    Send a Telegram notification. Returns True only on confirmed delivery.
    Caller should only mark the listing as seen when this returns True.
    """
    bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        logger.error(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set in .env"
        )
        return False

    message = _build_message(display_name, title, price_pln, discount_pct, condition, url)
    api_url = TELEGRAM_API.format(token=bot_token)
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    try:
        resp = requests.post(api_url, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            logger.error("Telegram API returned not-ok: %s", data)
            return False
        logger.info("Telegram notification sent for: %s", title)
        return True
    except requests.RequestException as exc:
        logger.error("Telegram send failed: %s", exc)
        return False
