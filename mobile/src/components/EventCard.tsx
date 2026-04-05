import { View, Text, StyleSheet, TouchableOpacity, Linking } from "react-native";
import { Event } from "../api";

const COLORS = {
  now: "#E8F5E9",
  soon: "#FFF8E1",
  weekend: "#E3F2FD",
  upcoming: "#F5F5F5",
};

const TAG_EMOJI: Record<string, string> = {
  arts: "🎨",
  music: "🎵",
  nature: "🌿",
  animals: "🐾",
  science: "🔬",
  food: "🍎",
  sports: "⚽",
  water: "💧",
  history: "🏛️",
  community: "🤝",
};

function eventEmoji(interest_tags: string[]): string {
  for (const tag of interest_tags) {
    const emoji = TAG_EMOJI[tag.toLowerCase()];
    if (emoji) return emoji;
  }
  return "📍";
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString("en-US", {
      hour: "numeric",
      minute: "2-digit",
      timeZone: "America/Los_Angeles",
    });
  } catch {
    return iso;
  }
}

function cleanAddress(address?: string): string | null {
  if (!address) return null;
  return address
    .replace(/, San Francisco Public Library$/, "")
    .replace(/\bBranch\b/, "Library")
    .trim();
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString("en-US", {
      weekday: "short",
      month: "short",
      day: "numeric",
      timeZone: "America/Los_Angeles",
    });
  } catch {
    return iso;
  }
}

interface Props {
  event: Event;
  isFavorite: boolean;
  onToggleFavorite: (id: string) => void;
}

export function EventCard({ event, isFavorite, onToggleFavorite }: Props) {
  const bg = COLORS[event.time_status] ?? COLORS.upcoming;
  const emoji = event.emoji || eventEmoji(event.interest_tags);
  const address = cleanAddress(event.address);

  return (
    <View>
      <View style={[styles.card, { backgroundColor: bg }]}>
        <View style={styles.header}>
          <Text style={styles.emoji}>{emoji}</Text>
          <Text style={styles.name} numberOfLines={2}>{event.name}</Text>
          <TouchableOpacity onPress={() => onToggleFavorite(event.id)} hitSlop={8}>
            <Text style={[styles.heart, !isFavorite && styles.heartEmpty]}>{isFavorite ? "❤️" : "♡"}</Text>
          </TouchableOpacity>
        </View>

        <Text style={styles.meta}>
          {formatDate(event.starts_at)} · {formatTime(event.starts_at)}
          {event.ends_at ? ` – ${formatTime(event.ends_at)}` : ""}
        </Text>

        {address ? (
          <Text style={styles.address} numberOfLines={1}>
            📍 {address}{event.distance_miles != null ? `  ·  ${event.distance_miles} mi` : ""}
          </Text>
        ) : null}

        {event.description ? (
          <Text style={styles.description}>{event.description}</Text>
        ) : null}

        <View style={styles.pills}>
          {event.cost_tier ? (
            <View style={[styles.pill, event.cost_tier === "free" && styles.pillFree]}>
              <Text style={[styles.pillText, event.cost_tier === "free" && styles.pillFreeText]}>
                {event.cost_tier === "free" ? "Free" : event.cost_tier}
              </Text>
            </View>
          ) : null}
          {event.indoor_outdoor ? (
            <View style={styles.pill}>
              <Text style={styles.pillText}>{event.indoor_outdoor}</Text>
            </View>
          ) : null}
          {event.interest_tags.slice(0, 2).map((tag) => (
            <View key={tag} style={styles.pill}>
              <Text style={styles.pillText}>{tag}</Text>
            </View>
          ))}
        </View>

        {event.source_url ? (
          <View style={styles.footer}>
            <TouchableOpacity onPress={() => Linking.openURL(event.source_url!)}>
              <Text style={styles.link}>More info →</Text>
            </TouchableOpacity>
          </View>
        ) : null}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    borderRadius: 12,
    padding: 16,
    marginBottom: 12,
    shadowColor: "#000",
    shadowOpacity: 0.06,
    shadowRadius: 4,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  header: {
    flexDirection: "row",
    alignItems: "flex-start",
    gap: 8,
    marginBottom: 4,
  },
  emoji: { fontSize: 18, marginTop: 1, flexShrink: 0 },
  name: { fontSize: 16, fontWeight: "600", color: "#1A1A1A", flex: 1 },
  heart: { fontSize: 16, flexShrink: 0, marginTop: 2 },
  heartEmpty: { color: "#CCC" },
  meta: { fontSize: 13, color: "#555", marginBottom: 4, marginLeft: 26 },
  address: { fontSize: 13, color: "#555", marginBottom: 8, marginLeft: 26 },
  pills: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginLeft: 26, marginBottom: 8 },
  pill: { backgroundColor: "rgba(0,0,0,0.07)", borderRadius: 99, paddingHorizontal: 10, paddingVertical: 3 },
  pillFree: { backgroundColor: "#E8F5E9" },
  pillText: { fontSize: 12, color: "#333", textTransform: "capitalize" },
  pillFreeText: { color: "#2E7D32", fontWeight: "600" },
  description: { fontSize: 13, color: "#444", marginLeft: 26, marginBottom: 8, lineHeight: 19 },
  footer: { flexDirection: "row", justifyContent: "flex-end", marginLeft: 26 },
  link: { fontSize: 13, color: "#1E88E5", fontWeight: "500" },
});
