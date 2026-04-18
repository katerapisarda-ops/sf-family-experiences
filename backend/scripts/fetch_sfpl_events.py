"""
fetch_sfpl_events.py
Fetches Early Childhood events from SF Public Library,
classifies with Claude Haiku (quality + taxonomy),
writes approved events to Supabase as 'pending_review'.

Usage:
  python fetch_sfpl_events.py [--pages N] [--dry-run]

  --pages N   Number of pages to fetch (10 events/page). Default: 3.
  --dry-run   Classify but don't write to Supabase.
"""

import os
import re
import json
import time
import argparse
from datetime import datetime, timezone
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup
import anthropic
from supabase import create_client

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../.env"))

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

SFPL_AJAX_URL = "https://sfpl.org/views/ajax"
SFPL_BASE_URL = "https://sfpl.org"

# Branch name → approximate lat/lng for geo storage
BRANCH_COORDS = {
    "Main": (37.7786, -122.4158),
    "Mission": (37.7599, -122.4148),
    "Sunset": (37.7531, -122.4890),
    "Noe Valley": (37.7507, -122.4337),
    "Excelsior": (37.7237, -122.4319),
    "North Beach": (37.8006, -122.4095),
    "Richmond": (37.7799, -122.4653),
    "Park": (37.7708, -122.4553),
    "Glen Park": (37.7330, -122.4335),
    "Eureka Valley": (37.7612, -122.4349),
    "Castro": (37.7612, -122.4349),
    "Potrero": (37.7586, -122.4072),
    "Bernal Heights": (37.7392, -122.4156),
    "Bayview": (37.7294, -122.3886),
    "Visitacion Valley": (37.7134, -122.4085),
    "Ingleside": (37.7237, -122.4495),
    "West Portal": (37.7406, -122.4660),
    "Chinatown": (37.7952, -122.4067),
    "Western Addition": (37.7812, -122.4379),
    "Anza": (37.7799, -122.4653),
    "Marina": (37.8024, -122.4368),
    "Portola": (37.7237, -122.4085),
    "Ocean View": (37.7194, -122.4618),
    "Merced": (37.7258, -122.4811),
    "Mission Bay": (37.7694, -122.3927),
}

SYSTEM_PROMPT = """You are a quality filter for a curated family activity app in San Francisco.
You will receive an event from the SF Public Library calendar.

Your job is TWO things:
1. QUALITY + FAMILY CHECK — Should a busy SF parent with young kids (0-9) know about this?
   - INCLUDE: events families with young children (0-9) can attend that feel special or worth the trip:
     * "Early Learning" branded SFPL programs — always include these (e.g. "Saturday Morning Playtime", "Big SF Play Date")
     * Author visits and read-alouds
     * Science, art, music, or craft workshops for kids
     * Animal-related events (e.g. "Puppy Dog Tales" - kids read to therapy dogs)
     * Cultural/multicultural celebrations
     * Open-ended play (LEGO, Magna Tiles, etc.)
     * Anything clearly designed for young children with a specific theme or activity
   - SKIP:
     * Adult-only events (tax prep, senior programs, adult lectures, teen programs 13+)
     * Generic recurring storytime at a branch with no special theme or guest (e.g. "Storytime: For Families" or "Storytime: For Babies" at a branch)
     * Cancelled events
   - When in doubt about a children's event, INCLUDE it

2. CLASSIFY — If including, assign taxonomy tags.

TAXONOMY:
- interest_tags (pick 1-3): nature, arts, sports, food, music, science, history, animals, water, community
- vibe_tags (pick 1-3): chill, adventurous, educational, social, creative, outdoorsy, foodie, cultural, romantic
- best_age_range (pick all that apply): Baby (0-1), Toddler (1-3), Preschool (3-5), Older Kids (6-9), All Ages
- cost_tier: always "free" for SFPL events
- indoor_outdoor: always "indoor" for SFPL events
- weather_sensitivity: always "none" for SFPL events

3. DESCRIBE — If including, write a short 1-2 sentence description a parent would actually find useful.
   - Be specific and practical: what will kids do, what makes it special, any useful logistics
   - Example: "Kids read aloud to certified therapy dogs at the library — a low-pressure way to build reading confidence. Drop-in, no registration needed."
   - Avoid generic filler like "a fun event for the whole family"

4. EMOJI — Pick a single emoji that best represents this specific event (not the category — the actual activity).
   Choose something specific: 🎨 painting, 📚 storytime/reading, 🫧 bubbles, 🧵 crafts/sewing, 🎭 theater/puppets,
   🎵 music/singing, 🐾 animals, 🔬 science, 🌿 nature, 🍎 food, ⚽ sports, 💃 dance, 🎪 circus/performance,
   🥚 egg hunt, 🎬 movie, 🌊 water, 🧱 building/lego, 🎠 fair/festival, 🌙 night event, 🎤 storytelling

Respond ONLY with valid JSON:
{
  "include": true or false,
  "confidence": 0.0 to 1.0,
  "skip_reason": "only if include=false, one sentence why",
  "description": "1-2 sentence parent-friendly description (only if include=true)",
  "emoji": "single emoji (only if include=true)",
  "interest_tags": [...],
  "vibe_tags": [...],
  "best_age_range": [...],
  "cost_tier": "free",
  "indoor_outdoor": "indoor",
  "weather_sensitivity": "none",
  "reasoning": "one sentence why this is worth including (if include=true)"
}"""


def fetch_page(page: int) -> list[dict]:
    """Fetch one page of SFPL events, return all event cards."""
    resp = requests.get(
        f"{SFPL_BASE_URL}/events",
        params={"page": page},
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"},
        timeout=15,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    events = []
    for card in soup.select(".event--teaser"):
        event = parse_card(card)
        if event:
            events.append(event)

    return events


def parse_card(card) -> dict | None:
    """Parse a single event card into a dict."""
    title_el = card.select_one(".event__title a, .event__name a")
    if not title_el:
        return None

    name = title_el.get_text(strip=True)
    path = title_el.get("href", "")
    source_url = SFPL_BASE_URL + path if path.startswith("/") else path

    # Source ID from URL slug
    source_id = path.strip("/").replace("/", "-")

    # Date — extract from date text like "Friday, 3/20/2026, 10:00 - 11:00"
    date_el = card.select_one(".event__date .field__item, .field--name-field-event-date-and-time")
    date_text = date_el.get_text(strip=True) if date_el else ""
    starts_at, ends_at = parse_date(date_text)

    # Location / branch
    location_el = card.select_one(".event__location, .field--name-field-event-location")
    branch = location_el.get_text(strip=True) if location_el else ""
    address = f"{branch} Branch, San Francisco Public Library" if branch else "San Francisco Public Library"

    lat, lng = None, None
    for key, coords in BRANCH_COORDS.items():
        if key.lower() in branch.lower():
            lat, lng = coords
            break

    # Topics
    topics_el = card.select(".field--name-field-event-topic .field__item")
    topics = [t.get_text(strip=True) for t in topics_el]

    audience_el = card.select(".field--name-field-event-audience .field__item")
    audience = [a.get_text(strip=True) for a in audience_el]

    return {
        "source": "sfpl",
        "source_id": source_id,
        "source_url": source_url,
        "name": name,
        "description": "",  # fetched lazily if needed; description is on detail page
        "address": address,
        "lat": lat,
        "lng": lng,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "sfpl_topics": topics,
        "sfpl_branch": branch,
        "sfpl_audience": audience,
    }


def parse_date(text: str):
    """Parse 'Friday, 3/20/2026, 10:00 - 11:00' → (starts_at ISO, ends_at ISO)."""
    if not text:
        return None, None
    try:
        # Extract date and time range, optionally with AM/PM
        m = re.search(r"(\d{1,2}/\d{1,2}/\d{4}),?\s*(\d{1,2}:\d{2}\s*(?:AM|PM)?)\s*-\s*(\d{1,2}:\d{2}\s*(?:AM|PM)?)", text, re.IGNORECASE)
        if not m:
            return None, None
        date_str, start_str, end_str = m.group(1), m.group(2).strip(), m.group(3).strip()
        date = datetime.strptime(date_str, "%m/%d/%Y")

        def parse_time(t: str):
            if re.search(r"[AP]M", t, re.IGNORECASE):
                return datetime.strptime(t.upper(), "%I:%M %p")
            dt = datetime.strptime(t, "%H:%M")
            # Library events never happen between 1–8 AM — assume PM for those hours
            if 1 <= dt.hour <= 8:
                dt = dt.replace(hour=dt.hour + 12)
            return dt

        start_t = parse_time(start_str)
        end_t = parse_time(end_str)
        sf_tz = "-07:00"  # PDT — adjust to -08:00 for PST in winter if needed
        starts_at = f"{date.strftime('%Y-%m-%d')}T{start_t.strftime('%H:%M:%S')}{sf_tz}"
        ends_at = f"{date.strftime('%Y-%m-%d')}T{end_t.strftime('%H:%M:%S')}{sf_tz}"
        return starts_at, ends_at
    except Exception:
        return None, None


def classify(ai_client: anthropic.Anthropic, event: dict) -> dict:
    topics_str = ", ".join(event.get("sfpl_topics", [])) or "none listed"
    audience_str = ", ".join(event.get("sfpl_audience", [])) or "unspecified"
    prompt = (
        f"Event: {event['name']}\n"
        f"Audience: {audience_str}\n"
        f"Branch: {event.get('sfpl_branch', 'unknown')}\n"
        f"Date: {event.get('starts_at', 'unknown')}\n"
        f"Topics: {topics_str}"
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
        # Extract just the JSON object robustly
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end])
        raise


def build_row(event: dict, classification: dict) -> dict:
    return {
        "name": event["name"],
        "description": classification.get("description") or event.get("description") or None,
        "address": event["address"],
        "neighborhood": event.get("sfpl_branch") or None,
        "lat": event.get("lat"),
        "lng": event.get("lng"),
        "starts_at": event["starts_at"],
        "ends_at": event.get("ends_at"),
        "source": event["source"],
        "source_id": event["source_id"],
        "source_url": event["source_url"],
        "emoji": classification.get("emoji") or None,
        "interest_tags": classification.get("interest_tags", []),
        "vibe_tags": classification.get("vibe_tags", []),
        "best_age_range": classification.get("best_age_range", []),
        "cost_tier": "free",
        "indoor_outdoor": "indoor",
        "weather_sensitivity": "none",
        "kid_friendly": True,
        "status": "pending_review",
        "ai_confidence": classification.get("confidence"),
        "ai_raw_response": classification,
    }


def classify_date(starts_at: str | None, days_ahead: int) -> str:
    """Return 'in_window', 'past', or 'future' for a given starts_at."""
    if not starts_at:
        return "past"
    try:
        dt = datetime.fromisoformat(starts_at)
        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        # Compare by date only so today's events aren't excluded mid-day
        today = now.date()
        event_date = dt.date()
        cutoff = today + __import__("datetime").timedelta(days=days_ahead)
        if event_date < today:
            return "past"
        elif event_date > cutoff:
            return "future"
        else:
            return "in_window"
    except Exception:
        return "past"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, default=50, help="Max pages to fetch before stopping (stops early once window is reached)")
    parser.add_argument("--days-ahead", type=int, default=7, help="Only include events within this many days. Default: 7")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    ai_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    db_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY) if not args.dry_run else None

    print(f"Fetching SFPL events within {args.days_ahead} days (up to {args.pages} pages max)...\n")

    all_events = []
    for page in range(args.pages):
        events = fetch_page(page)

        in_window, past, future = [], [], []
        for e in events:
            bucket = classify_date(e.get("starts_at"), args.days_ahead)
            if bucket == "in_window":
                in_window.append(e)
            elif bucket == "past":
                past.append(e)
            else:
                future.append(e)

        print(f"  Page {page + 1}: {len(in_window)} in window, {len(past)} past, {len(future)} beyond {args.days_ahead} days")
        all_events.extend(in_window)

        # Only stop when we hit events beyond the forward window
        if len(future) > 0:
            print(f"  → Reached {args.days_ahead}-day limit, stopping early")
            break

        time.sleep(0.5)  # be polite to SFPL

    # Deduplicate by source_id
    seen = set()
    unique = []
    for e in all_events:
        if e["source_id"] not in seen:
            seen.add(e["source_id"])
            unique.append(e)
    print(f"\nTotal unique events: {len(unique)}")

    valid = unique

    # Pre-filter obvious skips by name before paying for Claude calls
    SKIP_PREFIXES = [
        "storytime:", "services:", "tutorial:", "book club:",
        "canceled:", "cancelled:", "full:", "aarp", "tax prep",
        "knitting", "walking club", "qigong", "chess club",
        "puzzle swap", "crochet", "budgeting for teens",
    ]
    def is_obvious_skip(name: str) -> bool:
        n = name.lower()
        return any(n.startswith(p) or p in n for p in SKIP_PREFIXES)

    pre_filtered = [e for e in valid if not is_obvious_skip(e["name"])]
    pre_skipped = len(valid) - len(pre_filtered)
    if pre_skipped:
        print(f"Pre-filtered {pre_skipped} obvious skips (storytime, adult services, etc.)")

    print(f"\nClassifying {len(pre_filtered)} events with Claude Haiku...\n")
    valid = pre_filtered

    included = []
    skipped_quality = 0

    for i, event in enumerate(valid):
        if i > 0 and i % 45 == 0:
            print("  (rate limit pause 65s...)")
            time.sleep(65)
        classification = classify(ai_client, event)

        if not classification.get("include"):
            reason = classification.get("skip_reason", "quality filter")
            print(f"  ✗ SKIP  {event['name'][:55]:<55} — {reason[:50]}")
            skipped_quality += 1
        else:
            row = build_row(event, classification)
            included.append(row)
            print(f"  ✓ KEEP  {event['name'][:55]:<55} | {', '.join(row['interest_tags'])}")

    print(f"\n{'='*60}")
    print(f"Kept: {len(included)}  |  Skipped (quality): {skipped_quality}")

    if args.dry_run or not included:
        print("\n--- dry run, nothing written ---" if args.dry_run else "\nNothing to write.")
        return

    print("\nWriting to Supabase...")
    # Fetch existing source_ids to avoid overwriting already-reviewed events
    source_ids = [r["source_id"] for r in included]
    existing = db_client.table("events").select("source_id").eq("source", "sfpl").in_("source_id", source_ids).execute()
    existing_ids = {r["source_id"] for r in existing.data}

    new_rows = [r for r in included if r["source_id"] not in existing_ids]
    if existing_ids:
        print(f"  Skipping {len(existing_ids)} already-existing events (approved/pending).")
    if new_rows:
        db_client.table("events").insert(new_rows).execute()
    print(f"Done. {len(new_rows)} new events written with status='pending_review'.")


if __name__ == "__main__":
    main()
