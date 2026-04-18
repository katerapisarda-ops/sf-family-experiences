"""
SF Family Experiences — FastAPI backend
GET /events  →  filtered, time-bucketed event list
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from typing import List, Optional
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from supabase import create_client

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../.env"))

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

SF_TZ = ZoneInfo("America/Los_Angeles")
SOON_WINDOW_HOURS = 3  # "soon" = starts within 3 hours

app = FastAPI(title="SF Family Experiences", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


# ─────────────────────────── Response models ───────────────────────────

class Event(BaseModel):
    id: str
    name: str
    emoji: Optional[str] = None
    description: Optional[str] = None
    address: Optional[str] = None
    neighborhood: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    starts_at: str
    ends_at: Optional[str] = None
    source: Optional[str] = None
    source_url: Optional[str] = None
    interest_tags: List[str] = []
    vibe_tags: List[str] = []
    best_age_range: List[str] = []
    cost_tier: Optional[str] = None
    indoor_outdoor: Optional[str] = None
    weather_sensitivity: Optional[str] = None
    time_status: str  # "now" | "soon" | "weekend" | "upcoming"
    distance_miles: Optional[float] = None


class EventsResponse(BaseModel):
    events: List[Event]
    count: int
    filters_applied: dict


# ─────────────────────────── Helpers ───────────────────────────

def haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Straight-line distance in miles between two lat/lng points."""
    import math
    R = 3958.8  # Earth radius in miles
    lat1, lng1, lat2, lng2 = map(math.radians, [lat1, lng1, lat2, lng2])
    dlat, dlng = lat2 - lat1, lng2 - lng1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def get_time_status(starts_at: str, ends_at: Optional[str]) -> str:
    """Bucket an event into now / soon / weekend / upcoming."""
    try:
        now = datetime.now(SF_TZ)

        start = datetime.fromisoformat(starts_at)
        if start.tzinfo is None:
            start = start.replace(tzinfo=SF_TZ)

        end = None
        if ends_at:
            end = datetime.fromisoformat(ends_at)
            if end.tzinfo is None:
                end = end.replace(tzinfo=SF_TZ)

        # Past
        if end and end < now:
            return "past"
        if not end and start < now - timedelta(hours=2):
            return "past"

        # Happening now
        if end and start <= now <= end:
            return "now"

        # Soon — starts within 3 hours
        if start > now and (start - now).total_seconds() <= SOON_WINDOW_HOURS * 3600:
            return "soon"

        # This weekend — Fri–Sun of current or upcoming weekend
        wd = now.weekday()  # Mon=0 … Sun=6
        if wd <= 3:  # Mon–Thu: upcoming Fri
            friday = (now + timedelta(days=(4 - wd))).date()
        else:  # Fri–Sun: current weekend's Fri
            friday = (now - timedelta(days=(wd - 4))).date()
        sunday = friday + timedelta(days=2)

        if friday <= start.date() <= sunday:
            return "weekend"

        return "upcoming"

    except Exception:
        return "upcoming"


def age_fits(best_age_range: List[str], child_ages: List[float]) -> bool:
    """Return True if the event is appropriate for any of the selected ages."""
    if not child_ages or not best_age_range:
        return True  # no filter applied
    for r in best_age_range:
        rl = r.lower()
        if "all ages" in rl:
            return True
        for child_age in child_ages:
            if "baby" in rl and child_age <= 1:
                return True
            if "toddler" in rl and 1 < child_age <= 3:
                return True
            if "preschool" in rl and 3 < child_age <= 5:
                return True
            if "older kid" in rl and child_age > 5:
                return True
    return False


def weather_ok(weather_sensitivity: Optional[str], is_raining: bool) -> bool:
    """Return False if event should be skipped in current weather."""
    if not is_raining:
        return True
    if weather_sensitivity == "avoid_rain":
        return False
    # soft_avoid_rain — keep in results but app can flag it
    return True


# ─────────────────────────── Endpoint ───────────────────────────

@app.get("/events", response_model=EventsResponse)
def get_events(
    time_filter: Optional[str] = Query(None, description="now | soon | weekend | upcoming"),
    child_age: Optional[List[float]] = Query(None, description="Child age(s) in years, can pass multiple"),
    is_raining: bool = Query(False, description="Current weather is rainy"),
    lat: Optional[float] = Query(None, description="User latitude"),
    lng: Optional[float] = Query(None, description="User longitude"),
    max_distance: Optional[float] = Query(None, description="Max distance in miles from user location"),
):
    # Fetch approved events within the next 7 days
    now = datetime.now(SF_TZ)
    window_end = now + timedelta(days=7)

    result = (
        db.table("events")
        .select("id,name,emoji,description,address,neighborhood,lat,lng,starts_at,ends_at,source,source_url,interest_tags,vibe_tags,best_age_range,cost_tier,indoor_outdoor,weather_sensitivity")
        .eq("status", "approved")
        .gte("starts_at", now.isoformat())
        .lte("starts_at", window_end.isoformat())
        .order("starts_at")
        .execute()
    )

    rows = result.data or []

    # Apply filters
    events = []
    for row in rows:
        # Age filter
        if not age_fits(row.get("best_age_range") or [], child_age or []):
            continue

        # Weather filter
        if not weather_ok(row.get("weather_sensitivity"), is_raining):
            continue

        # Time status
        status = get_time_status(row["starts_at"], row.get("ends_at"))

        if status == "past":
            continue

        # Time filter
        if time_filter:
            if time_filter == "upcoming":
                pass  # show all non-past events
            elif status != time_filter:
                continue

        event_lat, event_lng = row.get("lat"), row.get("lng")
        distance = None
        if lat is not None and lng is not None and event_lat is not None and event_lng is not None:
            distance = round(haversine_miles(lat, lng, event_lat, event_lng), 1)

        # Distance filter — skip events with no location when filter is active
        if max_distance is not None:
            if distance is None or distance > max_distance:
                continue

        events.append(Event(
            id=row["id"],
            name=row["name"],
            emoji=row.get("emoji"),
            description=row.get("description"),
            address=row.get("address"),
            neighborhood=row.get("neighborhood"),
            lat=event_lat,
            lng=event_lng,
            starts_at=row["starts_at"],
            ends_at=row.get("ends_at"),
            source=row.get("source"),
            source_url=row.get("source_url"),
            interest_tags=row.get("interest_tags") or [],
            vibe_tags=row.get("vibe_tags") or [],
            best_age_range=row.get("best_age_range") or [],
            cost_tier=row.get("cost_tier"),
            indoor_outdoor=row.get("indoor_outdoor"),
            weather_sensitivity=row.get("weather_sensitivity"),
            time_status=status,
            distance_miles=distance,
        ))

    return EventsResponse(
        events=events,
        count=len(events),
        filters_applied={
            "time_filter": time_filter,
            "child_age": child_age,
            "is_raining": is_raining,
        },
    )


@app.get("/health")
def health():
    return {"status": "ok", "ts": int(time.time())}
