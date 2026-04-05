"""
fetch_sfrecpark_events.py
Scrapes events from SF Rec & Parks calendar,
classifies with Claude Haiku (family filter + movie rating check),
writes to Supabase as 'pending_review'.

Usage:
  python fetch_sfrecpark_events.py [--days-ahead N] [--dry-run]
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

BASE_URL = "https://sfrecpark.org"
CALENDAR_URL = f"{BASE_URL}/Calendar.aspx"
SF_TZ_OFFSET = "-07:00"

SKIP_KEYWORDS = [
    "committee", "commission meeting", "board meeting", "staff meeting",
    "advisory", "permit", "budget", "public hearing", "workshop for adults",
    "senior", "aarp", "tax prep", "mahjong & mixers",
]

SYSTEM_PROMPT = """You are a quality filter for a curated family activity app in San Francisco.
You will receive an event from the SF Recreation & Parks calendar.

Your job is TWO things:
1. QUALITY + FAMILY CHECK — Apply a HIGH bar. Only include if the event is clearly and explicitly designed for young kids or families with young children.
   - INCLUDE only:
     * Events with "kids", "family", "children", "youth", "junior", or "Easter" explicitly in the name or description
     * Named seasonal family festivals (SpringFling, EcoCenter Celebration, Easter at the Beach, etc.)
     * Movie nights — ONLY if the film is rated G or PG. Skip PG-13, R, and NR. If unsure of rating, skip.
     * Explicitly labeled kids concerts or kids festivals (e.g. "Kids Festival", "Family Concert")
   - SKIP everything else, including:
     * General bandshell concerts or park events NOT explicitly for kids/families
     * Union Square programming unless explicitly for kids
     * Fitness classes, hobby classes, or adult recreational activities
     * Cultural performances or concerts with no explicit kids/family angle
     * Any event where you're not confident young families are the intended audience

2. CLASSIFY — If including, assign taxonomy tags.

IMPORTANT for movie nights: The description will include the film title and rating (e.g. "Missing Link (PG)").
Include G and PG films only. Skip PG-13, R, and NR. If no rating is provided, skip.
When including a movie night, name the film and rating in your description, e.g.:
"Free screening of Missing Link (PG) at Glen Park Rec Center — a charming animated adventure about a Bigfoot searching for his family."

TAXONOMY:
- interest_tags (pick 1-3): nature, arts, sports, food, music, science, history, animals, water, community
- vibe_tags (pick 1-3): chill, adventurous, educational, social, creative, outdoorsy, foodie, cultural
- best_age_range (pick all that apply): Baby (0-1), Toddler (1-3), Preschool (3-5), Older Kids (6-9), All Ages
- cost_tier: "free" for Rec & Park events
- indoor_outdoor: "outdoor" for most events
- weather_sensitivity: "soft_avoid_rain" for outdoor, "none" for indoor

3. DESCRIBE — If including, write a short 1-2 sentence description a parent would find useful.

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
  "cost_tier": "free",
  "indoor_outdoor": "outdoor",
  "weather_sensitivity": "soft_avoid_rain"
}"""


def fetch_movie_detail(detail_url: str) -> str:
    """Fetch the event detail page and return description including movie title/rating."""
    try:
        resp = requests.get(detail_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        soup = BeautifulSoup(resp.text, "lxml")
        desc_el = soup.select_one("[itemprop='description']")
        if desc_el:
            return desc_el.get_text(" ", strip=True)[:600]
        return ""
    except Exception:
        return ""


def is_movie_event(name: str) -> bool:
    return any(kw in name.lower() for kw in ["movie night", "movie nights", "film", "screening", "cinema"])


def fetch_events(days_ahead: int) -> list[dict]:
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)
    events = []
    seen_eids = set()

    # Fetch month by month covering the window
    months_to_fetch = set()
    d = today
    while d <= cutoff:
        months_to_fetch.add((d.year, d.month))
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)

    for year, month in sorted(months_to_fetch):
        resp = requests.get(
            CALENDAR_URL,
            params={"view": "list", "month": month, "year": year},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=15,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        for li in soup.select("li:has(h3 a[href*='EID'])"):
            # Event ID
            a = li.select_one("h3 a[href*='EID']")
            if not a:
                continue
            eid_m = re.search(r"EID=(\d+)", a.get("href", ""))
            if not eid_m:
                continue
            eid = eid_m.group(1)
            if eid in seen_eids:
                continue
            seen_eids.add(eid)

            name = a.get_text(strip=True)

            # ISO date from schema.org microdata
            start_el = li.select_one("[itemprop='startDate']")
            if not start_el:
                continue
            start_iso = start_el.get("content") or start_el.get_text(strip=True)
            try:
                event_date = datetime.fromisoformat(start_iso).date()
            except Exception:
                continue

            if event_date < today or event_date > cutoff:
                continue

            starts_at = start_iso if "+" in start_iso or start_iso.endswith("Z") else start_iso + SF_TZ_OFFSET

            # Date display for end time
            date_div = li.select_one(".date")
            ends_at = None
            if date_div:
                date_text = date_div.get_text(strip=True)
                end_m = re.search(r"-\s*(\d{1,2}:\d{2}\s*[APM]+)", date_text, re.IGNORECASE)
                if end_m:
                    end_str = end_m.group(1).strip().upper().replace(" ", "")
                    try:
                        end_t = datetime.strptime(end_str, "%I:%M%p")
                        ends_at = f"{event_date.isoformat()}T{end_t.strftime('%H:%M:%S')}{SF_TZ_OFFSET}"
                    except Exception:
                        pass

            # Location
            loc_el = li.select_one("[itemprop='name']:not(h3 [itemprop])")
            location = loc_el.get_text(strip=True) if loc_el else ""
            addr_street = li.select_one("[itemprop='streetAddress']")
            addr_city = li.select_one("[itemprop='addressLocality']")
            addr_zip = li.select_one("[itemprop='postalCode']")
            address = ""
            if addr_street:
                address = f"{addr_street.get_text(strip=True)}, San Francisco, CA"
                if addr_zip:
                    address += f" {addr_zip.get_text(strip=True)}"
            elif location:
                address = f"{location}, San Francisco, CA"

            # Description — for movie nights, also fetch the detail page
            desc_el = li.select_one("[itemprop='description']")
            description = desc_el.get_text(strip=True) if desc_el else ""

            source_url = BASE_URL + a.get("href", "")

            if is_movie_event(name):
                detail_desc = fetch_movie_detail(source_url)
                if detail_desc:
                    description = detail_desc
                time.sleep(0.3)

            events.append({
                "source": "sfrecpark",
                "source_id": f"sfrecpark-{eid}",
                "source_url": source_url,
                "name": name,
                "raw_description": description[:600],
                "location": location,
                "address": address or "San Francisco, CA",
                "lat": None,
                "lng": None,
                "starts_at": starts_at,
                "ends_at": ends_at,
            })

        time.sleep(0.5)

    return events


def classify(ai_client: anthropic.Anthropic, event: dict) -> dict:
    prompt = (
        f"Event: {event['name']}\n"
        f"Date: {event.get('starts_at', 'unknown')}\n"
        f"Location: {event.get('location', 'San Francisco park')}\n"
        f"Description: {event.get('raw_description', '')}"
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
        "lat": event.get("lat"),
        "lng": event.get("lng"),
        "starts_at": event["starts_at"],
        "ends_at": event.get("ends_at"),
        "source": event["source"],
        "source_id": event["source_id"],
        "source_url": event["source_url"],
        "interest_tags": cl.get("interest_tags", []),
        "vibe_tags": cl.get("vibe_tags", []),
        "best_age_range": cl.get("best_age_range", []),
        "cost_tier": "free",
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

    print(f"Fetching SF Rec & Parks events (next {args.days_ahead} days)...\n")
    events = fetch_events(args.days_ahead)
    print(f"Found {len(events)} events in window.\n")

    if not events:
        print("Nothing to process.")
        return

    pre_filtered = [e for e in events if not any(kw in e["name"].lower() for kw in SKIP_KEYWORDS)]
    pre_skipped = len(events) - len(pre_filtered)
    if pre_skipped:
        print(f"Pre-filtered {pre_skipped} obvious skips.\n")
    events = pre_filtered

    ai_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    db_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if not args.dry_run else None

    if db_client:
        source_ids = [e["source_id"] for e in events]
        existing = db_client.table("events").select("source_id").eq("source", "sfrecpark").in_("source_id", source_ids).execute()
        existing_ids = {r["source_id"] for r in existing.data}
        events = [e for e in events if e["source_id"] not in existing_ids]
        if existing_ids:
            print(f"Skipping {len(existing_ids)} already-existing events.\n")

    print(f"Classifying {len(events)} events with Claude Haiku...\n")
    included, skipped = [], 0

    for i, event in enumerate(events):
        if i > 0 and i % 45 == 0:
            print("  (rate limit pause 65s...)")
            time.sleep(65)
        cl = classify(ai_client, event)
        if not cl.get("include"):
            print(f"  ✗ SKIP  {event['name'][:55]:<55} — {cl.get('skip_reason', '')[:50]}")
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
