"""
scraper.py — Fetch OLX.pl search results and extract listing data.

Primary:  parse <script id="__NEXT_DATA__"> JSON blob (Next.js).
Fallback: BeautifulSoup CSS selector parsing.

Anti-bot: rotating User-Agents, Polish Accept-Language, random delays,
          exponential backoff retry, captcha/block detection.
"""

import json
import logging
import random
import re
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

OLX_SEARCH_URL = "https://www.olx.pl/elektronika/q-{query}/"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
]

BASE_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "DNT": "1",
}

BLOCK_SIGNALS = ("captcha", "robot", "blocked", "challenge", "verify you are human")


def _headers() -> dict:
    h = dict(BASE_HEADERS)
    h["User-Agent"] = random.choice(USER_AGENTS)
    return h


def _is_blocked(response: requests.Response) -> bool:
    if response.status_code == 403:
        return True
    snippet = response.text[:3000].lower()
    return any(s in snippet for s in BLOCK_SIGNALS)


def _fetch_page(url: str, max_retries: int = 3, backoff_base: float = 2.0) -> Optional[str]:
    for attempt in range(1, max_retries + 1):
        try:
            logger.debug("GET %s (attempt %d/%d)", url, attempt, max_retries)
            resp = requests.get(url, headers=_headers(), timeout=20, allow_redirects=True)

            if _is_blocked(resp):
                logger.warning("Blocked/CAPTCHA on %s (status=%d). Skipping.", url, resp.status_code)
                return None

            resp.raise_for_status()
            return resp.text

        except requests.HTTPError as exc:
            logger.warning("HTTP error attempt %d for %s: %s", attempt, url, exc)
        except requests.ConnectionError as exc:
            logger.warning("Connection error attempt %d for %s: %s", attempt, url, exc)
        except requests.Timeout:
            logger.warning("Timeout attempt %d for %s", attempt, url)
        except requests.RequestException as exc:
            logger.error("Fatal request error for %s: %s", url, exc)
            return None

        if attempt < max_retries:
            sleep = backoff_base ** attempt + random.uniform(0, 1)
            logger.debug("Retry in %.1fs", sleep)
            time.sleep(sleep)

    logger.error("All %d attempts failed for %s", max_retries, url)
    return None


def _extract_from_next_data(html: str) -> Optional[list[dict]]:
    """Parse the embedded Next.js JSON blob for listing data."""
    try:
        soup = BeautifulSoup(html, "html.parser")
        tag = soup.find("script", {"id": "__NEXT_DATA__"})
        if not tag or not tag.string:
            logger.debug("__NEXT_DATA__ tag not found")
            return None

        data = json.loads(tag.string)
        ads = (
            data
            .get("props", {})
            .get("pageProps", {})
            .get("listing", {})
            .get("listing", {})
            .get("ads", [])
        )

        if not isinstance(ads, list):
            logger.debug("__NEXT_DATA__ ads path not a list")
            return None

        logger.debug("__NEXT_DATA__ yielded %d ads", len(ads))
        return ads

    except (json.JSONDecodeError, AttributeError, KeyError) as exc:
        logger.warning("__NEXT_DATA__ parse failed: %s", exc)
        return None


def _parse_price_text(text: str) -> Optional[float]:
    """Parse '2 500 zł' or '2500 zł' to float 2500.0."""
    digits = re.sub(r"[^\d]", "", text)
    if digits:
        try:
            return float(digits)
        except ValueError:
            pass
    return None


def _extract_fallback_bs4(html: str) -> list[dict]:
    """Best-effort BS4 fallback when __NEXT_DATA__ path changes."""
    listings = []
    try:
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.find_all("div", attrs={"data-cy": "l-card"})
        logger.debug("BS4 fallback found %d cards", len(cards))

        for card in cards:
            lid = card.get("data-id", "")
            title_tag = card.find("h6") or card.find("h4") or card.find("h3")
            title = title_tag.get_text(strip=True) if title_tag else ""
            link_tag = card.find("a", href=True)
            href = link_tag["href"] if link_tag else ""
            if href and not href.startswith("http"):
                href = "https://www.olx.pl" + href

            price_tag = card.find("p", attrs={"data-testid": "ad-price"})
            price_text = price_tag.get_text(strip=True) if price_tag else ""
            price_val = _parse_price_text(price_text)

            if lid or href:
                listings.append({
                    "id": lid,
                    "title": title,
                    "url": href,
                    "price": {"value": price_val, "label": price_text},
                    "params": [],
                    "description": "",
                })
    except Exception as exc:
        logger.error("BS4 fallback extraction failed: %s", exc)
    return listings


def _normalize(raw: dict) -> dict:
    """Guarantee a consistent schema for all downstream modules."""
    url = raw.get("url", "")
    if url and not url.startswith("http"):
        url = "https://www.olx.pl" + url

    # OLX may expose description as 'description' or 'shortDescription'
    description = (
        raw.get("description")
        or raw.get("shortDescription")
        or ""
    )

    return {
        "id": str(raw.get("id", "")),
        "title": str(raw.get("title", "")).strip(),
        "url": url,
        "price": raw.get("price") or {},
        "params": raw.get("params") or [],
        "description": str(description).strip(),
    }


def fetch_listings(
    product_key: str,
    search_query: str,
    delay_range: tuple[float, float] = (2.0, 4.0),
    max_retries: int = 3,
    backoff_base: float = 2.0,
) -> list[dict]:
    """
    Fetch and normalize OLX listings for one search query.

    Pass delay_range=(0, 0) for the first product to skip the leading sleep.
    Returns an empty list on any failure — callers should handle this gracefully.
    """
    url = OLX_SEARCH_URL.format(query=search_query)

    delay = random.uniform(*delay_range)
    if delay > 0:
        logger.debug("Sleeping %.1fs before fetching '%s'", delay, product_key)
        time.sleep(delay)

    html = _fetch_page(url, max_retries=max_retries, backoff_base=backoff_base)
    if html is None:
        logger.warning("No HTML retrieved for '%s'", product_key)
        return []

    raw = _extract_from_next_data(html)
    method = "__NEXT_DATA__"

    if raw is None:
        logger.info("Primary extraction failed for '%s'; trying BS4 fallback", product_key)
        raw = _extract_fallback_bs4(html)
        method = "BS4 fallback"

    if not raw:
        logger.warning("No listings extracted for '%s' via %s", product_key, method)
        return []

    logger.info("Extracted %d listings for '%s' via %s", len(raw), product_key, method)
    return [_normalize(r) for r in raw]
