"""
review_queue.py
Interactive CLI to review pending events from the AI pipeline.

Usage: python review_queue.py [--all] [--kid-friendly-only]

Controls:
  a / y  → approve
  r / n  → reject
  s      → skip (leave as pending)
  q      → quit
"""

import os
import sys
import argparse
import textwrap
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../.env"))

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

# Terminal colors
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"


def get_input(prompt: str) -> str:
    try:
        return input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return "q"


def format_tags(tags: list | None) -> str:
    if not tags:
        return f"{DIM}—{RESET}"
    return ", ".join(tags)


def format_date(dt_str: str | None) -> str:
    if not dt_str:
        return "—"
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        dt = dt.astimezone(ZoneInfo("America/Los_Angeles"))
        return dt.strftime("%a %b %-d, %Y · %-I:%M %p")
    except Exception:
        return dt_str


def print_event(event: dict, index: int, total: int):
    ai = event.get("ai_raw_response") or {}
    confidence = event.get("ai_confidence")
    kid_friendly = event.get("kid_friendly")

    kid_label = f"{GREEN}✓ family-friendly{RESET}" if kid_friendly else f"{RED}✗ adult-only{RESET}"
    conf_label = f"{confidence:.0%}" if confidence else "—"

    print(f"\n{'─' * 60}")
    print(f"{BOLD}[{index}/{total}] {event['name']}{RESET}")
    print(f"{'─' * 60}")
    print(f"  {CYAN}When:{RESET}     {format_date(event.get('starts_at'))}")
    print(f"  {CYAN}Where:{RESET}    {event.get('address') or '—'}")
    print(f"  {CYAN}Source:{RESET}   {event.get('source', '—')} · {event.get('source_url') or '—'}")
    print()
    if event.get("description"):
        wrapped = textwrap.fill(event["description"], width=56, initial_indent="  ", subsequent_indent="  ")
        print(wrapped)
    print()
    print(f"  {CYAN}AI verdict:{RESET}  {kid_label}  {DIM}(confidence: {conf_label}){RESET}")
    if ai.get("reasoning"):
        print(f"  {CYAN}Reasoning:{RESET}   {DIM}{ai['reasoning']}{RESET}")
    print()
    print(f"  {CYAN}Tags:{RESET}     interests: {format_tags(event.get('interest_tags'))}")
    print(f"            vibes:     {format_tags(event.get('vibe_tags'))}")
    print(f"            ages:      {format_tags(event.get('best_age_range'))}")
    print(f"            cost:      {event.get('cost_tier') or '—'}  |  {event.get('indoor_outdoor') or '—'}  |  weather: {event.get('weather_sensitivity') or '—'}")
    print()


def update_status(client, event_id: str, status: str):
    from datetime import timezone
    client.table("events").update({
        "status": status,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "reviewed_by": "manual",
    }).eq("id", event_id).execute()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="Show all statuses, not just pending")
    parser.add_argument("--include-adult", action="store_true", help="Also show events Claude marked adult-only")
    args = parser.parse_args()

    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    query = client.table("events").select("*").order("starts_at")
    if not args.all:
        query = query.eq("status", "pending_review")
    if not args.include_adult:
        query = query.eq("kid_friendly", True)

    result = query.execute()
    events = result.data

    if not events:
        print(f"\n{GREEN}No pending events to review.{RESET}\n")
        return

    approved = rejected = skipped = 0
    total = len(events)

    print(f"\n{BOLD}SF Family Experiences — Event Review Queue{RESET}")
    print(f"  {total} events to review")
    print(f"  {DIM}Controls: [a]pprove  [r]eject  [s]kip  [q]uit{RESET}")

    for i, event in enumerate(events, 1):
        print_event(event, i, total)

        while True:
            choice = get_input(f"  {BOLD}Decision:{RESET} [a/r/s/q] → ")
            if choice in ("a", "y"):
                update_status(client, event["id"], "approved")
                print(f"  {GREEN}✓ Approved{RESET}")
                approved += 1
                break
            elif choice in ("r", "n"):
                update_status(client, event["id"], "rejected")
                print(f"  {RED}✗ Rejected{RESET}")
                rejected += 1
                break
            elif choice == "s":
                print(f"  {YELLOW}→ Skipped{RESET}")
                skipped += 1
                break
            elif choice == "q":
                print(f"\n{BOLD}Stopped early.{RESET}")
                break
            else:
                print(f"  {DIM}Press a (approve), r (reject), s (skip), or q (quit){RESET}")
        else:
            continue
        if choice == "q":
            break

    print(f"\n{'─' * 60}")
    print(f"{BOLD}Session summary:{RESET}  {GREEN}✓ {approved} approved{RESET}  ·  {RED}✗ {rejected} rejected{RESET}  ·  {YELLOW}→ {skipped} skipped{RESET}")
    print()


if __name__ == "__main__":
    main()
