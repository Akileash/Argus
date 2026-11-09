import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";
import {
  ArgusEventSchema,
  ArgusReportSchema,
  EVENT_TYPES,
  EVENT_TYPE_TO_CLASS,
  LOCATION_PLACEHOLDER,
  REPORT_FOOTER,
  SensorTraceSchema,
  makeEvent,
  makeReport,
  makeTrace,
} from "../src/index";

const here = dirname(fileURLToPath(import.meta.url));
const load = (name: string) => JSON.parse(readFileSync(resolve(here, "../../../contracts", name), "utf8"));

const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
const validateEvent = ajv.compile(load("event.schema.json"));
const validateReport = ajv.compile(load("report.schema.json"));
const validateTrace = ajv.compile(load("sensor_trace.schema.json"));

describe("zod and JSON Schema agree on valid fixtures", () => {
  for (const type of EVENT_TYPES) {
    it(`event + trace: ${type}`, () => {
      const ev = makeEvent(type);
      expect(ArgusEventSchema.safeParse(ev).success).toBe(true);
      expect(validateEvent(ev), JSON.stringify(validateEvent.errors)).toBe(true);
      const tr = makeTrace(type);
      expect(SensorTraceSchema.safeParse(tr).success).toBe(true);
      expect(validateTrace(tr), JSON.stringify(validateTrace.errors)).toBe(true);
    });
  }
  it("trace with null gyro (accel-only IMU source)", () => {
    const tr = makeTrace("fall");
    const n = tr.imu.t.length;
    const nulls = Array.from({ length: n }, () => null);
    const t = { ...tr, imu: { ...tr.imu, gx: nulls, gy: nulls, gz: nulls } };
    expect(SensorTraceSchema.safeParse(t).success).toBe(true);
    expect(validateTrace(t), JSON.stringify(validateTrace.errors)).toBe(true);
  });
  it("report", () => {
    const r = makeReport();
    expect(ArgusReportSchema.safeParse(r).success).toBe(true);
    expect(validateReport(r), JSON.stringify(validateReport.errors)).toBe(true);
  });
});

describe("zod and JSON Schema agree on invalid fixtures", () => {
  const bad: Array<[string, unknown, unknown]> = [];
  const ev = makeEvent();
  bad.push(["event extra key", { ...ev, extra: 1 }, "event"]);
  bad.push(["event bad type", { ...ev, type: "nope" }, "event"]);
  bad.push(["event bad sha", { ...ev, media: [{ ...ev.media[0]!, sha256: "xyz" }] }, "event"]);
  bad.push(["event bad uuid", { ...ev, event_id: "not-a-uuid" }, "event"]);
  bad.push(["event missing hat_on", { ...ev, metrics: {} }, "event"]);
  const rep = makeReport();
  bad.push(["report wrong location", { ...rep, header: { ...rep.header, location_on_site: "Bay 4" } }, "report"]);
  bad.push(["report wrong footer", { ...rep, footer: "x" }, "report"]);
  bad.push(["report risk 6", { ...rep, risk: { ...rep.risk, severity: 6 } }, "report"]);
  bad.push(["report classification system", { ...rep, header: { ...rep.header, classification: "system" } }, "report"]);
  bad.push(["report suggested ppe_log", { ...rep, classification_review: { suggested_class: "ppe_log", reason: "x" } }, "report"]);
  bad.push(["report extra key", { ...rep, extra: 1 }, "report"]);
  const tr = makeTrace();
  bad.push(["trace bad range key", { ...tr, ranges: { R9: { t: [], range_m: [], closing_mps: [] } } }, "trace"]);
  bad.push(["trace missing detections", { ...tr, detections: undefined }, "trace"]);

  for (const [name, obj, kind] of bad) {
    it(name, () => {
      const zodOk =
        kind === "event" ? ArgusEventSchema.safeParse(obj).success : kind === "report" ? ArgusReportSchema.safeParse(obj).success : SensorTraceSchema.safeParse(obj).success;
      const v = kind === "event" ? validateEvent : kind === "report" ? validateReport : validateTrace;
      expect(zodOk).toBe(false);
      expect(v(obj)).toBe(false);
    });
  }
});

describe("constants match contracts", () => {
  it("report constants", () => {
    const schema = load("report.schema.json");
    expect(schema.properties.footer.const).toBe(REPORT_FOOTER);
    expect(schema.properties.header.properties.location_on_site.const).toBe(LOCATION_PLACEHOLDER);
  });
  it("event types and class map", () => {
    const schema = load("event.schema.json");
    expect([...EVENT_TYPES]).toEqual(schema.$defs.event_type.enum);
    for (const t of EVENT_TYPES) expect(schema.$defs.event_class.enum).toContain(EVENT_TYPE_TO_CLASS[t]);
  });
});
