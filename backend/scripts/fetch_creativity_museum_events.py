"""
fetch_creativity_museum_events.py
Scrapes the current show from creativity.org/theater/,
uses Claude to extract performance dates, writes to Supabase as 'pending_review'.

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

THEATER_URL = "https://creativity.org/theater/"
SF_TZ = ZoneInfo("America/Los_Angeles")
LAT = 37.7847
LNG = -122.4028

EXTRACT_PROMPT = """You are parsing a Children's Creativity Museum theater page.

Extract the current show's information and return it as JSON.
Today's date is {today}.

Return ONLY valid JSON in this format:
{{
  "show_name": "full title of the show",
  "description": "2-3 sentence description a parent would find useful — be specific about what kids will experience",
  "emoji": "single emoji that best represents this show",
  "age_range": "recommended age range string",
  "cost_tier": "free" or "paid",
  "requires_reservation": true or false,
  "reservation_note": "practical note about tickets/registration, or null",
  "performances": [
    {{"date": "YYYY-MM-DD", "starts_at": "HH:MM", "ends_at": "HH:MM or null"}},
    ...
  ]
}}

For performances:
- Only include dates that are >= today ({today}) and within the next {days_ahead} days
- Use 24-hour time format (e.g. "11:00", "14:00")
- If a show has two times on one day, include two entries
- If end time or duration is not mentioned, set ends_at to null

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
    return main.get_text(" ", strip=True)[:4000] if main else ""


def extract_show(ai_client: anthropic.Anthropic, page_text: str, days_ahead: int) -> dict | None:
    today = date.today().isoformat()
    prompt = EXTRACT_PROMPT.format(today=today, days_ahead=days_ahead, page_text=page_text)

    msg = ai_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end])
        raise


def build_rows(show: dict, source_url: str) -> list[dict]:
    rows = []
    today = date.today()

    # Map best_age_range from show's age_range string
    age_str = (show.get("age_range") or "").lower()
    if "all ages" in age_str or not age_str:
        best_age_range = ["All Ages"]
    else:
        best_age_range = ["Toddler (1-3)", "Preschool (3-5)", "Older Kids (6-9)", "All Ages"]

    for perf in show.get("performances", []):
        perf_date = perf.get("date", "")
        starts_time = perf.get("starts_at", "")
        ends_time = perf.get("ends_at")

        if not perf_date or not starts_time:
            continue

        try:
            starts_at = datetime.fromisoformat(f"{perf_date}T{starts_time}:00").replace(tzinfo=SF_TZ)
            ends_at = datetime.fromisoformat(f"{perf_date}T{ends_time}:00").replace(tzinfo=SF_TZ) if ends_time else None
        except Exception:
            continue

        if starts_at.date() < today:
            continue

        source_id = f"ccm_{perf_date}_{starts_time.replace(':', '')}"

        rows.append({
            "name": show["show_name"],
            "emoji": show.get("emoji") or "🎪",
            "description": show.get("description") or None,
            "address": "221 4th St, San Francisco, CA 94103",
            "neighborhood": "SoMa",
            "lat": LAT,
            "lng": LNG,
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat() if ends_at else None,
            "source": "creativity_museum",
            "source_id": source_id,
            "source_url": source_url,
            "interest_tags": ["arts", "music"],
            "vibe_tags": ["creative", "cultural", "educational"],
            "best_age_range": best_age_range,
            "cost_tier": show.get("cost_tier", "paid"),
            "indoor_outdoor": "indoor",
            "weather_sensitivity": "none",
            "requires_reservation": show.get("requires_reservation") or False,
            "reservation_note": show.get("reservation_note") or None,
            "kid_friendly": True,
            "status": "pending_review",
            "ai_confidence": 0.95,
            "ai_raw_response": show,
        })

    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days-ahead", type=int, default=17)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"Fetching Children's Creativity Museum theater events (next {args.days_ahead} days)...\n")

    page_text = fetch_page_text()
    if not page_text:
        print("Could not fetch theater page.")
        return

    ai_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    show = extract_show(ai_client, page_text, args.days_ahead)

    if not show or not show.get("performances"):
        print("No upcoming performances found.")
        return

    print(f"Show: {show.get('show_name')}")
    print(f"Performances found: {len(show.get('performances', []))}\n")

    rows = build_rows(show, THEATER_URL)
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
