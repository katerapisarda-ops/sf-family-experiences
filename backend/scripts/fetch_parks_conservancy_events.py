"""
fetch_parks_conservancy_events.py
Scrapes SF-area events from parksconservancy.org,
classifies with Claude Haiku, writes to Supabase as 'pending_review'.

Usage:
  python fetch_parks_conservancy_events.py [--days-ahead N] [--dry-run]
"""

import os
import re
import json
import time
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

BASE_URL = "https://www.parksconservancy.org"
EVENTS_URL = f"{BASE_URL}/events"
SF_TZ_OFFSET = "-07:00"

# SF-area parks to include (case-insensitive substring match)
SF_PARKS = [
    "presidio",
    "crissy field",
    "fort point",
    "tunnel tops",
    "lands end",
    "sutro",
    "baker beach",
    "ocean beach",
    "marin headlands",
    "alcatraz",
    "golden gate",
    "fort mason",
]

# Park name → approx coordinates
PARK_COORDS = {
    "presidio": (37.7989, -122.4662),
    "crissy field": (37.8038, -122.4489),
    "fort point": (37.8107, -122.4773),
    "tunnel tops": (37.8000, -122.4580),
    "lands end": (37.7784, -122.5120),
    "sutro": (37.7799, -122.5135),
    "baker beach": (37.7937, -122.4836),
    "ocean beach": (37.7602, -122.5100),
    "marin headlands": (37.8322, -122.4997),
    "alcatraz": (37.8270, -122.4230),
    "golden gate": (37.7694, -122.4862),
    "fort mason": (37.8065, -122.4322),
}

SYSTEM_PROMPT = """You are a quality filter for a curated family activity app in San Francisco.
You will receive an event from the Golden Gate National Recreation Area / Parks Conservancy.

Your job is TWO things:
1. QUALITY + FAMILY CHECK — Should a busy SF parent with young kids (0-9) know about this?
   - INCLUDE:
     * Junior ranger programs, kids nature walks, family-oriented hikes or outdoor programs
     * Wildlife, animal, or nature discovery events for kids
     * Festivals, celebrations, or community events open to families
     * Art, music, or cultural events accessible to young children
     * Campfire programs or evening nature programs suitable for families
     * Anything clearly fun or educational for young kids outdoors
   - SKIP:
     * Brief 15-minute ranger/history talks — not worth a trip for families
     * Adult-only hikes, lectures, or volunteer work sessions
     * Events clearly not family-relevant (adult fitness, restoration work, etc.)
   - When in doubt about a kids-friendly outdoor event, INCLUDE it

2. CLASSIFY — If including, assign taxonomy tags.

TAXONOMY:
- interest_tags (pick 1-3): nature, arts, sports, food, music, science, history, animals, water, community
- vibe_tags (pick 1-3): chill, adventurous, educational, social, creative, outdoorsy, foodie, cultural
- best_age_range (pick all that apply): Baby (0-1), Toddler (1-3), Preschool (3-5), Older Kids (6-9), All Ages
- cost_tier: "free" if free, "paid" if ticketed
- indoor_outdoor: usually "outdoor"
- weather_sensitivity: "soft_avoid_rain" for outdoor events

3. DESCRIBE — If including, write a short 1-2 sentence description a parent would find useful.
   - Be specific about what kids will do and what makes it special
   - Avoid generic filler

Respond ONLY with valid JSON:
{
  "include": true or false,
  "confidence": 0.0 to 1.0,
  "skip_reason": "only if include=false",
  "description": "1-2 sentence description (only if include=true)",
  "interest_tags": [...],
  "vibe_tags": [...],
  "best_age_range": [...],
  "cost_tier": "free" or "paid",
  "indoor_outdoor": "outdoor",
  "weather_sensitivity": "soft_avoid_rain"
}"""

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

SKIP_KEYWORDS = [
    "15-minute talk", "15 minute talk", "history talk", "welcome to the woods",
    "campfire program", "campfire talk", "volunteer:", "volunteering",
    "restoration", "stewardship", "habitat work",
]


def parse_date(date_text: str) -> tuple[str | None, str | None]:
    """Parse 'Sat, Mar 21, 2026, 1:15 - 1:30pm' → (starts_at ISO, ends_at ISO)."""
    try:
        m = re.search(
            r"([A-Za-z]{3}),\s+([A-Za-z]{3})\s+(\d{1,2}),\s+(\d{4}),\s+"
            r"(\d{1,2}:\d{2})\s*([apmAPM]*)\s*-\s*(\d{1,2}:\d{2})\s*([apmAPM]+)",
            date_text
        )
        if not m:
            return None, None

        month_str = m.group(2)
        day = int(m.group(3))
        year = int(m.group(4))
        start_t_str = m.group(5)
        start_ampm = m.group(6).lower()
        end_t_str = m.group(7)
        end_ampm = m.group(8).lower()

        month = MONTH_MAP.get(month_str[:3].lower())
        if not month:
            return None, None

        # If start has no AM/PM, inherit from end
        if not start_ampm:
            start_ampm = end_ampm

        def to_24h(t: str, ampm: str) -> str:
            h, mn = map(int, t.split(":"))
            if ampm == "pm" and h != 12:
                h += 12
            elif ampm == "am" and h == 12:
                h = 0
            return f"{h:02d}:{mn:02d}:00"

        event_date = f"{year}-{month:02d}-{day:02d}"
        starts_at = f"{event_date}T{to_24h(start_t_str, start_ampm)}{SF_TZ_OFFSET}"
        ends_at = f"{event_date}T{to_24h(end_t_str, end_ampm)}{SF_TZ_OFFSET}"
        return starts_at, ends_at
    except Exception:
        return None, None


def get_coords(parks: list[str]) -> tuple[float, float]:
    parks_lower = " ".join(parks).lower()
    for key, coords in PARK_COORDS.items():
        if key in parks_lower:
            return coords
    return (37.7989, -122.4662)  # Default: Presidio area


def is_sf_park(parks: list[str]) -> bool:
    parks_lower = " ".join(parks).lower()
    return any(sf in parks_lower for sf in SF_PARKS)


def is_obvious_skip(name: str) -> bool:
    n = name.lower()
    return any(kw in n for kw in SKIP_KEYWORDS)


def fetch_events(days_ahead: int) -> list[dict]:
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)
    events = []
    seen_urls = set()

    for page in range(20):  # Max 20 pages
        url = f"{EVENTS_URL}?page={page}"
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        cards = soup.select("div.node-search-index.node-event")
        if not cards:
            break

        hit_future = False
        for card in cards:
            title_el = card.select_one("a.h4")
            if not title_el:
                continue

            name = title_el.get_text(strip=True)
            href = title_el.get("href", "")
            source_url = BASE_URL + href if href.startswith("/") else href

            if source_url in seen_urls:
                continue
            seen_urls.add(source_url)

            date_el = card.select_one("div.date")
            date_text = date_el.get_text(strip=True) if date_el else ""
            starts_at, ends_at = parse_date(date_text)
            if not starts_at:
                continue

            event_date = datetime.fromisoformat(starts_at).date()
            if event_date < today:
                continue
            if event_date > cutoff:
                hit_future = True
                continue

            parks = [a.get_text(strip=True) for a in card.select("div.parks a")]
            if not is_sf_park(parks):
                continue

            desc_el = card.select_one("div.body p")
            description = desc_el.get_text(strip=True) if desc_el else ""

            lat, lng = get_coords(parks)
            park_name = parks[0] if parks else "Golden Gate National Recreation Area"

            source_id = href.strip("/").split("/")[-1]

            events.append({
                "source": "parks_conservancy",
                "source_id": source_id,
                "source_url": source_url,
                "name": name,
                "raw_description": description,
                "parks": parks,
                "address": f"{park_name}, San Francisco, CA",
                "lat": lat,
                "lng": lng,
                "starts_at": starts_at,
                "ends_at": ends_at,
            })

        if hit_future:
            break
        time.sleep(0.5)

    return events


def classify(ai_client: anthropic.Anthropic, event: dict) -> dict:
    parks_str = ", ".join(event.get("parks", [])) or "Golden Gate NRA"
    prompt = (
        f"Event: {event['name']}\n"
        f"Date: {event.get('starts_at', 'unknown')}\n"
        f"Location: {parks_str}\n"
        f"Description: {event.get('raw_description', '')[:300]}"
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
        "description": cl.get("description") or None,
        "address": event["address"],
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

    print(f"Fetching Parks Conservancy events (next {args.days_ahead} days, SF parks only)...\n")
    events = fetch_events(args.days_ahead)
    print(f"Found {len(events)} SF-area events in window.\n")

    if not events:
        print("Nothing to process.")
        return

    # Pre-filter obvious skips
    pre_filtered = [e for e in events if not is_obvious_skip(e["name"])]
    pre_skipped = len(events) - len(pre_filtered)
    if pre_skipped:
        print(f"Pre-filtered {pre_skipped} obvious skips.\n")
    events = pre_filtered

    ai_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    db_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if not args.dry_run else None

    if db_client:
        source_ids = [e["source_id"] for e in events]
        existing = db_client.table("events").select("source_id").eq("source", "parks_conservancy").in_("source_id", source_ids).execute()
        existing_ids = {r["source_id"] for r in existing.data}
        events = [e for e in events if e["source_id"] not in existing_ids]
        if existing_ids:
            print(f"Skipping {len(existing_ids)} already-existing events.\n")

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
