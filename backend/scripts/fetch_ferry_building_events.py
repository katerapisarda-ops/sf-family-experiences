"""
fetch_ferry_building_events.py
Scrapes events from ferrybuildingmarketplace.com,
classifies with Claude Haiku, writes to Supabase as 'pending_review'.

Usage:
  python fetch_ferry_building_events.py [--days-ahead N] [--dry-run]
"""

import os
import re
import json
import html
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

EVENTS_URL = "https://www.ferrybuildingmarketplace.com/events/"
SF_TZ = ZoneInfo("America/Los_Angeles")
LAT = 37.7955
LNG = -122.3937

SYSTEM_PROMPT = """You are a quality filter for a curated family activity app in San Francisco.
You will receive an event from the Ferry Building Marketplace.

Your job is TWO things:
1. QUALITY + FAMILY CHECK — Should a busy SF parent with young kids (0-9) know about this?
   - INCLUDE:
     * Markets, outdoor activations, community events open to all
     * Food, cooking, or tasting events families can attend together
     * Seasonal events, holiday activities, cultural celebrations
     * Kid-friendly performances, crafts, or activities
     * Board games, interactive activities in the marketplace
   - SKIP:
     * Adult-only tastings (wine, cocktail, beer) without family context
     * Corporate or private events
     * Evening dinner events clearly not suitable for young kids
     * Anything with no family-relevant angle
   - When in doubt about a daytime public marketplace event, INCLUDE it

2. CLASSIFY — If including, assign taxonomy tags.

TAXONOMY:
- interest_tags (pick 1-3): nature, arts, sports, food, music, science, history, animals, water, community
- vibe_tags (pick 1-3): chill, adventurous, educational, social, creative, outdoorsy, foodie, cultural
- best_age_range (pick all that apply): Baby (0-1), Toddler (1-3), Preschool (3-5), Older Kids (6-9), All Ages
- cost_tier: "free" if free or included with entry, "paid" if ticketed
- indoor_outdoor: "indoor" for marketplace events, "outdoor" for plaza/waterfront
- weather_sensitivity: "none" for indoor events, "soft_avoid_rain" for outdoor

3. DESCRIBE — Write a 1-2 sentence description a parent would find useful.
   - Be specific about what kids can do and why it's worth the trip
   - Avoid generic filler

4. RESERVATION — If the event mentions reservations, registration, sign-up, tickets required, or
   limited/reserved space, set requires_reservation=true and write a brief reservation_note.
   Otherwise requires_reservation=false and reservation_note=null.

Respond ONLY with valid JSON:
{
  "include": true or false,
  "confidence": 0.0 to 1.0,
  "skip_reason": "only if include=false",
  "description": "1-2 sentence description (only if include=true)",
  "emoji": "single emoji that best represents this activity (only if include=true)",
  "interest_tags": [...],
  "vibe_tags": [...],
  "best_age_range": [...],
  "cost_tier": "free" or "paid",
  "indoor_outdoor": "indoor" or "outdoor",
  "weather_sensitivity": "none" or "soft_avoid_rain",
  "requires_reservation": true or false,
  "reservation_note": "short practical note, or null",
  "reasoning": "one sentence why this is or isn't worth including"
}"""


def parse_eventon_date(dt_str: str) -> datetime:
    """Parse EventON's non-standard date '2026-7-3T17:00+0:00' → datetime in SF time.

    EventON incorrectly labels local SF times as +0:00 (UTC). We strip the offset
    and attach the SF timezone so '17:00+0:00' becomes 5 PM Pacific, not 10 AM Pacific.
    """
    m = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})T(\d{1,2}):(\d{2})', dt_str)
    if not m:
        raise ValueError(f"Cannot parse date: {dt_str!r}")
    year, month, day, hour, minute = m.groups()
    return datetime(int(year), int(month), int(day), int(hour), int(minute),
                    tzinfo=ZoneInfo("America/Los_Angeles"))


def clean_html(text: str) -> str:
    if not text:
        return ""
    unescaped = html.unescape(text)
    return BeautifulSoup(unescaped, "lxml").get_text(" ", strip=True)[:500]


def fetch_events(days_ahead: int) -> list[dict]:
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)
    events = []
    seen_ids = set()

    resp = requests.get(
        EVENTS_URL,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"},
        timeout=15,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    for div in soup.select("div.eventon_list_event"):
        div_id = div.get("id", "")
        if not div_id or div_id in seen_ids:
            continue
        seen_ids.add(div_id)

        ld_script = div.select_one('script[type="application/ld+json"]')
        if not ld_script or not ld_script.string:
            continue

        try:
            data = json.loads(ld_script.string)
        except json.JSONDecodeError:
            continue

        name = data.get("name", "").strip()
        source_url = data.get("url", "")
        raw_start = data.get("startDate", "")
        raw_end = data.get("endDate", "")
        description = clean_html(data.get("description", ""))

        if not name or not raw_start:
            continue

        try:
            starts_at = parse_eventon_date(raw_start)
            ends_at = parse_eventon_date(raw_end) if raw_end else None
        except Exception:
            continue

        event_date = starts_at.date()
        if event_date < today or event_date > cutoff:
            continue

        events.append({
            "source": "ferry_building",
            "source_id": div_id,
            "source_url": source_url,
            "name": name,
            "raw_description": description,
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat() if ends_at else None,
        })

    return events


def classify(ai_client: anthropic.Anthropic, event: dict) -> dict:
    prompt = (
        f"Event: {event['name']}\n"
        f"Date: {event.get('starts_at', 'unknown')}\n"
        f"Venue: Ferry Building Marketplace, San Francisco (waterfront marketplace)\n"
        f"Description: {event.get('raw_description', '')}"
    )
    for attempt in range(2):
        msg = ai_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            system=SYSTEM_PROMPT,
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
                try:
                    return json.loads(raw[start:end])
                except json.JSONDecodeError:
                    pass
        if attempt == 0:
            time.sleep(2)
    return {"include": False, "confidence": 0.0, "skip_reason": "parse error"}


def build_row(event: dict, cl: dict) -> dict:
    return {
        "name": event["name"],
        "emoji": cl.get("emoji") or None,
        "description": cl.get("description") or None,
        "address": "1 Ferry Building, San Francisco, CA 94105",
        "neighborhood": "Embarcadero",
        "lat": LAT,
        "lng": LNG,
        "starts_at": event["starts_at"],
        "ends_at": event.get("ends_at"),
        "source": event["source"],
        "source_id": event["source_id"],
        "source_url": event["source_url"],
        "interest_tags": cl.get("interest_tags", []),
        "vibe_tags": cl.get("vibe_tags", []),
        "best_age_range": cl.get("best_age_range", []),
        "cost_tier": cl.get("cost_tier", "free"),
        "indoor_outdoor": cl.get("indoor_outdoor", "indoor"),
        "weather_sensitivity": cl.get("weather_sensitivity", "none"),
        "requires_reservation": cl.get("requires_reservation") or False,
        "reservation_note": cl.get("reservation_note") or None,
        "kid_friendly": True,
        "status": "pending_review",
        "ai_confidence": cl.get("confidence"),
        "ai_raw_response": cl,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days-ahead", type=int, default=17)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"Fetching Ferry Building events (next {args.days_ahead} days)...\n")
    events = fetch_events(args.days_ahead)
    print(f"Found {len(events)} events in window.\n")

    if not events:
        print("Nothing to process.")
        return

    ai_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    db_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if not args.dry_run else None

    if db_client:
        source_ids = [e["source_id"] for e in events]
        # Only skip events that are approved or pending_review — rejected events
        # may have been rejected due to bad data and should be re-fetched.
        existing = (
            db_client.table("events")
            .select("source_id")
            .eq("source", "ferry_building")
            .in_("source_id", source_ids)
            .in_("status", ["approved", "pending_review"])
            .execute()
        )
        existing_ids = {r["source_id"] for r in existing.data}
        events = [e for e in events if e["source_id"] not in existing_ids]
        if existing_ids:
            print(f"Skipping {len(existing_ids)} already-approved/pending events.\n")
        # Delete stale rejected rows so we can re-insert with correct data
        rejected_ids = [e["source_id"] for e in events]
        if rejected_ids:
            db_client.table("events").delete().eq("source", "ferry_building").in_("source_id", rejected_ids).eq("status", "rejected").execute()

    print(f"Classifying {len(events)} events with Claude Haiku...\n")
    rows, skipped = [], 0

    for event in events:
        cl = classify(ai_client, event)
        if not cl.get("include"):
            print(f"  ✗ SKIP  {event['name'][:55]:<55} — {cl.get('skip_reason', '')[:50]}")
            skipped += 1
        else:
            row = build_row(event, cl)
            rows.append(row)
            print(f"  ✓ KEEP  {event['name'][:55]:<55} | {', '.join(row['interest_tags'])}")

    print(f"\n{'='*60}")
    print(f"Kept: {len(rows)}  |  Skipped: {skipped}")

    if args.dry_run or not rows:
        print("\n--- dry run, nothing written ---" if args.dry_run else "\nNothing to write.")
        return

    print("\nWriting to Supabase...")
    db_client.table("events").upsert(rows, on_conflict="source,source_id", ignore_duplicates=True).execute()
    print(f"Done. {len(rows)} events written with status='pending_review'.")


if __name__ == "__main__":
    main()
