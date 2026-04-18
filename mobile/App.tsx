import AsyncStorage from "@react-native-async-storage/async-storage";
import * as Location from "expo-location";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  RefreshControl,
  SafeAreaView,
  ScrollView,
  SectionList,
  StyleSheet,
  Switch,
  Text,
  TouchableOpacity,
  View,
} from "react-native";
import { Event, fetchEvents } from "./src/api";
import { EventCard } from "./src/components/EventCard";
import { EventDetail } from "./src/components/EventDetail";

type TimeFilter = "now" | "soon" | "weekend" | "upcoming" | "saved";

const TIME_TABS: { key: TimeFilter; label: string; emoji: string }[] = [
  { key: "now", label: "Now", emoji: "🟢" },
  { key: "soon", label: "Soon", emoji: "⏱️" },
  { key: "weekend", label: "Weekend", emoji: "🌤️" },
  { key: "upcoming", label: "Upcoming", emoji: "📅" },
  { key: "saved", label: "Saved", emoji: "❤️" },
];

const AGE_OPTIONS: { label: string; value: number }[] = [
  { label: "Baby", value: 0.5 },
  { label: "Toddler", value: 2 },
  { label: "Preschool", value: 4 },
  { label: "6-9", value: 7 },
];

const DISTANCE_OPTIONS: { label: string; value: number }[] = [
  { label: "½ mi", value: 0.5 },
  { label: "1 mi", value: 1 },
  { label: "2 mi", value: 2 },
  { label: "5 mi", value: 5 },
];

const FAVORITES_KEY = "little_city_favorites";

function dayLabel(iso: string): string {
  const d = new Date(iso);
  const today = new Date();
  const tomorrow = new Date();
  tomorrow.setDate(today.getDate() + 1);

  const fmt = (date: Date) =>
    date.toLocaleDateString("en-US", { timeZone: "America/Los_Angeles", month: "short", day: "numeric" });
  const dayOf = (date: Date) =>
    date.toLocaleDateString("en-US", { timeZone: "America/Los_Angeles", weekday: "long", month: "long", day: "numeric" });

  if (fmt(d) === fmt(today)) return "Today";
  if (fmt(d) === fmt(tomorrow)) return "Tomorrow";
  return dayOf(d);
}

function groupByDay(events: Event[]): { title: string; data: Event[] }[] {
  const groups: Record<string, Event[]> = {};
  for (const e of events) {
    const label = dayLabel(e.starts_at);
    if (!groups[label]) groups[label] = [];
    groups[label].push(e);
  }
  return Object.entries(groups).map(([title, data]) => ({ title, data }));
}

export default function App() {
  const [timeFilter, setTimeFilter] = useState<TimeFilter>("upcoming");
  const [childAges, setChildAges] = useState<number[]>([]);
  const [maxDistance, setMaxDistance] = useState<number | undefined>(undefined);
  const [isRaining, setIsRaining] = useState(false);
  const [userLocation, setUserLocation] = useState<{ lat: number; lng: number } | undefined>(undefined);
  const [events, setEvents] = useState<Event[]>([]);
  const [allEvents, setAllEvents] = useState<Event[]>([]);
  const [favoriteIds, setFavoriteIds] = useState<Set<string>>(new Set());
  const [tabCounts, setTabCounts] = useState<Record<string, number>>({ now: 0, soon: 0, weekend: 0, upcoming: 0 });
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedEvent, setSelectedEvent] = useState<Event | null>(null);

  // Load favorites from storage
  useEffect(() => {
    AsyncStorage.getItem(FAVORITES_KEY).then((val) => {
      if (val) setFavoriteIds(new Set(JSON.parse(val)));
    });
  }, []);

  // Load location
  useEffect(() => {
    (async () => {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status === "granted") {
        const loc = await Location.getCurrentPositionAsync({});
        setUserLocation({ lat: loc.coords.latitude, lng: loc.coords.longitude });
      }
    })();
  }, []);

  const toggleFavorite = useCallback((id: string) => {
    setFavoriteIds((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      AsyncStorage.setItem(FAVORITES_KEY, JSON.stringify([...next]));
      return next;
    });
  }, []);

  const toggleAge = (val: number) =>
    setChildAges((prev) => prev.includes(val) ? prev.filter((a) => a !== val) : [...prev, val]);

  const load = useCallback(
    async (isRefresh = false) => {
      isRefresh ? setRefreshing(true) : setLoading(true);
      setError(null);
      try {
        const baseParams = {
          child_ages: childAges,
          is_raining: isRaining,
          lat: userLocation?.lat,
          lng: userLocation?.lng,
          max_distance: timeFilter === "saved" ? undefined : maxDistance,
        };
        const apiFilter = timeFilter === "saved" ? "upcoming" : timeFilter;
        const [data, ...tabData] = await Promise.all([
          fetchEvents({ ...baseParams, time_filter: apiFilter }),
          fetchEvents({ ...baseParams, time_filter: "now" }),
          fetchEvents({ ...baseParams, time_filter: "soon" }),
          fetchEvents({ ...baseParams, time_filter: "weekend" }),
          fetchEvents({ ...baseParams, time_filter: "upcoming" }),
        ]);
        setEvents(data.events);
        setAllEvents(data.events);
        setTabCounts({ now: tabData[0].count, soon: tabData[1].count, weekend: tabData[2].count, upcoming: tabData[3].count });
      } catch (e: any) {
        setError("Couldn't load events. Is the API running?");
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [timeFilter, childAges, isRaining, userLocation, maxDistance],
  );

  useEffect(() => { load(); }, [load]);

  const displayEvents = timeFilter === "saved"
    ? allEvents.filter((e) => favoriteIds.has(e.id))
    : events;

  const sections = groupByDay(displayEvents);

  const emptyTitles: Record<TimeFilter, string> = {
    now: "Nothing happening right now",
    soon: "Nothing starting in the next 3 hours",
    weekend: "No weekend events found",
    upcoming: "No upcoming events found",
    saved: "No saved events yet",
  };

  const emptySubs: Record<TimeFilter, string> = {
    now: "Check back later or browse another tab",
    soon: "Check back later or browse another tab",
    weekend: "Check back later or browse another tab",
    upcoming: "Check back later or browse another tab",
    saved: "Tap ❤️ on any event to save it",
  };

  return (
    <SafeAreaView style={styles.safe}>
      {/* Header */}
      <View style={styles.header}>
        <Text style={styles.title}>Little City</Text>
        <Text style={styles.subtitle}>Family events in SF</Text>
      </View>

      {/* Time tabs */}
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.tabsScroll} contentContainerStyle={styles.tabs}>
        {TIME_TABS.map((tab) => {
          const count = tab.key === "saved" ? favoriteIds.size : tabCounts[tab.key];
          const isActive = timeFilter === tab.key;
          return (
            <TouchableOpacity
              key={tab.key}
              style={[styles.tab, isActive && styles.tabActive]}
              onPress={() => setTimeFilter(tab.key)}
            >
              <Text style={[styles.tabText, isActive && styles.tabTextActive]}>
                {tab.emoji} {tab.label}
              </Text>
              {count > 0 && (
                <View style={[styles.badge, isActive && styles.badgeActive]}>
                  <Text style={[styles.badgeText, isActive && styles.badgeTextActive]}>{count}</Text>
                </View>
              )}
            </TouchableOpacity>
          );
        })}
      </ScrollView>

      {/* Filters — hide on saved tab */}
      {timeFilter !== "saved" && (
        <View style={styles.filters}>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filterRow}>
            <Text style={styles.filterLabel}>Age:</Text>
            <TouchableOpacity
              style={[styles.pill, childAges.length === 0 && styles.pillActive]}
              onPress={() => setChildAges([])}
            >
              <Text style={[styles.pillText, childAges.length === 0 && styles.pillTextActive]}>Any</Text>
            </TouchableOpacity>
            {AGE_OPTIONS.map((opt) => (
              <TouchableOpacity
                key={opt.value}
                style={[styles.pill, childAges.includes(opt.value) && styles.pillActive]}
                onPress={() => toggleAge(opt.value)}
              >
                <Text style={[styles.pillText, childAges.includes(opt.value) && styles.pillTextActive]}>{opt.label}</Text>
              </TouchableOpacity>
            ))}
          </ScrollView>

          {userLocation && (
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.filterRow}>
              <Text style={styles.filterLabel}>Within:</Text>
              <TouchableOpacity
                style={[styles.pill, maxDistance === undefined && styles.pillActive]}
                onPress={() => setMaxDistance(undefined)}
              >
                <Text style={[styles.pillText, maxDistance === undefined && styles.pillTextActive]}>Any</Text>
              </TouchableOpacity>
              {DISTANCE_OPTIONS.map((opt) => (
                <TouchableOpacity
                  key={opt.value}
                  style={[styles.pill, maxDistance === opt.value && styles.pillActive]}
                  onPress={() => setMaxDistance(maxDistance === opt.value ? undefined : opt.value)}
                >
                  <Text style={[styles.pillText, maxDistance === opt.value && styles.pillTextActive]}>{opt.label}</Text>
                </TouchableOpacity>
              ))}
            </ScrollView>
          )}

          <View style={styles.rainRow}>
            <Switch value={isRaining} onValueChange={setIsRaining} />
            <Text style={styles.filterLabel}>
              {isRaining ? "🌧️ Hiding outdoor events" : "🌤️ It's not raining"}
            </Text>
          </View>
        </View>
      )}

      {/* Results — flex: 1 ensures only the list scrolls, not the whole page */}
      <View style={{ flex: 1 }}>
      {loading ? (
        <ActivityIndicator style={{ marginTop: 40 }} size="large" color="#1E88E5" />
      ) : error ? (
        <Text style={styles.error}>{error}</Text>
      ) : (
        <SectionList
          sections={sections}
          keyExtractor={(e) => e.id}
          renderItem={({ item }) => (
            <EventCard
              event={item}
              isFavorite={favoriteIds.has(item.id)}
              onToggleFavorite={toggleFavorite}
              onPress={setSelectedEvent}
            />
          )}
          renderSectionHeader={({ section: { title } }) => (
            <View style={styles.sectionHeader}>
              <Text style={styles.sectionHeaderText}>{title}</Text>
            </View>
          )}
          contentContainerStyle={styles.list}
          style={{ backgroundColor: "#FFF" }}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => load(true)} />}
          ListEmptyComponent={
            <View style={styles.emptyContainer}>
              <Text style={styles.emptyEmoji}>
                {timeFilter === "saved" ? "💔" : timeFilter === "now" ? "😴" : timeFilter === "soon" ? "🕐" : "🗓️"}
              </Text>
              <Text style={styles.emptyTitle}>{emptyTitles[timeFilter]}</Text>
              <Text style={styles.emptySub}>{emptySubs[timeFilter]}</Text>
            </View>
          }
        />
      )}
      </View>
      <EventDetail event={selectedEvent} onClose={() => setSelectedEvent(null)} />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: "#FAFAFA" },
  header: { paddingHorizontal: 20, paddingTop: 12, paddingBottom: 8 },
  title: { fontSize: 28, fontWeight: "700", color: "#1A1A1A" },
  subtitle: { fontSize: 14, color: "#888", marginTop: 2 },

  tabsScroll: { marginBottom: 12, flexGrow: 0, flexShrink: 0 },
  tabs: { paddingHorizontal: 16, paddingVertical: 4, gap: 6, flexDirection: "row", alignItems: "center" },
  tab: { paddingVertical: 8, paddingHorizontal: 12, borderRadius: 10, backgroundColor: "#EEE", alignItems: "center", justifyContent: "center", minWidth: 60 },
  tabActive: { backgroundColor: "#1E88E5" },
  tabText: { fontSize: 12, fontWeight: "600", color: "#555" },
  tabTextActive: { color: "#FFF" },
  badge: { backgroundColor: "#1E88E5", borderRadius: 99, paddingHorizontal: 6, paddingVertical: 1, marginTop: 3 },
  badgeActive: { backgroundColor: "rgba(255,255,255,0.3)" },
  badgeText: { fontSize: 11, fontWeight: "700", color: "#FFF" },
  badgeTextActive: { color: "#FFF" },

  filters: { paddingHorizontal: 16, marginBottom: 12, gap: 8 },
  filterRow: { flexDirection: "row", alignItems: "center", gap: 6 },
  filterLabel: { fontSize: 13, color: "#666", marginRight: 4, alignSelf: "center" },
  pill: { paddingHorizontal: 14, paddingVertical: 6, borderRadius: 99, backgroundColor: "#EEE" },
  pillActive: { backgroundColor: "#1A1A1A" },
  pillText: { fontSize: 13, color: "#555", fontWeight: "500" },
  pillTextActive: { color: "#FFF" },
  rainRow: { flexDirection: "row", alignItems: "center", gap: 8 },

  sectionHeader: { backgroundColor: "#FAFAFA", paddingHorizontal: 16, paddingTop: 12, paddingBottom: 4 },
  sectionHeaderText: { fontSize: 13, fontWeight: "700", color: "#888", textTransform: "uppercase", letterSpacing: 0.8 },

  list: { paddingBottom: 32 },
  emptyContainer: { alignItems: "center", marginTop: 60, paddingHorizontal: 32 },
  emptyEmoji: { fontSize: 40, marginBottom: 12 },
  emptyTitle: { fontSize: 16, fontWeight: "600", color: "#555", textAlign: "center", marginBottom: 6 },
  emptySub: { fontSize: 13, color: "#AAA", textAlign: "center" },
  error: { textAlign: "center", color: "#E53935", marginTop: 60, fontSize: 15, paddingHorizontal: 24 },
});
