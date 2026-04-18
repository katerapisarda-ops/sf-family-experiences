"""
fetch_randall_events.py
Scrapes events from Randall Museum (server-rendered WordPress/ai1ec calendar),
classifies with Claude Haiku for tags + description, writes to Supabase as 'pending_review'.

Usage:
  python fetch_randall_events.py [--days-ahead N] [--dry-run]
"""

import os
import re
import json
import time
import argparse
from datetime import datetime, date, timedelta, timezone
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup
import anthropic
from supabase import create_client

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../.env"))

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

BASE_URL = "https://randallmuseum.org"
EVENTS_URL = f"{BASE_URL}/randall-museum-events/"

LAT = 37.7671
LNG = -122.4394
ADDRESS = "199 Museum Way, San Francisco, CA 94114"
SF_TZ_OFFSET = "-07:00"  # PDT

SYSTEM_PROMPT = """You are writing content for a curated family activity app in San Francisco.
You will receive an event from Randall Museum — a hands-on science and nature museum for kids in SF.
All Randall Museum events are family-friendly, so always include them.

Your job is to:
1. CLASSIFY — assign taxonomy tags
2. DESCRIBE — write a short 1-2 sentence description a parent would find useful

TAXONOMY:
- interest_tags (pick 1-3): nature, arts, sports, food, music, science, history, animals, water, community
- vibe_tags (pick 1-3): chill, adventurous, educational, social, creative, outdoorsy, foodie, cultural
- best_age_range (pick all that apply): Baby (0-1), Toddler (1-3), Preschool (3-5), Older Kids (6-9), All Ages
- cost_tier: "free" if free/included with admission, "paid" if extra cost
- indoor_outdoor: usually "indoor" for Randall Museum

For the description:
- Be specific about what kids will do and what makes it special
- Example: "Kids get up-close time with one of Randall Museum's live animal ambassadors — snakes, owls, and other wildlife residents of the museum."
- Avoid generic filler like "a fun event for the whole family"

Respond ONLY with valid JSON:
{
  "description": "1-2 sentence parent-friendly description",
  "emoji": "single emoji that best represents this specific activity (only if include=true)",
  "interest_tags": [...],
  "vibe_tags": [...],
  "best_age_range": [...],
  "cost_tier": "free" or "paid",
  "indoor_outdoor": "indoor",
  "weather_sensitivity": "none"
}"""


MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def parse_event_time(time_text: str, ref_date: date) -> tuple[str | None, str | None]:
    """Parse 'Mar 28 @ 1:00 pm – 4:00 pm' into ISO starts_at / ends_at."""
    m = re.search(
        r"([A-Za-z]{3})\s+(\d{1,2})\s*@\s*(\d{1,2}:\d{2}\s*[apm]+)\s*[–-]\s*(\d{1,2}:\d{2}\s*[apm]+)",
        time_text, re.IGNORECASE
    )
    if not m:
        return None, None
    try:
        month_str, day_str, start_str, end_str = m.group(1), m.group(2), m.group(3).strip(), m.group(4).strip()
        month = MONTH_MAP.get(month_str[:3].lower())
        if not month:
            return None, None
        day = int(day_str)
        # Infer year: if month/day is before today, assume next year
        year = ref_date.year
        if date(year, month, day) < ref_date:
            year += 1
        event_date = date(year, month, day)

        def parse_t(t: str) -> str:
            t = t.upper().replace(" ", "")
            fmt = "%I:%M%p" if len(t) > 5 else "%I%p"
            return datetime.strptime(t, fmt).strftime("%H:%M:%S")

        starts_at = f"{event_date.isoformat()}T{parse_t(start_str)}{SF_TZ_OFFSET}"
        ends_at = f"{event_date.isoformat()}T{parse_t(end_str)}{SF_TZ_OFFSET}"
        return starts_at, ends_at
    except Exception:
        return None, None


def fetch_events(days_ahead: int) -> list[dict]:
    """Fetch events from the Randall Museum events page (and page 2 if needed)."""
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)
    events = []
    seen_ids = set()

    # Fetch up to 3 pages
    urls = [
        EVENTS_URL,
        f"{EVENTS_URL}action~agenda/page_offset~1/",
        f"{EVENTS_URL}action~agenda/page_offset~2/",
    ]

    for url in urls:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        hit_future = False
        for card in soup.select(".ai1ec-event"):
            # ID
            classes = card.get("class", [])
            instance_id = next((c for c in classes if "instance-id" in c), None)
            if instance_id in seen_ids:
                continue
            seen_ids.add(instance_id)

            # Title (strip the location span)
            title_el = card.select_one(".ai1ec-event-title")
            if not title_el:
                continue
            for loc in title_el.select(".ai1ec-event-location"):
                loc.decompose()
            name = title_el.get_text(strip=True)

            # Time
            time_el = card.select_one(".ai1ec-event-time")
            time_text = time_el.get_text(strip=True) if time_el else ""
            starts_at, ends_at = parse_event_time(time_text, today)
            if not starts_at:
                continue

            event_date = datetime.fromisoformat(starts_at).date()
            if event_date < today:
                continue
            if event_date > cutoff:
                hit_future = True
                continue

            # URL
            link_el = card.select_one("a.ai1ec-read-more")
            source_url = link_el["href"] if link_el and link_el.get("href") else ""
            source_id = source_url.rstrip("/").split("/")[-2] if source_url else instance_id or name.lower().replace(" ", "-")

            # Description from listing page
            desc_el = card.select_one(".ai1ec-event-description")
            raw_desc = desc_el.get_text(" ", strip=True)[:500] if desc_el else ""

            # Categories
            categories = [a.get_text(strip=True) for a in card.select("a.ai1ec-category")]

            events.append({
                "source": "randall_museum",
                "source_id": source_id,
                "source_url": source_url,
                "name": name,
                "raw_description": raw_desc,
                "categories": categories,
                "address": ADDRESS,
                "lat": LAT,
                "lng": LNG,
                "starts_at": starts_at,
                "ends_at": ends_at,
            })

        if hit_future:
            break
        time.sleep(0.5)

    return events


def classify(ai_client: anthropic.Anthropic, event: dict) -> dict:
    cats = ", ".join(event.get("categories", [])) or "none"
    prompt = (
        f"Event: {event['name']}\n"
        f"Date: {event.get('starts_at', 'unknown')}\n"
        f"Categories: {cats}\n"
        f"Description excerpt: {event.get('raw_description', '')[:300]}"
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
        "neighborhood": "Cole Valley",
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
        "cost_tier": cl.get("cost_tier", "paid"),
        "indoor_outdoor": cl.get("indoor_outdoor", "indoor"),
        "weather_sensitivity": cl.get("weather_sensitivity", "none"),
        "kid_friendly": True,
        "status": "pending_review",
        "ai_confidence": 1.0,
        "ai_raw_response": cl,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days-ahead", type=int, default=14)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"Fetching Randall Museum events (next {args.days_ahead} days)...\n")
    events = fetch_events(args.days_ahead)
    print(f"Found {len(events)} events in window.\n")

    if not events:
        print("Nothing to process.")
        return

    ai_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    db_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if not args.dry_run else None

    # Skip already-existing events
    if db_client:
        source_ids = [e["source_id"] for e in events]
        existing = db_client.table("events").select("source_id").eq("source", "randall_museum").in_("source_id", source_ids).execute()
        existing_ids = {r["source_id"] for r in existing.data}
        new_events = [e for e in events if e["source_id"] not in existing_ids]
        if existing_ids:
            print(f"Skipping {len(existing_ids)} already-existing events.\n")
    else:
        new_events = events

    print(f"Classifying {len(new_events)} events with Claude Haiku...\n")
    rows = []
    for event in new_events:
        cl = classify(ai_client, event)
        row = build_row(event, cl)
        rows.append(row)
        print(f"  ✓ {event['name'][:60]:<60} | {', '.join(row['interest_tags'])}")

    print(f"\n{'='*60}")
    print(f"Total: {len(rows)} events")

    if args.dry_run or not rows:
        print("\n--- dry run, nothing written ---" if args.dry_run else "\nNothing to write.")
        return

    print("\nWriting to Supabase...")
    db_client.table("events").upsert(rows, on_conflict="source,source_id", ignore_duplicates=True).execute()
    print(f"Done. {len(rows)} events written with status='pending_review'.")


if __name__ == "__main__":
    main()
