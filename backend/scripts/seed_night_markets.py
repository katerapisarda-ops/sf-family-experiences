"""
seed_night_markets.py
Seeds SF night market events into Supabase.
Run weekly to keep upcoming occurrences populated.

Usage:
  python seed_night_markets.py [--weeks N] [--dry-run]
"""

import os
import argparse
from datetime import date, datetime, timedelta
from dotenv import load_dotenv
from supabase import create_client

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../.env"))

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

SF_TZ_OFFSET = "-07:00"

TAGS = {
    "interest_tags": ["food", "community", "music"],
    "vibe_tags": ["social", "foodie", "cultural"],
    "best_age_range": ["Toddler (1-3)", "Preschool (3-5)", "Older Kids (6-9)", "All Ages"],
    "cost_tier": "free",
    "indoor_outdoor": "outdoor",
    "weather_sensitivity": "soft_avoid_rain",
    "kid_friendly": True,
    "status": "approved",
    "ai_confidence": 1.0,
}


def nth_weekday(year: int, month: int, n: int, weekday: int) -> date:
    """Return the nth occurrence of weekday (0=Mon…6=Sun) in a given month."""
    first = date(year, month, 1)
    delta = (weekday - first.weekday()) % 7
    first_occurrence = first + timedelta(days=delta)
    return first_occurrence + timedelta(weeks=n - 1)


def make_row(name: str, slug: str, d: date, start_time: str, end_time: str,
             address: str, lat: float, lng: float, description: str,
             source_url: str = "") -> dict:
    source_id = f"night-market-{slug}-{d.isoformat()}"
    return {
        "source": "night_market",
        "source_id": source_id,
        "source_url": source_url,
        "name": name,
        "description": description,
        "address": address,
        "lat": lat,
        "lng": lng,
        "starts_at": f"{d.isoformat()}T{start_time}:00{SF_TZ_OFFSET}",
        "ends_at": f"{d.isoformat()}T{end_time}:00{SF_TZ_OFFSET}",
        **TAGS,
    }


def generate_rows(weeks: int) -> list[dict]:
    today = date.today()
    cutoff = today + timedelta(weeks=weeks)
    rows = []

    # ── Castro Night Market ──────────────────────────────────────
    # 3rd Friday of every month, 18th Street, ~5–9 PM
    for month_offset in range(4):  # cover next 4 months
        d = today.replace(day=1) + timedelta(days=31 * month_offset)
        d = d.replace(day=1)
        occurrence = nth_weekday(d.year, d.month, 3, 4)  # 3rd Friday
        if today <= occurrence <= cutoff:
            rows.append(make_row(
                name="Castro Night Market",
                slug="castro",
                d=occurrence,
                start_time="17:00", end_time="21:00",
                address="18th Street, Castro, San Francisco, CA 94114",
                lat=37.7610, lng=-122.4348,
                description="Monthly night market on 18th Street in the Castro — queer-owned vendors, local food, artisan goods, and live music in a lively outdoor setting.",
            ))

    # ── Fort Mason Friday Night Market ───────────────────────────
    # Monthly on the 3rd Friday, starting April 17, 2026
    fort_mason_start = date(2026, 4, 17)
    for month_offset in range(4):
        d = fort_mason_start.replace(day=1) + timedelta(days=31 * month_offset)
        d = d.replace(day=1)
        occurrence = nth_weekday(d.year, d.month, 3, 4)  # 3rd Friday
        if occurrence < fort_mason_start:
            continue
        if today <= occurrence <= cutoff:
            rows.append(make_row(
                name="Fort Mason Friday Night Market",
                slug="fort-mason",
                d=occurrence,
                start_time="17:00", end_time="21:00",
                address="Fort Mason Center, Marina Blvd, San Francisco, CA 94123",
                lat=37.8065, lng=-122.4322,
                description="Monthly night market at Fort Mason with 100+ local makers, 15+ food trucks, and live entertainment curated by Stern Grove Festival.",
                source_url="https://fortmason.org",
            ))

    # ── Sunset Night Market ──────────────────────────────────────
    # Themed series on Irving Street
    sunset_events = [
        (date(2026, 6, 12), "Sunset Night Market – Dragon Boat Festival", "Celebrating the Dragon Boat Festival with 35+ food vendors, live performances, and local crafts on Irving Street in the Inner Sunset."),
        (date(2026, 9, 25), "Sunset Night Market – Autumn Moon Festival", "Mid-Autumn Moon Festival celebration on Irving Street with food vendors, mooncakes, live performances, and local crafts in the Inner Sunset."),
        (date(2026, 12, 11), "Sunset Night Market – Dong Zhi Holiday Market", "Holiday night market on Irving Street celebrating the Dong Zhi winter solstice festival with seasonal food, vendors, and community festivities."),
    ]
    for d, name, description in sunset_events:
        if today <= d <= cutoff:
            rows.append(make_row(
                name=name,
                slug=f"sunset-{d.isoformat()}",
                d=d,
                start_time="17:00", end_time="21:00",
                address="Irving Street, Inner Sunset, San Francisco, CA 94122",
                lat=37.7634, lng=-122.4692,
                description=description,
            ))

    # ── Cole Valley Nights ───────────────────────────────────────
    # Thursdays 4–9 PM, Cole Street between Carl and Parnassus
    cole_events = [
        (date(2026, 3, 19), "Cole Valley Nights – Spring Fling", "Spring Fling-themed night market on Cole Street with local eateries, live music, special activities, and neighborhood businesses."),
        (date(2026, 4, 16), "Cole Valley Nights – Party for the Planet", "Earth Day-inspired night market on Cole Street celebrating sustainability with local vendors, food, live music, and eco-themed activities."),
        (date(2026, 5, 21), "Cole Valley Nights – Cinema in the Valley", "Cinema-themed night market on Cole Street with local vendors, food, live entertainment, and movie-inspired fun for the whole family."),
        (date(2026, 8, 20), "Cole Valley Nights – Cole Valley (State) Fair", "State Fair-themed night market on Cole Street with local food vendors, games, live entertainment, and classic fair vibes in the neighborhood."),
    ]
    for d, name, description in cole_events:
        if today <= d <= cutoff:
            rows.append(make_row(
                name=name,
                slug=f"cole-valley-{d.isoformat()}",
                d=d,
                start_time="16:00", end_time="21:00",
                address="Cole Street (between Carl & Parnassus Ave), Cole Valley, San Francisco, CA 94117",
                lat=37.7671, lng=-122.4488,
                description=description,
            ))

    # ── Heart of the Richmond Night Market ───────────────────────
    # 3rd Saturday of each month, 4–8 PM, Clement Street (22nd–25th Ave)
    richmond_months = [5, 6, 7, 8]  # May, June, July, August
    for month in richmond_months:
        d = nth_weekday(2026, month, 3, 5)  # 3rd Saturday
        if today <= d <= cutoff:
            rows.append(make_row(
                name="Heart of the Richmond Night Market",
                slug=f"richmond-{d.isoformat()}",
                d=d,
                start_time="16:00", end_time="20:00",
                address="Clement Street (22nd–25th Ave), Richmond District, San Francisco, CA 94121",
                lat=37.7823, lng=-122.4793,
                description="Family-friendly night market on Clement Street in the Richmond District with food, shopping, live entertainment, and activities for kids.",
            ))

    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--weeks", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rows = generate_rows(args.weeks)
    print(f"Generated {len(rows)} night market occurrences.\n")
    for r in rows:
        print(f"  {r['name']:<40} {r['starts_at'][:10]}")

    if args.dry_run:
        print("\n--- dry run, nothing written ---")
        return

    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    source_ids = [r["source_id"] for r in rows]
    existing = db.table("events").select("source_id").eq("source", "night_market").in_("source_id", source_ids).execute()
    existing_ids = {r["source_id"] for r in existing.data}
    new_rows = [r for r in rows if r["source_id"] not in existing_ids]

    if existing_ids:
        print(f"\nSkipping {len(existing_ids)} already-existing occurrences.")
    if new_rows:
        db.table("events").insert(new_rows).execute()
    print(f"Done. {len(new_rows)} new night market occurrences written.")


if __name__ == "__main__":
    main()
