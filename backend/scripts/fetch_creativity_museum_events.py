"""
fetch_creativity_museum_events.py
Scrapes the PLAY Theatre Company season page from creativity.org/play-theatre/
(a season listing of multiple shows, each with a date range + recurring weekday
schedule rather than individual dates), uses Claude to extract and expand
performances into concrete dates, writes to Supabase as 'pending_review'.

Usage:
  python fetch_creativity_museum_events.py [--days-ahead N] [--dry-run]
"""

import os
import json
import time
import argparse
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup
import anthropic
from supabase import create_client

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../.env"))

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

THEATER_URL = "https://creativity.org/play-theatre/"
SF_TZ = ZoneInfo("America/Los_Angeles")
LAT = 37.7847
LNG = -122.4028

EXTRACT_PROMPT = """You are parsing the Children's Creativity Museum's PLAY Theatre Company season page.
The page lists several shows, each with a date range and a recurring weekday schedule
(e.g. "September 26 - October 15, 2026, Saturdays & Sundays at 10:30am and 2pm") rather
than individual performance dates.

Today's date is {today}. Only extract shows that have at least one performance in the
window from today through {cutoff} ({days_ahead} days ahead).

Return ONLY valid JSON in this format:
{{
  "shows": [
    {{
      "show_name": "full title of the show",
      "description": "2-3 sentence description a parent would find useful — be specific about what kids will experience",
      "emoji": "single emoji that best represents this show",
      "age_range": "recommended age range string",
      "cost_tier": "free" or "paid",
      "requires_reservation": true,
      "reservation_note": "practical note about tickets/registration, or null",
      "date_range_start": "YYYY-MM-DD",
      "date_range_end": "YYYY-MM-DD",
      "weekdays": ["Saturday", "Sunday"],
      "showtimes": ["10:30", "14:00"]
    }}
  ]
}}

Rules:
- weekdays: full weekday names the show runs on, from its stated schedule
- showtimes: 24-hour "HH:MM" format, one entry per showtime listed per day
- Only include shows whose date_range_end is >= today
- If a show requires tickets (nearly all do here), requires_reservation=true

Page content:
{page_text}"""


def fetch_page_text() -> str:
    resp = requests.get(
        THEATER_URL,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"},
        timeout=15,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    main = soup.select_one("main") or soup.select_one(".entry-content") or soup.body
    return main.get_text(" ", strip=True)[:6000] if main else ""


def extract_shows(ai_client: anthropic.Anthropic, page_text: str, days_ahead: int) -> list[dict]:
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)
    prompt = EXTRACT_PROMPT.format(
        today=today.isoformat(), cutoff=cutoff.isoformat(), days_ahead=days_ahead, page_text=page_text
    )

    msg = ai_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1536,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(raw[start:end])
        else:
            raise
    return parsed.get("shows", [])


WEEKDAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def expand_performances(show: dict, days_ahead: int) -> list[tuple[date, str]]:
    """Turn a date-range + weekday-schedule show into concrete (date, time) performances."""
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)

    try:
        range_start = date.fromisoformat(show["date_range_start"])
        range_end = date.fromisoformat(show["date_range_end"])
    except (KeyError, ValueError):
        return []

    weekdays = {WEEKDAY_NAMES.index(w.lower()) for w in show.get("weekdays", []) if w.lower() in WEEKDAY_NAMES}
    showtimes = show.get("showtimes", [])
    if not weekdays or not showtimes:
        return []

    window_start = max(range_start, today)
    window_end = min(range_end, cutoff)

    performances = []
    d = window_start
    while d <= window_end:
        if d.weekday() in weekdays:
            for t in showtimes:
                performances.append((d, t))
        d += timedelta(days=1)
    return performances


def build_rows(show: dict, source_url: str, days_ahead: int) -> list[dict]:
    rows = []

    # Map best_age_range from show's age_range string
    age_str = (show.get("age_range") or "").lower()
    if "all ages" in age_str or not age_str:
        best_age_range = ["All Ages"]
    else:
        best_age_range = ["Toddler (1-3)", "Preschool (3-5)", "Older Kids (6-9)", "All Ages"]

    for perf_date, starts_time in expand_performances(show, days_ahead):
        try:
            starts_at = datetime.fromisoformat(f"{perf_date.isoformat()}T{starts_time}:00").replace(tzinfo=SF_TZ)
        except Exception:
            continue

        source_id = f"ccm_{perf_date.isoformat()}_{starts_time.replace(':', '')}"

        rows.append({
            "name": show["show_name"],
            "emoji": show.get("emoji") or "🎪",
            "description": show.get("description") or None,
            "address": "221 4th St, San Francisco, CA 94103",
            "neighborhood": "SoMa",
            "lat": LAT,
            "lng": LNG,
            "starts_at": starts_at.isoformat(),
            "ends_at": None,
            "source": "creativity_museum",
            "source_id": source_id,
            "source_url": source_url,
            "interest_tags": ["arts", "music"],
            "vibe_tags": ["creative", "cultural", "educational"],
            "best_age_range": best_age_range,
            "cost_tier": show.get("cost_tier", "paid"),
            "indoor_outdoor": "indoor",
            "weather_sensitivity": "none",
            "requires_reservation": show.get("requires_reservation") if show.get("requires_reservation") is not None else True,
            "reservation_note": show.get("reservation_note") or None,
            "kid_friendly": True,
            "status": "pending_review",
            "ai_confidence": 0.9,
            "ai_raw_response": show,
        })

    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days-ahead", type=int, default=17)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"Fetching Children's Creativity Museum theater events (next {args.days_ahead} days)...\n")

    try:
        page_text = fetch_page_text()
    except requests.RequestException as e:
        print(f"  ⚠ WARNING: creativity.org unreachable ({type(e).__name__}) — check the site manually")
        return
    if not page_text:
        print("Could not fetch theater page.")
        return

    ai_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    shows = extract_shows(ai_client, page_text, args.days_ahead)

    if not shows:
        print("No upcoming performances found in window.")
        return

    rows = []
    for show in shows:
        show_rows = build_rows(show, THEATER_URL, args.days_ahead)
        print(f"Show: {show.get('show_name')} — {len(show_rows)} performances in window")
        rows.extend(show_rows)
    print()

    if not rows:
        print("No performances within the date window.")
        return

    db_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if not args.dry_run else None

    if db_client:
        source_ids = [r["source_id"] for r in rows]
        existing = db_client.table("events").select("source_id").eq("source", "creativity_museum").in_("source_id", source_ids).execute()
        existing_ids = {r["source_id"] for r in existing.data}
        rows = [r for r in rows if r["source_id"] not in existing_ids]
        if existing_ids:
            print(f"Skipping {len(existing_ids)} already-existing performances.\n")

    for row in rows:
        print(f"  ✓  {row['name'][:50]}  |  {row['starts_at']}")

    print(f"\n{'='*60}")
    print(f"Total: {len(rows)} performances to write")

    if args.dry_run or not rows:
        print("\n--- dry run, nothing written ---" if args.dry_run else "\nNothing to write.")
        return

    print("\nWriting to Supabase...")
    db_client.table("events").upsert(rows, on_conflict="source,source_id", ignore_duplicates=True).execute()
    print(f"Done. {len(rows)} performances written with status='pending_review'.")


if __name__ == "__main__":
    main()
