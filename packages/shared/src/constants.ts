/** Constants shared across the device API, worker and dashboard. Mirrors contracts/*.json. */

export const LOCATION_PLACEHOLDER = "Location to be determined" as const;

export const REPORT_FOOTER =
  "AI-generated draft for review by a competent person. Does not replace provincial OHS reporting or your employer's incident-reporting procedure." as const;

export const EVENT_TYPES = [
  "struck_by_near_miss",
  "struck_by_contact",
  "head_impact",
  "fall",
  "hat_knocked_off",
  "hat_removed",
  "module_fallback",
] as const;
export type EventType = (typeof EVENT_TYPES)[number];

export const EVENT_CLASSES = ["near_miss", "incident", "ppe_log", "system"] as const;
export type EventClass = (typeof EVENT_CLASSES)[number];

/** Report classifications (system events never get a report). */
export const REPORT_CLASSES = ["near_miss", "incident", "ppe_log"] as const;
export type ReportClass = (typeof REPORT_CLASSES)[number];

/** Deterministic device class per event type (spec 3.4). */
export const EVENT_TYPE_TO_CLASS: Record<EventType, EventClass> = {
  struck_by_near_miss: "near_miss",
  struck_by_contact: "incident",
  head_impact: "incident",
  fall: "incident",
  hat_knocked_off: "incident",
  hat_removed: "ppe_log",
  module_fallback: "system",
};

/** events.status lifecycle (contracts/api.md). */
export const EVENT_STATUSES = [
  "received",
  "uploading",
  "uploaded",
  "analyzing",
  "ready",
  "failed",
] as const;
export type EventStatus = (typeof EVENT_STATUSES)[number];

/** reports.status */
export const REPORT_STATUSES = ["draft", "signed"] as const;
export type ReportStatus = (typeof REPORT_STATUSES)[number];

export const MEDIA_KINDS = ["clip", "sensor_trace", "thumbnail"] as const;
export type MediaKind = (typeof MEDIA_KINDS)[number];

export const STORAGE_BUCKET = "event-media" as const;
