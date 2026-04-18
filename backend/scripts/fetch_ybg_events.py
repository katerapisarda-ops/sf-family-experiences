"""
fetch_ybg_events.py
Scrapes events from Yerba Buena Gardens Festival via JSON-LD,
classifies with Claude Haiku for tags + description,
writes to Supabase as 'pending_review'.

Usage:
  python fetch_ybg_events.py [--days-ahead N] [--dry-run]
"""

import os
import json
import time
import argparse
import html
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

BASE_URL = "https://ybgfestival.org"
EVENTS_URL = f"{BASE_URL}/events/"

SYSTEM_PROMPT = """You are a quality filter for a curated family activity app in San Francisco.
You will receive a free outdoor event from Yerba Buena Gardens Festival.

Your job is TWO things:
1. QUALITY + FAMILY CHECK — Should a busy SF parent with young kids (0-9) know about this?
   - INCLUDE: performances, festivals, egg hunts, cultural celebrations, concerts, art events, kids activities
   - SKIP: adult fitness or workout classes (yoga, dance fitness, aerobics, bootcamp, etc.) — these are not family activity events

2. CLASSIFY + DESCRIBE — If including, assign taxonomy tags and write a 1-2 sentence description

TAXONOMY:
- interest_tags (pick 1-3): nature, arts, sports, food, music, science, history, animals, water, community
- vibe_tags (pick 1-3): chill, adventurous, educational, social, creative, outdoorsy, foodie, cultural
- best_age_range (pick all that apply): Baby (0-1), Toddler (1-3), Preschool (3-5), Older Kids (6-9), All Ages
- cost_tier: always "free" for YBG events
- indoor_outdoor: always "outdoor"
- weather_sensitivity: always "soft_avoid_rain"

For the description:
- Be specific about what kids will experience
- Example: "Free Easter egg hunt on the Great Lawn at Yerba Buena Gardens — kids of all ages search for eggs in a festive outdoor setting in the heart of SoMa."
- Avoid generic filler

Respond ONLY with valid JSON:
{
  "include": true or false,
  "skip_reason": "only if include=false",
  "description": "1-2 sentence parent-friendly description (only if include=true)",
  "emoji": "single emoji that best represents this specific activity (only if include=true)",
  "interest_tags": [...],
  "vibe_tags": [...],
  "best_age_range": [...],
  "cost_tier": "free",
  "indoor_outdoor": "outdoor",
  "weather_sensitivity": "soft_avoid_rain"
}"""


def clean_html(text: str) -> str:
    """Strip HTML tags and unescape entities."""
    if not text:
        return ""
    unescaped = html.unescape(text)
    return BeautifulSoup(unescaped, "lxml").get_text(" ", strip=True)[:400]


def fetch_events(days_ahead: int) -> list[dict]:
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)
    events = []
    seen_urls = set()

    for page in range(1, 10):
        url = EVENTS_URL if page == 1 else f"{EVENTS_URL}page/{page}/"
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}, timeout=15)
        if resp.status_code == 404:
            break
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")
        scripts = soup.find_all("script", type="application/ld+json")

        # Find the list of Event objects
        event_list = []
        for s in scripts:
            try:
                data = json.loads(s.string)
                if isinstance(data, list) and data and data[0].get("@type") == "Event":
                    event_list = data
                    break
            except Exception:
                continue

        if not event_list:
            break

        hit_future = False
        for e in event_list:
            source_url = e.get("url", "")
            if source_url in seen_urls:
                continue
            seen_urls.add(source_url)

            starts_at = e.get("startDate")
            ends_at = e.get("endDate")
            if not starts_at:
                continue

            event_date = datetime.fromisoformat(starts_at).date()
            if event_date < today:
                continue
            if event_date > cutoff:
                hit_future = True
                continue

            # Cancelled check
            status = e.get("eventStatus", "")
            if "Cancelled" in status:
                continue

            location = e.get("location", {})
            geo = location.get("geo", {})
            lat = geo.get("latitude") or 37.7847
            lng = geo.get("longitude") or -122.4027
            addr = location.get("address", {})
            address = f"{addr.get('streetAddress', 'Yerba Buena Gardens')}, San Francisco, CA {addr.get('postalCode', '94103')}"

            slug = source_url.rstrip("/").split("/")[-1]
            price = e.get("offers", {}).get("price", "0")
            cost_tier = "free" if str(price) in ("0", "0.0", "") else "paid"

            events.append({
                "source": "ybg",
                "source_id": slug,
                "source_url": source_url,
                "name": e.get("name", ""),
                "raw_description": clean_html(e.get("description", "")),
                "address": address,
                "lat": float(lat),
                "lng": float(lng),
                "starts_at": starts_at,
                "ends_at": ends_at,
                "cost_tier": cost_tier,
            })

        if hit_future:
            break
        time.sleep(0.5)

    return events


def classify(ai_client: anthropic.Anthropic, event: dict) -> dict:
    prompt = (
        f"Event: {event['name']}\n"
        f"Date: {event.get('starts_at', 'unknown')}\n"
        f"Venue: Yerba Buena Gardens, San Francisco\n"
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
        "neighborhood": "SoMa",
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
        "cost_tier": event.get("cost_tier", "free"),
        "indoor_outdoor": "outdoor",
        "weather_sensitivity": "soft_avoid_rain",
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

    print(f"Fetching Yerba Buena Gardens events (next {args.days_ahead} days)...\n")
    events = fetch_events(args.days_ahead)
    print(f"Found {len(events)} events in window.\n")

    if not events:
        print("Nothing to process.")
        return

    ai_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    db_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if not args.dry_run else None

    if db_client:
        source_ids = [e["source_id"] for e in events]
        existing = db_client.table("events").select("source_id").eq("source", "ybg").in_("source_id", source_ids).execute()
        existing_ids = {r["source_id"] for r in existing.data}
        events = [e for e in events if e["source_id"] not in existing_ids]
        if existing_ids:
            print(f"Skipping {len(existing_ids)} already-existing events.\n")

    print(f"Classifying {len(events)} events with Claude Haiku...\n")
    rows, skipped = [], 0
    for event in events:
        cl = classify(ai_client, event)
        if not cl.get("include", True):
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
