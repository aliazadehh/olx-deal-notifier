"""
scraper.py — Fetch OLX.pl listings via the public REST API.

Anti-bot: rotating User-Agents, Polish Accept-Language, random delays,
          exponential backoff retry.
"""

import logging
import random
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

OLX_API_URL = "https://www.olx.pl/api/v1/offers/"
OLX_API_LIMIT = 50

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
]

BASE_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "DNT": "1",
}


def _headers() -> dict:
    h = dict(BASE_HEADERS)
    h["User-Agent"] = random.choice(USER_AGENTS)
    return h


def _fetch_api(
    query: str,
    max_retries: int = 3,
    backoff_base: float = 2.0,
) -> Optional[list[dict]]:
    params = {
        "query": query.replace("-", " "),
        "limit": OLX_API_LIMIT,
        "offset": 0,
    }

    for attempt in range(1, max_retries + 1):
        try:
            logger.debug("API GET %s params=%s (attempt %d/%d)", OLX_API_URL, params, attempt, max_retries)
            resp = requests.get(OLX_API_URL, params=params, headers=_headers(), timeout=20)

            if resp.status_code == 403:
                logger.warning("API blocked (403) on attempt %d", attempt)
            else:
                resp.raise_for_status()
                offers = resp.json().get("data", [])
                logger.debug("API returned %d offers", len(offers))
                return offers

        except requests.HTTPError as exc:
            logger.warning("HTTP error attempt %d: %s", attempt, exc)
        except requests.ConnectionError as exc:
            logger.warning("Connection error attempt %d: %s", attempt, exc)
        except requests.Timeout:
            logger.warning("Timeout attempt %d", attempt)
        except (requests.RequestException, ValueError) as exc:
            logger.error("Fatal error fetching '%s': %s", query, exc)
            return None

        if attempt < max_retries:
            sleep = backoff_base ** attempt + random.uniform(0, 1)
            logger.debug("Retry in %.1fs", sleep)
            time.sleep(sleep)

    logger.error("All %d attempts failed for '%s'", max_retries, query)
    return None


def _extract_price(params: list) -> dict:
    for p in params:
        if p.get("key") == "price":
            v = p.get("value", {})
            return {
                "value": v.get("value"),
                "label": v.get("label", ""),
                "currency": v.get("currency", "PLN"),
            }
    return {}


def _normalize(raw: dict) -> dict:
    return {
        "id": str(raw.get("id", "")),
        "title": str(raw.get("title", "")).strip(),
        "url": raw.get("url", ""),
        "price": _extract_price(raw.get("params", [])),
        "params": raw.get("params", []),
        "description": str(raw.get("description") or "").strip(),
        "created_time": raw.get("created_time", ""),
    }


def fetch_listings(
    product_key: str,
    search_query: str,
    delay_range: tuple[float, float] = (2.0, 4.0),
    max_retries: int = 3,
    backoff_base: float = 2.0,
) -> list[dict]:
    """
    Fetch and normalize OLX listings for one search query via the REST API.

    Pass delay_range=(0, 0) for the first product to skip the leading sleep.
    Returns an empty list on any failure.
    """
    delay = random.uniform(*delay_range)
    if delay > 0:
        logger.debug("Sleeping %.1fs before fetching '%s'", delay, product_key)
        time.sleep(delay)

    raw = _fetch_api(search_query, max_retries=max_retries, backoff_base=backoff_base)
    if not raw:
        logger.warning("No listings returned for '%s'", product_key)
        return []

    logger.info("Fetched %d listings for '%s' via API", len(raw), product_key)
    return [_normalize(r) for r in raw]
