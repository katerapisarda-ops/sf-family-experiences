"""
fetch_salesforce_park_events.py
Scrapes events from Salesforce Park (TJPA activities page),
classifies with Claude Haiku, writes to Supabase as 'pending_review'.

Usage:
  python fetch_salesforce_park_events.py [--days-ahead N] [--dry-run]
"""

import os
import json
import time
import re
import argparse
from datetime import date, datetime, timedelta
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup
import anthropic
from supabase import create_client

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../.env"))

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

ACTIVITIES_URL = "https://www.tjpa.org/salesforce-transit-center/activities"
BASE_URL = "https://www.tjpa.org"
LAT = 37.7895
LNG = -122.3961

SYSTEM_PROMPT = """You are a quality filter for a curated family activity app in San Francisco.
You will receive an event from Salesforce Park, a free elevated rooftop park above the Salesforce Transit Center in SoMa.

Your job is TWO things:
1. QUALITY + FAMILY CHECK — Should a busy SF parent with young kids (0-9) know about this?
   - INCLUDE:
     * Free outdoor performances, concerts, festivals
     * Circus acts, kids shows, cultural performances
     * Community events, sports screenings that are daytime and family-oriented
     * Seasonal celebrations or holiday events
     * Yoga, fitness, or wellness events that are family-accessible
   - SKIP:
     * Adult-only fitness classes (bootcamp, HIIT, etc.) not designed for families
     * Corporate or private events
     * Events clearly not relevant to young children
   - When in doubt about a free outdoor daytime event, INCLUDE it

2. CLASSIFY — If including, assign taxonomy tags.

TAXONOMY:
- interest_tags (pick 1-3): nature, arts, sports, food, music, science, history, animals, water, community
- vibe_tags (pick 1-3): chill, adventurous, educational, social, creative, outdoorsy, foodie, cultural
- best_age_range (pick all that apply): Baby (0-1), Toddler (1-3), Preschool (3-5), Older Kids (6-9), All Ages
- cost_tier: always "free" for Salesforce Park events
- indoor_outdoor: always "outdoor"
- weather_sensitivity: "soft_avoid_rain" for outdoor events

3. DESCRIBE — Write a 1-2 sentence description a parent would find useful.
   - Be specific about what kids will experience
   - Mention that it's free and outdoors on the elevated park

4. RESERVATION — If the event mentions reservations, registration, or tickets required,
   set requires_reservation=true and write a brief reservation_note.
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
  "cost_tier": "free",
  "indoor_outdoor": "outdoor",
  "weather_sensitivity": "soft_avoid_rain",
  "requires_reservation": true or false,
  "reservation_note": "short practical note, or null",
  "reasoning": "one sentence why"
}"""


def make_source_id(name: str, starts_at: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')[:30]
    dt_part = starts_at[:16].replace(':', '').replace('T', '_').replace('-', '')
    return f"sfpark_{slug}_{dt_part}"


def fetch_events(days_ahead: int) -> list[dict]:
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)

    resp = requests.get(
        ACTIVITIES_URL,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"},
        timeout=15,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    events = []
    seen_ids = set()

    for row in soup.select("div.views-row"):
        title_el = row.select_one("div.views-field-title h2")
        if not title_el:
            continue
        name = title_el.get_text(strip=True)

        times = row.select("time.datetime")
        if not times:
            continue
        starts_at_raw = times[0].get("datetime", "")
        ends_at_raw = times[1].get("datetime", "") if len(times) > 1 else ""

        if not starts_at_raw:
            continue

        try:
            starts_at = datetime.fromisoformat(starts_at_raw)
            ends_at = datetime.fromisoformat(ends_at_raw) if ends_at_raw else None
        except Exception:
            continue

        event_date = starts_at.date()
        if event_date < today or event_date > cutoff:
            continue

        link_el = row.select_one("div.views-field-field-link a")
        href = link_el.get("href", "") if link_el else ""
        source_url = (BASE_URL + href) if href.startswith("/") else (href or ACTIVITIES_URL)

        desc_el = row.select_one("div.views-field-field-body .field-content")
        description = desc_el.get_text(" ", strip=True)[:500] if desc_el else ""

        source_id = make_source_id(name, starts_at_raw)
        if source_id in seen_ids:
            continue
        seen_ids.add(source_id)

        events.append({
            "source": "salesforce_park",
            "source_id": source_id,
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
        f"Venue: Salesforce Park (free elevated rooftop park, SoMa, San Francisco)\n"
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
        "address": "101 Mission St, San Francisco, CA 94105",
        "neighborhood": "SoMa",
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
        "cost_tier": "free",
        "indoor_outdoor": "outdoor",
        "weather_sensitivity": "soft_avoid_rain",
        "requires_reservation": cl.get("requires_reservation") or False,
        "reservation_note": cl.get("reservation_note") or None,
        "kid_friendly": True,
        "status": "pending_review",
        "ai_confidence": cl.get("confidence"),
        "ai_raw_response": cl,
    }


# ── Recurring children's programs ──────────────────────────────────────────

RECURRING_PROGRAMS = [
    {
        "name": "Toddler Tuesday at Salesforce Park",
        "emoji": "👶",
        "description": "Free weekly toddler program at Salesforce Park's rooftop Main Plaza — an outdoor gathering designed for little ones, right in the heart of SoMa.",
        "weekday": 1,        # Tuesday (Mon=0)
        "frequency": "weekly",
        "starts_time": "10:00",
        "ends_time": "11:00",
        "interest_tags": ["community", "music"],
        "vibe_tags": ["social", "chill", "educational"],
        "best_age_range": ["Baby (0-1)", "Toddler (1-3)", "Preschool (3-5)"],
        "source_prefix": "sfpark_tt",
    },
    {
        "name": "Toddler Thursday at Salesforce Park",
        "emoji": "👶",
        "description": "Free weekly toddler program at Salesforce Park's rooftop Main Plaza — a fun outdoor morning for babies and toddlers above the Salesforce Transit Center.",
        "weekday": 3,        # Thursday
        "frequency": "weekly",
        "starts_time": "10:00",
        "ends_time": "10:45",
        "interest_tags": ["community", "music"],
        "vibe_tags": ["social", "chill", "educational"],
        "best_age_range": ["Baby (0-1)", "Toddler (1-3)", "Preschool (3-5)"],
        "source_prefix": "sfpark_tth",
    },
    {
        "name": "Family Storytime at Salesforce Park",
        "emoji": "📚",
        "description": "Free family storytime on the 1st and 3rd Wednesdays of the month at Salesforce Park's rooftop Main Plaza — a lovely outdoor story session for young kids in SoMa.",
        "weekday": 2,        # Wednesday
        "frequency": "1st_3rd",  # 1st and 3rd Wednesday of each month
        "starts_time": "10:00",
        "ends_time": "10:30",
        "interest_tags": ["community", "arts"],
        "vibe_tags": ["educational", "chill", "social"],
        "best_age_range": ["Baby (0-1)", "Toddler (1-3)", "Preschool (3-5)"],
        "source_prefix": "sfpark_fs",
    },
]


def nth_weekday_of_month(d: date, weekday: int) -> int:
    """Return which occurrence (1-based) of `weekday` within its month `d` falls on."""
    first_of_month = d.replace(day=1)
    first_occurrence = first_of_month + timedelta(days=(weekday - first_of_month.weekday()) % 7)
    return (d.day - first_occurrence.day) // 7 + 1


def generate_recurring(program: dict, days_ahead: int) -> list[dict]:
    from zoneinfo import ZoneInfo
    SF_TZ = ZoneInfo("America/Los_Angeles")
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)
    rows = []

    current = today
    while current <= cutoff:
        if current.weekday() == program["weekday"]:
            include = False
            if program["frequency"] == "weekly":
                include = True
            elif program["frequency"] == "1st_3rd":
                nth = nth_weekday_of_month(current, program["weekday"])
                include = nth in (1, 3)

            if include:
                starts_at = datetime(
                    current.year, current.month, current.day,
                    *map(int, program["starts_time"].split(":")),
                    tzinfo=SF_TZ,
                )
                ends_at = datetime(
                    current.year, current.month, current.day,
                    *map(int, program["ends_time"].split(":")),
                    tzinfo=SF_TZ,
                )
                date_str = current.strftime("%Y%m%d")
                rows.append({
                    "name": program["name"],
                    "emoji": program["emoji"],
                    "description": program["description"],
                    "address": "101 Mission St, San Francisco, CA 94105",
                    "neighborhood": "SoMa",
                    "lat": LAT,
                    "lng": LNG,
                    "starts_at": starts_at.isoformat(),
                    "ends_at": ends_at.isoformat(),
                    "source": "salesforce_park",
                    "source_id": f"{program['source_prefix']}_{date_str}",
                    "source_url": ACTIVITIES_URL,
                    "interest_tags": program["interest_tags"],
                    "vibe_tags": program["vibe_tags"],
                    "best_age_range": program["best_age_range"],
                    "cost_tier": "free",
                    "indoor_outdoor": "outdoor",
                    "weather_sensitivity": "soft_avoid_rain",
                    "requires_reservation": False,
                    "reservation_note": None,
                    "kid_friendly": True,
                    "status": "pending_review",
                    "ai_confidence": 1.0,
                    "ai_raw_response": {},
                })
        current += timedelta(days=1)

    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days-ahead", type=int, default=17)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"Fetching Salesforce Park events (next {args.days_ahead} days)...\n")
    events = fetch_events(args.days_ahead)
    print(f"Found {len(events)} special events in window.\n")

    ai_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    db_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if not args.dry_run else None

    # ── Special events (scraped + classified) ──────────────────────────────
    special_rows = []
    if events:
        if db_client:
            source_ids = [e["source_id"] for e in events]
            existing = db_client.table("events").select("source_id").eq("source", "salesforce_park").in_("source_id", source_ids).execute()
            existing_ids = {r["source_id"] for r in existing.data}
            events = [e for e in events if e["source_id"] not in existing_ids]
            if existing_ids:
                print(f"Skipping {len(existing_ids)} already-existing special events.\n")

        print(f"Classifying {len(events)} special events with Claude Haiku...\n")
        skipped = 0
        for event in events:
            cl = classify(ai_client, event)
            if not cl.get("include"):
                print(f"  ✗ SKIP  {event['name'][:55]:<55} — {cl.get('skip_reason', '')[:50]}")
                skipped += 1
            else:
                row = build_row(event, cl)
                special_rows.append(row)
                print(f"  ✓ KEEP  {event['name'][:55]:<55} | {', '.join(row['interest_tags'])}")

        print(f"\nSpecial events — Kept: {len(special_rows)}  |  Skipped: {skipped}")

    # ── Recurring children's programs (seeded, no classification needed) ──
    print(f"\nGenerating recurring children's programs...\n")
    all_recurring = []
    for program in RECURRING_PROGRAMS:
        occurrences = generate_recurring(program, args.days_ahead)
        for occ in occurrences:
            print(f"  {occ['name'][:45]:<45} | {occ['starts_at'][:10]}")
        all_recurring.extend(occurrences)

    new_recurring = all_recurring
    if db_client and all_recurring:
        rec_ids = [r["source_id"] for r in all_recurring]
        existing_rec = db_client.table("events").select("source_id").eq("source", "salesforce_park").in_("source_id", rec_ids).execute()
        existing_rec_ids = {r["source_id"] for r in existing_rec.data}
        new_recurring = [r for r in all_recurring if r["source_id"] not in existing_rec_ids]
        if existing_rec_ids:
            print(f"\nSkipping {len(existing_rec_ids)} already-existing recurring occurrences.")

    print(f"\nRecurring programs — {len(new_recurring)} new occurrences to write")

    # ── Write everything ───────────────────────────────────────────────────
    all_rows = special_rows + new_recurring
    print(f"\n{'='*60}")
    print(f"Total to write: {len(all_rows)}")

    if args.dry_run or not all_rows:
        print("\n--- dry run, nothing written ---" if args.dry_run else "\nNothing to write.")
        return

    print("\nWriting to Supabase...")
    db_client.table("events").upsert(all_rows, on_conflict="source,source_id", ignore_duplicates=True).execute()
    print(f"Done. {len(all_rows)} events written with status='pending_review'.")


if __name__ == "__main__":
    main()
