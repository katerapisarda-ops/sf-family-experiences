#!/bin/bash
# refresh_all.sh — Expire past events and fetch fresh ones from all sources.
# Run weekly from the project root with: bash refresh_all.sh

set -e
cd "$(dirname "$0")"
source .venv/bin/activate

echo "========================================"
echo " SF Family Experiences — Weekly Refresh"
echo "========================================"

echo ""
echo "Step 1/3 — Expiring past events..."
python3 backend/scripts/cleanup_events.py

echo ""
echo "Step 2/3 — Fetching new events from all sources..."
python3 backend/scripts/fetch_sfpl_events.py --days-ahead 7
python3 backend/scripts/fetch_randall_events.py --days-ahead 14
python3 backend/scripts/fetch_presidio_events.py --days-ahead 14
python3 backend/scripts/fetch_fort_mason_events.py --days-ahead 14
python3 backend/scripts/fetch_sfzoo_events.py --days-ahead 14
python3 backend/scripts/fetch_ybg_events.py --days-ahead 14
python3 backend/scripts/fetch_thrive_city_events.py
python3 backend/scripts/fetch_sfrecpark_events.py --days-ahead 14

echo ""
echo "Step 3/3 — Refreshing recurring seeds (farmers markets + night markets)..."
python3 backend/scripts/seed_farmers_markets.py
python3 backend/scripts/seed_night_markets.py

echo ""
echo "========================================"
echo " Done! Run the review queue next:"
echo " python3 backend/scripts/review_queue.py"
echo "========================================"
