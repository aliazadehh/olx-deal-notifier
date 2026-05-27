"""
deal_checker.py — Evaluate whether a listing qualifies as a deal.

Condition tiers and their max-price-as-fraction-of-new-price thresholds
are defined in config.json under "deal_thresholds". Condition is supplied
by condition_llm.py — this module only does the arithmetic.
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)


def extract_price(listing: dict) -> Optional[float]:
    """
    Return the numeric PLN price from a normalized listing dict.

    Tries listing["price"]["value"] first (numeric), then parses
    listing["price"]["label"] (e.g. "2 500 zł") as a fallback.
    Returns None if no price can be determined (e.g. barter listings).
    """
    price_obj = listing.get("price") or {}

    value = price_obj.get("value")
    if value is not None:
        try:
            return float(value)
        except (ValueError, TypeError):
            pass

    label = str(price_obj.get("label", ""))
    cleaned = re.sub(r"[^\d]", "", label)
    if cleaned:
        try:
            return float(cleaned)
        except ValueError:
            pass

    logger.debug("Could not extract price from listing %s", listing.get("id"))
    return None


def is_deal(
    listing: dict,
    new_price_pln: float,
    condition: str,
    thresholds: dict,
) -> tuple[bool, float]:
    """
    Determine whether this listing qualifies as a deal.

    Parameters
    ----------
    listing       : normalized listing dict from scraper.py
    new_price_pln : reference new price in PLN (from config.json)
    condition     : tier string from condition_llm.py
    thresholds    : mapping of condition → max fraction of new price

    Returns
    -------
    (qualifies: bool, discount_pct: float)
    """
    price = extract_price(listing)
    if price is None:
        return False, 0.0

    threshold = thresholds.get(condition, thresholds.get("unknown", 0.50))
    max_deal_price = new_price_pln * threshold
    qualifies = price <= max_deal_price
    discount_pct = round((1.0 - price / new_price_pln) * 100, 1)

    logger.debug(
        "Listing %s: price=%.0f PLN, condition=%s, threshold=%.0f%%, "
        "max_deal=%.0f PLN, qualifies=%s, discount=%.1f%%",
        listing.get("id"), price, condition,
        threshold * 100, max_deal_price, qualifies, discount_pct,
    )

    return qualifies, discount_pct
