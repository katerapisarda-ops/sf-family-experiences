import {
  Linking,
  Modal,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";

import { Event } from "../api";

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

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString("en-US", {
      weekday: "long",
      month: "long",
      day: "numeric",
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

interface Props {
  event: Event | null;
  onClose: () => void;
}

export function EventDetail({ event, onClose }: Props) {
  if (!event) return null;
  const address = cleanAddress(event.address);

  return (
    <Modal visible={!!event} animationType="slide" presentationStyle="pageSheet">
      <SafeAreaView style={styles.safe}>
        {/* Header */}
        <View style={styles.header}>
          <TouchableOpacity onPress={onClose} style={styles.backBtn}>
            <Text style={styles.backText}>← Back</Text>
          </TouchableOpacity>
        </View>

        <ScrollView contentContainerStyle={styles.body}>
          {/* Emoji + name */}
          <Text style={styles.emoji}>{event.emoji || "📍"}</Text>
          <Text style={styles.name}>{event.name}</Text>

          {/* Date / time */}
          <Text style={styles.meta}>
            {formatDate(event.starts_at)} · {formatTime(event.starts_at)}
            {event.ends_at ? ` – ${formatTime(event.ends_at)}` : ""}
          </Text>

          {/* Address */}
          {address ? (
            <Text style={styles.address}>
              📍 {address}{event.distance_miles != null ? `  ·  ${event.distance_miles} mi away` : ""}
            </Text>
          ) : null}

          {/* Description */}
          {event.description ? (
            <Text style={styles.description}>{event.description}</Text>
          ) : null}

          {/* Meta rows */}
          <View style={styles.metaSection}>
            {event.best_age_range?.length > 0 && (
              <View style={styles.metaRow}>
                <Text style={styles.metaLabel}>Ages</Text>
                <Text style={styles.metaValue}>{event.best_age_range.join(", ")}</Text>
              </View>
            )}
            {event.cost_tier && (
              <View style={styles.metaRow}>
                <Text style={styles.metaLabel}>Cost</Text>
                <Text style={[styles.metaValue, event.cost_tier === "free" && styles.metaFree]}>
                  {event.cost_tier === "free" ? "Free" : event.cost_tier}
                </Text>
              </View>
            )}
            {event.indoor_outdoor && (
              <View style={styles.metaRow}>
                <Text style={styles.metaLabel}>Setting</Text>
                <Text style={styles.metaValue}>{event.indoor_outdoor}</Text>
              </View>
            )}
          </View>

          {/* More info link */}
          {event.source_url ? (
            <TouchableOpacity
              style={styles.linkBtn}
              onPress={() => Linking.openURL(event.source_url!)}
            >
              <Text style={styles.linkBtnText}>More info →</Text>
            </TouchableOpacity>
          ) : null}
        </ScrollView>
      </SafeAreaView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: "#FAFAFA" },
  header: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 16,
    paddingTop: 12,
    paddingBottom: 8,
    borderBottomWidth: 1,
    borderBottomColor: "#EEE",
  },
  backBtn: { padding: 8 },
  backText: { fontSize: 16, color: "#1E88E5", fontWeight: "500" },

  body: { padding: 24, paddingBottom: 48 },
  emoji: { fontSize: 48, marginBottom: 12 },
  name: { fontSize: 22, fontWeight: "700", color: "#1A1A1A", marginBottom: 8 },
  meta: { fontSize: 14, color: "#555", marginBottom: 6 },
  address: { fontSize: 14, color: "#555", marginBottom: 16 },
  description: { fontSize: 15, color: "#333", lineHeight: 22, marginBottom: 20 },

  metaSection: {
    marginBottom: 24,
    borderRadius: 10,
    backgroundColor: "#F5F5F5",
    overflow: "hidden",
  },
  metaRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: "#E0E0E0",
  },
  metaLabel: { fontSize: 14, color: "#888", fontWeight: "500" },
  metaValue: { fontSize: 14, color: "#1A1A1A", fontWeight: "500", textTransform: "capitalize" },
  metaFree: { color: "#2E7D32" },

  linkBtn: {
    backgroundColor: "#1E88E5",
    borderRadius: 10,
    paddingVertical: 14,
    alignItems: "center",
  },
  linkBtnText: { color: "#FFF", fontWeight: "600", fontSize: 15 },
});
