import { StyleSheet, Text, TouchableOpacity, View } from "react-native";
import { Event } from "../api";


function decodeEntities(str: string): string {
  return str
    .replace(/&#8217;/g, "'").replace(/&#8216;/g, "'")
    .replace(/&#8220;/g, '"').replace(/&#8221;/g, '"')
    .replace(/&#8211;/g, '–').replace(/&#8212;/g, '—')
    .replace(/&amp;/g, '&').replace(/&nbsp;/g, ' ')
    .replace(/&#\d+;/g, '');
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

      {/* Middle: emoji + name + meta */}
      <View style={styles.middle}>
        <Text style={styles.name} numberOfLines={2}>
          {event.emoji ? `${event.emoji} ${decodeEntities(event.name)}` : decodeEntities(event.name)}
        </Text>
        {metaParts.length > 0 && (
          <Text style={styles.meta} numberOfLines={1}>{metaParts.join(" · ")}</Text>
        )}
        {event.requires_reservation && (
          <Text style={styles.reservationBadge} numberOfLines={1}>⚠️ Reservation required</Text>
        )}
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
    width: 68,
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
  reservationBadge: {
    fontSize: 12,
    color: "#B45309",
    marginTop: 2,
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
