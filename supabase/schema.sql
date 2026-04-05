-- SF Family Experiences — Supabase Schema
-- Run this in the Supabase SQL editor

-- Enable PostGIS for geo queries
create extension if not exists postgis;

-- ============================================================
-- VENUES
-- Permanent/semi-permanent places (parks, museums, cafes, etc.)
-- ============================================================
create table venues (
  id                    uuid primary key default gen_random_uuid(),
  created_at            timestamptz default now(),
  updated_at            timestamptz default now(),

  -- Core identity
  name                  text not null,
  description           text,
  address               text,
  lat                   numeric(9,6),
  lng                   numeric(10,6),
  location              geography(Point, 4326),  -- PostGIS for geo queries
  google_place_id       text unique,             -- fetch live data via Places API

  -- Geography
  area                  text,                    -- "Golden Gate Park Area", etc.
  neighborhood          text,

  -- Taxonomy (fixed lists — see taxonomy.md)
  interest_tags         text[],                  -- e.g. ['nature', 'arts']
  vibe_tags             text[],                  -- e.g. ['chill', 'adventurous']
  sub_tags              text[],                  -- e.g. ['bakery', 'playground']
  type_tags             text[],                  -- e.g. ['hidden gem', 'must-try']

  -- Age
  best_age_range        text[],                  -- ['All Ages', 'Toddler (1-3)', ...]

  -- Logistics
  cost_tier             text,                    -- 'free', '$', '$$', '$$$'
  time_estimate_mins    int,
  indoor_outdoor        text,                    -- 'indoor', 'outdoor', 'both'
  weather_sensitivity   text,                    -- 'none', 'avoid_rain', 'soft_avoid_rain'

  -- Boolean attributes
  has_restroom          boolean default false,
  has_changing_station  boolean default false,
  food_nearby           boolean default false,
  stroller_friendly     boolean default false,
  has_playground        boolean default false,
  has_outdoor_space     boolean default false,
  less_crowded          boolean default false,
  kid_friendly          boolean default true,

  -- Editorial
  insider_tips          text,

  -- Status
  is_active             boolean default true
);

-- Auto-populate PostGIS point from lat/lng
create or replace function venues_set_location()
returns trigger as $$
begin
  if new.lat is not null and new.lng is not null then
    new.location = st_setsrid(st_makepoint(new.lng, new.lat), 4326);
  end if;
  return new;
end;
$$ language plpgsql;

create trigger venues_location_trigger
before insert or update on venues
for each row execute function venues_set_location();

-- Indexes
create index venues_location_idx on venues using gist(location);
create index venues_interest_tags_idx on venues using gin(interest_tags);
create index venues_vibe_tags_idx on venues using gin(vibe_tags);
create index venues_age_idx on venues using gin(best_age_range);


-- ============================================================
-- EVENTS
-- One-time or recurring events (Eventbrite, SF Rec & Parks, etc.)
-- ============================================================
create table events (
  id                    uuid primary key default gen_random_uuid(),
  created_at            timestamptz default now(),
  updated_at            timestamptz default now(),

  -- Core identity
  name                  text not null,
  description           text,
  address               text,
  lat                   numeric(9,6),
  lng                   numeric(10,6),
  location              geography(Point, 4326),

  -- Optional link to a venue
  venue_id              uuid references venues(id) on delete set null,

  -- Timing
  starts_at             timestamptz not null,
  ends_at               timestamptz,
  is_recurring          boolean default false,
  recurrence_rule       text,                    -- iCal RRULE string if recurring

  -- Source
  source                text,                    -- 'eventbrite', 'sf_rec_parks', 'manual'
  source_id             text,                    -- original ID from source system
  source_url            text,
  unique (source, source_id),                    -- prevent duplicates

  -- Taxonomy (same fixed lists as venues)
  interest_tags         text[],
  vibe_tags             text[],
  type_tags             text[],
  best_age_range        text[],

  -- Logistics
  cost_tier             text,
  indoor_outdoor        text,
  weather_sensitivity   text,

  -- Boolean attributes
  has_restroom          boolean,
  stroller_friendly     boolean,
  kid_friendly          boolean default true,

  -- Pipeline / review queue
  status                text default 'pending_review',  -- 'pending_review', 'approved', 'rejected'
  ai_confidence         numeric(4,3),                   -- 0.000–1.000, from Claude classification
  ai_raw_response       jsonb,                          -- full Claude response for debugging
  reviewed_at           timestamptz,
  reviewed_by           text
);

-- Auto-populate PostGIS point from lat/lng
create trigger events_location_trigger
before insert or update on events
for each row execute function venues_set_location();

-- Indexes
create index events_starts_at_idx on events(starts_at);
create index events_status_idx on events(status);
create index events_location_idx on events using gist(location);
create index events_interest_tags_idx on events using gin(interest_tags);
create index events_vibe_tags_idx on events using gin(vibe_tags);
