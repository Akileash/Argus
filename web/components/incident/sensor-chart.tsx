import type { SensorTrace } from "@argus/shared";
import { timeToX, type Domain } from "@/lib/report/timeline";

/**
 * Pure SVG sensor chart: |a| (g) with impact peaks, per-sensor range and closing rate, hat-on band,
 * detection markers, t0 marker and an optional playhead cursor. No hooks, so it renders on the server
 * (print route) and inside the interactive wrapper alike. Geometry is exported for pointer mapping.
 */

export const CHART_MARGIN = { left: 46, right: 10 } as const;
const MARKER_ROW = 22;
const AXIS_ROW = 22;
const PANEL_GAP = 10;

export const SENSOR_COLORS: Record<string, string> = {
  R1: "#1d4a7a",
  R2: "#b84700",
  R3: "#0f766e",
  R4: "#7c3aed",
  TOP: "#4a483f",
};

// Keys are the detection types the device writes (device/argus/events/trace.py).
const DETECTION_LABEL: Record<string, string> = {
  struck_by_warning: "WARN",
  struck_by_near_miss: "NEAR MISS",
  struck_by_contact: "CONTACT",
  head_impact: "IMPACT",
  fall: "FALL",
  hat_knocked_off: "KNOCKED OFF",
  hat_removed: "HAT OFF",
  hat_on: "HAT ON",
};
const labelFor = (type: string) => DETECTION_LABEL[type] ?? type.replaceAll("_", " ").toUpperCase();
/** Approximate rendered width of a 9px bold label, used to stop neighbouring labels overlapping. */
const labelWidth = (text: string) => text.length * 6 + 6;

export interface DetectionCluster {
  t: number;
  /** Distinct labels in time order, with repeat counts ("WARN x10"). */
  label: string;
  /** Marker times to draw: the first and last of each repeated type, every other detection once. */
  times: number[];
}

/**
 * Collapse detections whose labels would overlap on the axis (e.g. ten struck_by_warning rows and the
 * confirmed near miss all within 0.5 s of t0 on a 30 s axis) into one label, so nothing piles up at t0.
 * `pxPerSecond` maps seconds to chart pixels.
 */
export function clusterDetections(dets: Array<{ t: number; type: string }>, pxPerSecond: number): DetectionCluster[] {
  const sorted = [...dets].sort((a, b) => a.t - b.t);
  const groups: Array<Array<{ t: number; type: string }>> = [];
  for (const d of sorted) {
    const g = groups[groups.length - 1];
    const text = g ? Array.from(new Set(g.map((x) => labelFor(x.type)))).join(" \u00b7 ") : "";
    if (g && (d.t - g[0]!.t) * pxPerSecond < labelWidth(text)) g.push(d);
    else groups.push([d]);
  }
  return groups.map((g) => {
    const order: string[] = [];
    const byType = new Map<string, number[]>();
    for (const d of g) {
      if (!byType.has(d.type)) order.push(d.type);
      byType.set(d.type, [...(byType.get(d.type) ?? []), d.t]);
    }
    const label = order
      .map((ty) => {
        const n = byType.get(ty)!.length;
        return n > 1 ? `${labelFor(ty)} \u00d7${n}` : labelFor(ty);
      })
      .join(" \u00b7 ");
    const times = order.flatMap((ty) => {
      const ts = byType.get(ty)!;
      return ts.length > 2 ? [ts[0]!, ts[ts.length - 1]!] : ts;
    });
    return { t: g[0]!.t, label, times };
  });
}

export interface ChartPanel {
  key: "accel" | "range" | "closing" | "hat";
  title: string;
  y: number;
  h: number;
}

export function chartLayout(panelHeight: number) {
  const panels: ChartPanel[] = [];
  let y = MARKER_ROW;
  const add = (key: ChartPanel["key"], title: string, h: number) => {
    panels.push({ key, title, y, h });
    y += h + PANEL_GAP;
  };
  add("accel", "|a|  g", panelHeight);
  add("range", "Range  m", panelHeight);
  add("closing", "Closing rate  m/s", panelHeight);
  add("hat", "Hat", 16);
  const height = y - PANEL_GAP + AXIS_ROW;
  return { panels, height, plotTop: MARKER_ROW, plotBottom: y - PANEL_GAP };
}

function niceExtent(vals: number[], floor?: number, ceil?: number): [number, number] {
  let lo = Math.min(...vals);
  let hi = Math.max(...vals);
  if (!isFinite(lo) || !isFinite(hi)) return [floor ?? 0, ceil ?? 1];
  if (floor != null) lo = Math.min(lo, floor);
  if (ceil != null) hi = Math.max(hi, ceil);
  if (hi - lo < 1e-6) hi = lo + 1;
  const pad = (hi - lo) * 0.08;
  return [lo - pad, hi + pad];
}

function pathFor(t: number[], v: (number | null)[], d: Domain, x0: number, x1: number, y: (v: number) => number): string {
  let out = "";
  let pen = false;
  for (let i = 0; i < t.length; i++) {
    const val = v[i];
    if (val == null || t[i] < d.min || t[i] > d.max) {
      pen = false;
      continue;
    }
    out += `${pen ? "L" : "M"}${timeToX(t[i], d, x0, x1).toFixed(1)},${y(val).toFixed(1)}`;
    pen = true;
  }
  return out;
}

const ink = { fill: "var(--ink)" } as const;
const mutedText = { fill: "var(--muted)" } as const;

export function SensorChart({
  trace,
  domain,
  width,
  panelHeight = 78,
  playhead = null,
  className,
}: {
  trace: SensorTrace;
  domain: Domain;
  width: number;
  panelHeight?: number;
  playhead?: number | null;
  className?: string;
}) {
  const { panels, height, plotTop, plotBottom } = chartLayout(panelHeight);
  const x0 = CHART_MARGIN.left;
  const x1 = Math.max(x0 + 10, width - CHART_MARGIN.right);
  const X = (t: number) => timeToX(t, domain, x0, x1);

  // Visible samples only for scaling.
  const inWin = (t: number) => t >= domain.min && t <= domain.max;
  const pxPerSecond = (x1 - x0) / Math.max(1e-6, domain.max - domain.min);
  const aVals = trace.imu.a_mag_g.filter((_, i) => inWin(trace.imu.t[i]));
  const peaks = (trace.imu_peaks ?? []).filter((p) => inWin(p.t));
  const [aLo, aHi] = niceExtent([...aVals, ...peaks.map((p) => p.a_mag_g)], 0, 3);

  const sensors = (Object.keys(trace.ranges) as (keyof typeof trace.ranges)[]).filter((k) => trace.ranges[k]);
  const rangeVals = sensors.flatMap((k) => trace.ranges[k]!.range_m.filter((v): v is number => v != null));
  const closeVals = sensors.flatMap((k) => trace.ranges[k]!.closing_mps.filter((v): v is number => v != null));
  const [rLo, rHi] = niceExtent(rangeVals, 0, 4);
  const [cLo, cHi] = niceExtent(closeVals, -1, 1);

  const panel = (key: ChartPanel["key"]) => panels.find((p) => p.key === key)!;
  const yScale = (p: ChartPanel, lo: number, hi: number) => (v: number) => p.y + p.h - ((v - lo) / (hi - lo)) * p.h;

  const pa = panel("accel");
  const pr = panel("range");
  const pc = panel("closing");
  const ph = panel("hat");
  const ya = yScale(pa, aLo, aHi);
  const yr = yScale(pr, rLo, rHi);
  const yc = yScale(pc, cLo, cHi);

  // Hat-on segments (first entry = state at window start).
  const hat = [...trace.hat_on].sort((a, b) => a.t - b.t);
  const hatSegs = hat.map((h, i) => ({
    from: Math.max(domain.min, i === 0 ? domain.min : h.t),
    to: Math.min(domain.max, hat[i + 1]?.t ?? domain.max),
    on: h.on,
  }));

  const ticks: number[] = [];
  const span = domain.max - domain.min;
  const step = span > 20 ? 5 : span > 8 ? 2 : span > 3 ? 1 : 0.5;
  for (let t = Math.ceil(domain.min / step) * step; t <= domain.max + 1e-9; t += step) ticks.push(Math.round(t * 100) / 100);

  const yTicks = (p: ChartPanel, lo: number, hi: number, y: (v: number) => number, zero = false) => {
    const vals = [lo + (hi - lo) * 0.08, hi - (hi - lo) * 0.08];
    if (zero && lo < 0 && hi > 0) vals.push(0);
    return vals.map((v) => ({ v, y: y(v), p }));
  };

  return (
    <svg
      role="img"
      aria-label="Sensor trace: acceleration, range and closing rate, hat state"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={className}
      style={{ display: "block", fontFamily: "var(--font-jetbrains-mono, ui-monospace, monospace)", fontSize: 10 }}
    >
      {/* panel backgrounds + gridlines */}
      {panels.map((p) => (
        <g key={p.key}>
          <rect x={x0} y={p.y} width={x1 - x0} height={p.h} style={{ fill: "var(--surface)", stroke: "var(--line)" }} />
          {ticks.map((t) => (
            <line key={t} x1={X(t)} x2={X(t)} y1={p.y} y2={p.y + p.h} style={{ stroke: "var(--line)", strokeWidth: 0.5 }} />
          ))}
          <text x={x0 + 4} y={p.y + 10} style={{ ...mutedText, fontWeight: 600, fontSize: 9 }}>
            {p.title}
          </text>
        </g>
      ))}

      {/* y tick labels */}
      {[
        ...yTicks(pa, aLo, aHi, ya),
        ...yTicks(pr, rLo, rHi, yr),
        ...yTicks(pc, cLo, cHi, yc, true),
      ].map((tk, i) => (
        <text key={i} x={x0 - 4} y={tk.y + 3} textAnchor="end" style={mutedText}>
          {tk.v.toFixed(Math.abs(tk.v) < 10 ? 1 : 0)}
        </text>
      ))}
      {cLo < 0 && cHi > 0 && (
        <line x1={x0} x2={x1} y1={yc(0)} y2={yc(0)} style={{ stroke: "var(--line-strong)", strokeDasharray: "2 2" }} />
      )}
      <line x1={x0} x2={x1} y1={ya(1)} y2={ya(1)} style={{ stroke: "var(--line-strong)", strokeDasharray: "2 2", strokeWidth: 0.7 }} />

      {/* |a| trace */}
      <path d={pathFor(trace.imu.t, trace.imu.a_mag_g, domain, x0, x1, ya)} fill="none" style={{ stroke: "var(--ink)", strokeWidth: 1.2 }} />
      {peaks.map((p, i) => (
        <g key={i}>
          {p.saturated ? (
            <path d={`M${X(p.t) - 5},${ya(p.a_mag_g) + 5}L${X(p.t) + 5},${ya(p.a_mag_g) + 5}L${X(p.t)},${ya(p.a_mag_g) - 5}Z`} style={{ fill: "var(--incident-fg)" }} />
          ) : (
            <circle cx={X(p.t)} cy={ya(p.a_mag_g)} r={3.5} style={{ fill: "var(--incident-fg)" }} />
          )}
          <text x={X(p.t) + 7} y={ya(p.a_mag_g) + 3} style={{ ...ink, fontWeight: 700 }}>
            {p.a_mag_g.toFixed(1)}g{p.saturated ? " SAT" : ""}
          </text>
        </g>
      ))}

      {/* range + closing rate per sensor */}
      {sensors.map((k) => {
        const s = trace.ranges[k]!;
        const color = SENSOR_COLORS[k] ?? "#4a483f";
        return (
          <g key={k} fill="none" style={{ stroke: color, strokeWidth: 1.4 }}>
            <path d={pathFor(s.t, s.range_m, domain, x0, x1, yr)} />
            <path d={pathFor(s.t, s.closing_mps, domain, x0, x1, yc)} />
          </g>
        );
      })}
      {sensors.length === 0 && (
        <>
          <text x={x0 + 90} y={pr.y + pr.h / 2 + 3} style={mutedText}>No ranging sensor data in this window</text>
          <text x={x0 + 90} y={pc.y + pc.h / 2 + 3} style={mutedText}>No closing-rate track</text>
        </>
      )}
      {sensors.length > 0 && (
        <g>
          {sensors.map((k, i) => (
            <g key={k} transform={`translate(${x1 - 6 - (sensors.length - i) * 34},${pr.y + 4})`}>
              <rect width={8} height={8} y={0} style={{ fill: SENSOR_COLORS[k] ?? "#4a483f" }} />
              <text x={11} y={8} style={{ ...ink, fontWeight: 600 }}>{k}</text>
            </g>
          ))}
        </g>
      )}

      {/* hat-on band */}
      {hatSegs.map((s, i) => (
        <g key={i}>
          <rect
            x={X(s.from)}
            y={ph.y}
            width={Math.max(0, X(s.to) - X(s.from))}
            height={ph.h}
            style={{ fill: s.on ? "var(--risk-1-bg)" : "var(--incident-bg)" }}
          />
          <text x={X(s.from) + 4} y={ph.y + 11} style={{ fill: s.on ? "var(--risk-1-fg)" : "var(--incident-fg)", fontWeight: 600, fontSize: 9 }}>
            {s.on ? "HAT ON" : "HAT OFF"}
          </text>
        </g>
      ))}

      {/* detection markers: overlapping labels are collapsed into one */}
      {clusterDetections(trace.detections.filter((d) => inWin(d.t)), pxPerSecond).map((c, i) => (
        <g key={i}>
          {c.times.map((tt, j) => (
            <line key={j} x1={X(tt)} x2={X(tt)} y1={plotTop - 4} y2={plotBottom} style={{ stroke: "var(--incident-fg)", strokeDasharray: "3 3", strokeWidth: 1, opacity: 0.75 }} />
          ))}
          <text x={X(c.t) + 2} y={i % 2 === 0 ? 9 : 19} style={{ fill: "var(--incident-fg)", fontWeight: 700, fontSize: 9 }}>
            {c.label}
          </text>
        </g>
      ))}

      {/* t0 */}
      {inWin(0) && (
        <g>
          <line x1={X(0)} x2={X(0)} y1={plotTop - 4} y2={plotBottom} style={{ stroke: "var(--accent)", strokeWidth: 2 }} />
          <text x={X(0)} y={height - 6} textAnchor="middle" style={{ fill: "var(--accent-text)", fontWeight: 800, fontSize: 10 }}>t0</text>
        </g>
      )}

      {/* x axis */}
      {ticks.filter((t) => t !== 0).map((t) => (
        <text key={t} x={X(t)} y={height - 6} textAnchor="middle" style={mutedText}>
          {t > 0 ? `+${t}` : t}
        </text>
      ))}

      {/* playhead */}
      {playhead != null && playhead >= domain.min && playhead <= domain.max && (
        <g pointerEvents="none">
          <line x1={X(playhead)} x2={X(playhead)} y1={plotTop - 4} y2={plotBottom} style={{ stroke: "var(--ink)", strokeWidth: 1.5 }} />
          <polygon points={`${X(playhead) - 4},${plotBottom + 2} ${X(playhead) + 4},${plotBottom + 2} ${X(playhead)},${plotBottom - 3}`} style={{ fill: "var(--ink)" }} />
        </g>
      )}
    </svg>
  );
}
