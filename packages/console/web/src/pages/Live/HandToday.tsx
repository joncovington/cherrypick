import type { ReactElement } from "react";
import type { LiveTodayHand, LiveTodayHandOrder } from "@console/shared";
import { DataCard, PnlCell } from "../../components/DataTable";
import { fmtCash, fmtMoney, fmtPrice } from "../../lib/format";
import { etClock } from "../../lib/etTime";

/**
 * Live → today's hand row and card (2026-11-03): the 0DTE trades placed by hand, which no loop
 * manages, stops, force-closes or settles. Everything is packages/manage's `zero_dte` block as
 * `readers/liveHand.ts` shaped it: the split from the loops' fills, the reconciliation and the money
 * are manage's. Its net sits beside the suite total and is never added to it.
 */

/** `-1 7555P` from an OCC symbol and the fill's action. */
export function legLabel(symbol: string, action: string, quantity: number | null): string {
  const s = symbol.replace(/\s+/g, "");
  const m = /([CP])(\d{8})$/.exec(s);
  const q = quantity ?? 1;
  const signed = action.startsWith("Sell") ? -q : q;
  const sign = signed > 0 ? "+" : "−";
  if (m === null) return `${sign}${Math.abs(signed)} ${symbol}`;
  const strike = Number(m[2]) / 1000;
  return `${sign}${Math.abs(signed)} ${strike}${m[1]}`;
}

/** The order's net per-share price (`1.80 cr`) when every leg is the same size; null otherwise. */
export function orderPrice(o: LiveTodayHandOrder): number | null {
  const sizes = new Set(o.legs.map((l) => l.quantity));
  if (sizes.size !== 1) return null;
  const q = [...sizes][0];
  return q === null || q === undefined || q === 0 ? null : Math.round((o.cash / (100 * q)) * 100) / 100;
}

/** A mid as quoted: `0.025` stays three places (a nickel-wide 0DTE quote), whole cents stay two. */
export function fmtMid(v: number | null): string {
  if (v === null) return "—";
  return Math.round(v * 1000) % 10 === 0 ? v.toFixed(2) : v.toFixed(3);
}

/** `2 expired legs still listed by the broker … (2026-11-03: −1 7555P +1 7545P)`, one clause per
 *  expiry; null when nothing expired is listed. */
export function expiredWords(legs: LiveTodayHand["expiredHeld"]): string | null {
  if (legs.length === 0) return null;
  const byExpiry = new Map<string, string[]>();
  for (const l of legs) {
    const q = l.quantity ?? 0;
    const key = l.expiry ?? "?";
    byExpiry.set(key, [...(byExpiry.get(key) ?? []), legLabel(l.symbol, q < 0 ? "Sell" : "Buy", Math.abs(q))]);
  }
  const parts = [...byExpiry].sort(([a], [b]) => a.localeCompare(b)).map(([exp, labels]) => `${exp}: ${labels.join(" ")}`);
  const n = legs.length;
  return `${n} expired ${n === 1 ? "leg" : "legs"} still listed by the broker until it processes the expiry, not counted here (${parts.join("; ")})`;
}

/** The one-line state for the row's `state` cell. */
export function handStateWords(h: LiveTodayHand): string {
  if (h.status === "none") return h.reason ?? "no evaluation today";
  if (h.status === "unread") return `not read: ${h.reason ?? "unknown"}`;
  const snap = etClock(h.snapshotAt, "?");
  return h.markedAt !== null ? `fills ${snap} · marked ${etClock(h.markedAt, "?")}` : `as of ${snap}`;
}

/** One `<td>` per loops-table column, so the hand row lines up with the loops above it. */
export function handCell(key: string, h: LiveTodayHand): ReactElement {
  const ok = h.status === "ok";
  const open = h.orders.filter((o) => o.kind === "open" || o.kind === "mixed").length;
  const closes = h.orders.filter((o) => o.kind === "close").length;
  switch (key) {
    case "loop":
      return (
        <td key={key} title="0DTE trades placed by hand on the names manage leaves to the loops; no loop manages, stops or settles them">
          by hand
          {h.status !== "ok" && <span className="muted"> · {h.status === "none" ? "no evaluation" : "not read"}</span>}
          {ok && h.unreconciled.length > 0 && <span className="pnl-neg"> · does not reconcile</span>}
        </td>
      );
    case "arm":
      return (
        <td key={key} className="muted">
          no loop
        </td>
      );
    case "gate":
      return (
        <td key={key} className="muted">
          — · {h.underlyings.join(", ") || "?"}
        </td>
      );
    case "state":
      return (
        <td key={key} className="muted" title="the position evaluation's broker snapshot (every 30 minutes) and its last re-mark from the stream cache">
          {handStateWords(h)}
        </td>
      );
    case "open":
      return (
        <td key={key} title="legs still held by hand">
          {ok ? `${h.openLegs.length} leg${h.openLegs.length === 1 ? "" : "s"}` : "—"}
        </td>
      );
    case "working":
      return (
        <td key={key} className="muted" title="not read for hand orders">
          —
        </td>
      );
    case "worst":
      return (
        <td key={key} className="muted" title="no worst case is computed for hand trades">
          —
        </td>
      );
    case "mark":
      return (
        <td key={key} title="the legs still held by hand, at mid">
          <PnlCell v={ok && h.openLegs.length > 0 ? h.mark : null} />
        </td>
      );
    case "entries":
      return <td key={key}>{ok ? String(open) : "—"}</td>;
    case "resolved":
      return (
        <td key={key} title="closing orders filled today">
          {ok ? String(closes) : "—"}
        </td>
      );
    case "gross":
      return (
        <td key={key} title="signed cash from today's hand fills, plus the held legs at mid">
          {ok ? fmtMoney(h.gross) : "—"}
        </td>
      );
    case "fees":
      return <td key={key}>{ok ? fmtMoney(h.fees) : "—"}</td>;
    case "settlement":
      return (
        <td key={key} title="n/r while a held leg has still to settle (booked the next morning)">
          {!ok ? "—" : h.settlement === null ? "n/r" : fmtMoney(h.settlement)}
        </td>
      );
    case "net":
      return (
        <td key={key} title={h.netBasis ?? undefined}>
          {ok && h.net === null && h.orders.length > 0 ? <span className="muted">n/r</span> : <PnlCell v={ok ? h.net : null} />}
          {ok && h.net !== null && h.openLegs.length > 0 && <span className="muted"> at mark</span>}
        </td>
      );
    case "breaker":
      return (
        <td key={key} className="muted" title="no daily-loss breaker covers hand trades">
          none
        </td>
      );
    default:
      return <td key={key} />;
  }
}

/** The hand trades' detail: each order today and each leg still held. Drawn only when there is something to show. */
export function HandCard({ hand: h, session }: { hand: LiveTodayHand; session: string }) {
  if (
    h.status !== "ok" ||
    (h.orders.length === 0 && h.unreconciled.length === 0 && h.caveats.length === 0 && h.expiredHeld.length === 0)
  )
    return null;
  // Red is kept for what puts today's figures in doubt; caveats and the expired legs are muted, or
  // a note every morning after a 0DTE session would train the eye to pass over red here.
  const warnings = [
    ...h.unreconciled.map(
      (u) => `${u.symbol.trim()}: ${u.held} held, but today's fills explain ${u.loopFills + u.handFills} (loops ${u.loopFills}, hand ${u.handFills}); mark and net are not stated`,
    ),
    ...(h.afterPublish.length > 0
      ? [`filled after the loops last listed their orders, so possibly a loop's: ${h.afterPublish.join(", ")}`]
      : []),
  ];
  const expired = expiredWords(h.expiredHeld);
  return (
    <DataCard
      title={`By hand — ${h.underlyings.join(", ")} 0DTE, ${session}`}
      headers={["time", "order", "kind", "legs", "price", "cash", "fees"]}
      numFrom={4}
      loading={false}
      rowCount={h.orders.length}
      empty="no hand orders filled today"
      footer={
        <div style={{ display: "flex", flexDirection: "column", gap: "0.3rem" }}>
          {h.openLegs.length > 0 && (
            <p style={{ margin: 0 }}>
              Held:{" "}
              {h.openLegs.map((l) => (
                <span key={l.symbol} title={`${l.markSource ?? "?"} mark${l.markAgeSeconds !== null ? `, ${Math.round(l.markAgeSeconds)}s old` : ""}`}>
                  {legLabel(l.symbol, l.quantity < 0 ? "Sell" : "Buy", Math.abs(l.quantity))} @ {fmtMid(l.mid)}{" "}
                </span>
              ))}
              · mark {fmtCash(h.mark)}
            </p>
          )}
          {warnings.map((n) => (
            <p key={n} className="pnl-neg" style={{ margin: 0 }}>
              {n}
            </p>
          ))}
          {h.caveats.map((n) => (
            <p key={n} className="muted" style={{ margin: 0 }}>
              {n}
            </p>
          ))}
          {expired !== null && (
            <p className="muted" style={{ margin: 0 }} title="the broker lists an expired leg until it processes the expiry overnight">
              {expired}
            </p>
          )}
          <p className="muted" style={{ margin: 0, fontSize: 11 }}>
            No loop manages these: no stop, no force-close, no settlement row, and no breaker counts them. A fill is a loop's when its
            order id is one a loop has published ({Object.keys(h.knownModules).join(", ") || "none read"}); {h.loopFills} of today's fills were.
          </p>
        </div>
      }
    >
      {h.orders.map((o) => (
        <tr key={o.orderId ?? o.at ?? ""}>
          <td>{etClock(o.at)}</td>
          <td className="muted">{o.orderId ?? "—"}</td>
          <td>{o.kind}</td>
          <td>{o.legs.map((l) => legLabel(l.symbol, l.action, l.quantity)).join("  ")}</td>
          <td>{fmtPrice(orderPrice(o))}</td>
          <td>{fmtCash(o.cash)}</td>
          <td>{fmtMoney(o.fees)}</td>
        </tr>
      ))}
    </DataCard>
  );
}
