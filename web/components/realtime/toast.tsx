"use client";

import Link from "next/link";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, MotionConfig, motion } from "motion/react";
import { AlertTriangle, CheckCircle2, Loader2, X } from "lucide-react";
import { cn } from "@/lib/utils";

export type ToastTone = "progress" | "ready" | "failed";

export interface ToastInput {
  /** Same key updates the existing toast in place. */
  key: string;
  tone: ToastTone;
  title: string;
  body?: string;
  href?: string;
  /** Auto-dismiss after this many ms; omit to keep until dismissed or updated. */
  ttl?: number;
}

interface ToastApi {
  show: (t: ToastInput) => void;
  dismiss: (key: string) => void;
  has: (key: string) => boolean;
}

const Ctx = createContext<ToastApi | null>(null);

export function useToast(): ToastApi {
  const v = useContext(Ctx);
  if (!v) throw new Error("useToast must be used inside <ToastProvider>");
  return v;
}

const MAX_TOASTS = 4;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastInput[]>([]);
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  const keys = useRef(new Set<string>());

  const dismiss = useCallback((key: string) => {
    const t = timers.current.get(key);
    if (t) clearTimeout(t);
    timers.current.delete(key);
    keys.current.delete(key);
    setToasts((cur) => cur.filter((x) => x.key !== key));
  }, []);

  const show = useCallback(
    (t: ToastInput) => {
      const prev = timers.current.get(t.key);
      if (prev) clearTimeout(prev);
      timers.current.delete(t.key);
      keys.current.add(t.key);
      setToasts((cur) => {
        const i = cur.findIndex((x) => x.key === t.key);
        if (i >= 0) {
          const next = [...cur];
          next[i] = t;
          return next;
        }
        return [t, ...cur].slice(0, MAX_TOASTS);
      });
      if (t.ttl) timers.current.set(t.key, setTimeout(() => dismiss(t.key), t.ttl));
    },
    [dismiss],
  );

  const has = useCallback((key: string) => keys.current.has(key), []);

  useEffect(() => {
    const m = timers.current;
    return () => m.forEach(clearTimeout);
  }, []);

  const api = useMemo(() => ({ show, dismiss, has }), [show, dismiss, has]);

  return (
    <Ctx.Provider value={api}>
      {children}
      <MotionConfig reducedMotion="user">
        <div
          className="no-print pointer-events-none fixed bottom-4 right-4 z-50 flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2"
          role="region"
          aria-label="Notifications"
          aria-live="polite"
        >
          <AnimatePresence initial={false}>
            {toasts.map((t) => (
              <motion.div
                key={t.key}
                layout="position"
                initial={{ opacity: 0, x: 16 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: 16, transition: { duration: 0.15, ease: [0.23, 1, 0.32, 1] } }}
                transition={{ duration: 0.2, ease: [0.23, 1, 0.32, 1] }}
                className={cn(
                  "pointer-events-auto flex items-start gap-2.5 rounded-md border bg-surface p-3 shadow-pop",
                  t.tone === "progress" && "border-line-strong border-l-4 border-l-accent",
                  t.tone === "ready" && "border-line-strong border-l-4 border-l-status-signed-fg",
                  t.tone === "failed" && "border-line-strong border-l-4 border-l-status-failed-fg",
                )}
              >
                <span className="mt-0.5 shrink-0" aria-hidden>
                  {t.tone === "progress" && <Loader2 className="h-4 w-4 animate-spin text-accent-text motion-reduce:animate-none" />}
                  {t.tone === "ready" && <CheckCircle2 className="h-4 w-4 text-status-signed-fg" />}
                  {t.tone === "failed" && <AlertTriangle className="h-4 w-4 text-status-failed-fg" />}
                </span>
                <div className="min-w-0 flex-1 text-sm leading-snug">
                  <div className="font-semibold">{t.title}</div>
                  {t.body && <div className="text-muted">{t.body}</div>}
                  {t.href && (
                    <Link href={t.href} className="mt-1 inline-block font-semibold text-accent-text underline-offset-2 hover:underline" onClick={() => dismiss(t.key)}>
                      Open incident
                    </Link>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => dismiss(t.key)}
                  className="-m-1 shrink-0 rounded-sm p-1.5 text-muted transition-colors duration-150 hover:bg-surface-2 hover:text-ink"
                  aria-label="Dismiss notification"
                >
                  <X className="h-4 w-4" />
                </button>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      </MotionConfig>
    </Ctx.Provider>
  );
}
