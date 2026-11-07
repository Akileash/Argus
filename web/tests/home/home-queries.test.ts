import { describe, expect, it } from "vitest";
import {
  filterRows,
  formatRelative,
  sortDevices,
  sortRows,
  toDeviceSeen,
  toIncidentRow,
  toKpis,
  toastTitle,
  typeLabel,
  upsertRow,
} from "@/lib/queries/home";

const raw = (over: Record<string, unknown> = {}) => ({
  id: "00000000-0000-4000-8000-000000000001",
  type: "struck_by_near_miss",
  t0: "2026-11-01T12:00:00Z",
  device_class: "near_miss",
  final_class: null,
  llm_suggested_class: null,
  severity: 12,
  status: "ready",
  device: { serial: "ARG-0001" },
  worker: { full_name: "J. Smith", crew: { name: "Concrete A" } },
  report: { report_no: "ARG-2026-0001", status: "draft" },
  ...over,
});

describe("toIncidentRow", () => {
  it("maps joins and derived flags", () => {
    const r = toIncidentRow(raw());
    expect(r).toMatchObject({ ref: "ARG-2026-0001", device: "ARG-0001", worker: "J. Smith", crew: "Concrete A", needsSignoff: true, quiet: false, aiSuggestsReclass: false });
  });
  it("accepts array-shaped embeds and missing joins", () => {
    const r = toIncidentRow(raw({ device: [{ serial: "ARG-0002" }], worker: null, report: [] }));
    expect(r.device).toBe("ARG-0002");
    expect(r.worker).toBeNull();
    expect(r.ref).toBe("EVT-0001");
    expect(r.needsSignoff).toBe(false);
  });
  it("uses final_class over device_class", () => {
    expect(toIncidentRow(raw({ final_class: "incident" })).cls).toBe("incident");
  });
  it("flags AI reclass only while unsigned and different", () => {
    expect(toIncidentRow(raw({ llm_suggested_class: "incident" })).aiSuggestsReclass).toBe(true);
    expect(toIncidentRow(raw({ llm_suggested_class: "incident", report: { report_no: "X", status: "signed" } })).aiSuggestsReclass).toBe(false);
    expect(toIncidentRow(raw({ llm_suggested_class: "near_miss" })).aiSuggestsReclass).toBe(false);
  });
  it("marks hat_removed quiet", () => {
    expect(toIncidentRow(raw({ type: "hat_removed", device_class: "ppe_log", report: null })).quiet).toBe(true);
  });
});

describe("sorting and filtering", () => {
  const a = toIncidentRow(raw({ id: "a", t0: "2026-11-01T10:00:00Z" }));
  const b = toIncidentRow(raw({ id: "b", t0: "2026-11-02T10:00:00Z", device_class: "incident", type: "head_impact", report: { report_no: "R2", status: "signed" } }));
  const c = toIncidentRow(raw({ id: "c", t0: "2026-11-03T10:00:00Z", type: "hat_removed", device_class: "ppe_log", report: null }));
  it("sorts newest first and upserts in place", () => {
    expect(sortRows([a, b, c]).map((r) => r.id)).toEqual(["c", "b", "a"]);
    const updated = { ...a, status: "failed" as const };
    const next = upsertRow([c, b, a], updated);
    expect(next).toHaveLength(3);
    expect(next.find((r) => r.id === "a")?.status).toBe("failed");
    const d = toIncidentRow(raw({ id: "d", t0: "2026-11-04T10:00:00Z" }));
    expect(upsertRow([c, b, a], d)[0].id).toBe("d");
  });
  it("filters by class and sign-off", () => {
    expect(filterRows([a, b, c], "incident", false).map((r) => r.id)).toEqual(["b"]);
    expect(filterRows([a, b, c], "ppe_log", false).map((r) => r.id)).toEqual(["c"]);
    expect(filterRows([a, b, c], "all", true).map((r) => r.id)).toEqual(["a"]);
    expect(filterRows([a, b, c], "all", false)).toHaveLength(3);
  });
});

describe("formatting", () => {
  const now = new Date("2026-11-07T12:00:00Z").getTime();
  it("formats relative time", () => {
    expect(formatRelative(null, now)).toBe("never");
    expect(formatRelative("2026-11-07T11:59:50Z", now)).toBe("just now");
    expect(formatRelative("2026-11-07T11:56:00Z", now)).toBe("4 min ago");
    expect(formatRelative("2026-11-07T09:00:00Z", now)).toBe("3 h ago");
    expect(formatRelative("2026-11-02T12:00:00Z", now)).toBe("5 d ago");
    expect(formatRelative("2026-11-08T12:00:00Z", now)).toBe("just now");
  });
  it("labels types and toast titles", () => {
    expect(typeLabel("struck_by_near_miss")).toBe("Struck-by near miss");
    expect(typeLabel("weird_new_type")).toBe("Weird new type");
    expect(toastTitle(toIncidentRow(raw()))).toBe("Struck-by near miss — ARG-0001 / J. Smith");
  });
});

describe("kpis and devices", () => {
  it("coerces kpis, defaulting to zero", () => {
    expect(toKpis(null)).toEqual({ nearMissesWeek: 0, incidentsWeek: 0, awaitingSignoff: 0 });
    expect(toKpis({ near_misses_week: 3, incidents_week: 1, reports_awaiting_signoff: 2 })).toEqual({ nearMissesWeek: 3, incidentsWeek: 1, awaitingSignoff: 2 });
  });
  it("sorts devices by last seen, never-seen last", () => {
    const d = [
      toDeviceSeen({ device_id: "1", serial: "ARG-0003", last_event_at: null }),
      toDeviceSeen({ device_id: "2", serial: "ARG-0002", last_event_at: "2026-11-01T00:00:00Z" }),
      toDeviceSeen({ device_id: "3", serial: "ARG-0001", last_event_at: "2026-11-05T00:00:00Z" }),
    ];
    expect(sortDevices(d).map((x) => x.serial)).toEqual(["ARG-0001", "ARG-0002", "ARG-0003"]);
  });
});
