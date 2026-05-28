"""
condition_llm.py — Classify listing condition using an LLM via OpenRouter.

Returns one of: heavily_used | used | good | very_good | like_new | unknown
Falls back to 'unknown' on any API error so the pipeline never crashes.
"""

import logging
import os
from typing import Optional

from openai import OpenAI

logger = logging.getLogger(__name__)

VALID_CONDITIONS = {"heavily_used", "used", "good", "very_good", "like_new", "unknown", "irrelevant"}

SYSTEM_PROMPT = (
    "You are evaluating second-hand product listings from a Polish marketplace. "
    "First decide if the listing is actually selling the product itself. "
    "If the listing is for an accessory, bag, case, cover, cable, part, rental, service, "
    "or any item other than the product itself, respond with: irrelevant. "
    "Otherwise classify the product's physical condition as exactly one of: "
    "heavily_used, used, good, very_good, like_new, unknown. "
    "Use 'unknown' only when there is truly no condition information. "
    "Respond with exactly ONE word — no punctuation, no explanation."
)

USER_TEMPLATE = "Title: {title}\nDescription: {description}"

# In-process cache: listing_id → condition string
_cache: dict[str, str] = {}

# Module-level client — created once when first needed
_client: Optional[OpenAI] = None


def _get_client() -> Optional[OpenAI]:
    global _client
    if _client is not None:
        return _client

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        logger.warning("OPENROUTER_API_KEY not set; defaulting condition to 'unknown'")
        return None

    base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    _client = OpenAI(api_key=api_key, base_url=base_url)
    return _client


def classify_condition(
    listing_id: str,
    title: str,
    description: str,
    model: str = "openai/gpt-4o-mini",
) -> str:
    """
    Return the condition tier for a listing.

    Caches results within a single run to avoid duplicate API calls.
    Always returns a valid condition string (never raises).
    """
    if listing_id in _cache:
        return _cache[listing_id]

    client = _get_client()
    if client is None:
        return "unknown"

    text = (title or "").strip()
    desc = (description or "").strip()

    if not text and not desc:
        _cache[listing_id] = "unknown"
        return "unknown"

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": USER_TEMPLATE.format(title=text, description=desc)},
            ],
            max_tokens=10,
            temperature=0,
        )
        raw = response.choices[0].message.content.strip().lower()
        condition = raw if raw in VALID_CONDITIONS else "unknown"
        if condition == "unknown" and raw not in VALID_CONDITIONS:
            logger.debug("LLM returned unexpected label %r for listing %s; using 'unknown'", raw, listing_id)
    except Exception as exc:
        logger.warning("LLM condition classification failed for listing %s: %s", listing_id, exc)
        condition = "unknown"

    _cache[listing_id] = condition
    logger.debug("Condition for listing %s: %s", listing_id, condition)
    return condition
