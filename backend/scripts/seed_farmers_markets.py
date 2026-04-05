"""
seed_farmers_markets.py
Generates upcoming occurrences of SF farmers markets and upserts to Supabase.
Run weekly to keep the next 8 weeks populated.

Usage:
  python seed_farmers_markets.py [--weeks N] [--dry-run]
"""

import os
import argparse
from datetime import date, datetime, timedelta
from dotenv import load_dotenv
from supabase import create_client

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../.env"))

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

# weekday: 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
MARKETS = [
    {
        "name": "Ferry Plaza Farmers Market",
        "weekday": 5,  # Saturday
        "start_time": "08:00",
        "end_time": "14:00",
        "address": "Ferry Building, San Francisco, CA 94111",
        "lat": 37.7955,
        "lng": -122.3937,
        "source_url": "https://www.cuesa.org/markets/ferry-plaza-farmers-market",
        "seasonal_months": None,
    },
    {
        "name": "Noe Valley Farmers Market",
        "weekday": 5,  # Saturday
        "start_time": "08:00",
        "end_time": "13:00",
        "address": "3861 24th St, San Francisco, CA 94114",
        "lat": 37.7507,
        "lng": -122.4337,
        "source_url": "https://noevalleyfarmersmarket.com",
        "seasonal_months": None,
    },
    {
        "name": "North Beach Farmers Market",
        "weekday": 5,  # Saturday
        "start_time": "09:00",
        "end_time": "13:00",
        "address": "659 Columbus Ave, San Francisco, CA 94133",
        "lat": 37.8006,
        "lng": -122.4095,
        "source_url": "https://northbeachfarmersmarket.com",
        "seasonal_months": list(range(5, 12)),  # May–November
    },
    {
        "name": "Clement Street Farmers Market",
        "weekday": 6,  # Sunday
        "start_time": "09:00",
        "end_time": "14:00",
        "address": "200 Clement St, San Francisco, CA 94118",
        "lat": 37.7830,
        "lng": -122.4628,
        "source_url": "https://sffarmersmarkets.org",
        "seasonal_months": None,
    },
    {
        "name": "Divisadero Farmers Market",
        "weekday": 6,  # Sunday
        "start_time": "09:00",
        "end_time": "13:00",
        "address": "1377 Fell St, San Francisco, CA 94117",
        "lat": 37.7726,
        "lng": -122.4376,
        "source_url": "https://sffarmersmarkets.org",
        "seasonal_months": None,
    },
    {
        "name": "Fort Mason Farmers Market",
        "weekday": 6,  # Sunday
        "start_time": "09:30",
        "end_time": "13:30",
        "address": "Fort Mason Center, Marina Blvd, San Francisco, CA 94109",
        "lat": 37.8065,
        "lng": -122.4322,
        "source_url": "https://sffarmersmarkets.org",
        "seasonal_months": None,
    },
]

TAGS = {
    "interest_tags": ["food", "community"],
    "vibe_tags": ["social", "foodie", "outdoorsy"],
    "best_age_range": ["Baby (0-1)", "Toddler (1-3)", "Preschool (3-5)", "Older Kids (6-9)", "All Ages"],
    "cost_tier": "free",
    "indoor_outdoor": "outdoor",
    "weather_sensitivity": "soft_avoid_rain",
}

DESCRIPTION = (
    "Browse fresh local produce, artisan foods, and flowers at one of SF's beloved weekly farmers markets. "
    "Great for a casual weekend outing with kids — most markets have food vendors and a lively atmosphere."
)

SF_TZ_OFFSET = "-07:00"  # PDT; change to -08:00 Nov–Mar


def next_weekday(from_date: date, weekday: int) -> date:
    """Return the next occurrence of `weekday` on or after `from_date`."""
    days_ahead = (weekday - from_date.weekday()) % 7
    return from_date + timedelta(days=days_ahead)


def make_source_id(market: dict, d: date) -> str:
    slug = market["name"].lower().replace(" ", "-")
    day = "thu" if market["weekday"] == 3 else ("sat" if market["weekday"] == 5 else "sun")
    return f"farmers-market-{slug}-{day}-{d.isoformat()}"


def generate_rows(weeks: int) -> list[dict]:
    today = date.today()
    rows = []

    for market in MARKETS:
        occurrence = next_weekday(today, market["weekday"])
        for _ in range(weeks):
            # Seasonal check
            if market["seasonal_months"] and occurrence.month not in market["seasonal_months"]:
                occurrence += timedelta(weeks=1)
                continue

            starts_at = f"{occurrence.isoformat()}T{market['start_time']}:00{SF_TZ_OFFSET}"
            ends_at = f"{occurrence.isoformat()}T{market['end_time']}:00{SF_TZ_OFFSET}"
            source_id = make_source_id(market, occurrence)

            rows.append({
                "source": "farmers_market",
                "source_id": source_id,
                "source_url": market["source_url"],
                "name": market["name"],
                "description": DESCRIPTION,
                "address": market["address"],
                "lat": market["lat"],
                "lng": market["lng"],
                "starts_at": starts_at,
                "ends_at": ends_at,
                "status": "approved",
                "kid_friendly": True,
                "ai_confidence": 1.0,
                **TAGS,
            })
            occurrence += timedelta(weeks=1)

    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--weeks", type=int, default=8, help="How many weeks ahead to generate. Default: 8")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rows = generate_rows(args.weeks)
    print(f"Generated {len(rows)} market occurrences across {args.weeks} weeks.\n")

    for r in rows:
        print(f"  {r['name']:<35} {r['starts_at'][:10]}")

    if args.dry_run:
        print("\n--- dry run, nothing written ---")
        return

    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    # Only insert new rows — skip any source_id already in the table
    source_ids = [r["source_id"] for r in rows]
    existing = db.table("events").select("source_id").eq("source", "farmers_market").in_("source_id", source_ids).execute()
    existing_ids = {r["source_id"] for r in existing.data}

    new_rows = [r for r in rows if r["source_id"] not in existing_ids]
    if existing_ids:
        print(f"\nSkipping {len(existing_ids)} already-existing occurrences.")

    if new_rows:
        db.table("events").insert(new_rows).execute()
        print(f"Done. {len(new_rows)} new market occurrences written.")
    else:
        print("Nothing new to write.")


if __name__ == "__main__":
    main()
