"""
main.py — OLX Deal Notifier entry point.

Run once per invocation; scheduling is handled externally by cron.
Usage: python main.py
"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load .env before any module reads os.getenv
_ROOT = Path(__file__).parent
load_dotenv(_ROOT / ".env")

import condition_llm
import db
import deal_checker
import notifier
import scraper


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def setup_logging() -> None:
    log_dir = Path(os.getenv("LOG_DIR", str(_ROOT / "logs")))
    log_dir.mkdir(parents=True, exist_ok=True)

    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(fmt, datefmt=datefmt))

    fh = logging.FileHandler(log_dir / "app.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(fmt, datefmt=datefmt))

    root.addHandler(ch)
    root.addHandler(fh)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    path = _ROOT / "config.json"
    if not path.exists():
        raise FileNotFoundError(f"config.json not found at {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Env validation
# ---------------------------------------------------------------------------

def validate_env() -> None:
    logger = logging.getLogger(__name__)
    required = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "OPENROUTER_API_KEY")
    missing = [v for v in required if not os.getenv(v)]
    if missing:
        logger.error(
            "Missing required environment variables: %s. "
            "Copy .env.example → .env and fill in the values.",
            ", ".join(missing),
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_created(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _loosest_threshold(thresholds: dict) -> float:
    """Return the loosest (largest) fraction of new_price across all condition tiers."""
    return max(thresholds.values()) if thresholds else 1.0


def _parse_label(label: str) -> tuple[str, str]:
    """
    Split an LLM label into (kind, condition).
      ("match", "like_new")   for match_<cond>
      ("wrong_model", "")
      ("irrelevant", "")
    """
    if label.startswith("match_"):
        return "match", label[len("match_"):]
    return label, ""


# ---------------------------------------------------------------------------
# Per-product processing
# ---------------------------------------------------------------------------

def process_product(
    product_key: str,
    product_cfg: dict,
    thresholds: dict,
    scraper_cfg: dict,
    condition_model: str,
    fallback_models: list[str],
    is_first: bool,
    cutoff: datetime,
) -> dict:
    logger = logging.getLogger(__name__)
    display_name = product_cfg["display_name"]
    search_query = product_cfg["search_query"]
    new_price_pln = product_cfg["new_price_pln"]

    max_price = new_price_pln * _loosest_threshold(thresholds)

    logger.info(
        "--- %s (new: %.0f PLN, max deal price: %.0f PLN) ---",
        display_name, new_price_pln, max_price,
    )

    delay_range = (0.0, 0.0) if is_first else (
        scraper_cfg.get("request_delay_min_seconds", 2),
        scraper_cfg.get("request_delay_max_seconds", 4),
    )

    listings = scraper.fetch_listings(
        product_key=product_key,
        search_query=search_query,
        max_price=max_price,
        delay_range=delay_range,
        max_retries=scraper_cfg.get("max_retries", 3),
        backoff_base=scraper_cfg.get("retry_backoff_base_seconds", 2),
    )

    result = dict(
        product_key=product_key,
        display_name=display_name,
        listings_fetched=len(listings),
        deals_found=0,
        notifications_sent=0,
        skipped_old=0,
        skipped_already_seen=0,
        skipped_wrong_model=0,
        skipped_irrelevant=0,
        not_a_deal=0,
        errors=0,
    )

    for listing in listings:
        listing_id = listing["id"]
        title = listing["title"]
        url = listing["url"]

        if not listing_id:
            result["errors"] += 1
            continue

        # Client-side cutoff (sort doesn't work, so we filter after fetch)
        created = _parse_created(listing.get("created_time", ""))
        if created and created < cutoff:
            result["skipped_old"] += 1
            continue

        if db.is_seen(listing_id):
            result["skipped_already_seen"] += 1
            continue

        # LLM-as-judge: model match + condition in one call
        label = condition_llm.classify_listing(
            listing_id=listing_id,
            display_name=display_name,
            title=title,
            description=listing.get("description", ""),
            model=condition_model,
            fallback_models=fallback_models,
        )

        kind, condition = _parse_label(label)

        if kind == "wrong_model":
            logger.debug("Skipping '%s' — LLM flagged wrong_model", title)
            result["skipped_wrong_model"] += 1
            continue

        if kind == "irrelevant":
            logger.debug("Skipping '%s' — LLM flagged irrelevant", title)
            result["skipped_irrelevant"] += 1
            continue

        if kind != "match":
            # Unexpected — treat as unknown match
            condition = "unknown"

        try:
            qualifies, discount_pct = deal_checker.is_deal(
                listing=listing,
                new_price_pln=new_price_pln,
                condition=condition,
                thresholds=thresholds,
            )
        except Exception as exc:
            logger.error("Error evaluating listing %s: %s", listing_id, exc)
            result["errors"] += 1
            continue

        if not qualifies:
            result["not_a_deal"] += 1
            continue

        result["deals_found"] += 1
        price_pln = deal_checker.extract_price(listing) or 0.0

        logger.info(
            "DEAL: %s | %.0f PLN | %.1f%% off | condition=%s",
            title, price_pln, discount_pct, condition,
        )

        sent = notifier.send_deal_notification(
            display_name=display_name,
            title=title,
            price_pln=price_pln,
            discount_pct=discount_pct,
            condition=condition,
            url=url,
        )

        if sent:
            result["notifications_sent"] += 1
            db.mark_seen(
                listing_id=listing_id,
                url=url,
                title=title,
                price_pln=price_pln,
                product_key=product_key,
                condition=condition,
            )
        else:
            logger.error("Notification failed for %s — will retry on next run.", listing_id)
            result["errors"] += 1

    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    setup_logging()
    logger = logging.getLogger(__name__)
    logger.info("========== OLX Deal Notifier starting ==========")

    validate_env()

    try:
        config = load_config()
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logging.getLogger(__name__).error("Failed to load config.json: %s", exc)
        sys.exit(1)

    db.init_db()

    products = config.get("products", {})
    thresholds = config.get("deal_thresholds", {})
    scraper_cfg = config.get("scraper", {})
    condition_model = config.get("condition_model", "openai/gpt-oss-120b:free")
    fallback_models = config.get("condition_model_fallbacks", [])

    if not products:
        logger.error("No products defined in config.json")
        sys.exit(1)

    # Record run start time before fetching — this becomes the cutoff for the next run
    run_started_at = datetime.now(timezone.utc)

    last_run = db.get_last_run()
    if last_run:
        cutoff = last_run
        logger.info("Filtering listings posted after %s", cutoff.isoformat())
    else:
        cutoff = run_started_at - timedelta(hours=18)
        logger.info("First run — using cutoff of 18 hours ago (%s)", cutoff.isoformat())

    summary = []
    for idx, (product_key, product_cfg) in enumerate(products.items()):
        try:
            result = process_product(
                product_key=product_key,
                product_cfg=product_cfg,
                thresholds=thresholds,
                scraper_cfg=scraper_cfg,
                condition_model=condition_model,
                fallback_models=fallback_models,
                is_first=(idx == 0),
                cutoff=cutoff,
            )
            summary.append(result)
        except Exception as exc:
            logger.error("Unhandled error for '%s': %s", product_key, exc, exc_info=True)
            summary.append({
                "product_key": product_key, "listings_fetched": 0,
                "deals_found": 0, "notifications_sent": 0,
                "skipped_old": 0, "skipped_already_seen": 0,
                "skipped_wrong_model": 0, "skipped_irrelevant": 0,
                "not_a_deal": 0, "errors": 1,
            })

    db.set_last_run(run_started_at)

    logger.info("========== Run Summary ==========")
    total_deals = total_notified = 0
    for r in summary:
        total_deals += r.get("deals_found", 0)
        total_notified += r.get("notifications_sent", 0)
        logger.info(
            "  %-22s fetched=%-3d old=%-3d seen=%-3d wrong=%-2d irrel=%-2d not_deal=%-3d deal=%-2d notif=%-2d err=%d",
            r.get("product_key", "?"),
            r.get("listings_fetched", 0),
            r.get("skipped_old", 0),
            r.get("skipped_already_seen", 0),
            r.get("skipped_wrong_model", 0),
            r.get("skipped_irrelevant", 0),
            r.get("not_a_deal", 0),
            r.get("deals_found", 0),
            r.get("notifications_sent", 0),
            r.get("errors", 0),
        )
    logger.info("Total: %d deal(s), %d notification(s) sent.", total_deals, total_notified)
    logger.info("========== Done ==========")


if __name__ == "__main__":
    main()
