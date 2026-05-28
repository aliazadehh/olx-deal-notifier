"""
condition_llm.py — LLM-as-judge for OLX listings via OpenRouter.

For each listing the LLM returns ONE label that encodes both whether the
listing is the right product AND its condition:

  match_heavily_used, match_used, match_good, match_very_good,
  match_like_new, match_unknown   → it IS the product; use the tier
  wrong_model                     → different product / related model
  irrelevant                      → accessory, bag, rental, etc.

On rate limits / timeouts / 5xx the call is retried with exponential
backoff. After exhausting retries we fall back to 'match_unknown' so the
pipeline keeps running.
"""

import logging
import os
import random
import time
from typing import Optional

from openai import OpenAI
from openai import APIError, APITimeoutError, RateLimitError

logger = logging.getLogger(__name__)

VALID_LABELS = {
    "match_heavily_used",
    "match_used",
    "match_good",
    "match_very_good",
    "match_like_new",
    "match_unknown",
    "wrong_model",
    "irrelevant",
}

SYSTEM_PROMPT = (
    "You evaluate second-hand listings from a Polish marketplace (OLX). "
    "The user message specifies the target product and a listing's title and description.\n\n"
    "Step 1: Decide whether the listing is selling the exact target product.\n"
    "  • If it is selling a related but different model (e.g. target is MPC Live II "
    "but the listing is MPC Live III, or target is DDJ-FLX4 but the listing is DDJ-FLX2), "
    "respond with: wrong_model\n"
    "  • If it is selling an accessory, bag, case, cover, cable, part, rental, "
    "service, or anything other than the product itself, respond with: irrelevant\n\n"
    "Step 2: Otherwise, classify the product's physical condition and prefix it with 'match_'. "
    "Pick exactly one of:\n"
    "  match_heavily_used, match_used, match_good, match_very_good, match_like_new, match_unknown\n"
    "Use match_unknown only when there is genuinely no condition information.\n\n"
    "Respond with EXACTLY ONE label — no punctuation, no explanation."
)

USER_TEMPLATE = (
    "Target product: {display_name}\n\n"
    "Listing title: {title}\n"
    "Listing description: {description}"
)

MAX_RETRIES = 3

# In-process cache: listing_id → label string
_cache: dict[str, str] = {}

# Module-level client — created once when first needed
_client: Optional[OpenAI] = None


def _get_client() -> Optional[OpenAI]:
    global _client
    if _client is not None:
        return _client

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        logger.warning("OPENROUTER_API_KEY not set; defaulting to match_unknown")
        return None

    base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    _client = OpenAI(api_key=api_key, base_url=base_url)
    return _client


def _call_with_retry(client: OpenAI, model: str, messages: list) -> Optional[str]:
    """Call the chat API with exponential backoff on 429/5xx/timeout."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=10,
                temperature=0,
            )
            return response.choices[0].message.content
        except RateLimitError as exc:
            logger.warning("LLM rate limited (attempt %d/%d): %s", attempt, MAX_RETRIES, exc)
        except APITimeoutError as exc:
            logger.warning("LLM timeout (attempt %d/%d): %s", attempt, MAX_RETRIES, exc)
        except APIError as exc:
            # Retry on 5xx; surface client errors immediately
            status = getattr(exc, "status_code", None)
            if status and 500 <= status < 600:
                logger.warning("LLM 5xx (attempt %d/%d): %s", attempt, MAX_RETRIES, exc)
            else:
                logger.warning("LLM API error (non-retriable): %s", exc)
                return None
        except Exception as exc:
            logger.warning("LLM unexpected error (attempt %d/%d): %s", attempt, MAX_RETRIES, exc)

        if attempt < MAX_RETRIES:
            sleep = 2 ** attempt + random.uniform(0, 1)
            logger.debug("LLM retry in %.1fs", sleep)
            time.sleep(sleep)

    logger.warning("LLM call failed after %d attempts", MAX_RETRIES)
    return None


def classify_listing(
    listing_id: str,
    display_name: str,
    title: str,
    description: str,
    model: str = "openai/gpt-oss-120b:free",
) -> str:
    """
    Return the classification label for a listing.

    Caches within a single run. Always returns a valid label (never raises);
    falls back to 'match_unknown' on any persistent failure so the pipeline
    keeps running.
    """
    if listing_id in _cache:
        return _cache[listing_id]

    client = _get_client()
    if client is None:
        return "match_unknown"

    text = (title or "").strip()
    desc = (description or "").strip()

    if not text and not desc:
        _cache[listing_id] = "match_unknown"
        return "match_unknown"

    raw_output = _call_with_retry(
        client=client,
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_TEMPLATE.format(
                display_name=display_name, title=text, description=desc,
            )},
        ],
    )

    if raw_output is None:
        label = "match_unknown"
    else:
        cleaned = raw_output.strip().lower().rstrip(".,:;!?")
        label = cleaned if cleaned in VALID_LABELS else "match_unknown"
        if label == "match_unknown" and cleaned not in VALID_LABELS:
            logger.debug("LLM returned unexpected label %r for %s", cleaned, listing_id)

    _cache[listing_id] = label
    logger.debug("Label for %s: %s", listing_id, label)
    return label
