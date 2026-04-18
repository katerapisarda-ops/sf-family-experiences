"""
fetch_fort_mason_events.py
Fetches events from Fort Mason Center via WordPress REST API,
classifies with Claude Haiku, writes to Supabase as 'pending_review'.

Usage:
  python fetch_fort_mason_events.py [--days-ahead N] [--dry-run]
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

API_URL = "https://fortmason.org/wp-json/tribe/events/v1/events"
SF_TZ_OFFSET = "-07:00"

LAT = 37.8065
LNG = -122.4322

SYSTEM_PROMPT = """You are a quality filter for a curated family activity app in San Francisco.
You will receive an event from Fort Mason Center for Arts & Culture in San Francisco.

Your job is TWO things:
1. QUALITY + FAMILY CHECK — Should a busy SF parent with young kids (0-9) know about this?
   - INCLUDE:
     * Art fairs, markets, or festivals families can browse together
     * Kids or family-specific workshops, performances, or activities
     * Cultural celebrations or community events open to all ages
     * Food events or markets accessible to families
     * Film screenings or performances suitable for families
   - SKIP:
     * Static art exhibitions, murals, or gallery installations (not a ticketed event with a schedule — just something on display)
     * Adult theater, opera, or drama performances not suitable for young children
     * Adult fitness classes, adult workshops, or professional development events
     * Nightlife, bar events, or late-night adult events
     * Private or members-only events
     * Events clearly not family-relevant

2. CLASSIFY — If including, assign taxonomy tags.

TAXONOMY:
- interest_tags (pick 1-3): nature, arts, sports, food, music, science, history, animals, water, community
- vibe_tags (pick 1-3): chill, adventurous, educational, social, creative, outdoorsy, foodie, cultural
- best_age_range (pick all that apply): Baby (0-1), Toddler (1-3), Preschool (3-5), Older Kids (6-9), All Ages
- cost_tier: "free" if free, "paid" if ticketed
- indoor_outdoor: "indoor", "outdoor", or "both"
- weather_sensitivity: "none" for indoor, "soft_avoid_rain" for outdoor/both

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
  "cost_tier": "free" or "paid",
  "indoor_outdoor": "indoor",
  "weather_sensitivity": "none"
}"""

SKIP_KEYWORDS = [
    "yoga", "fitness", "bootcamp", "members only", "private event",
    "staff meeting", "board meeting", "farmers market",
]


def clean_html(text: str) -> str:
    if not text:
        return ""
    return BeautifulSoup(html.unescape(text), "lxml").get_text(" ", strip=True)[:400]


def fetch_events(days_ahead: int) -> list[dict]:
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)
    events = []
    seen_ids = set()

    page = 1
    while True:
        resp = requests.get(
            API_URL,
            params={
                "per_page": 50,
                "page": page,
                "start_date": today.isoformat(),
                "end_date": cutoff.isoformat(),
            },
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://fortmason.org/events/",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        raw_events = data.get("events", [])
        if not raw_events:
            break

        for e in raw_events:
            event_id = e.get("id")
            if event_id in seen_ids:
                continue
            seen_ids.add(event_id)

            starts_at = e.get("start_date", "").replace(" ", "T") + SF_TZ_OFFSET
            ends_at = e.get("end_date", "").replace(" ", "T") + SF_TZ_OFFSET if e.get("end_date") else None

            cost = e.get("cost", "")
            cost_tier = "free" if not cost or cost.strip().lower() in ("free", "0", "") else "paid"

            categories = [c.get("name") for c in e.get("categories", [])]
            slug = e.get("url", "").rstrip("/").split("/")[-2] or str(event_id)

            events.append({
                "source": "fort_mason",
                "source_id": slug,
                "source_url": e.get("url", ""),
                "name": clean_html(e.get("title", "")),
                "raw_description": clean_html(e.get("excerpt", "") or e.get("description", "")),
                "categories": categories,
                "cost": cost,
                "cost_tier": cost_tier,
                "address": "Fort Mason Center, Marina Blvd, San Francisco, CA 94123",
                "lat": LAT,
                "lng": LNG,
                "starts_at": starts_at,
                "ends_at": ends_at,
            })

        if page >= data.get("total_pages", 1):
            break
        page += 1
        time.sleep(0.5)

    return events


def classify(ai_client: anthropic.Anthropic, event: dict) -> dict:
    cats = ", ".join(event.get("categories", [])) or "none"
    prompt = (
        f"Event: {event['name']}\n"
        f"Date: {event.get('starts_at', 'unknown')}\n"
        f"Categories: {cats}\n"
        f"Cost: {event.get('cost') or 'unknown'}\n"
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
        "neighborhood": "Marina",
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
        "cost_tier": event["cost_tier"],
        "indoor_outdoor": cl.get("indoor_outdoor", "indoor"),
        "weather_sensitivity": cl.get("weather_sensitivity", "none"),
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

    print(f"Fetching Fort Mason events (next {args.days_ahead} days)...\n")
    events = fetch_events(args.days_ahead)
    print(f"Found {len(events)} events.\n")

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
        existing = db_client.table("events").select("source_id").eq("source", "fort_mason").in_("source_id", source_ids).execute()
        existing_ids = {r["source_id"] for r in existing.data}
        events = [e for e in events if e["source_id"] not in existing_ids]
        if existing_ids:
            print(f"Skipping {len(existing_ids)} already-existing events.\n")

    print(f"Classifying {len(events)} events with Claude Haiku...\n")
    included, skipped = [], 0

    for event in events:
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
