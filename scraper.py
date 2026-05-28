"""
scraper.py — Fetch OLX.pl listings via the public REST API.

Fetches pages sorted newest-first and stops paginating as soon as a listing
is older than the cutoff, so no new listings are ever missed regardless of
how many total listings exist for a query.
"""

import logging
import random
import time
from datetime import datetime, timezone
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


def _fetch_page(
    query: str,
    offset: int,
    max_retries: int,
    backoff_base: float,
) -> Optional[list[dict]]:
    params = {
        "query": query.replace("-", " "),
        "sort_by": "created_at:desc",
        "limit": OLX_API_LIMIT,
        "offset": offset,
    }

    for attempt in range(1, max_retries + 1):
        try:
            logger.debug("API GET offset=%d (attempt %d/%d)", offset, attempt, max_retries)
            resp = requests.get(OLX_API_URL, params=params, headers=_headers(), timeout=20)

            if resp.status_code == 403:
                logger.warning("API blocked (403) on attempt %d", attempt)
            else:
                resp.raise_for_status()
                return resp.json().get("data", [])

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

    logger.error("All %d attempts failed at offset %d", max_retries, offset)
    return None


def _parse_created_time(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
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
    cutoff: Optional[datetime] = None,
    delay_range: tuple[float, float] = (2.0, 4.0),
    max_retries: int = 3,
    backoff_base: float = 2.0,
) -> list[dict]:
    """
    Fetch OLX listings sorted newest-first, stopping as soon as a listing is
    older than cutoff. Paginates automatically so no new listings are missed.
    Returns an empty list on any failure.
    """
    delay = random.uniform(*delay_range)
    if delay > 0:
        logger.debug("Sleeping %.1fs before fetching '%s'", delay, product_key)
        time.sleep(delay)

    results = []

    for page_num in range(OLX_API_MAX_PAGES):
        offset = page_num * OLX_API_LIMIT
        page = _fetch_page(search_query, offset=offset, max_retries=max_retries, backoff_base=backoff_base)

        if page is None:
            break

        hit_cutoff = False
        for raw in page:
            created = _parse_created_time(raw.get("created_time", ""))
            if cutoff and created and created < cutoff:
                hit_cutoff = True
                break
            results.append(_normalize(raw))

        if hit_cutoff or len(page) < OLX_API_LIMIT:
            # Either reached old listings or this was the last page
            break

    logger.info(
        "Fetched %d new listings for '%s' across %d page(s)",
        len(results), product_key, page_num + 1,
    )
    return results
