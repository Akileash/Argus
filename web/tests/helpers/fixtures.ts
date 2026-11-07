import type { ArgusEvent } from "@/lib/contracts/event";

export const EVENT_ID = "11111111-1111-4111-8111-111111111111";

export function nearMissEvent(over: Partial<ArgusEvent> = {}): ArgusEvent {
  return {
    schema_version: "1",
    event_id: EVENT_ID,
    device_serial: "ARG-0001",
    type: "struck_by_near_miss",
    device_class: "near_miss",
    t0: "2026-11-07T15:00:00Z",
    detected_at: "2026-11-07T15:00:01Z",
    window: { pre_s: 20, post_s: 10 },
    metrics: { peak_g: 2.1, peak_g_saturated: false, closing_rate_mps: 3.2, ttc_s: 0.8, range_m: 2.4, sensor_id: "R2", hat_on: true },
    env: { co2_ppm: 600, temp_c: 21.5, rh_pct: 40 },
    merged_triggers: [],
    media: [
      { kind: "clip", camera: "front", filename: "front.mp4", content_type: "video/mp4", bytes: 1000, sha256: "a".repeat(64), duration_s: 30 },
      { kind: "sensor_trace", filename: "sensor_trace.json", content_type: "application/json", bytes: 200, sha256: "b".repeat(64) },
    ],
    firmware: { version: "0.1.0", impls: { imu: "mock", range: "mock" }, fallbacks_active: [] },
    ...over,
  };
}

export function hatRemovedEvent(): ArgusEvent {
  return nearMissEvent({
    event_id: "22222222-2222-4222-8222-222222222222",
    type: "hat_removed",
    device_class: "ppe_log",
    metrics: { hat_on: false },
    media: [],
  });
}

export const DEVICE = { id: "dev-1", serial: "ARG-0001", org_id: "org-1", site_id: null };
