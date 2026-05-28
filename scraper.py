"""
scraper.py — Fetch OLX.pl listings via the public REST API.

The OLX API does NOT honor sort_by params (verified empirically), so we
cannot rely on newest-first ordering. Strategy:

  1. Apply API-level price cap via filter_float_price:to=<max_price>.
     This dramatically reduces the result set before we ever paginate.
  2. Paginate using the response's links.next URL until it's absent,
     or until we hit a safety cap.
  3. Caller (main.py) does client-side filtering by created_time.

Returns normalized listing dicts; caller decides which to keep.
"""

import logging
import random
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

OLX_API_URL = "https://www.olx.pl/api/v1/offers/"
OLX_API_LIMIT = 50
OLX_API_MAX_PAGES = 10  # safety cap — 500 listings max per product per run

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


def _fetch(url: str, params: Optional[dict], max_retries: int, backoff_base: float) -> Optional[dict]:
    """Fetch a single page (initial URL+params, or a links.next URL with no params)."""
    for attempt in range(1, max_retries + 1):
        try:
            logger.debug("API GET %s params=%s (attempt %d/%d)", url, params, attempt, max_retries)
            resp = requests.get(url, params=params, headers=_headers(), timeout=20)

            if resp.status_code == 403:
                logger.warning("API blocked (403) on attempt %d", attempt)
            else:
                resp.raise_for_status()
                return resp.json()

        except requests.HTTPError as exc:
            logger.warning("HTTP error attempt %d: %s", attempt, exc)
        except requests.ConnectionError as exc:
            logger.warning("Connection error attempt %d: %s", attempt, exc)
        except requests.Timeout:
            logger.warning("Timeout attempt %d", attempt)
        except (requests.RequestException, ValueError) as exc:
            logger.error("Fatal error: %s", exc)
            return None

        if attempt < max_retries:
            sleep = backoff_base ** attempt + random.uniform(0, 1)
            logger.debug("Retry in %.1fs", sleep)
            time.sleep(sleep)

    logger.error("All %d attempts failed for %s", max_retries, url)
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
    max_price: Optional[float] = None,
    delay_range: tuple[float, float] = (2.0, 4.0),
    max_retries: int = 3,
    backoff_base: float = 2.0,
) -> list[dict]:
    """
    Fetch all OLX listings for one query, applying the API-level price cap.

    Pagination follows the response's links.next URL until absent (or safety cap).
    Caller is responsible for any date-based filtering.
    Returns an empty list on any failure.
    """
    delay = random.uniform(*delay_range)
    if delay > 0:
        logger.debug("Sleeping %.1fs before fetching '%s'", delay, product_key)
        time.sleep(delay)

    params: Optional[dict] = {
        "query": search_query.replace("-", " "),
        "limit": OLX_API_LIMIT,
        "offset": 0,
    }
    if max_price is not None:
        params["filter_float_price:to"] = int(max_price)

    url = OLX_API_URL
    results = []
    total_elements = None
    pages_fetched = 0

    for _ in range(OLX_API_MAX_PAGES):
        response = _fetch(url, params, max_retries=max_retries, backoff_base=backoff_base)
        if response is None:
            break

        pages_fetched += 1
        data = response.get("data", [])

        if total_elements is None:
            total_elements = response.get("metadata", {}).get("total_elements")

        for raw in data:
            results.append(_normalize(raw))

        next_link = response.get("links", {}).get("next", {}).get("href")
        if not next_link or not data:
            break

        # Subsequent pages: use links.next URL directly, no extra params
        url = next_link
        params = None

    logger.info(
        "product=%s total_elements=%s pages_fetched=%d listings_fetched=%d max_price=%s",
        product_key, total_elements, pages_fetched, len(results), max_price,
    )
    return results
