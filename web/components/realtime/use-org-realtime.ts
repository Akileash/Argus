"use client";

import { useEffect, useRef, useState } from "react";
import { createClient } from "@/lib/supabase/client";

export type RealtimeState = "connecting" | "live" | "reconnecting";

export interface OrgChange {
  table: "events" | "reports";
  eventType: "INSERT" | "UPDATE" | "DELETE";
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  new: any;
}

interface Options {
  /** Unique per subscriber so two components on one page do not share a channel. */
  name: string;
  orgId: string;
  onChange: (c: OrgChange) => void;
  /** Called whenever the channel (re)subscribes after a drop; refetch to cover missed messages. */
  onResync?: () => void;
}

/** Subscribe to events + reports changes for one org (RLS also scopes them). Re-subscribes automatically after drops. */
export function useOrgRealtime({ name, orgId, onChange, onResync }: Options): RealtimeState {
  const [state, setState] = useState<RealtimeState>("connecting");
  const changeRef = useRef(onChange);
  const resyncRef = useRef(onResync);
  useEffect(() => {
    changeRef.current = onChange;
    resyncRef.current = onResync;
  });

  useEffect(() => {
    const supabase = createClient();
    let everLive = false;
    let cancelled = false;
    let channel: ReturnType<typeof supabase.channel> | null = null;

    // Realtime must hold the user's JWT before joining, otherwise RLS-checked rows arrive as "401 Unauthorized".
    void (async () => {
      const { data } = await supabase.auth.getSession();
      if (cancelled) return;
      if (data.session?.access_token) await supabase.realtime.setAuth(data.session.access_token);
      if (cancelled) return;
      // No server-side column filter: Realtime rejected `org_id=eq.` on reports ("invalid column for filter") and a
      // failed filter kills the whole channel. RLS already limits delivered rows to the user's org.
      channel = supabase
        .channel(`argus-${name}-${orgId}`)
        .on("postgres_changes", { event: "*", schema: "public", table: "events" }, (p) =>
          changeRef.current({ table: "events", eventType: p.eventType, new: p.new }),
        )
        .on("postgres_changes", { event: "*", schema: "public", table: "reports" }, (p) =>
          changeRef.current({ table: "reports", eventType: p.eventType, new: p.new }),
        )
        .subscribe((status) => {
          if (status === "SUBSCRIBED") {
            setState("live");
            if (everLive) resyncRef.current?.();
            everLive = true;
          } else if (status === "CHANNEL_ERROR" || status === "TIMED_OUT" || status === "CLOSED") {
            // supabase-js retries the channel on its own; reflect it in the UI.
            setState(everLive ? "reconnecting" : "connecting");
          }
        });
    })();

    return () => {
      cancelled = true;
      if (channel) void supabase.removeChannel(channel);
    };
  }, [name, orgId]);

  return state;
}
