"""
event_pipeline.py
Fetches events (mock or live), classifies with Claude Haiku,
writes to Supabase events table with status='pending_review'.

Usage: python event_pipeline.py [--dry-run]
"""

import os
import json
import argparse
from datetime import datetime, timezone
from dotenv import load_dotenv
import anthropic
from supabase import create_client

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../.env"))

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

# Fixed taxonomy Claude must choose from
TAXONOMY = {
    "interest_tags": ["nature", "arts", "sports", "food", "music", "science", "history", "animals", "water", "community"],
    "vibe_tags": ["chill", "adventurous", "educational", "social", "creative", "outdoorsy", "foodie", "cultural", "romantic"],
    "age_ranges": ["All Ages", "Baby (0-1)", "Toddler (1-3)", "Preschool (3-5)", "Older Kids (6-9)"],
    "cost_tiers": ["free", "$", "$$", "$$$"],
    "indoor_outdoor": ["indoor", "outdoor", "both"],
    "weather_sensitivity": ["none", "avoid_rain", "soft_avoid_rain"],
}

# ---------------------------------------------------------------
# Mock events — realistic SF events, mix of family/non-family
# ---------------------------------------------------------------
MOCK_EVENTS = [
    {
        "source": "mock",
        "source_id": "mock-001",
        "source_url": "https://sfrecpark.org/event/001",
        "name": "Toddler Story Time at Koret Children's Quarter",
        "description": "Join us for a fun story time session for toddlers ages 2-4. We'll read picture books, sing songs, and do simple crafts. Parents and caregivers welcome.",
        "address": "Koret Children's Quarter, Golden Gate Park, San Francisco, CA",
        "lat": 37.7694,
        "lng": -122.4588,
        "starts_at": "2026-03-22T10:00:00-07:00",
        "ends_at": "2026-03-22T11:00:00-07:00",
    },
    {
        "source": "mock",
        "source_id": "mock-002",
        "source_url": "https://sfrecpark.org/event/002",
        "name": "Adult Pottery Workshop — Mission Cultural Center",
        "description": "An evening pottery class for adults. Learn hand-building techniques. Wine and cheese provided. 21+ only.",
        "address": "2868 Mission St, San Francisco, CA 94110",
        "lat": 37.7524,
        "lng": -122.4184,
        "starts_at": "2026-03-25T19:00:00-07:00",
        "ends_at": "2026-03-25T21:00:00-07:00",
    },
    {
        "source": "mock",
        "source_id": "mock-003",
        "source_url": "https://sfrecpark.org/event/003",
        "name": "Family Nature Walk — Crissy Field",
        "description": "A guided nature walk along the waterfront at Crissy Field. Learn about local birds, plants, and the history of the area. Free, all ages welcome, stroller friendly.",
        "address": "Crissy Field, San Francisco, CA 94129",
        "lat": 37.8036,
        "lng": -122.4672,
        "starts_at": "2026-03-23T09:00:00-07:00",
        "ends_at": "2026-03-23T10:30:00-07:00",
    },
    {
        "source": "mock",
        "source_id": "mock-004",
        "source_url": "https://sfrecpark.org/event/004",
        "name": "Kids Science Day — California Academy of Sciences",
        "description": "Hands-on science activities for children ages 5-10. Explore live animals, planetarium shows, and interactive exhibits. Ticket required.",
        "address": "55 Music Concourse Dr, San Francisco, CA 94118",
        "lat": 37.7699,
        "lng": -122.4661,
        "starts_at": "2026-03-29T10:00:00-07:00",
        "ends_at": "2026-03-29T17:00:00-07:00",
    },
    {
        "source": "mock",
        "source_id": "mock-005",
        "source_url": "https://sfrecpark.org/event/005",
        "name": "Bar Crawl — SoMa Neighborhood",
        "description": "Join the annual SoMa bar crawl. Visit 8 bars in one night. Must be 21+. Wristband required.",
        "address": "SoMa, San Francisco, CA",
        "lat": 37.7785,
        "lng": -122.4056,
        "starts_at": "2026-03-28T20:00:00-07:00",
        "ends_at": "2026-03-29T02:00:00-07:00",
    },
    {
        "source": "mock",
        "source_id": "mock-006",
        "source_url": "https://sfrecpark.org/event/006",
        "name": "Baby Swim Lessons — Hamilton Recreation Center",
        "description": "Introductory swim lessons for babies 6 months to 2 years. Parent/caregiver must be in the water. Small class sizes. Registration required.",
        "address": "1900 Geary Blvd, San Francisco, CA 94115",
        "lat": 37.7836,
        "lng": -122.4350,
        "starts_at": "2026-03-21T09:30:00-07:00",
        "ends_at": "2026-03-21T10:00:00-07:00",
    },
    {
        "source": "mock",
        "source_id": "mock-007",
        "source_url": "https://sfrecpark.org/event/007",
        "name": "Farmers Market — Ferry Building",
        "description": "Weekly farmers market at the Ferry Building. Fresh produce, artisan foods, flowers, and local vendors. Free to browse.",
        "address": "1 Ferry Building, San Francisco, CA 94111",
        "lat": 37.7956,
        "lng": -122.3935,
        "starts_at": "2026-03-22T08:00:00-07:00",
        "ends_at": "2026-03-22T14:00:00-07:00",
    },
    {
        "source": "mock",
        "source_id": "mock-008",
        "source_url": "https://sfrecpark.org/event/008",
        "name": "Sunset District Community Cleanup",
        "description": "Help keep our neighborhood clean! Families welcome. Gloves and bags provided. Meet at Sunset Playground.",
        "address": "2 Sunset Blvd, San Francisco, CA 94122",
        "lat": 37.7558,
        "lng": -122.4941,
        "starts_at": "2026-03-28T09:00:00-07:00",
        "ends_at": "2026-03-28T12:00:00-07:00",
    },
    {
        "source": "mock",
        "source_id": "mock-009",
        "source_url": "https://sfrecpark.org/event/009",
        "name": "Late Night Jazz — SFJAZZ Center",
        "description": "An intimate late-night jazz session featuring local artists. Doors at 10pm. Bar service. 18+ only.",
        "address": "201 Franklin St, San Francisco, CA 94102",
        "lat": 37.7762,
        "lng": -122.4221,
        "starts_at": "2026-03-27T22:00:00-07:00",
        "ends_at": "2026-03-28T01:00:00-07:00",
    },
    {
        "source": "mock",
        "source_id": "mock-010",
        "source_url": "https://sfrecpark.org/event/010",
        "name": "Playground Playdate — Dolores Park",
        "description": "Informal meetup for families with young kids at Dolores Park playground. Bring snacks to share. Free, no registration needed.",
        "address": "Dolores Park, San Francisco, CA 94114",
        "lat": 37.7596,
        "lng": -122.4269,
        "starts_at": "2026-03-26T10:00:00-07:00",
        "ends_at": "2026-03-26T12:00:00-07:00",
    },
]


# ---------------------------------------------------------------
# Claude Haiku classification
# ---------------------------------------------------------------
SYSTEM_PROMPT = """You are a classifier for a family activity recommendation app in San Francisco.
Given an event, determine if it is family-friendly and classify it using the fixed taxonomy below.

TAXONOMY:
- interest_tags (pick 1-3): nature, arts, sports, food, music, science, history, animals, water, community
- vibe_tags (pick 1-3): chill, adventurous, educational, social, creative, outdoorsy, foodie, cultural, romantic
- best_age_range (pick all that apply): All Ages, Baby (0-1), Toddler (1-3), Preschool (3-5), Older Kids (6-9)
- cost_tier: free, $, $$, $$$
- indoor_outdoor: indoor, outdoor, both
- weather_sensitivity: none, avoid_rain, soft_avoid_rain

Respond ONLY with valid JSON in this exact format:
{
  "kid_friendly": true or false,
  "confidence": 0.0 to 1.0,
  "interest_tags": [...],
  "vibe_tags": [...],
  "best_age_range": [...],
  "cost_tier": "...",
  "indoor_outdoor": "...",
  "weather_sensitivity": "...",
  "reasoning": "one sentence"
}

If the event is clearly adult-only (alcohol-focused, 21+, late night, explicit), set kid_friendly to false."""


def classify_event(client: anthropic.Anthropic, event: dict) -> dict:
    prompt = f"Event name: {event['name']}\nDescription: {event['description']}\nAddress: {event.get('address', '')}"

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
        system=SYSTEM_PROMPT,
    )

    raw = message.content[0].text.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    parsed = json.loads(raw.strip())
    return parsed


def build_event_row(event: dict, classification: dict) -> dict:
    return {
        "name": event["name"],
        "description": event["description"],
        "address": event.get("address"),
        "lat": event.get("lat"),
        "lng": event.get("lng"),
        "starts_at": event["starts_at"],
        "ends_at": event.get("ends_at"),
        "source": event["source"],
        "source_id": event["source_id"],
        "source_url": event.get("source_url"),
        "interest_tags": classification.get("interest_tags", []),
        "vibe_tags": classification.get("vibe_tags", []),
        "best_age_range": classification.get("best_age_range", []),
        "cost_tier": classification.get("cost_tier"),
        "indoor_outdoor": classification.get("indoor_outdoor"),
        "weather_sensitivity": classification.get("weather_sensitivity"),
        "kid_friendly": classification.get("kid_friendly", True),
        "status": "pending_review",
        "ai_confidence": classification.get("confidence"),
        "ai_raw_response": classification,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Classify but don't write to Supabase")
    args = parser.parse_args()

    ai_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    db_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if not args.dry_run else None

    print(f"Processing {len(MOCK_EVENTS)} events...\n")

    rows = []
    skipped = 0
    for event in MOCK_EVENTS:
        print(f"  Classifying: {event['name']}")
        classification = classify_event(ai_client, event)

        if not classification.get("kid_friendly"):
            print(f"    → ✗ adult-only (skipped) | {classification['reasoning']}")
            skipped += 1
            continue

        row = build_event_row(event, classification)
        rows.append(row)
        print(f"    → ✓ family | confidence: {classification['confidence']} | {classification['reasoning']}")

    print(f"\n{'='*60}")
    print(f"Results: {len(rows)} family-friendly, {skipped} adult-only (dropped)")

    if args.dry_run:
        print("\n--- dry run, nothing written ---")
        return

    if not rows:
        print("Nothing to write.")
        return

    print("\nWriting to Supabase...")
    db_client.table("events").upsert(rows, on_conflict="source,source_id").execute()
    print(f"Done. {len(rows)} events written with status='pending_review'.")


if __name__ == "__main__":
    main()
