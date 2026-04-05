"""
cleanup_events.py
Removes stale events from the database.

Run weekly (after the pipeline) to keep the events table tidy.

What it does:
  - Auto-rejects approved/pending events that have already passed
  - Deletes rejected events older than 30 days (optional, off by default)

Usage:
  python cleanup_events.py [--delete-old] [--dry-run]
"""

import os
import argparse
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from supabase import create_client

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../.env"))

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--delete-old", action="store_true", help="Also delete rejected events older than 30 days")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    # 1. Find past events that are still pending or approved
    past = client.table("events") \
        .select("id, name, starts_at, status") \
        .lt("starts_at", now_iso) \
        .in_("status", ["pending_review", "approved"]) \
        .execute()

    print(f"Found {len(past.data)} past events to expire")
    for e in past.data[:5]:
        print(f"  - {e['name'][:50]} ({e['status']}) → starts_at: {e['starts_at'][:10]}")
    if len(past.data) > 5:
        print(f"  ... and {len(past.data) - 5} more")

    if not args.dry_run and past.data:
        ids = [e["id"] for e in past.data]
        client.table("events").update({
            "status": "rejected",
            "reviewed_by": "auto-cleanup",
            "reviewed_at": now_iso,
        }).in_("id", ids).execute()
        print(f"  ✓ Expired {len(ids)} past events → status='rejected'")

    # 2. Optionally delete old rejected events
    if args.delete_old:
        cutoff = (now - timedelta(days=30)).isoformat()
        old = client.table("events") \
            .select("id, name") \
            .eq("status", "rejected") \
            .lt("starts_at", cutoff) \
            .execute()

        print(f"\nFound {len(old.data)} rejected events older than 30 days")
        if not args.dry_run and old.data:
            ids = [e["id"] for e in old.data]
            client.table("events").delete().in_("id", ids).execute()
            print(f"  ✓ Deleted {len(ids)} old rejected events")

    if args.dry_run:
        print("\n--- dry run, nothing changed ---")
    else:
        print("\nCleanup complete.")


if __name__ == "__main__":
    main()
