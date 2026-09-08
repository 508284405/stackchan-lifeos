// Pure helpers shared by the console components. Formatting helpers take the
// translation function so language switches re-render immediately.

const STATE_KEYS = {
  online: "state.online",
  offline: "state.offline",
  degraded: "state.degraded",
  rejected: "state.rejected",
};

export function currentSession(device) {
  return device?.session || null;
}

export function deriveAttention(device) {
  const sessionState = currentSession(device)?.state;
  const health = device?.health || {};
  const faultCount = Array.isArray(health.faults) ? health.faults.length : 0;
  const feedbackStale = Number.isFinite(health.feedback_age_ms) && health.feedback_age_ms > 400;
  return (
    sessionState === "degraded" ||
    sessionState === "offline" ||
    sessionState === "rejected" ||
    health.fault === true ||
    health.feedback_frozen === true ||
    health.link_lost === true ||
    feedbackStale ||
    faultCount > 0
  );
}

export function labelState(value, t) {
  if (!value) return t("state.unknown");
  const key = STATE_KEYS[value];
  return key ? t(key) : value.replaceAll("_", " ");
}

export function formatAge(iso, t) {
  if (!iso) return t("age.none");
  const age = Math.max(0, Date.now() - new Date(iso).getTime());
  if (age < 1000) return t("age.now");
  if (age < 60_000) return t("age.seconds", Math.round(age / 1000));
  return t("age.minutes", Math.round(age / 60_000));
}

export function formatUptime(value, t) {
  if (!Number.isFinite(value)) return "—";
  const seconds = Math.floor(value / 1000);
  if (seconds < 60) return t("uptime.seconds", seconds);
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return t("uptime.minutes", minutes, seconds % 60);
  return t("uptime.hours", Math.floor(minutes / 60), minutes % 60);
}

export function describeEvent(event, t) {
  const payload = event.payload || {};
  if (event.type === "command.state.changed") {
    return `${labelState(payload.from || "new", t)} → ${labelState(payload.to || "unknown", t)}`;
  }
  if (event.type === "device.session.changed") return labelState(payload.state || "updated", t);
  if (event.type === "device.safety.changed") return payload.reason || t("event.safetyBoundary");
  if (event.type === "telemetry.sampled") return t("event.healthSample");
  return payload.reason || t("event.recorded");
}

export function eventTimeString(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function deviceMatchesFilter(device, filter) {
  if (!filter) return true;
  return `${device.display_name} ${device.device_id} ${device.hardware_id}`
    .toLowerCase()
    .includes(filter);
}
