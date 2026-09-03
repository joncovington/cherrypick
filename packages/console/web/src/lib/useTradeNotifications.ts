import { useEffect, useRef } from "react";
import { pushToast, type ToastTone } from "./toast";

interface NotifyEvent {
  ts: string;
  level: string;
  key: string;
  title: string;
  message: string;
}

const POLL_MS = 15_000;

/** `key` is `trade.<module>.<type>...` -- `trade_notifier.py`'s own vocabulary (entry/exit/
 *  settlement/completion/stop/addon/review/summary), read off the segment rather than guessed
 *  from message text (which carries its own emoji, chosen by the Python side, and is rendered
 *  verbatim -- this is only for the toast's accent color). */
function toneFor(key: string): ToastTone {
  const seg = key.split(".")[2] ?? "";
  if (seg === "entry") return "entry";
  if (seg === "exit" || seg === "settlement" || seg === "completion") return "exit";
  if (seg === "stop" || seg === "addon") return "warn";
  return "info";
}

/**
 * Trade-event toasts, mounted once (Shell.tsx) so they fire regardless of which page is open --
 * matching the Discord feed the suite already runs, reading the SAME log floor
 * (`readers/notify.ts`'s own docstring has the "why this source" reasoning).
 *
 * Polled, not WS-pushed: these events land every couple of minutes at most (trade_notifier's own
 * cadence), nothing here needs sub-second latency the way live quotes do, and polling matches
 * every other near-real-time surface in this app.
 *
 * No backfill on first mount -- the watermark seeds to "now" rather than the log's history, the
 * same rule trade_notifier.py states for its own first activation. A tab left open across a
 * session keeps its watermark in memory; a fresh tab starts clean.
 */
export function useTradeNotifications(): void {
  const since = useRef<string | null>(null);

  useEffect(() => {
    if (since.current === null) since.current = new Date().toISOString();
    let cancelled = false;

    async function poll(): Promise<void> {
      try {
        const qs = since.current !== null ? `?since=${encodeURIComponent(since.current)}` : "";
        const res = await fetch(`/api/notify/trades${qs}`);
        if (!res.ok || cancelled) return;
        const body = (await res.json()) as { events: NotifyEvent[] };
        for (const e of body.events) {
          pushToast({ tone: toneFor(e.key), title: e.title, message: e.message });
          if (since.current === null || e.ts > since.current) since.current = e.ts;
        }
      } catch {
        // A missed poll costs one 15s window of toasts, never a crash -- the next tick catches up.
      }
    }

    void poll();
    const id = window.setInterval(() => void poll(), POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);
}
