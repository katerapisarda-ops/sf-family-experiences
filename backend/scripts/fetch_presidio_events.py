"""
fetch_presidio_events.py
Fetches events from the Presidio via WordPress REST API,
classifies with Claude Haiku (quality + taxonomy + description),
writes approved events to Supabase as 'pending_review'.

Usage:
  python fetch_presidio_events.py [--days-ahead N] [--dry-run]
"""

import os
import json
import time
import argparse
from datetime import date, datetime, timedelta
from dotenv import load_dotenv
import requests
import anthropic
from supabase import create_client

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../.env"))

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

API_URL = "https://wp.presidio.gov/wp-json/tribe/events/v1/events"

DEFAULT_LAT = 37.7989
DEFAULT_LNG = -122.4662

SYSTEM_PROMPT = """You are a quality filter for a curated family activity app in San Francisco.
You will receive an event from The Presidio, a national park in SF.

Your job is TWO things:
1. QUALITY + FAMILY CHECK — Should a busy SF parent with young kids (0-9) know about this?
   - INCLUDE:
     * Kids & Family events, festivals, Easter egg hunts, holiday celebrations
     * Circus, acrobatic, or family-friendly theater performances (any start time)
     * Nature walks or outdoor programs families can join
     * Free outdoor concerts, markets, or community events open to the public
     * Film screenings or cultural events suitable for families
     * Star parties or science events
     * Any public community event open to all, regardless of the organizer (including church or nonprofit events)
   - SKIP:
     * History talks, ranger talks, campfire talks — geared toward older audiences, not young kids
     * Volunteer stewardship/habitat work sessions (not suitable for young kids)
     * Adult-only concerts or performances with no family angle
     * Golf events, adult fitness classes, bar/restaurant events
     * Board meetings or administrative events
     * Events clearly not family-relevant
   - When in doubt about a kids-adjacent outdoor event, INCLUDE it

2. CLASSIFY — If including, assign taxonomy tags.

TAXONOMY:
- interest_tags (pick 1-3): nature, arts, sports, food, music, science, history, animals, water, community
- vibe_tags (pick 1-3): chill, adventurous, educational, social, creative, outdoorsy, foodie, cultural
- best_age_range (pick all that apply): Baby (0-1), Toddler (1-3), Preschool (3-5), Older Kids (6-9), All Ages
- cost_tier: "free" if free, "paid" if ticketed
- indoor_outdoor: "outdoor", "indoor", or "both"
- weather_sensitivity: "soft_avoid_rain" for outdoor, "none" for indoor

3. DESCRIBE — If including, write a short 1-2 sentence description a parent would find useful.
   - Be specific: what will kids experience, what makes it worth the trip
   - Avoid generic filler

Respond ONLY with valid JSON:
{
  "include": true or false,
  "confidence": 0.0 to 1.0,
  "skip_reason": "only if include=false",
  "description": "1-2 sentence description (only if include=true)",
  "emoji": "single emoji that best represents this specific activity (only if include=true)",
  "interest_tags": [...],
  "vibe_tags": [...],
  "best_age_range": [...],
  "cost_tier": "free" or "paid",
  "indoor_outdoor": "outdoor",
  "weather_sensitivity": "soft_avoid_rain"
}"""


def fetch_events(days_ahead: int) -> list[dict]:
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)

    resp = requests.get(
        API_URL,
        params={
            "per_page": 100,
            "start_date": today.isoformat(),
            "end_date": cutoff.isoformat(),
        },
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    raw_events = data.get("events", [])

    events = []
    for e in raw_events:
        venue = e.get("venue") or {}
        lat = venue.get("geo_lat") or DEFAULT_LAT
        lng = venue.get("geo_lng") or DEFAULT_LNG
        address_parts = [
            venue.get("address", ""),
            venue.get("city", "San Francisco"),
            venue.get("stateprovince", "CA"),
            venue.get("zip", ""),
        ]
        address = ", ".join(p for p in address_parts if p) or "The Presidio, San Francisco, CA"

        # Convert to float if string
        try:
            lat = float(lat) if lat else DEFAULT_LAT
            lng = float(lng) if lng else DEFAULT_LNG
        except (ValueError, TypeError):
            lat, lng = DEFAULT_LAT, DEFAULT_LNG

        events.append({
            "source": "presidio",
            "source_id": e.get("slug") or str(e.get("id")),
            "source_url": e.get("url", ""),
            "name": e.get("title", ""),
            "raw_description": e.get("excerpt", "") or e.get("description", ""),
            "categories": [c.get("name") for c in e.get("categories", [])],
            "cost": e.get("cost", ""),
            "address": address,
            "lat": lat,
            "lng": lng,
            "starts_at": e.get("utc_start_date", "").replace(" ", "T") + "+00:00" if e.get("utc_start_date") else None,
            "ends_at": e.get("utc_end_date", "").replace(" ", "T") + "+00:00" if e.get("utc_end_date") else None,
        })

    return events


def to_pacific(utc_iso: str) -> str:
    """Convert a UTC ISO string to a Pacific-time display string for Claude."""
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(utc_iso).astimezone(ZoneInfo("America/Los_Angeles"))
        return dt.strftime("%a, %b %-d at %-I:%M %p PT")
    except Exception:
        return utc_iso


def classify(ai_client: anthropic.Anthropic, event: dict) -> dict:
    cats = ", ".join(event.get("categories", [])) or "none"
    from bs4 import BeautifulSoup
    raw_desc = BeautifulSoup(event.get("raw_description", ""), "lxml").get_text(" ", strip=True)[:300]
    starts_display = to_pacific(event["starts_at"]) if event.get("starts_at") else "unknown"
    prompt = (
        f"Event: {event['name']}\n"
        f"Date: {starts_display}\n"
        f"Categories: {cats}\n"
        f"Cost: {event.get('cost') or 'unknown'}\n"
        f"Description: {raw_desc}"
    )
    msg = ai_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
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
            return json.loads(raw[start:end])
        raise


def build_row(event: dict, cl: dict) -> dict:
    return {
        "name": event["name"],
        "emoji": cl.get("emoji") or None,
        "description": cl.get("description") or None,
        "address": event["address"],
        "neighborhood": "Presidio",
        "lat": event["lat"],
        "lng": event["lng"],
        "starts_at": event["starts_at"],
        "ends_at": event.get("ends_at"),
        "source": event["source"],
        "source_id": event["source_id"],
        "source_url": event["source_url"],
        "interest_tags": cl.get("interest_tags", []),
        "vibe_tags": cl.get("vibe_tags", []),
        "best_age_range": cl.get("best_age_range", []),
        "cost_tier": cl.get("cost_tier", "free"),
        "indoor_outdoor": cl.get("indoor_outdoor", "outdoor"),
        "weather_sensitivity": cl.get("weather_sensitivity", "soft_avoid_rain"),
        "kid_friendly": True,
        "status": "pending_review",
        "ai_confidence": cl.get("confidence"),
        "ai_raw_response": cl,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days-ahead", type=int, default=14)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"Fetching Presidio events (next {args.days_ahead} days)...\n")
    events = fetch_events(args.days_ahead)
    print(f"Found {len(events)} events.\n")

    if not events:
        print("Nothing to process.")
        return

    ai_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    db_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if not args.dry_run else None

    # Skip already-existing events
    if db_client:
        source_ids = [e["source_id"] for e in events]
        existing = db_client.table("events").select("source_id").eq("source", "presidio").in_("source_id", source_ids).execute()
        existing_ids = {r["source_id"] for r in existing.data}
        events = [e for e in events if e["source_id"] not in existing_ids]
        if existing_ids:
            print(f"Skipping {len(existing_ids)} already-existing events.\n")

    SKIP_KEYWORDS = [
        "history talk", "campfire talk", "campfire talks", "habitat steward",
        "forest steward", "tunnel tops steward", "volunteering", "volunteer:",
        "golf", "bingo", "pub quiz", "board of directors", "park management",
        "ironwoods bar", "wings & swings",
    ]

    def is_obvious_skip(name: str) -> bool:
        n = name.lower()
        return any(kw in n for kw in SKIP_KEYWORDS)

    pre_filtered = [e for e in events if not is_obvious_skip(e["name"])]
    pre_skipped = len(events) - len(pre_filtered)
    if pre_skipped:
        print(f"Pre-filtered {pre_skipped} obvious skips.\n")
    events = pre_filtered

    print(f"Classifying {len(events)} events with Claude Haiku...\n")
    included, skipped = [], 0

    for event in events:
        cl = classify(ai_client, event)
        if not cl.get("include"):
            reason = cl.get("skip_reason", "quality filter")
            print(f"  ✗ SKIP  {event['name'][:55]:<55} — {reason[:50]}")
            skipped += 1
        else:
            row = build_row(event, cl)
            included.append(row)
            print(f"  ✓ KEEP  {event['name'][:55]:<55} | {', '.join(row['interest_tags'])}")

    print(f"\n{'='*60}")
    print(f"Kept: {len(included)}  |  Skipped: {skipped}")

    if args.dry_run or not included:
        print("\n--- dry run, nothing written ---" if args.dry_run else "\nNothing to write.")
        return

    print("\nWriting to Supabase...")
    db_client.table("events").upsert(included, on_conflict="source,source_id", ignore_duplicates=True).execute()
    print(f"Done. {len(included)} events written with status='pending_review'.")


if __name__ == "__main__":
    main()
