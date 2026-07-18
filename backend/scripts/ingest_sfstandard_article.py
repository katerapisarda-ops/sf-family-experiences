"""
ingest_sfstandard_article.py
Extracts family-friendly events from an SF Standard "best events" article,
classifies with Claude, writes to Supabase as 'pending_review'.

Run manually with each week's article URL:
  python ingest_sfstandard_article.py --url "https://sfstandard.com/2026/07/01/best-events-sf-week-..."

Does NOT run as part of refresh_all.sh — user provides the URL each week.
"""

import os
import json
import argparse
import html as html_module
from datetime import date, datetime
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

SF_TZ = ZoneInfo("America/Los_Angeles")

EXTRACT_PROMPT = """You are extracting events from an SF Standard weekly events article.
Today is {today}.

Extract every event that could be family-friendly (for kids ages 0-9). For each event:

1. EXTRACT: name, date(s), time, venue/location, description, source_url (link if mentioned), cost
2. CLASSIFY: Is this worth a busy SF parent knowing about for kids 0-9?
   - INCLUDE: festivals, parades, outdoor events, kid-specific activities, family concerts, fireworks, fairs
   - SKIP: late-night clubs, bars, adult concerts at 9pm+, adult comedy shows, drink-focused events
3. CLASSIFY TAGS: interest_tags, vibe_tags, best_age_range, cost_tier, indoor_outdoor, weather_sensitivity
4. RESERVATION: does it require tickets, registration, or reservations?

Return ONLY valid JSON:
{{
  "events": [
    {{
      "include": true or false,
      "skip_reason": "only if include=false",
      "name": "event name",
      "description": "1-2 sentence parent-friendly description",
      "emoji": "single emoji",
      "venue": "venue or location name",
      "address": "street address if mentioned, or null",
      "neighborhood": "SF neighborhood if known, or null",
      "lat": null,
      "lng": null,
      "starts_at": "YYYY-MM-DDTHH:MM:00-07:00 (use -07:00 for PDT, guess time if not mentioned)",
      "ends_at": "YYYY-MM-DDTHH:MM:00-07:00 or null",
      "source_url": "event URL if mentioned, or null",
      "interest_tags": [...],
      "vibe_tags": [...],
      "best_age_range": [...],
      "cost_tier": "free" or "paid",
      "indoor_outdoor": "indoor", "outdoor", or "both",
      "weather_sensitivity": "none", "soft_avoid_rain", or "avoid_rain",
      "requires_reservation": true or false,
      "reservation_note": "short practical note or null"
    }}
  ]
}}

TAXONOMY:
- interest_tags (pick 1-3): nature, arts, sports, food, music, science, history, animals, water, community
- vibe_tags (pick 1-3): chill, adventurous, educational, social, creative, outdoorsy, foodie, cultural
- best_age_range (pick all that apply): Baby (0-1), Toddler (1-3), Preschool (3-5), Older Kids (6-9), All Ages

For multi-day events, create one entry with the start date/time.
If an event repeats on multiple days, create a separate entry for each day.

Article text:
{article_text}"""


def fetch_article_text(url: str) -> str:
    resp = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"},
        timeout=15,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    # Try to get article body
    article = (
        soup.select_one("article")
        or soup.select_one('[class*="article"]')
        or soup.select_one("main")
        or soup.body
    )
    return article.get_text(" ", strip=True)[:8000] if article else ""


def extract_events(ai_client: anthropic.Anthropic, article_text: str) -> list[dict]:
    today = date.today().isoformat()
    prompt = EXTRACT_PROMPT.format(today=today, article_text=article_text)

    msg = ai_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}") + 1
        data = json.loads(raw[start:end])

    return data.get("events", [])


def build_row(event: dict, article_url: str, idx: int) -> dict:
    # Generate a source_id from the article URL slug + index
    slug = article_url.rstrip("/").split("/")[-1][:40]
    source_id = f"sfs_{slug}_{idx}"

    starts_at = event.get("starts_at", "")
    ends_at = event.get("ends_at")

    return {
        "name": event["name"],
        "emoji": event.get("emoji") or None,
        "description": event.get("description") or None,
        "address": event.get("address") or event.get("venue") or None,
        "neighborhood": event.get("neighborhood") or None,
        "lat": event.get("lat"),
        "lng": event.get("lng"),
        "starts_at": starts_at,
        "ends_at": ends_at,
        "source": "sf_standard",
        "source_id": source_id,
        "source_url": event.get("source_url") or article_url,
        "interest_tags": event.get("interest_tags", []),
        "vibe_tags": event.get("vibe_tags", []),
        "best_age_range": event.get("best_age_range", []),
        "cost_tier": event.get("cost_tier", "free"),
        "indoor_outdoor": event.get("indoor_outdoor", "outdoor"),
        "weather_sensitivity": event.get("weather_sensitivity", "soft_avoid_rain"),
        "requires_reservation": event.get("requires_reservation") or False,
        "reservation_note": event.get("reservation_note") or None,
        "kid_friendly": True,
        "status": "pending_review",
        "ai_confidence": 0.8,
        "ai_raw_response": event,
    }


def main():
    parser = argparse.ArgumentParser(description="Ingest events from an SF Standard article")
    parser.add_argument("--url", required=True, help="URL of the SF Standard events article")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"Fetching article: {args.url}\n")
    article_text = fetch_article_text(args.url)
    if not article_text:
        print("Could not fetch article.")
        return

    print(f"Extracting events with Claude (article length: {len(article_text)} chars)...\n")
    ai_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    events = extract_events(ai_client, article_text)
    print(f"Claude extracted {len(events)} events.\n")

    included, skipped = [], 0
    for i, event in enumerate(events):
        if not event.get("include"):
            print(f"  ✗ SKIP  {event.get('name', '?')[:55]:<55} — {event.get('skip_reason', '')[:50]}")
            skipped += 1
        else:
            row = build_row(event, args.url, i)
            included.append(row)
            tags = ", ".join(row["interest_tags"])
            print(f"  ✓ KEEP  {event.get('name', '?')[:55]:<55} | {tags}")

    print(f"\n{'='*60}")
    print(f"Kept: {len(included)}  |  Skipped: {skipped}")

    if not included:
        print("\nNothing to write.")
        return

    if args.dry_run:
        print("\n--- dry run, nothing written ---")
        return

    db_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    # Deduplicate by source_id
    source_ids = [r["source_id"] for r in included]
    existing = db_client.table("events").select("source_id").eq("source", "sf_standard").in_("source_id", source_ids).execute()
    existing_ids = {r["source_id"] for r in existing.data}
    new_rows = [r for r in included if r["source_id"] not in existing_ids]
    if existing_ids:
        print(f"\nSkipping {len(existing_ids)} already-existing events.")

    if not new_rows:
        print("Nothing new to write.")
        return

    print(f"\nWriting {len(new_rows)} events to Supabase...")
    db_client.table("events").insert(new_rows).execute()
    print(f"Done. {len(new_rows)} events written with status='pending_review'.")


if __name__ == "__main__":
    main()


