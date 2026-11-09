import { useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import type { ManageLedger, ManageLedgerBucket, ManageLedgerLot, ManageLedgerWindow } from "@console/shared";
import { LEDGER_WINDOWS } from "@console/shared";
import { fmtCash } from "../../lib/format";
import { Card } from "../../components/DataTable";
import { HelpSection, type CardHelpContent } from "../../components/CardHelp";
import {
  FilterGroup,
  FilterSortButton,
  FilterSortPanel,
  FilterSummary,
  SortSection,
  sortRows,
  useSortChoice,
  type SortOption,
} from "../../components/FilterSort";
import { CardLightbox, LightboxFrame, useOpenParam, type LightboxTab } from "../Backtest/CardLightbox";
import { GuideLink } from "../../components/GuideLink";

/**
 * Live → wheel's ledger (2026-11-04): what each name has earned over the account's whole history, read
 * from manage's `ledger` (packages/manage `ledger.py`). Puts and calls are each option leg's flat-to-flat
 * net after fees on its closing day; shares a stock span's; dividends on their day. Every figure is
 * manage's, per window as manage counted it; nothing here adds a window up or decides what was kept.
 * A name card opens its closed spans and what it still holds (`?ledger=`), with a CSV of them.
 */

const WINDOW_PARAM = "lw";
const DEFAULT_WINDOW: ManageLedgerWindow = "ALL";

export function ledgerWindow(raw: string | null): ManageLedgerWindow {
  return (LEDGER_WINDOWS as string[]).includes(raw ?? "") ? (raw as ManageLedgerWindow) : DEFAULT_WINDOW;
}

function tone(v: number): string {
  return v > 0 ? "pnl-pos" : v < 0 ? "pnl-neg" : "";
}

/** `2026-11-20 45P` from an OCC symbol; "shares" or "dividend" for the other kinds. */
export function lotLeg(l: { symbol: string; right: string }): string {
  if (l.right === "D") return "dividend";
  if (l.right === "S") return "shares";
  const s = l.symbol.replace(/\s+/g, "");
  const m = /(\d{2})(\d{2})(\d{2})([CP])(\d{8})$/.exec(s);
  if (m === null) return l.symbol;
  return `20${m[1]}-${m[2]}-${m[3]} ${Number(m[5]) / 1000}${m[4]}`;
}

function csvCell(v: string | number | null): string {
  const s = v === null ? "" : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** The lots as CSV, as manage wrote them: one row per closed span or dividend. */
export function ledgerCsv(lots: ManageLedgerLot[]): string {
  const head = ["closed", "opened", "underlying", "leg", "how", "net", "fees"];
  const rows = lots.map((l) => [l.closed, l.opened, l.underlying, lotLeg(l), l.how, l.net.toFixed(2), l.fees.toFixed(2)]);
  return [head, ...rows].map((r) => r.map(csvCell).join(",")).join("\n") + "\n";
}

function download(name: string, text: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

function Tile({ label, v, sub, big = false }: { label: string; v: number; sub?: string; big?: boolean }) {
  return (
    <div className={big ? "ldg-tile is-big" : "ldg-tile"}>
      <span className="ldg-tile-label">{label}</span>
      <span className={`ldg-tile-value ${tone(v)}`}>{fmtCash(v)}</span>
      {sub ? <span className="ldg-tile-sub">{sub}</span> : null}
    </div>
  );
}

/** Each month's net as a bar above or below zero; the sources are in its title. */
function MonthBars({ months }: { months: ManageLedger["months"] }) {
  if (months.length === 0) return null;
  const W = 720;
  const H = 180;
  const pad = 18;
  const top = Math.max(0, ...months.map((m) => m.net));
  const bot = Math.min(0, ...months.map((m) => m.net));
  const span = top - bot || 1;
  const y = (v: number) => pad + ((top - v) / span) * (H - 2 * pad);
  const slot = W / months.length;
  const bw = Math.min(56, slot * 0.6);
  return (
    <svg className="ldg-months" viewBox={`0 0 ${W} ${H + 18}`} role="img" aria-label="Net by month">
      <line x1={0} x2={W} y1={y(0)} y2={y(0)} className="ldg-zero" />
      {months.map((m, i) => {
        const x = i * slot + (slot - bw) / 2;
        const y0 = y(Math.max(0, m.net));
        const h = Math.max(1, Math.abs(y(m.net) - y(0)));
        return (
          <g key={m.month}>
            <title>
              {`${m.month}: net ${fmtCash(m.net)} · puts ${fmtCash(m.puts)} · calls ${fmtCash(m.calls)} · shares ${fmtCash(m.shares)} · dividends ${fmtCash(m.dividends)} · fees ${fmtCash(m.fees)}`}
            </title>
            <rect x={x} y={y0} width={bw} height={h} rx={3} className={m.net < 0 ? "ldg-bar is-neg" : "ldg-bar"} />
            <text x={x + bw / 2} y={m.net < 0 ? y(m.net) + 12 : y0 - 4} textAnchor="middle" className="ldg-bar-label">
              {m.net === 0 ? "" : fmtCash(Math.round(m.net))}
            </text>
            <text x={x + bw / 2} y={H + 14} textAnchor="middle" className="ldg-axis">
              {m.month.slice(2)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

type NameTab = "closed" | "held";

function NameDialog({
  ledger,
  names,
  index,
  win,
  onStep,
  onClose,
}: {
  ledger: ManageLedger;
  names: string[];
  index: number;
  win: ManageLedgerWindow;
  onStep: (to: number) => void;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<NameTab>("closed");
  const name = names[index]!;
  const b = ledger.names.find((n) => n.underlying === name)?.windows[win];
  const lots = ledger.lots.filter((l) => l.underlying === name);
  const held = ledger.open.filter((o) => o.underlying === name);
  const tabs: LightboxTab<NameTab>[] = [
    { id: "closed", label: "closed", badge: String(lots.length) },
    { id: "held", label: "still held", badge: held.length ? String(held.length) : null },
  ];
  return (
    <CardLightbox count={names.length} index={index} onStep={onStep} onClose={onClose}>
      {(nav) => (
        <LightboxFrame
          kicker={`ledger · since ${ledger.historyFrom ?? "?"}`}
          title={name}
          chips={
            b ? (
              <>
                <span className={`chip ${tone(b.net)}`}>
                  {win} net {fmtCash(b.net)}
                </span>
                <span className="chip">
                  puts {fmtCash(b.puts)} · calls {fmtCash(b.calls)} · shares {fmtCash(b.shares)} · dividends {fmtCash(b.dividends)}
                </span>
              </>
            ) : null
          }
          tabs={tabs}
          tab={tab}
          onTab={setTab}
          position={nav.position}
          onPrev={nav.prev}
          onNext={nav.next}
          onClose={onClose}
        >
          {tab === "closed" ? (
            <>
              <div className="ldg-actions">
                <button type="button" className="study-step-btn" onClick={() => download(`ledger-${name}.csv`, ledgerCsv(lots))}>
                  Download CSV
                </button>
              </div>
              <div className="table-scroll">
                <table className="ldg-table">
                  <thead>
                    <tr>
                      <th>closed</th>
                      <th>opened</th>
                      <th>leg</th>
                      <th>how</th>
                      <th className="num">net</th>
                      <th className="num">fees</th>
                    </tr>
                  </thead>
                  <tbody>
                    {lots.map((l, i) => (
                      <tr key={`${l.symbol}-${l.closed}-${i}`}>
                        <td>{l.closed}</td>
                        <td>{l.opened ?? "—"}</td>
                        <td>{lotLeg(l)}</td>
                        <td>{l.how}</td>
                        <td className={`num ${tone(l.net)}`}>{fmtCash(l.net)}</td>
                        <td className="num">{fmtCash(l.fees)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : held.length === 0 ? (
            <p className="muted">nothing held on {name}</p>
          ) : (
            <>
              <p className="muted">the cash each open leg has taken in or paid so far; none of it counts as kept until it closes</p>
              <ul className="ldg-held">
                {held.map((o) => (
                  <li key={o.symbol}>
                    {lotLeg(o)} · {o.quantity ?? "?"} since {o.opened ?? "?"} · <span className={tone(o.net)}>{fmtCash(o.net)}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </LightboxFrame>
      )}
    </CardLightbox>
  );
}

/** What the ledger's names can be ordered by: size, count, then the alphabet. Never a rate or a yield. */
export interface LedgerRow {
  name: string;
  b: ManageLedgerBucket;
}

export const LEDGER_SORTS: SortOption<LedgerRow>[] = [
  { id: "net", label: "net", hint: "best first", key: (r) => -r.b.net },
  { id: "legs", label: "legs closed", hint: "most first", key: (r) => -r.b.legs },
  { id: "name", label: "name", hint: "A to Z", key: (r) => r.name },
];

const WINDOW_WORDS: Record<ManageLedgerWindow, string> = {
  "1M": "1 month",
  "3M": "3 months",
  "6M": "6 months",
  "1Y": "1 year",
  YTD: "year to date",
  ALL: "since the history starts",
};

/** The ledger's explanation, behind its ?: where the figures come from and what was left out. */
export function ledgerHelp(ledger: Pick<ManageLedger, "scope" | "historyFrom" | "rows">): CardHelpContent {
  return {
    title: "Reading the ledger",
    body: (
      <>
        <HelpSection title="By closing day">
          <p>
            Read from the account's history rows: puts, calls, shares and dividends, after fees, counted when each leg went flat.
          </p>
          <p>{ledgerHeadWords(ledger)}</p>
        </HelpSection>
        <HelpSection title="Left out">
          <p>0DTE legs (the live loops' and the hand's own books, on their pages), and spans the history entered mid-position.</p>
        </HelpSection>
        <GuideLink to="/help/live#wheel" />
      </>
    ),
  };
}

/** The ledger's head line. The row count and its start date are the account's whole history (manage's
 *  `ledger.rows` and `history_from` are read before the scope is applied), so they are said as the
 *  account's, never placed after the names as if they were theirs. */
export function ledgerHeadWords(ledger: Pick<ManageLedger, "scope" | "historyFrom" | "rows">): string {
  const names = ledger.scope === null ? "" : `${ledger.scope.length ? ledger.scope.join(", ") : "no wheel names"} · `;
  return `${names}read from the account's ${ledger.rows ?? "?"} history rows since ${ledger.historyFrom ?? "?"} · by closing day`;
}

/** The ledger: tiles for the window, the months, a button a name. The window, the order of the names
 *  and the CSV sit behind filter and sort; a name's button opens its spans in a lightbox. */
export function LedgerDesk({ ledger }: { ledger: ManageLedger | null }) {
  const [params, setParams] = useSearchParams();
  const lightbox = useOpenParam("ledger");
  const [sort, setSort] = useSortChoice("ledger", "net");
  const [panel, setPanel] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  if (ledger === null) return null;
  if (ledger.error) return <Card title="ledger"><p className="muted">ledger: {ledger.error}</p></Card>;
  const win = ledgerWindow(params.get(WINDOW_PARAM));
  const pick = (w: ManageLedgerWindow) => {
    const next = new URLSearchParams(params);
    if (w === DEFAULT_WINDOW) next.delete(WINDOW_PARAM);
    else next.set(WINDOW_PARAM, w);
    setParams(next, { replace: true });
  };
  const all = ledger.windows[win];
  const active = ledger.names.filter((n) => (n.windows[win]?.legs ?? 0) > 0 || (n.windows[win]?.dividends ?? 0) !== 0);
  const rows: LedgerRow[] = active.map((n) => ({ name: n.underlying, b: n.windows[win]! }));
  const option = LEDGER_SORTS.find((s) => s.id === sort.id) ?? LEDGER_SORTS[0];
  const shown = sortRows(rows, option, sort.reversed);
  const order = shown.map((r) => r.name);
  const first = ledger.historyFrom?.slice(0, 7) ?? "";
  const months = ledger.months.filter((m) => m.month >= first);
  const z = ledger.excluded.zeroDte;
  const index = lightbox.open === null ? -1 : order.indexOf(lightbox.open);
  const changed = (win !== DEFAULT_WINDOW ? 1 : 0) + (sort.id !== "net" || sort.reversed ? 1 : 0);
  const reset = () => {
    pick(DEFAULT_WINDOW);
    setSort({ id: "net", reversed: false });
  };
  return (
    <Card
      title="ledger"
      collapseKey="wheel-ledger"
      help={ledgerHelp(ledger)}
      controls={<FilterSortButton changed={changed} open={panel} onOpen={() => setPanel(!panel)} anchor={anchor} />}
    >
      <FilterSummary
        parts={[WINDOW_WORDS[win], `by ${option?.label ?? sort.id}${sort.reversed ? ", reversed" : ""}`]}
        note={`counted by closing day · history from ${ledger.historyFrom ?? "?"}`}
        changed={changed}
        onOpen={() => setPanel(true)}
        onReset={reset}
      />
      {all ? (
        <div className="ldg-tiles">
          <Tile big label={`net · ${win}`} v={all.net} sub={`${all.legs} closed · after ${fmtCash(all.fees)} in fees`} />
          <Tile label="puts" v={all.puts} />
          <Tile label="calls" v={all.calls} />
          <Tile label="shares" v={all.shares} />
          <Tile label="dividends" v={all.dividends} />
        </div>
      ) : null}
      <MonthBars months={months} />
      {z.legs > 0 || ledger.excluded.unmatched.legs > 0 ? (
        <p className="muted ldg-note">
          {z.legs > 0
            ? `left out: ${z.legs} 0DTE legs on ${z.underlyings.join(", ")}, net ${fmtCash(z.net)} (the live loops' and the hand's own books, on their pages)`
            : ""}
          {ledger.excluded.unmatched.legs > 0
            ? `${z.legs > 0 ? " · " : ""}${ledger.excluded.unmatched.legs} spans the history entered mid-position, net ${fmtCash(ledger.excluded.unmatched.net)}`
            : ""}
        </p>
      ) : null}
      {shown.length === 0 ? (
        <p className="muted">nothing closed in {win}</p>
      ) : (
        <div className="pf-mores pf-push">
          {shown.map((r) => (
            <button key={r.name} type="button" className="pf-more" onClick={() => lightbox.show(r.name)} aria-label={`Open ${r.name}'s ledger`}>
              {r.name} <span className={`pf-more-k ${tone(r.b.net)}`}>{fmtCash(r.b.net)}</span> <span aria-hidden="true">›</span>
            </button>
          ))}
        </div>
      )}
      <FilterSortPanel open={panel} onClose={() => setPanel(false)} onReset={reset} anchor={anchor}>
        <FilterGroup title="window" note="by the day each leg went flat">
          <div className="ldg-windows" role="group" aria-label="Window">
            {LEDGER_WINDOWS.map((w) => (
              <button key={w} type="button" className={w === win ? "ldg-win is-on" : "ldg-win"} aria-pressed={w === win} onClick={() => pick(w)}>
                {w}
              </button>
            ))}
          </div>
        </FilterGroup>
        <SortSection sorts={LEDGER_SORTS} choice={sort} fallback="net" onChange={setSort} />
        <FilterGroup title="export">
          <button type="button" className="study-step-btn" onClick={() => download("ledger.csv", ledgerCsv(ledger.lots))}>
            Download CSV
          </button>
          <span className="muted sm"> every closed leg</span>
        </FilterGroup>
      </FilterSortPanel>
      {index >= 0 ? (
        <NameDialog ledger={ledger} names={order} index={index} win={win} onStep={(to) => lightbox.step(order[to] ?? "")} onClose={lightbox.close} />
      ) : null}
    </Card>
  );
}
