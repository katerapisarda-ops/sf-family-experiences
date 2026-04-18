const API_BASE = "https://little-city-api.onrender.com";

export interface Event {
  id: string;
  name: string;
  emoji?: string;
  description?: string;
  address?: string;
  neighborhood?: string;
  lat?: number;
  lng?: number;
  starts_at: string;
  ends_at?: string;
  source?: string;
  source_url?: string;
  interest_tags: string[];
  vibe_tags: string[];
  best_age_range: string[];
  cost_tier?: string;
  indoor_outdoor?: string;
  weather_sensitivity?: string;
  time_status: "now" | "soon" | "weekend" | "upcoming" | "upcoming_7d";
  distance_miles?: number;
}

export interface EventsResponse {
  events: Event[];
  count: number;
  filters_applied: Record<string, unknown>;
}

export async function fetchEvents(params: {
  time_filter?: "now" | "soon" | "weekend" | "upcoming";
  child_ages?: number[];
  is_raining?: boolean;
  lat?: number;
  lng?: number;
  max_distance?: number;
}): Promise<EventsResponse> {
  const query = new URLSearchParams();
  if (params.time_filter) query.set("time_filter", params.time_filter);
  if (params.child_ages?.length) params.child_ages.forEach((a) => query.append("child_age", String(a)));
  if (params.is_raining) query.set("is_raining", "true");
  if (params.lat != null) query.set("lat", String(params.lat));
  if (params.lng != null) query.set("lng", String(params.lng));
  if (params.max_distance != null) query.set("max_distance", String(params.max_distance));

  const res = await fetch(`${API_BASE}/events?${query}`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}
