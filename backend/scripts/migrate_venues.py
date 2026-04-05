"""
migrate_venues.py
Imports Airtable CSV export into Supabase venues table.
Usage: python migrate_venues.py --csv "/path/to/Experiences-Grid view.csv" [--dry-run]
"""

import csv
import os
import re
import sys
import argparse
from dotenv import load_dotenv
from supabase import create_client

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../.env"))

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

CSV_PATH = "/Users/katerapisarda/Documents/Projects/Travel App/Experiences-Grid view.csv"


def parse_lat_lng(val: str):
    """Parse '37.762, -122.500' → (37.762, -122.500)"""
    if not val or not val.strip():
        return None, None
    match = re.match(r"\s*([-\d.]+)\s*,\s*([-\d.]+)\s*", val)
    if match:
        return float(match.group(1)), float(match.group(2))
    return None, None


def parse_tags(val: str) -> list[str]:
    """Parse comma-separated tag string → clean list."""
    if not val or not val.strip():
        return []
    return [t.strip() for t in val.split(",") if t.strip()]


def parse_bool(val: str) -> bool | None:
    if not val or not val.strip():
        return None
    return val.strip().upper() in ("TRUE", "1", "YES")


def parse_indoor_outdoor(val: str) -> str | None:
    if not val or not val.strip():
        return None
    v = val.strip().lower()
    if "indoor" in v and "outdoor" in v:
        return "both"
    if "indoor" in v:
        return "indoor"
    if "outdoor" in v:
        return "outdoor"
    return v


def parse_cost_tier(val: str) -> str | None:
    mapping = {"$": "$", "$$": "$$", "$$$": "$$$", "free": "free", "0": "free"}
    return mapping.get(val.strip().lower()) if val else None


def row_to_venue(row: dict) -> dict:
    lat, lng = parse_lat_lng(row.get("lat_lng", ""))

    return {
        "name":                row.get("title", "").strip() or None,
        "description":         row.get("description", "").strip() or None,
        "address":             row.get("Street Address", "").strip() or None,
        "lat":                 lat,
        "lng":                 lng,
        "google_place_id":     row.get("Google Place ID", "").strip() or None,
        "area":                row.get("parent_location", "").strip() or None,
        "neighborhood":        row.get("Neighborhood", "").strip() or None,
        "interest_tags":       parse_tags(row.get("interest_tags", "")),
        "vibe_tags":           parse_tags(row.get("vibe_tags", "")),
        "sub_tags":            parse_tags(row.get("title_sub_tags", "")),
        "type_tags":           parse_tags(row.get("type_tags", "")),
        "best_age_range":      parse_tags(row.get("best_age_range", "")),
        "cost_tier":           parse_cost_tier(row.get("cost_tier", "")),
        "time_estimate_mins":  int(row["time_estimate_mins"]) if row.get("time_estimate_mins", "").strip().isdigit() else None,
        "indoor_outdoor":      parse_indoor_outdoor(row.get("indoor_outdoor", "")),
        "weather_sensitivity": row.get("weather_sensitive", "").strip() or None,
        "has_restroom":        parse_bool(row.get("has_restroom")),
        "has_changing_station":parse_bool(row.get("has_changing_station")),
        "food_nearby":         parse_bool(row.get("food_nearby")),
        "stroller_friendly":   parse_bool(row.get("stroller_friendly")),
        "has_playground":      parse_bool(row.get("has_playground")),
        "has_outdoor_space":   parse_bool(row.get("has_outdoor_space")),
        "less_crowded":        parse_bool(row.get("less_crowded_place")),
        "kid_friendly":        parse_bool(row.get("kid_friendly")),
        "insider_tips":        row.get("parent_insider_tips", "").strip() or None,
        "is_active":           True,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default=CSV_PATH)
    parser.add_argument("--dry-run", action="store_true", help="Parse only, don't write to Supabase")
    args = parser.parse_args()

    with open(args.csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    venues = [row_to_venue(r) for r in rows]
    # Drop rows with no name
    venues = [v for v in venues if v["name"]]

    # Deduplicate by google_place_id (keep first occurrence)
    seen_place_ids = set()
    deduped = []
    for v in venues:
        pid = v.get("google_place_id")
        if pid:
            if pid in seen_place_ids:
                print(f"  Skipping duplicate google_place_id: {pid} ({v['name']})")
                continue
            seen_place_ids.add(pid)
        deduped.append(v)
    venues = deduped

    print(f"Parsed {len(venues)} venues from CSV (after dedup)")

    if args.dry_run:
        for v in venues[:3]:
            print(v)
        print("--- dry run, nothing written ---")
        return

    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    # Upsert in batches of 50
    batch_size = 50
    inserted = 0
    errors = []

    for i in range(0, len(venues), batch_size):
        batch = venues[i : i + batch_size]
        try:
            result = client.table("venues").upsert(batch, on_conflict="google_place_id").execute()
            inserted += len(batch)
            print(f"  Inserted batch {i // batch_size + 1} ({inserted}/{len(venues)})")
        except Exception as e:
            errors.append((i, str(e)))
            print(f"  ERROR on batch {i // batch_size + 1}: {e}")

    print(f"\nDone. {inserted} venues upserted, {len(errors)} errors.")
    if errors:
        for idx, err in errors:
            print(f"  Batch starting at row {idx}: {err}")


if __name__ == "__main__":
    main()
