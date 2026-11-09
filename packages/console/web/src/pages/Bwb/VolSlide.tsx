import { useState } from "react";
import type { BwbEntryContextRow, BwbIvStats, BwbRegimeSplitCell, BwbVolEvidence } from "@console/shared";
import { useQuery } from "@tanstack/react-query";
import { Card, PnlCell, SortTh, sortRows, useSort } from "../../components/DataTable";
import { HelpSection, type CardHelpContent } from "../../components/CardHelp";
import { Section } from "../../components/Section";
import { fmtMoney, fmtNum } from "../../lib/format";
import { useOpenParam } from "../Backtest/CardLightbox";
import { BoxOpen, FliesBoxHost, type FliesBox } from "../Flies/FliesBoxes";

/**
 * bwb's entry vol evidence, every number the module's (`/api/bwb/vol`, bridged):
 *
 * - **regime split** (`bwb regime-split`): each arm's closed results cut by VIX9D/VIX at entry. A
 *   hypothesis from a modelled backtest that chose the best of six gates, measured here and not
 *   traded on paper (honesty rule 12); a row with no reading is `unmeasured`, never given a side.
 * - **implied vs realised** (`bwb iv-premium`): implied at entry against realised to expiry, one
 *   window per (entry session, expiration), arms counted once; a path with a hole is refused.
 * - **entry context**: every position's VIX9D/VIX/VIX3M prints with their ages, the reason a print
 *   was not taken, the ratios (derived on read by the module) and the expiration's implied vol --
 *   `entry_iv_complete = 0` is a lower bound, because the cache's strike window cut a wing.
 *
 * Laid out 2026-11-07 in the portfolio tab's sections: the gate, the premium, and the entry context,
 * whose per-position table is a lightbox from its card (`?bv=context`). The method of each sits
 * behind the card's ?.
 */

const SIDES = ["open", "shut", "unmeasured"] as const;
const PAGE = 40;

const SPLIT_HELP: CardHelpContent = {
  title: "Regime split",
  body: (
    <>
      <HelpSection title="The cut">
        <p>Each arm&apos;s closed positions cut by VIX9D/VIX at entry: net · positions · net per trade.</p>
      </HelpSection>
      <HelpSection title="A hypothesis">
        <p>
          The gate is a hypothesis from a modelled backtest that picked the best of six; paper measures it and never trades on it. A row
          recorded before 2026-10-09, or with a missing or stale print, is <em>unmeasured</em> and never given a side.
        </p>
      </HelpSection>
    </>
  ),
};

const PREMIUM_HELP: CardHelpContent = {
  title: "Implied vs realised",
  body: (
    <>
      <HelpSection title="Units">
        <p>Vol points, annualised; premium = implied − realised.</p>
      </HelpSection>
      <HelpSection title="Lower bound">
        <p>A cut-off strip is a lower bound on implied, so the complete-only row is stated beside it. Implied is recorded from 2026-10-09.</p>
      </HelpSection>
    </>
  ),
};

const CONTEXT_HELP: CardHelpContent = {
  title: "Entry context",
  body: (
    <>
      <HelpSection title="What it holds">
        <p>
          Every position&apos;s VIX9D, VIX and VIX3M prints with their ages, the reason a print was not taken, the ratios, and the
          expiration&apos;s implied vol at entry.
        </p>
      </HelpSection>
      <HelpSection title="Lower bound">
        <p>A ≥ on implied vol marks a lower bound: the cache&apos;s strike window cut a wing off the strip.</p>
      </HelpSection>
    </>
  ),
};

function SplitCell({ c }: { c: BwbRegimeSplitCell | undefined }) {
  if (c === undefined) return <td className="muted">—</td>;
  return (
    <td title={`${c.first_session} → ${c.last_session} · ${String(c.fired)} add-on fire${c.fired === 1 ? "" : "s"}`}>
      <PnlCell v={c.net_pnl} />
      <span className="muted"> · {String(c.positions)} · {fmtMoney(c.net_per_trade)}/trade</span>
    </td>
  );
}

function ivStats(label: string, s: BwbIvStats | undefined) {
  if (s === undefined || s.windows === 0) {
    return (
      <tr>
        <td>{label}</td>
        <td colSpan={6} className="muted">no window with an implied reading yet</td>
      </tr>
    );
  }
  return (
    <tr>
      <td>{label}</td>
      <td>{String(s.windows)}</td>
      <td>{fmtNum(s.mean_implied_vol ?? null)}</td>
      <td>{fmtNum(s.mean_realised_vol ?? null)}</td>
      <td>{fmtNum(s.mean_premium_vol ?? null)}</td>
      <td>{fmtNum(s.median_premium_vol ?? null)}</td>
      <td>{`${String(s.implied_above_realised ?? 0)} of ${String(s.windows)}`}</td>
    </tr>
  );
}

function print(v: number | null, age: number | null): string {
  if (v === null) return "—";
  return age === null ? fmtNum(v) : `${fmtNum(v)} (${age.toFixed(0)}s)`;
}

function ivLabel(r: BwbEntryContextRow): string {
  if (r.entry_iv_vol === null) return r.entry_iv_reason ?? "—";
  return r.entry_iv_complete === 0 ? `≥ ${fmtNum(r.entry_iv_vol)}` : fmtNum(r.entry_iv_vol);
}

/** The per-position entry-context table, sortable, forty rows at a time. Its own component so the sort and the page live in the box. */
export function EntryContextTable({ positions }: { positions: BwbEntryContextRow[] }) {
  const [limit, setLimit] = useState(PAGE);
  const sort = useSort();
  // Sorted before the "show more" slice, so the first page of a sort is the top of every position.
  // A print sorts by its value, not its age; the reason is prose and stays unsorted.
  const rows = sortRows(positions, sort, {
    session: (r) => r.entry_session,
    arm: (r) => r.arm,
    status: (r) => r.status,
    vix9d: (r) => r.entry_vix9d,
    vix: (r) => r.entry_vix,
    vix3m: (r) => r.entry_vix3m,
    ratio9d: (r) => r.vix9d_vix,
    ratio3m: (r) => r.vix_vix3m,
    gate: (r) => r.gate_state,
    iv: (r) => r.entry_iv_vol,
    net: (r) => r.net_pnl,
  });
  return (
    <>
      <div className="table-scroll">
        <table className="data-table num-from-4">
          <thead>
            <tr>
              <SortTh label="session" k="session" sort={sort} />
              <SortTh label="arm" k="arm" sort={sort} />
              <SortTh label="status" k="status" sort={sort} />
              <SortTh label="VIX9D" k="vix9d" sort={sort} title="the print and its age at entry" />
              <SortTh label="VIX" k="vix" sort={sort} />
              <SortTh label="VIX3M" k="vix3m" sort={sort} />
              <SortTh label="9D / VIX" k="ratio9d" sort={sort} />
              <SortTh label="VIX / 3M" k="ratio3m" sort={sort} />
              <SortTh label="gate" k="gate" sort={sort} title="open / shut / unmeasured — the module's gate_state at VIX9D/VIX < 1" />
              <SortTh label="implied vol" k="iv" sort={sort} title="the expiration's model-free implied vol at entry; ≥ marks a lower bound (strip cut off)" />
              <th title="why a print was not taken">reason</th>
              <SortTh label="net" k="net" sort={sort} />
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, limit).map((r) => (
              <tr key={r.position_id}>
                <td>{r.entry_session}</td>
                <td className="muted">{r.arm}</td>
                <td className="muted">{r.status}</td>
                <td>{print(r.entry_vix9d, r.entry_vix9d_age)}</td>
                <td>{print(r.entry_vix, r.entry_vix_age)}</td>
                <td>{print(r.entry_vix3m, r.entry_vix3m_age)}</td>
                <td>{fmtNum(r.vix9d_vix, 3)}</td>
                <td>{fmtNum(r.vix_vix3m, 3)}</td>
                <td className={r.gate_state === "unmeasured" ? "muted" : ""}>{r.gate_state}</td>
                <td title={r.entry_iv_complete === 0 ? "lower bound: the cache's strike window cut a wing" : undefined}>{ivLabel(r)}</td>
                <td className="muted" style={{ fontSize: 11 }}>{r.entry_regime_reason ?? "—"}</td>
                <td>{r.net_pnl === null ? <span className="muted">—</span> : <PnlCell v={r.net_pnl} />}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > limit && (
        <button type="button" className="btn btn-quiet" onClick={() => setLimit(limit + PAGE)}>
          {`show ${String(Math.min(PAGE, rows.length - limit))} more of ${String(rows.length - limit)}`}
        </button>
      )}
    </>
  );
}

export function BwbVolSlide() {
  const { data, isLoading, dataUpdatedAt } = useQuery<BwbVolEvidence>({
    queryKey: ["bridged", "bwb-vol"],
    queryFn: async () => {
      const res = await fetch("/api/bwb/vol");
      if (!res.ok) throw new Error(`bwb vol: HTTP ${String(res.status)}`);
      return (await res.json()) as BwbVolEvidence;
    },
    refetchInterval: 120_000,
  });
  const lb = useOpenParam("bv");

  if (isLoading || data === undefined) {
    return (
      <div className="pf-layout">
        <Section concept="edge" tag="entry vol" question="What did the vol look like at entry?">
          <Card title="entry vol">
            <span className="skeleton skeleton-text" style={{ width: "50%" }} />
          </Card>
        </Section>
      </div>
    );
  }

  const split = data.regimeSplit;
  const iv = data.ivPremium;
  const ctx = data.entryContext;
  const positions = ctx.data?.positions ?? [];
  const byGate = (g: string) => positions.filter((r) => r.gate_state === g).length;
  const boxes: FliesBox[] = [
    {
      id: "context",
      kicker: "entry context ›",
      title: "every position's entry reading",
      tabs: [{ id: "rows", label: "positions" }],
      body: () => <EntryContextTable positions={positions} />,
    },
  ];
  return (
    <div className="pf-layout">
      <Section concept="edge" tag="the gate" question="Does the term-structure gate separate results?">
        <Card
          title={`regime split${split.data?.gate !== undefined ? ` — ${split.data.gate}` : ""}`}
          collapseKey="bwb-vol-split"
          isError={!split.ok}
          updatedAt={dataUpdatedAt}
          help={SPLIT_HELP}
        >
          {!split.ok || split.data === null ? (
            <p className="muted">{split.error ?? "regime-split unavailable"}</p>
          ) : (
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>arm</th>
                    <th title="VIX9D/VIX below the bar at entry">open</th>
                    <th title="VIX9D/VIX at or above the bar">shut</th>
                    <th title="no usable reading">unmeasured</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(split.data.arms).map(([arm, sides]) => (
                    <tr key={arm}>
                      <td>{arm}</td>
                      {SIDES.map((side) => (
                        <SplitCell key={side} c={sides[side]} />
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </Section>
      <Section concept="edge" tag="the premium" question="Is implied vol above what was realised?">
        <Card title="implied at entry vs realised to expiry" collapseKey="bwb-vol-iv" isError={!iv.ok} updatedAt={dataUpdatedAt} help={PREMIUM_HELP}>
          {!iv.ok || iv.data === null ? (
            <p className="muted">{iv.error ?? "iv-premium unavailable"}</p>
          ) : iv.data.summary === undefined ? (
            <p className="muted">No bwb paper ledger on this machine.</p>
          ) : (
            <>
              <p className="muted" style={{ fontSize: 12 }}>
                {String(iv.data.summary.settled_windows)} settled windows, realised measured on {String(iv.data.summary.realised_measured)}
                {Object.keys(iv.data.summary.refused).length > 0 &&
                  ` (refused: ${Object.entries(iv.data.summary.refused).map(([k, n]) => `${k} ${String(n)}`).join(", ")})`}
                .
              </p>
              <div className="table-scroll">
                <table className="data-table num-from-1">
                  <thead>
                    <tr>
                      <th />
                      <th>windows</th>
                      <th>implied</th>
                      <th>realised</th>
                      <th>mean premium</th>
                      <th>median premium</th>
                      <th>implied &gt; realised</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ivStats("with implied", iv.data.summary.with_implied)}
                    {ivStats("complete strip only", iv.data.summary.with_complete_implied)}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </Card>
      </Section>
      <Section concept="hist" tag="entry context" question="What did each entry see?">
        <Card title="entry context per position" collapseKey="bwb-vol-context" isError={!ctx.ok} updatedAt={dataUpdatedAt} help={CONTEXT_HELP}>
          {!ctx.ok || ctx.data === null ? (
            <p className="muted">{ctx.error ?? "entry-context unavailable"}</p>
          ) : positions.length === 0 ? (
            <p className="muted">No positions carry an entry reading (recorded from 2026-10-09).</p>
          ) : (
            <>
              <p style={{ fontSize: 13 }}>
                {String(positions.length)} position{positions.length === 1 ? "" : "s"} · gate open {String(byGate("open"))} · shut{" "}
                {String(byGate("shut"))} · unmeasured {String(byGate("unmeasured"))}
              </p>
              <div className="pf-mores pf-push">
                <BoxOpen lb={lb} id="context">
                  every position
                </BoxOpen>
              </div>
            </>
          )}
        </Card>
      </Section>
      <FliesBoxHost lb={lb} boxes={boxes} />
    </div>
  );
}
