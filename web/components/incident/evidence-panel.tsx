"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Film, Pause, Play, SkipBack, TriangleAlert } from "lucide-react";
import type { SensorTrace } from "@argus/shared";
import type { IncidentEvent, TraceState } from "@/lib/queries/incident";
import { clamp, fmtOffset, traceToVideo, videoToTrace, windowDomain, xToTime } from "@/lib/report/timeline";
import { CHART_MARGIN, SENSOR_COLORS, SensorChart } from "./sensor-chart";
import { Button } from "@/components/ui/button";

function nearest(t: number[], v: (number | null)[], at: number): number | null {
  if (t.length === 0) return null;
  let lo = 0;
  let hi = t.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (t[mid] <= at) lo = mid;
    else hi = mid;
  }
  const i = Math.abs(t[lo] - at) <= Math.abs(t[hi] - at) ? lo : hi;
  return Math.abs(t[i] - at) > 1 ? null : v[i];
}

export function EvidencePanel({
  event,
  clipUrl,
  clipDurationS,
  trace,
  traceState,
  processing,
}: {
  event: IncidentEvent;
  clipUrl: string | null;
  clipDurationS: number | null;
  trace: SensorTrace | null;
  traceState: TraceState;
  processing: boolean;
}) {
  const pre = event.window_pre_s;
  const post = event.window_post_s;
  const domain = useMemo(() => windowDomain(pre, post), [pre, post]);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [playhead, setPlayhead] = useState<number>(0); // trace time (s, relative to t0)
  const [playing, setPlaying] = useState(false);
  const [duration, setDuration] = useState<number>(clipDurationS ?? pre + post);
  const [videoError, setVideoError] = useState(false);

  // smooth playhead while playing (timeupdate is only ~4 Hz)
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    const tick = () => {
      const v = videoRef.current;
      if (v) setPlayhead(videoToTrace(v.currentTime, pre));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, pre]);

  /** Single entry point for both scrubbers: moves video (if any) and the chart cursor. */
  const seek = useCallback(
    (traceT: number) => {
      const t = clamp(traceT, domain.min, domain.max);
      setPlayhead(t);
      const v = videoRef.current;
      if (v) v.currentTime = traceToVideo(t, pre, v.duration || duration);
    },
    [domain, pre, duration],
  );

  const hasVideo = !!clipUrl && !videoError;

  const toggle = () => {
    const v = videoRef.current;
    if (!v) return;
    if (v.paused) void v.play();
    else v.pause();
  };

  return (
    <div className="space-y-4">
      <section aria-label="Video" className="rounded-md border border-line bg-surface">
        <div className="relative aspect-video w-full overflow-hidden rounded-t-md bg-ink">
          {hasVideo ? (
            <video
              ref={videoRef}
              src={clipUrl!}
              className="h-full w-full object-contain"
              playsInline
              preload="metadata"
              data-testid="incident-video"
              onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              onEnded={() => setPlaying(false)}
              onSeeked={(e) => setPlayhead(videoToTrace(e.currentTarget.currentTime, pre))}
              onTimeUpdate={(e) => !playing && setPlayhead(videoToTrace(e.currentTarget.currentTime, pre))}
              onError={() => setVideoError(true)}
              onClick={toggle}
            />
          ) : (
            <Placeholder processing={processing} videoError={videoError} />
          )}
          <span className="absolute left-2 top-2 rounded-sm bg-ink/80 px-1.5 py-0.5 text-[0.65rem] font-semibold uppercase tracking-wide text-surface">
            Front camera
          </span>
        </div>
        {hasVideo && (
          <div className="flex items-center gap-3 border-t border-line px-3 py-2">
            <Button type="button" size="sm" variant="outline" onClick={toggle} aria-label={playing ? "Pause" : "Play"}>
              {playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => seek(0)} title="Jump to trigger (t0)">
              <SkipBack className="h-4 w-4" /> t0
            </Button>
            <Scrubber playhead={playhead} domain={domain} trace={trace} onSeek={seek} />
            <span className="num w-20 text-right text-sm" data-testid="playhead-readout">
              {fmtOffset(playhead, 2)}
            </span>
          </div>
        )}
      </section>

      <section aria-label="Sensor trace" className="rounded-md border border-line bg-surface p-3">
        <div className="mb-2 flex items-baseline justify-between">
          <h2 className="field-label">Sensor trace</h2>
          <span className="text-xs text-muted">Click or drag the chart to seek the video</span>
        </div>
        {trace ? (
          <ChartWithCursor trace={trace} domain={domain} playhead={playhead} onSeek={seek} />
        ) : (
          <EmptyBox
            title={processing ? "Sensor trace is being processed" : traceState === "invalid" ? "Sensor trace could not be read" : "Sensor trace not available"}
            body={
              processing
                ? "The chart appears once the device has uploaded the trace."
                : traceState === "invalid"
                  ? "The trace file does not match the expected format. Summary values from the event record are shown below."
                  : "No trace file was uploaded for this event. Summary values from the event record are shown below."
            }
          />
        )}
        {trace && <Readout trace={trace} at={playhead} />}
        <KeyValues event={event} />
      </section>

      <EnvStrip event={event} trace={trace} />
    </div>
  );
}

function Placeholder({ processing, videoError }: { processing: boolean; videoError: boolean }) {
  return (
    <div className="flex h-full w-full flex-col items-center justify-center gap-2 px-6 text-center text-surface" data-testid="no-clip">
      <Film className="h-8 w-8 opacity-70" aria-hidden />
      <div className="text-sm font-semibold">
        {processing ? "Clip is being processed" : videoError ? "The clip could not be loaded" : "No clip for this event"}
      </div>
      <div className="max-w-sm text-xs opacity-80">
        {processing
          ? "The 30 s front-camera clip appears here as soon as the upload completes."
          : "The report and sensor data below are still available."}
      </div>
    </div>
  );
}

function EmptyBox({ title, body }: { title: string; body: string }) {
  return (
    <div className="flex items-start gap-2 rounded-md border border-dashed border-line-strong bg-surface-2 p-4 text-sm" data-testid="no-trace">
      <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-muted" aria-hidden />
      <div>
        <div className="font-semibold">{title}</div>
        <div className="text-muted">{body}</div>
      </div>
    </div>
  );
}

function Scrubber({
  playhead,
  domain,
  trace,
  onSeek,
}: {
  playhead: number;
  domain: { min: number; max: number };
  trace: SensorTrace | null;
  onSeek: (t: number) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const fromEvent = (e: React.PointerEvent) => {
    const r = ref.current!.getBoundingClientRect();
    onSeek(xToTime(e.clientX, domain, r.left, r.right));
  };
  const pct = (t: number) => `${((t - domain.min) / (domain.max - domain.min)) * 100}%`;
  return (
    <div
      ref={ref}
      role="slider"
      tabIndex={0}
      aria-label="Video position relative to trigger"
      aria-valuemin={domain.min}
      aria-valuemax={domain.max}
      aria-valuenow={Number(playhead.toFixed(2))}
      aria-valuetext={fmtOffset(playhead)}
      data-testid="scrubber"
      className="relative h-7 flex-1 cursor-pointer touch-none select-none"
      onPointerDown={(e) => {
        dragging.current = true;
        e.currentTarget.setPointerCapture(e.pointerId);
        fromEvent(e);
      }}
      onPointerMove={(e) => dragging.current && fromEvent(e)}
      onPointerUp={() => (dragging.current = false)}
      onKeyDown={(e) => {
        const d = e.shiftKey ? 1 : 0.1;
        if (e.key === "ArrowLeft") onSeek(playhead - d);
        else if (e.key === "ArrowRight") onSeek(playhead + d);
        else return;
        e.preventDefault();
      }}
    >
      <div className="absolute inset-x-0 top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-surface-2 ring-1 ring-line" />
      <div className="absolute left-0 top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-ink/70" style={{ width: pct(playhead) }} />
      {trace?.detections
        .filter((d) => d.t >= domain.min && d.t <= domain.max)
        .map((d, i) => (
          <span key={i} className="absolute top-1/2 h-3 w-0.5 -translate-y-1/2 bg-incident-fg/80" style={{ left: pct(d.t) }} title={`${d.type} ${fmtOffset(d.t)}`} />
        ))}
      {domain.min <= 0 && domain.max >= 0 && (
        <span className="absolute inset-y-0 w-0.5 bg-accent" style={{ left: pct(0) }} data-testid="t0-marker" title="Trigger (t0)">
          <span className="absolute -top-0.5 left-1 text-[0.6rem] font-extrabold leading-none text-accent-text">t0</span>
        </span>
      )}
      <span className="absolute top-1/2 h-4 w-2 -translate-x-1/2 -translate-y-1/2 rounded-sm border border-surface bg-ink" style={{ left: pct(playhead) }} />
    </div>
  );
}

function ChartWithCursor({
  trace,
  domain,
  playhead,
  onSeek,
}: {
  trace: SensorTrace;
  domain: { min: number; max: number };
  playhead: number;
  onSeek: (t: number) => void;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(640);
  const dragging = useRef(false);

  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setWidth(Math.max(320, Math.floor(e.contentRect.width))));
    ro.observe(el);
    setWidth(Math.max(320, Math.floor(el.clientWidth)));
    return () => ro.disconnect();
  }, []);

  const seekFrom = (e: React.PointerEvent) => {
    const r = wrap.current!.getBoundingClientRect();
    onSeek(xToTime(e.clientX - r.left, domain, CHART_MARGIN.left, width - CHART_MARGIN.right));
  };

  return (
    <div
      ref={wrap}
      className="w-full cursor-crosshair touch-none select-none"
      data-testid="sensor-chart"
      data-playhead={playhead.toFixed(3)}
      onPointerDown={(e) => {
        dragging.current = true;
        e.currentTarget.setPointerCapture(e.pointerId);
        seekFrom(e);
      }}
      onPointerMove={(e) => dragging.current && seekFrom(e)}
      onPointerUp={() => (dragging.current = false)}
    >
      <SensorChart trace={trace} domain={domain} width={width} playhead={playhead} />
    </div>
  );
}

function Readout({ trace, at }: { trace: SensorTrace; at: number }) {
  const a = nearest(trace.imu.t, trace.imu.a_mag_g, at);
  return (
    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 border-t border-line pt-2 text-xs" aria-live="off">
      <span className="text-muted">
        at <span className="num text-ink">{fmtOffset(at)}</span>
      </span>
      <span>
        |a| <span className="num font-semibold">{a == null ? "—" : `${a.toFixed(2)} g`}</span>
      </span>
      {(Object.keys(trace.ranges) as (keyof typeof trace.ranges)[]).map((k) => {
        const s = trace.ranges[k];
        if (!s) return null;
        const r = nearest(s.t, s.range_m, at);
        const c = nearest(s.t, s.closing_mps, at);
        return (
          <span key={k}>
            <span style={{ color: SENSOR_COLORS[k] }} className="font-semibold">{k}</span>{" "}
            <span className="num">{r == null ? "no echo" : `${r.toFixed(2)} m`}</span>
            {c != null && <span className="num text-muted"> {c.toFixed(1)} m/s</span>}
          </span>
        );
      })}
    </div>
  );
}

function Kv({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className="min-w-[5.5rem]">
      <div className="field-label">{label}</div>
      <div className={`num text-sm font-semibold ${warn ? "text-incident-fg" : ""}`}>{value}</div>
    </div>
  );
}

function KeyValues({ event }: { event: IncidentEvent }) {
  const f = (v: number | null, unit: string, d = 1) => (v == null ? "—" : `${v.toFixed(d)} ${unit}`);
  return (
    <div className="mt-3 flex flex-wrap gap-x-6 gap-y-2 border-t border-line pt-3" data-testid="key-values">
      <Kv label="Peak accel" value={event.peak_g == null ? "—" : `${event.peak_g.toFixed(1)} g${event.peak_g_saturated ? " (saturated)" : ""}`} warn={event.peak_g_saturated} />
      <Kv label="Closing rate" value={f(event.closing_rate_mps, "m/s")} />
      <Kv label="Time to contact" value={f(event.ttc_s, "s")} />
      <Kv label="Min range" value={f(event.range_m, "m")} />
      <Kv label="Sensor" value={event.sensor_id ?? "—"} />
      <Kv label="Hat on" value={event.hat_on == null ? "—" : event.hat_on ? "Yes" : "No"} />
    </div>
  );
}

function EnvStrip({ event, trace }: { event: IncidentEvent; trace: SensorTrace | null }) {
  const co2 = event.co2_ppm ?? trace?.env?.co2_ppm ?? null;
  const temp = event.temp_c ?? trace?.env?.temp_c ?? null;
  const rh = event.rh_pct ?? trace?.env?.rh_pct ?? null;
  return (
    <section aria-label="Environment" className="rounded-md border border-line bg-surface px-3 py-2" data-testid="env-strip">
      <div className="flex flex-wrap items-end gap-x-8 gap-y-1">
        <h2 className="field-label self-center">Environment</h2>
        <Kv label="CO₂" value={co2 == null ? "—" : `${Math.round(co2)} ppm`} />
        <Kv label="Temperature" value={temp == null ? "—" : `${temp.toFixed(1)} °C`} />
        <Kv label="Humidity" value={rh == null ? "—" : `${Math.round(rh)} % RH`} />
      </div>
      <p className="mt-1 text-xs text-muted">Readings are taken at the hat and are biased by the wearer (exhaled CO₂, body heat). Indicative only.</p>
    </section>
  );
}
