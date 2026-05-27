# OLX Deal Notifier

Scrapes OLX.pl daily for second-hand DJ/music gear deals and sends Telegram
notifications when a listing is priced well below the new retail price.

**Tracked products**: Akai MPC One+, Akai MPC Live II, Akai MPC Live III, Pioneer DDJ-FLX4

**How deal detection works**:
1. Each listing's title + description is sent to a cheap OpenAI OSS model
2. The model classifies condition on a 5-tier scale (heavily_used → like_new)
3. Better condition = smaller required discount to qualify as a deal

| Condition | Max price vs. new |
|---|---|
| heavily_used | ≤ 35% of new price |
| used | ≤ 50% |
| good | ≤ 60% |
| very_good | ≤ 70% |
| like_new | ≤ 78% |

---

## Requirements

- Python 3.11+
- Telegram bot token (from @BotFather)
- Your Telegram chat ID (from @userinfobot)
- OpenAI API key (for condition classification)

---

## Setup

### 1. Create the virtual environment

```bash
cd ~/Projects/olx-deal-notifier
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
nano .env   # fill in the three required values
```

**Telegram bot token**: open Telegram → @BotFather → `/newbot` → copy the token.

**Chat ID**: start a chat with @userinfobot → it replies with your numeric user ID.
For a group: add the bot to the group, send a message, then use the negative integer group ID.

**OpenAI API key**: from platform.openai.com → API keys.

### 3. Adjust config (optional)

Edit `config.json` to:
- Update reference prices (`new_price_pln`) if market prices change
- Change `condition_model` to swap the OpenAI model
- Tune `deal_thresholds` (lower = stricter, higher = more notifications)

### 4. Test a run

```bash
python main.py
```

First run creates `seen_listings.db` and `logs/app.log`. Check the log for
`DEAL:` lines. If you want to force a test notification, temporarily lower
one threshold in `config.json` (e.g. `"like_new": 0.99`).

---

## Scheduling with Cron

Run once daily at 09:00:

```bash
crontab -e
```

Add:

```
0 9 * * * /home/user/Projects/olx-deal-notifier/.venv/bin/python /home/user/Projects/olx-deal-notifier/main.py >> /home/user/Projects/olx-deal-notifier/logs/cron.log 2>&1
```

Or twice daily (09:00 and 18:00):

```
0 9,18 * * * /home/user/Projects/olx-deal-notifier/.venv/bin/python /home/user/Projects/olx-deal-notifier/main.py >> /home/user/Projects/olx-deal-notifier/logs/cron.log 2>&1
```

The script resolves all paths relative to its own location, so the `cd` prefix is not needed.

---

## File Structure

```
olx-deal-notifier/
├── main.py             # entry point
├── scraper.py          # OLX.pl fetcher + __NEXT_DATA__ parser
├── condition_llm.py    # OpenAI condition classifier
├── deal_checker.py     # deal evaluation arithmetic
├── notifier.py         # Telegram Bot API wrapper
├── db.py               # SQLite deduplication
├── config.json         # products, thresholds, model, scraper settings
├── .env                # secrets — never commit this
├── .env.example        # template
├── requirements.txt
├── logs/
│   ├── app.log         # full debug log (DEBUG level)
│   └── cron.log        # cron stdout/stderr
└── seen_listings.db    # SQLite DB (created on first run)
```

---

## Adding Products

Add a new entry under `"products"` in `config.json`:

```json
"roland_spd_sx": {
  "display_name": "Roland SPD-SX",
  "search_query": "roland-spd-sx",
  "new_price_pln": 3200
}
```

## Adjusting Thresholds

In `config.json`, under `"deal_thresholds"`:
- **Raise** a value (e.g. `"used": 0.60`) to get more notifications (looser filter)
- **Lower** a value (e.g. `"used": 0.40`) to get fewer, stricter alerts

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| No notifications | No qualifying deals yet | Check `logs/app.log` for `DEAL:` lines; lower a threshold to test |
| "Blocked/CAPTCHA" in logs | OLX rate limiting | Increase `request_delay_min_seconds` in config.json |
| "__NEXT_DATA__ parse failed" | OLX changed page structure | BS4 fallback activates automatically; open an issue |
| "Telegram API returned not-ok" | Wrong token or chat ID | Re-check `.env` values |
| Want to re-send a listing | It's in `seen_listings.db` | `sqlite3 seen_listings.db "DELETE FROM seen_listings WHERE listing_id='...';"` |
