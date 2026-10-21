import { Link } from "react-router-dom";
import type { LiveTodayLoop, LiveTodayPayload } from "@console/shared";
import { DataCard, PnlCell, fmtNum } from "../../components/DataTable";
import { TileGrid } from "../../components/performance/TileGrid";
import { fmtMoney } from "../../lib/format";
import { useLiveToday } from "../../lib/useLiveToday";
import { Tile } from "../Flies/PerformanceTab";

/**
 * The Live page's `today` tab (2026-10-15): every live loop's day on one page -- what is armed,
 * what is open, what settled, and anything that needs a person -- over `GET /api/live/today`
 * (`readers/liveToday.ts`, composed from the per-loop readers). Each loop's row links to its own
 * tab, which holds the detail. There is no button here that touches an order.
 *
 * Money follows the one layout: "net" is net, costs sit in their own columns, settlement never
 * recorded apart reads n/r. The suite total sums only today's realised net, which every loop states
 * the same way; the worst case and the mark are each loop's own measure and are never added up.
 */
export { useLiveToday };

/** The header badge's words: how many loops are armed now, and the halt flag. */
export function suiteBadgeText(d: LiveTodayPayload): string {
  const n = d.suite.armed.length;
  const armed = n === 0 ? "none armed" : `${n} armed`;
  return d.halted ? `${armed} · HALTED` : armed;
}

function armWords(l: LiveTodayLoop): { text: string; cls: string } {
  const a = l.arm;
  if (a.armed && a.pastDisarm) return { text: "past disarm", cls: "muted" };
  if (a.armed) return { text: "ARMED", cls: "pnl-pos" };
  if (a.stale) return { text: "stale arm", cls: "muted" };
  return { text: "not armed", cls: "muted" };
}

function gateWords(v: boolean | null): string {
  return v === null ? "unknown" : v ? "on" : "off";
}

function settlementWords(v: number | null, recorded: number, trades: number): string {
  return v === null ? `n/r (${recorded} of ${trades})` : fmtMoney(v);
}

/** No limit configured is "no limit", never "ok": a breaker that cannot fire is not a clear one. */
function breakerWords(l: LiveTodayLoop): string {
  const b = l.breaker;
  if (b.limit === null) return "no limit";
  if (b.tripped === null) return `unknown · limit ${fmtMoney(b.limit)}`;
  return `${b.tripped ? "TRIPPED" : "ok"} · ${fmtMoney(b.figure)} of −${fmtMoney(Math.abs(b.limit))}`;
}

function loopStateWords(l: LiveTodayLoop): string {
  if (l.loopState === null) return "no loop record";
  if (l.loopState.state === "no-data") return "no iterations";
  const age = l.loopState.ageSeconds;
  return age === null ? l.loopState.state : `${l.loopState.state} · ${age < 120 ? `${Math.round(age)}s` : `${Math.round(age / 60)}m`} ago`;
}

export function LiveTodayView({ data: d }: { data: LiveTodayPayload }) {
  const s = d.suite;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.8rem" }}>
      <section className="card">
        <TileGrid count={6}>
          <Tile
            label={`armed · ${d.session}`}
            value={s.armed.length === 0 ? `none of ${s.loops}` : `${s.armed.length} of ${s.loops} · ${s.armed.join(", ")}`}
            tone={s.armed.length > 0 ? "pos" : "dim"}
            title="A loop is armed when its arm record is dated today and the disarm time has not passed. Each loop is armed separately."
          />
          <Tile
            label="halt flag"
            value={d.halted ? "HALTED" : "clear"}
            tone={d.halted ? "neg" : "dim"}
            title="state/halt-live.flag, suite-wide: blocks new entries on every loop; fills are still confirmed and open positions still managed and settled."
          />
          <Tile
            label="net today · all loops"
            value={s.net === null ? "n/r" : fmtMoney(s.net)}
            tone={s.net === null ? "dim" : s.net >= 0 ? "pos" : "neg"}
            title={
              s.net === null
                ? `Not totalled: ${s.excluded.map((e) => `${e.loop} — ${e.reason}`).join("; ")}`
                : `Realised net today over ${s.summed.join(", ") || "no loop"}: gross − fees − settlement, positions resolved today.` +
                  (s.excluded.length > 0 ? ` Left out: ${s.excluded.map((e) => `${e.loop} (${e.reason})`).join(", ")}.` : "")
            }
          />
          <Tile
            label="gross · fees · settlement"
            value={
              s.gross === null
                ? "n/r"
                : `${fmtMoney(s.gross)} · ${fmtMoney(s.fees)} · ${s.settlementFees === null ? "n/r" : fmtMoney(s.settlementFees)}`
            }
            tone="dim"
            title="Summed over the same loops as the net. Settlement is n/r when any loop's is: some row never recorded it apart, so it stays inside that row's fees."
          />
          <Tile label="resolved today" value={s.trades === null ? "n/r" : String(s.trades)} tone="dim" title="Positions settled or closed today, every loop." />
          <Tile
            label="needs a person"
            value={String(d.attention.length)}
            tone={d.attention.length > 0 ? "neg" : "dim"}
            title="Orphaned orders, held submissions, a tripped daily-loss breaker, overdue settlement, failing broker contact or an unreadable ledger."
          />
        </TileGrid>
      </section>

      <section className="card">
        <h2>needs a person</h2>
        {d.attention.length === 0 ? (
          <p className="muted">Nothing needs a person: no orphaned or held orders, no tripped breaker, no overdue settlement, broker contact ok.</p>
        ) : (
          <ul style={{ margin: 0, paddingLeft: "1.1rem" }}>
            {d.attention.map((a, i) => (
              <li key={`${a.loop}-${a.kind}-${i}`}>
                <Link to={a.to}>{a.loop}</Link> <span className="pnl-neg">{a.kind.replace(/_/g, " ")}</span> — {a.detail}
              </li>
            ))}
          </ul>
        )}
      </section>

      <DataCard
        title={`Live loops — ${d.session}`}
        headers={["loop", "arm", "gate · symbol", "loop", "open", "working", "worst case", "mark", "entries", "resolved", "gross", "fees", "settlement", "net", "loss breaker"]}
        numFrom={4}
        loading={false}
        rowCount={d.loops.length}
        empty="no live loops"
      >
        {d.loops.map((l) => {
          // A disabled module holding nothing folds to one line; anything open, working, held,
          // orphaned or overdue (or an arm record for today) brings the full row back.
          if (l.retired)
            return (
              <tr key={l.loop} className="muted">
                <td>
                  <Link to={l.to}>{l.loop}</Link>
                </td>
                <td colSpan={14} title="modules.<name>.enabled is false in the orchestrator config">
                  off — module disabled, nothing open or working
                </td>
              </tr>
            );
          const arm = armWords(l);
          const t = l.today;
          return (
            <tr key={l.loop}>
              <td>
                <Link to={l.to}>{l.loop}</Link>
                {l.ledger !== "ok" && (
                  <span className={l.ledger === "failed" ? "pnl-neg" : "muted"} title={l.ledgerError ?? undefined}>
                    {" "}
                    · {l.ledger === "absent" ? "no ledger" : "read failed"}
                  </span>
                )}
              </td>
              <td
                className={arm.cls}
                title={`arm record ${l.arm.date ?? "none"}${l.arm.at !== null ? ` (written ${l.arm.at})` : ""}; disarm ${l.arm.disarmTime} ET`}
              >
                {arm.text}
              </td>
              <td title="the module's live master switch, and the symbol (and arm) the loop is pinned to">
                {gateWords(l.arm.liveEnabled)} · {l.arm.symbol ?? "?"}
                {l.arm.arm !== null ? ` · ${l.arm.arm}` : ""}
              </td>
              <td className="muted">{loopStateWords(l)}</td>
              <td>{l.ledger === "ok" ? fmtNum(l.open, 0) : "—"}</td>
              <td title={l.workingBasis}>{l.ledger === "ok" ? fmtNum(l.working, 0) : "—"}</td>
              <td title={l.worstCaseBasis}>{fmtMoney(l.worstCase)}</td>
              <td title={l.mark?.basis}>
                <PnlCell v={l.mark?.value ?? null} />
              </td>
              <td>{l.ledger === "ok" ? fmtNum(l.entriesToday, 0) : "—"}</td>
              <td title={t?.basis}>{t !== null ? fmtNum(t.trades, 0) : "—"}</td>
              <td>{t !== null ? fmtMoney(t.gross) : "—"}</td>
              <td>{t !== null ? fmtMoney(t.fees) : "—"}</td>
              <td>{t !== null ? settlementWords(t.settlementFees, t.settlementTrades, t.trades) : "—"}</td>
              <td>
                <PnlCell v={t?.net ?? null} />
              </td>
              <td className={l.breaker.tripped === true ? "pnl-neg" : "muted"} title={l.breaker.basis}>
                {breakerWords(l)}
              </td>
            </tr>
          );
        })}
      </DataCard>
      <p className="muted" style={{ fontSize: 11, margin: 0 }}>
        Net is realised today: gross − fees − settlement over positions resolved today, at the broker's fills. Worst case and mark are
        each loop's own measure (hover for which) and are not added across loops.
      </p>
    </div>
  );
}

export function LiveTodayPage() {
  const { data, isLoading, isError } = useLiveToday();
  if (isError) return <p className="pnl-neg">The live loops could not be read.</p>;
  if (isLoading || data === undefined) return <p className="muted">loading the live loops…</p>;
  return <LiveTodayView data={data} />;
}
