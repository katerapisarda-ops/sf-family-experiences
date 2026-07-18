"""
fetch_funcheap_events.py
Scrapes the Funcheap SF "Kids & Families" category pages,
classifies with Claude Haiku, writes to Supabase as 'pending_review'.

Funcheap is an aggregator, so events here may duplicate direct sources
(e.g. Union Square programming also comes from sfrecpark) — the review
queue is the dedup point.

Usage:
  python fetch_funcheap_events.py [--days-ahead N] [--dry-run]
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

CATEGORY_URL = "https://sf.funcheap.com/category/event/event-types/kids-families/"
SF_TZ_OFFSET = "-07:00"
MAX_PAGES = 12
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# Funcheap region CSS class -> our region value. Anything else (Sacramento,
# Tahoe, online-only) is dropped.
REGION_MAP = {
    "region-san-francisco": "sf",
    "region-east-bay": "east_bay",
    "region-north-bay": "north_bay",
    "region-peninsula": "peninsula",
    "region-south-bay": "south_bay",
}

SYSTEM_PROMPT = """You are a quality filter for a curated family activity app for San Francisco families.
You will receive an event from Funcheap SF's "Kids & Families" category — a curated list of
free and cheap Bay Area events, so most entries are already family-relevant.
Events may be in SF or in nearby regions (East Bay, Peninsula, South Bay, North Bay/Marin).
Regional events are welcome as DAY TRIPS — but apply a "worth the trip" test to anything outside
SF: would a busy SF family drive 30-60 minutes for this? INCLUDE destination events (festivals,
fairs, signature museum/venue programming, major seasonal celebrations). SKIP hyper-local
neighborhood programming (small library events, weekly plaza concerts, neighborhood block
parties, local vendor fairs) that only makes sense if you already live nearby.

Your job is TWO things:
1. QUALITY + FAMILY CHECK — Should a busy SF parent with young kids (0-9) know about this?
   - INCLUDE (when in doubt, include — these are pre-curated family events):
     * Street fairs, festivals, block parties, night markets
     * Kids performances, workshops, crafts, story times
     * Community celebrations, cultural events, outdoor movie nights (G/PG)
     * Museum free days, zoo days, hands-on activities
   - SKIP:
     * Guide/roundup articles rather than single events ("Top 10...", "Guide to...", "This Weekend's...")
     * Events explicitly 21+ or clearly adult-oriented despite the category tag
     * Contests, giveaways, or online-only events
     * Movie screenings rated PG-13 or above

2. CLASSIFY — If including, assign taxonomy tags.

TAXONOMY:
- interest_tags (pick 1-3): nature, arts, sports, food, music, science, history, animals, water, community
- vibe_tags (pick 1-3): chill, adventurous, educational, social, creative, outdoorsy, foodie, cultural
- best_age_range (pick all that apply): Baby (0-1), Toddler (1-3), Preschool (3-5), Older Kids (6-9), All Ages
- cost_tier: "free" if free, "paid" if ticketed/admission
- indoor_outdoor: "indoor", "outdoor", or "both"
- weather_sensitivity: "none" for indoor, "soft_avoid_rain" for outdoor/both

3. DESCRIBE — Write a 1-2 sentence description a parent would find useful.
   - Be specific about what kids can do and why it's worth the trip
   - Avoid generic filler

4. RESERVATION — If the event mentions reservations, registration, sign-up, tickets required, or
   limited/reserved space, set requires_reservation=true and write a brief reservation_note.
   Otherwise requires_reservation=false and reservation_note=null.

Respond ONLY with valid JSON:
{
  "include": true or false,
  "confidence": 0.0 to 1.0,
  "skip_reason": "only if include=false",
  "description": "1-2 sentence description (only if include=true)",
  "emoji": "single emoji that best represents this activity (only if include=true)",
  "neighborhood": "SF neighborhood for SF events (e.g. 'Union Square', 'Mission'); city name for regional events (e.g. 'Oakland', 'Berkeley', 'San Mateo')",
  "interest_tags": [...],
  "vibe_tags": [...],
  "best_age_range": [...],
  "cost_tier": "free" or "paid",
  "indoor_outdoor": "indoor" or "outdoor" or "both",
  "weather_sensitivity": "none" or "soft_avoid_rain",
  "requires_reservation": true or false,
  "reservation_note": "short practical note, or null",
  "reasoning": "one sentence why this is or isn't worth including"
}"""


def parse_box(box) -> dict | None:
    """Parse one .tanbox event listing. Returns None for ads/out-of-area/undated boxes."""
    classes = box.get("class", [])
    region = next((REGION_MAP[c] for c in classes if c in REGION_MAP), None)
    if not region:
        return None

    post_id = next((c.removeprefix("post-") for c in classes if re.fullmatch(r"post-\d+", c)), None)
    title_el = box.select_one(".title a")
    meta = box.select_one(".meta[data-event-date]")
    if not post_id or not title_el or not meta:
        return None

    try:
        starts = datetime.strptime(meta["data-event-date"], "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    ends = None
    if meta.get("data-event-date-end"):
        try:
            ends = datetime.strptime(meta["data-event-date-end"], "%Y-%m-%d %H:%M")
        except ValueError:
            pass

    cost_el = meta.select_one(".cost")
    cost_text = cost_el.get_text(strip=True).removeprefix("Cost:").strip() if cost_el else ""
    # Venue is the last plain span in the meta line
    venue = ""
    for span in meta.find_all("span", recursive=False):
        if not span.get("class"):
            venue = span.get_text(strip=True)

    # Description: box text minus title/meta/thumbnail
    desc_box = BeautifulSoup(str(box), "lxml")
    for sel in (".title", ".meta", ".thumbnail-wrapper"):
        for el in desc_box.select(sel):
            el.decompose()
    description = desc_box.get_text(" ", strip=True)[:500]

    return {
        "source": "funcheap",
        "region": region,
        "source_id": f"{post_id}_{starts.strftime('%Y%m%d')}",
        "source_url": title_el["href"],
        "name": title_el.get_text(strip=True),
        "raw_description": description,
        "venue": venue,
        "cost": cost_text,
        "starts_at": f"{starts.isoformat()}{SF_TZ_OFFSET}",
        "ends_at": f"{ends.isoformat()}{SF_TZ_OFFSET}" if ends else None,
        "event_date": starts.date(),
    }


def fetch_events(days_ahead: int) -> list[dict]:
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)
    events = []
    seen_ids = set()

    for page in range(1, MAX_PAGES + 1):
        url = CATEGORY_URL if page == 1 else f"{CATEGORY_URL}page/{page}/"
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=15, allow_redirects=True)
        if resp.status_code == 404:
            break
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        page_dates = []
        for box in soup.select(".tanbox"):
            event = parse_box(box)
            if not event:
                continue
            page_dates.append(event["event_date"])
            if event["source_id"] in seen_ids:
                continue
            seen_ids.add(event["source_id"])
            if today <= event["event_date"] <= cutoff:
                events.append(event)

        print(f"  Page {page}: {len(page_dates)} Bay Area events, {len(events)} in window so far")
        # Pages are chronological — stop once a whole page is past the cutoff
        if page_dates and min(page_dates) > cutoff:
            break
        time.sleep(1)

    return events


def classify(ai_client: anthropic.Anthropic, event: dict) -> dict:
    prompt = (
        f"Event: {event['name']}\n"
        f"Date: {event['starts_at']}\n"
        f"Venue: {event.get('venue') or 'unknown'} (region: {event['region']})\n"
        f"Cost: {event.get('cost') or 'unknown'}\n"
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
        "region": event["region"],
        "emoji": cl.get("emoji") or None,
        "description": cl.get("description") or None,
        "address": event.get("venue") or None,
        "neighborhood": cl.get("neighborhood") or None,
        "lat": None,
        "lng": None,
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
        "requires_reservation": cl.get("requires_reservation") or False,
        "reservation_note": cl.get("reservation_note") or None,
        "kid_friendly": True,
        "status": "pending_review",
        "ai_confidence": cl.get("confidence"),
        "ai_raw_response": cl,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days-ahead", type=int, default=17)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"Fetching Funcheap SF kids/families events (next {args.days_ahead} days)...\n")
    events = fetch_events(args.days_ahead)
    print(f"\nFound {len(events)} SF events in window.\n")

    if not events:
        print("Nothing to process.")
        return

    ai_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    db_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if not args.dry_run else None

    if db_client:
        source_ids = [e["source_id"] for e in events]
        # Only skip events that are approved or pending_review — rejected events
        # may have been rejected due to bad data and should be re-fetched.
        existing = (
            db_client.table("events")
            .select("source_id")
            .eq("source", "funcheap")
            .in_("source_id", source_ids)
            .in_("status", ["approved", "pending_review"])
            .execute()
        )
        existing_ids = {r["source_id"] for r in existing.data}
        events = [e for e in events if e["source_id"] not in existing_ids]
        if existing_ids:
            print(f"Skipping {len(existing_ids)} already-approved/pending events.\n")
        # Delete stale rejected rows so we can re-insert with correct data
        rejected_ids = [e["source_id"] for e in events]
        if rejected_ids:
            db_client.table("events").delete().eq("source", "funcheap").in_("source_id", rejected_ids).eq("status", "rejected").execute()

    print(f"Classifying {len(events)} events with Claude Haiku...\n")
    rows, skipped = [], 0

    for event in events:
        cl = classify(ai_client, event)
        if not cl.get("include"):
            print(f"  ✗ SKIP  {event['name'][:55]:<55} — {cl.get('skip_reason', '')[:50]}")
            skipped += 1
        else:
            row = build_row(event, cl)
            rows.append(row)
            print(f"  ✓ KEEP  [{event['region']:<9}] {event['name'][:48]:<48} | {', '.join(row['interest_tags'])}")

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
