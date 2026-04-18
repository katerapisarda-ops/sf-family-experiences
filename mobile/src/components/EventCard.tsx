import { StyleSheet, Text, TouchableOpacity, View } from "react-native";
import { Event } from "../api";

const STATUS_COLORS: Record<string, string> = {
  now: "#E8F5E9",
  soon: "#FFF8E1",
  weekend: "#E3F2FD",
  upcoming: "#F0F4FF",
};

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

const AGE_ORDER = ["baby", "toddler", "preschool", "older kid"];
const AGE_LOW: Record<string, string> = { "baby": "0", "toddler": "1", "preschool": "3", "older kid": "6" };
const AGE_HIGH: Record<string, string> = { "baby": "1", "toddler": "3", "preschool": "5", "older kid": "+" };

function formatAges(ranges: string[]): string | null {
  if (!ranges?.length) return null;
  const lower = ranges.map((r) => r.toLowerCase());
  if (lower.some((r) => r.includes("all ages"))) return "All ages";
  const matched = AGE_ORDER.filter((a) => lower.some((r) => r.includes(a)));
  if (!matched.length) return null;
  const first = matched[0];
  const last = matched[matched.length - 1];
  if (AGE_HIGH[last] === "+") return `${AGE_LOW[first]}+`;
  if (first === last) return `${AGE_LOW[first]}–${AGE_HIGH[first]}`;
  return `${AGE_LOW[first]}–${AGE_HIGH[last]}`;
}

interface Props {
  event: Event;
  isFavorite: boolean;
  onToggleFavorite: (id: string) => void;
  onPress: (event: Event) => void;
}

export function EventCard({ event, isFavorite, onToggleFavorite, onPress }: Props) {
  const emojiBg = STATUS_COLORS[event.time_status] ?? STATUS_COLORS.upcoming;

  const metaParts: string[] = [];
  if (event.neighborhood) metaParts.push(event.neighborhood);
  if (event.cost_tier === "free") metaParts.push("Free");
  else if (event.cost_tier) metaParts.push(event.cost_tier);
  const ageLabel = formatAges(event.best_age_range);
  if (ageLabel) metaParts.push(ageLabel);

  return (
    <TouchableOpacity style={styles.row} onPress={() => onPress(event)} activeOpacity={0.7}>
      {/* Time */}
      <Text style={styles.time}>{formatTime(event.starts_at)}</Text>

      {/* Middle: name + meta */}
      <View style={styles.middle}>
        <Text style={styles.name} numberOfLines={2}>{event.name}</Text>
        {metaParts.length > 0 && (
          <Text style={styles.meta} numberOfLines={1}>{metaParts.join(" · ")}</Text>
        )}
      </View>

      {/* Emoji thumbnail */}
      <View style={[styles.thumb, { backgroundColor: emojiBg }]}>
        {event.emoji ? <Text style={styles.thumbEmoji}>{event.emoji}</Text> : null}
      </View>

      {/* Heart */}
      <TouchableOpacity onPress={() => onToggleFavorite(event.id)} hitSlop={8} style={styles.heartBtn}>
        <Text style={[styles.heart, !isFavorite && styles.heartEmpty]}>
          {isFavorite ? "❤️" : "♡"}
        </Text>
      </TouchableOpacity>
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 12,
    paddingHorizontal: 16,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: "#E0E0E0",
    backgroundColor: "#FFF",
    gap: 10,
  },
  time: {
    fontSize: 12,
    fontWeight: "600",
    color: "#1E88E5",
    width: 52,
    flexShrink: 0,
  },
  middle: {
    flex: 1,
  },
  name: {
    fontSize: 15,
    fontWeight: "600",
    color: "#1A1A1A",
    marginBottom: 2,
  },
  meta: {
    fontSize: 12,
    color: "#888",
  },
  thumb: {
    width: 44,
    height: 44,
    borderRadius: 10,
    alignItems: "center",
    justifyContent: "center",
    flexShrink: 0,
  },
  thumbEmoji: {
    fontSize: 22,
  },
  heartBtn: {
    flexShrink: 0,
    paddingLeft: 4,
  },
  heart: {
    fontSize: 18,
  },
  heartEmpty: {
    color: "#CCC",
  },
});
