"""
seed_kids_workshops.py
Seeds Home Depot Kids Workshop and Lowe's DIY-U Kids Workshop dates at stores
near SF. Both chains publish fixed monthly schedules but their sites are
bot-protected, so dates + project names are maintained by hand below.
Run from refresh_all.sh — inserts are idempotent (skips existing source_ids).

2026 schedules:
  Home Depot — first Saturday, 9 AM–12 PM (plus one extra date in November)
  Lowe's     — one Saturday/month (varies), sessions 10 AM–1 PM

Usage:
  python seed_kids_workshops.py [--dry-run]
"""

import os
import argparse
from datetime import date
from dotenv import load_dotenv
from supabase import create_client

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../.env"))

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

# date -> project name. Update each year when the chains publish new schedules.
HOME_DEPOT_PROJECTS = {
    date(2026, 8, 1): "Rocket Game",
    date(2026, 9, 5): "School Bus Organizer",
    date(2026, 10, 3): "Witch Candy Box",
    date(2026, 11, 7): "Dump Truck",
    date(2026, 11, 21): "Christmas Car",
    date(2026, 12, 5): "Holiday Train",
}

LOWES_PROJECTS = {
    date(2026, 7, 25): "MrBeast Swarm Jet",
    date(2026, 8, 15): "Paw Patrol: The Dino Movie",
    date(2026, 9, 12): "Haunted House",
    date(2026, 10, 17): "Firefighting Plane",
    date(2026, 11, 14): "Holiday Engine",
    date(2026, 12, 12): "Holiday Trolley Car",
}

CHAINS = {
    "home_depot": {
        "label": "Home Depot Kids Workshop",
        "projects": HOME_DEPOT_PROJECTS,
        "start_time": "09:00",
        "end_time": "12:00",
        "source_url": "https://www.homedepot.com/c/kids-workshop",
        "emoji": "🔨",
        "description_tpl": (
            "Free monthly build-it-yourself workshop for kids 5-12 — this month's project is a {project}. "
            "Kids keep the project plus a Home Depot apron, pin, and certificate."
        ),
        "reservation_note": "Register free at homedepot.com/kidsworkshop — kits are limited and registration opens about a month ahead.",
        "stores": [
            {
                "slug": "dalycity",
                "name_suffix": "Daly City",
                "address": "303 E Lake Merced Blvd, Daly City, CA 94015",
                "neighborhood": "Daly City",
                "lat": 37.7025,
                "lng": -122.4700,
            },
            {
                "slug": "colma",
                "name_suffix": "Colma",
                "address": "2 Colma Blvd, Colma, CA 94014",
                "neighborhood": "Colma",
                "lat": 37.6800,
                "lng": -122.4620,
            },
        ],
    },
    "lowes": {
        "label": "Lowe's Kids Workshop",
        "projects": LOWES_PROJECTS,
        "start_time": "10:00",
        "end_time": "13:00",
        "source_url": "https://www.lowes.com/diy-projects-and-ideas/workshops",
        "emoji": "🛠️",
        "description_tpl": (
            "Free DIY-U workshop for kids 4-11 — build a {project} with all supplies provided, "
            "and collect the monthly badge for your builder's apron."
        ),
        "reservation_note": "Register free at lowes.com/kidsclub (MyLowe's account required) — pick one of four sessions between 10 AM and 1 PM.",
        "stores": [
            {
                "slug": "sf",
                "name_suffix": "SF",
                "address": "491 Bayshore Blvd, San Francisco, CA 94124",
                "neighborhood": "Bayview",
                "lat": 37.7419,
                "lng": -122.4046,
            },
        ],
    },
}

TAGS = {
    "interest_tags": ["arts", "science", "community"],
    "vibe_tags": ["creative", "educational"],
    "best_age_range": ["Preschool (3-5)", "Older Kids (6-9)"],
    "cost_tier": "free",
    "indoor_outdoor": "indoor",
    "weather_sensitivity": "none",
}


def tz_offset(d: date) -> str:
    # DST ends Nov 1, 2026; good enough for this year's remaining dates.
    return "-08:00" if (d.month >= 11 or d.month <= 3) else "-07:00"


def generate_rows() -> list[dict]:
    today = date.today()
    rows = []

    for chain_key, chain in CHAINS.items():
        for d, project in sorted(chain["projects"].items()):
            if d < today:
                continue
            for store in chain["stores"]:
                offset = tz_offset(d)
                rows.append({
                    "source": "kids_workshop",
                    "source_id": f"kw-{chain_key}-{store['slug']}-{d.isoformat()}",
                    "source_url": chain["source_url"],
                    "name": f"{chain['label']}: {project} ({store['name_suffix']})",
                    "description": chain["description_tpl"].format(project=project),
                    "emoji": chain["emoji"],
                    "address": store["address"],
                    "neighborhood": store["neighborhood"],
                    "lat": store["lat"],
                    "lng": store["lng"],
                    "starts_at": f"{d.isoformat()}T{chain['start_time']}:00{offset}",
                    "ends_at": f"{d.isoformat()}T{chain['end_time']}:00{offset}",
                    "status": "approved",
                    "kid_friendly": True,
                    "ai_confidence": 1.0,
                    "requires_reservation": True,
                    "reservation_note": chain["reservation_note"],
                    **TAGS,
                })

    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rows = generate_rows()
    print(f"Generated {len(rows)} kids workshop occurrences.\n")

    for r in rows:
        print(f"  {r['name']:<58} {r['starts_at'][:10]}")

    if args.dry_run:
        print("\n--- dry run, nothing written ---")
        return

    db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    source_ids = [r["source_id"] for r in rows]
    existing = db.table("events").select("source_id").eq("source", "kids_workshop").in_("source_id", source_ids).execute()
    existing_ids = {r["source_id"] for r in existing.data}

    new_rows = [r for r in rows if r["source_id"] not in existing_ids]
    if existing_ids:
        print(f"\nSkipping {len(existing_ids)} already-existing occurrences.")

    if new_rows:
        db.table("events").insert(new_rows).execute()
        print(f"Done. {len(new_rows)} new workshop occurrences written.")
    else:
        print("Nothing new to write.")


if __name__ == "__main__":
    main()
