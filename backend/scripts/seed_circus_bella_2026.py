"""
seed_circus_bella_2026.py
Seeds remaining 2026 Circus Bella "AH HA!" SF tour dates into pending_review.
Run once — skips any already-existing source_ids.

Already captured elsewhere:
  - YBG (June 26-27) — via fetch_ybg_events.py (past)
  - Salesforce Park (July 19) — via fetch_salesforce_park_events.py

Skipped (outside SF):
  - Burgess Park July 4 — Menlo Park
  - Burlingame Town Square July 25 — Burlingame

Usage:
  python seed_circus_bella_2026.py [--dry-run]
"""

import os
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from supabase import create_client

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../.env"))

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

SF_TZ = ZoneInfo("America/Los_Angeles")
SOURCE_URL = "https://www.circusbella.org/circus-in-the-parks"

DESCRIPTION = (
    "Circus Bella's free touring show AH HA! brings acrobats, aerialists, jugglers, "
    "clowns, and a live 6-piece band to SF parks — approximately 60 minutes, all ages welcome. "
    "Bring a blanket or low chair; no seats provided."
)

# (venue_name, address, neighborhood, lat, lng, date, [(start_time, end_time), ...])
SHOWS = [
    (
        "Circus Bella: AH HA! at Lincoln Square Park",
        "1661 Buchanan St, San Francisco, CA 94115",
        "Japantown",
        37.7851, -122.4310,
        "2026-07-09",
        [("18:00", "19:00")],
    ),
    (
        "Circus Bella: AH HA! at North Beach",
        "Washington Square Park, San Francisco, CA 94133",
        "North Beach",
        37.8006, -122.4117,
        "2026-07-17",
        [("18:00", "19:00")],
    ),
    (
        "Circus Bella: AH HA! at PROXY Hayes Valley",
        "432 Octavia Blvd, San Francisco, CA 94102",
        "Hayes Valley",
        37.7745, -122.4228,
        "2026-07-18",
        [("13:00", "14:00"), ("15:00", "16:00")],
    ),
    (
        "Circus Bella: AH HA! at Union Square",
        "Union Square, San Francisco, CA 94108",
        "Union Square",
        37.7879, -122.4075,
        "2026-07-26",
        [("13:00", "14:00"), ("15:00", "16:00")],
    ),
]


def build_rows() -> list[dict]:
    rows = []
    for name, address, neighborhood, lat, lng, date_str, times in SHOWS:
        for start_time, end_time in times:
            starts_at = datetime.fromisoformat(f"{date_str}T{start_time}:00").replace(tzinfo=SF_TZ)
            ends_at = datetime.fromisoformat(f"{date_str}T{end_time}:00").replace(tzinfo=SF_TZ)
            time_slug = start_time.replace(":", "")
            source_id = f"cb2026_{date_str.replace('-', '')}_{time_slug}"

            rows.append({
                "name": name,
                "emoji": "🎪",
                "description": DESCRIPTION,
                "address": address,
                "neighborhood": neighborhood,
                "lat": lat,
                "lng": lng,
                "starts_at": starts_at.isoformat(),
                "ends_at": ends_at.isoformat(),
                "source": "circus_bella",
                "source_id": source_id,
                "source_url": SOURCE_URL,
                "interest_tags": ["arts", "music", "community"],
                "vibe_tags": ["creative", "social", "cultural"],
                "best_age_range": ["Baby (0-1)", "Toddler (1-3)", "Preschool (3-5)", "Older Kids (6-9)", "All Ages"],
                "cost_tier": "free",
                "indoor_outdoor": "outdoor",
                "weather_sensitivity": "soft_avoid_rain",
                "requires_reservation": False,
                "reservation_note": None,
                "kid_friendly": True,
                "status": "pending_review",
                "ai_confidence": 1.0,
                "ai_raw_response": {},
            })
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rows = build_rows()
    print(f"Circus Bella AH HA! 2026 — {len(rows)} SF performances\n")
    for row in rows:
        print(f"  {row['name'][:55]:<55} | {row['starts_at'][:16]}")

    if args.dry_run:
        print("\n--- dry run, nothing written ---")
        return

    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    source_ids = [r["source_id"] for r in rows]
    existing = db.table("events").select("source_id").eq("source", "circus_bella").in_("source_id", source_ids).execute()
    existing_ids = {r["source_id"] for r in existing.data}
    new_rows = [r for r in rows if r["source_id"] not in existing_ids]

    if existing_ids:
        print(f"\nSkipping {len(existing_ids)} already-existing shows.")

    if not new_rows:
        print("Nothing new to write.")
        return

    print(f"\nWriting {len(new_rows)} shows to Supabase...")
    db.table("events").insert(new_rows).execute()
    print(f"Done. {len(new_rows)} shows written with status='pending_review'.")


if __name__ == "__main__":
    main()
