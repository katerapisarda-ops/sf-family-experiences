"""
fetch_thrive_city_events.py
Scrapes Thrive City events from Chase Center using Playwright,
classifies with Claude Haiku, writes approved events to Supabase as 'pending_review'.

Usage:
  python fetch_thrive_city_events.py [--dry-run]
"""

import os
import re
import json
import asyncio
import argparse
from datetime import datetime, timezone
from dotenv import load_dotenv
import anthropic
from supabase import create_client
from playwright.async_api import async_playwright

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../.env"))

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

THRIVE_CITY_URL = "https://www.chasecenter.com/events/?brand=Thrive%20city"
BASE_URL = "https://www.chasecenter.com"

LAT = 37.7680
LNG = -122.3877

SYSTEM_PROMPT = """You are a quality filter for a curated family activity app in San Francisco.
You will receive an event from the Thrive City outdoor plaza at Chase Center.

Your job is TWO things:
1. QUALITY + FAMILY CHECK — Should a busy SF parent with young kids (0-9) know about this?
   - INCLUDE: events families with young children can meaningfully attend:
     * Free outdoor activations, markets, and community events
     * Live music, performances, cultural events accessible to kids
     * Sports viewings, watch parties in an outdoor setting
     * Fitness classes or wellness events families can do together
     * Seasonal or holiday events
     * Anything clearly fun or interesting for families outdoors
   - SKIP:
     * Adult-only ticketed concerts inside the arena
     * Corporate or private events
     * Sports viewings / watch parties (just watching a game on a screen — not an activity for kids)
     * Adult fitness or wellness classes (yoga, bootcamp, etc.) not designed for families
     * Events clearly not family-relevant
   - When in doubt about a family-friendly outdoor event, INCLUDE it

2. CLASSIFY — If including, assign taxonomy tags.

TAXONOMY:
- interest_tags (pick 1-3): nature, arts, sports, food, music, science, history, animals, water, community
- vibe_tags (pick 1-3): chill, adventurous, educational, social, creative, outdoorsy, foodie, cultural, romantic
- best_age_range (pick all that apply): Baby (0-1), Toddler (1-3), Preschool (3-5), Older Kids (6-9), All Ages
- cost_tier: "free" if free, otherwise "paid"
- indoor_outdoor: always "outdoor" for Thrive City events
- weather_sensitivity: "soft_avoid_rain" for outdoor events

3. DESCRIBE — If including, write a short 1-2 sentence description a parent would find useful.
   - Be specific: what will kids experience, what makes it worth the trip
   - Example: "Free outdoor lucha libre wrestling show at the plaza outside Chase Center — colorful, theatrical, and totally kid-friendly."
   - Avoid generic filler

Respond ONLY with valid JSON:
{
  "include": true or false,
  "confidence": 0.0 to 1.0,
  "skip_reason": "only if include=false, one sentence why",
  "description": "1-2 sentence parent-friendly description (only if include=true)",
  "emoji": "single emoji that best represents this specific activity (only if include=true)",
  "interest_tags": [...],
  "vibe_tags": [...],
  "best_age_range": [...],
  "cost_tier": "free" or "paid",
  "indoor_outdoor": "outdoor",
  "weather_sensitivity": "soft_avoid_rain",
  "reasoning": "one sentence why this is worth including (if include=true)"
}"""


async def scrape_events() -> list[dict]:
    """Scrape all Thrive City events from Chase Center."""
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(THRIVE_CITY_URL, wait_until="networkidle", timeout=30000)

        cards = await page.query_selector_all("div.event[data-date]")
        events = []

        for card in cards:
            try:
                data_date = await card.get_attribute("data-date")  # ISO UTC
                name_el = await card.query_selector("a[class*='line-clamp']")
                if not name_el:
                    continue

                name = (await name_el.inner_text()).strip()
                href = await name_el.get_attribute("href") or ""
                source_url = BASE_URL + href if href.startswith("/") else href
                source_id = href.strip("/").replace("/", "-")

                # Parse UTC datetime
                starts_at = None
                if data_date:
                    try:
                        dt = datetime.fromisoformat(data_date.replace("+0000", "+00:00"))
                        starts_at = dt.isoformat()
                    except Exception:
                        pass

                events.append({
                    "source": "thrive_city",
                    "source_id": source_id,
                    "source_url": source_url,
                    "name": name,
                    "address": "Thrive City, Chase Center, San Francisco, CA 94158",
                    "lat": LAT,
                    "lng": LNG,
                    "starts_at": starts_at,
                    "ends_at": None,
                })
            except Exception as e:
                print(f"  Error parsing card: {e}")

        await browser.close()
        return events


def classify(ai_client: anthropic.Anthropic, event: dict) -> dict:
    prompt = (
        f"Event: {event['name']}\n"
        f"Date: {event.get('starts_at', 'unknown')}\n"
        f"Venue: Thrive City outdoor plaza, Chase Center, SF\n"
        f"URL: {event.get('source_url', '')}"
    )
    msg = ai_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=768,
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
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end])
        raise


def build_row(event: dict, classification: dict) -> dict:
    return {
        "name": event["name"],
        "emoji": classification.get("emoji") or None,
        "description": classification.get("description") or None,
        "address": event["address"],
        "neighborhood": "Mission Bay",
        "lat": event["lat"],
        "lng": event["lng"],
        "starts_at": event["starts_at"],
        "ends_at": event.get("ends_at"),
        "source": event["source"],
        "source_id": event["source_id"],
        "source_url": event["source_url"],
        "interest_tags": classification.get("interest_tags", []),
        "vibe_tags": classification.get("vibe_tags", []),
        "best_age_range": classification.get("best_age_range", []),
        "cost_tier": classification.get("cost_tier", "free"),
        "indoor_outdoor": "outdoor",
        "weather_sensitivity": "soft_avoid_rain",
        "kid_friendly": True,
        "status": "pending_review",
        "ai_confidence": classification.get("confidence"),
        "ai_raw_response": classification,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("Scraping Thrive City events from Chase Center...\n")
    events = asyncio.run(scrape_events())
    print(f"Found {len(events)} events.\n")

    if not events:
        print("Nothing to process.")
        return

    ai_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    db_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if not args.dry_run else None

    # Skip events already in Supabase
    if db_client:
        source_ids = [e["source_id"] for e in events]
        existing = db_client.table("events").select("source_id").eq("source", "thrive_city").in_("source_id", source_ids).execute()
        existing_ids = {r["source_id"] for r in existing.data}
        events = [e for e in events if e["source_id"] not in existing_ids]
        if existing_ids:
            print(f"Skipping {len(existing_ids)} already-existing events.\n")

    print(f"Classifying {len(events)} new events with Claude Haiku...\n")

    included = []
    skipped = 0

    for event in events:
        classification = classify(ai_client, event)
        if not classification.get("include"):
            reason = classification.get("skip_reason", "quality filter")
            print(f"  ✗ SKIP  {event['name'][:55]:<55} — {reason[:50]}")
            skipped += 1
        else:
            row = build_row(event, classification)
            included.append(row)
            print(f"  ✓ KEEP  {event['name'][:55]:<55} | {', '.join(row['interest_tags'])}")

    print(f"\n{'='*60}")
    print(f"Kept: {len(included)}  |  Skipped: {skipped}")

    if args.dry_run or not included:
        print("\n--- dry run, nothing written ---" if args.dry_run else "\nNothing to write.")
        return

    print("\nWriting to Supabase...")
    db_client.table("events").insert(included).execute()
    print(f"Done. {len(included)} events written with status='pending_review'.")


if __name__ == "__main__":
    main()
