"use client";

import { useSyncExternalStore } from "react";
import { fmtDateTime } from "@/lib/report/format";

const subscribe = () => () => {};

/**
 * Date/time in Mountain Time (see DISPLAY_TZ). Formatted in the browser so server and client output cannot disagree
 * (ICU tz data differs between the Node SSR process and browsers); SSR and hydration show UTC, then it swaps.
 */
export function Time({ iso }: { iso: string | null | undefined }) {
  const text = useSyncExternalStore(
    subscribe,
    () => fmtDateTime(iso),
    () => (iso ? `${iso.slice(0, 16).replace("T", " ")} UTC` : "—"),
  );
  return <span suppressHydrationWarning>{text}</span>;
}
