import type { TechnicalsMover, TechnicalsReport, TechnicalsStageMember } from "@console/shared";
import { Link } from "react-router-dom";
import { BarChart, SignedBar } from "../../components/Charts";

/**
 * The technicals report on the Morning tab: breadth, stages by sector, rotation, the relative-
 * strength leaders and the scan-rule signals — every one `packages/technicals`' own answer, read
 * from `data/technicals/report-<session>.json` and displayed in the order the report wrote it.
 *
 * Record-only, like the vol structure and the deployment score: nothing here gates, sizes or
 * suggests an order, so no reading borrows the phase banner's ok/warn colours. The one colour
 * carried is the sign of a count (more leaders than laggards), which is a fact, not a verdict.
 *
 * The report is the last one dated BEFORE the pack's session — the close the morning actually saw —
 * and each card says which session it is, because on a Monday that is the Friday before.
 */

const STAGE_ORDER = ["confirmed", "building", "early"];

/** "confirmed: MSFT, TEL · building: ADI" — members grouped by how far along their stage is. */
function byStage(members: TechnicalsStageMember[]): string {
  if (members.length === 0) return "—";
  const groups = new Map<string, string[]>();
  for (const m of members) {
    const key = m.stage ?? "unstaged";
    groups.set(key, [...(groups.get(key) ?? []), m.symbol]);
  }
  const keys = [...STAGE_ORDER.filter((k) => groups.has(k)), ...[...groups.keys()].filter((k) => !STAGE_ORDER.includes(k))];
  return keys.map((k) => `${k}: ${groups.get(k)!.join(", ")}`).join(" · ");
}

/** The vendor's scan-rule names, spelled for a reader. Unfamiliar rules pass through as named. */
const SIGNAL_LABEL: Record<string, string> = {
  BullishTrendFollowing: "Bullish trend following",
  BearishTrendFollowing: "Bearish trend following",
  BullishCounterTrend: "Bullish counter-trend",
  BearishCounterTrend: "Bearish counter-trend",
  CciDipInBullishTrend: "CCI dip in a bullish trend",
  CciRallyInBearishTrend: "CCI rally in a bearish trend",
};

const ROTATION_ORDER = ["leading", "improving", "weakening", "lagging"];

function pct(v: number | null, digits = 1): string {
  return v === null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

function Head({ title, t, children }: { title: string; t: TechnicalsReport; children?: React.ReactNode }) {
  return (
    <div className="card-head">
      <h2>{title}</h2>
      <span className="chip">record-only</span>
      {children}
      <span className="card-asof">close of {t.session}</span>
    </div>
  );
}

function BreadthCard({ t }: { t: TechnicalsReport }) {
  const last = t.breadth[t.breadth.length - 1];
  const bars = t.breadth.filter((b) => b.net !== null).map((b) => ({ x: b.session, y: b.net as number }));
  return (
    <section className="card">
      <Head title="Breadth" t={t}>
        {t.universe !== null && <span className="card-asof">{t.universe} stocks staged</span>}
      </Head>
      {last === undefined ? (
        <p className="muted">No breadth history in this report.</p>
      ) : (
        <>
          <div className="stats-grid">
            <div className="stat-tile">
              <span className="stat-label">leaders</span>
              <span className="stat-value">{last.leaders ?? "—"}</span>
            </div>
            <div className="stat-tile">
              <span className="stat-label">laggards</span>
              <span className="stat-value">{last.laggards ?? "—"}</span>
            </div>
            <div className="stat-tile">
              <span className="stat-label">net</span>
              <span className={`stat-value ${last.net === null || last.net === 0 ? "" : last.net > 0 ? "pnl-pos" : "pnl-neg"}`}>
                {last.net === null ? "—" : `${last.net > 0 ? "+" : ""}${last.net}`}
              </span>
            </div>
            <div className="stat-tile">
              <span className="stat-label">bullish share</span>
              <span className="stat-value">
                {last.bullishShare === null ? "—" : `${(last.bullishShare * 100).toFixed(0)}%`}
              </span>
              <span className="stat-label muted">of staged names</span>
            </div>
          </div>
          <BarChart bars={bars} height={140} yFormat={(v) => `${v > 0 ? "+" : ""}${v.toFixed(0)}`} />
          <p className="muted">
            Net leaders over the last {t.breadth.length} sessions: stocks staged as leaders against the S&amp;P 500
            minus those staged as laggards. A stock in neither is not counted.
          </p>
        </>
      )}
    </section>
  );
}

function StagesCard({ t }: { t: TechnicalsReport }) {
  const maxAbs = Math.max(1, ...t.stages.map((s) => Math.abs(s.net ?? 0)));
  return (
    <section className="card">
      <Head title="Stages by sector" t={t} />
      {t.stages.length === 0 ? (
        <p className="muted">No stages in this report.</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Sector</th>
              <th>Net</th>
              <th />
              <th>Leaders</th>
              <th>Laggards</th>
            </tr>
          </thead>
          <tbody>
            {t.stages.map((s) => (
              <tr key={s.sector}>
                <td>{s.sector}</td>
                <td className={s.net === null || s.net === 0 ? "" : s.net > 0 ? "pnl-pos" : "pnl-neg"}>
                  {s.net === null ? "—" : `${s.net > 0 ? "+" : ""}${s.net}`}
                </td>
                <td>{s.net !== null && <SignedBar value={s.net} maxAbs={maxAbs} compact />}</td>
                <td className="tech-members">{byStage(s.leaders)}</td>
                <td className="tech-members muted">{byStage(s.laggards)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function RotationCard({ t }: { t: TechnicalsReport }) {
  const none = t.rotation["none"] ?? [];
  return (
    <section className="card">
      <Head title="Rotation" t={t}>
        {t.rules["rotation"] !== undefined && <span className="card-asof">rule {t.rules["rotation"]}</span>}
      </Head>
      <div className="stats-grid">
        {ROTATION_ORDER.map((state) => {
          const funds = t.rotation[state] ?? [];
          return (
            <div key={state} className="stat-tile">
              <span className="stat-label">{state}</span>
              <span className={`tech-funds ${funds.length === 0 ? "muted" : ""}`}>
                {funds.length === 0 ? "—" : funds.join(" ")}
              </span>
            </div>
          );
        })}
      </div>
      <p className="muted">
        Sector and industry funds against SPY, asset-class funds against AOR; fast and slow relative-strength
        windows.
        {none.length > 0 && ` Not yet classified (a window short, or inside the margin): ${none.join(", ")}.`}
      </p>
    </section>
  );
}

function LeadersCard({ t }: { t: TechnicalsReport }) {
  return (
    <section className="card">
      <Head title="Relative-strength leaders" t={t} />
      {t.leaders.length === 0 ? (
        <p className="muted">No leaders in this report.</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Sector</th>
              <th>6-month return</th>
              <th>Rank</th>
              <th>Trend short / long</th>
              <th>Stage</th>
            </tr>
          </thead>
          <tbody>
            {t.leaders.map((l) => (
              <tr key={l.symbol}>
                <td>
                  <Link to={`/reports/chart?symbol=${encodeURIComponent(l.symbol)}`}>{l.symbol}</Link>
                </td>
                <td className="muted">{l.sector ?? "—"}</td>
                <td className={l.return6mPct === null ? "muted" : l.return6mPct >= 0 ? "pnl-pos" : "pnl-neg"}>
                  {pct(l.return6mPct)}
                </td>
                <td>{l.rank ?? "—"}</td>
                <td>
                  {l.trendShort ?? "—"} / {l.trendLong ?? "—"}
                </td>
                <td className={l.stage === null ? "muted" : ""}>{l.stage?.replace("/", " · ") ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="muted">Ranked by 125-session return; rank is that return's decile across the universe (10 = top).</p>
    </section>
  );
}

function MoverRows({ rows }: { rows: TechnicalsMover[] }) {
  return (
    <>
      {rows.map((m) => (
        <tr key={m.symbol}>
          <td>
            <Link to={`/reports/chart?symbol=${encodeURIComponent(m.symbol)}`}>{m.symbol}</Link>
          </td>
          <td className="muted">{m.sector ?? "—"}</td>
          <td className={m.changePct === null ? "muted" : m.changePct >= 0 ? "pnl-pos" : "pnl-neg"}>
            {m.changePct === null ? "—" : `${m.changePct > 0 ? "+" : ""}${m.changePct.toFixed(2)}%`}
          </td>
          <td className={m.volumeRatio === null ? "muted" : ""}>
            {m.volumeRatio === null ? "—" : `${m.volumeRatio.toFixed(2)}×`}
          </td>
          <td className={m.stage === null ? "muted" : ""}>{m.stage?.replace("/", " · ") ?? "—"}</td>
        </tr>
      ))}
    </>
  );
}

function MoversCard({ t }: { t: TechnicalsReport }) {
  const { gainers, losers } = t.movers;
  if (gainers.length + losers.length === 0) return null; // a version-1 report carries none
  return (
    <section className="card">
      <Head title="Session movers" t={t} />
      <table className="data-table">
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Sector</th>
            <th>Change</th>
            <th>Volume vs 50-day</th>
            <th>Stage</th>
          </tr>
        </thead>
        <tbody>
          <MoverRows rows={gainers} />
          <MoverRows rows={losers} />
        </tbody>
      </table>
      <p className="muted">The largest one-session moves in the universe. Why they moved is the narrative's to say.</p>
    </section>
  );
}

function SignalsCard({ t }: { t: TechnicalsReport }) {
  const rules = Object.entries(t.signals);
  return (
    <section className="card">
      <Head title="Scan signals" t={t} />
      {rules.length === 0 ? (
        <p className="muted">No scan rules in this report.</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Rule</th>
              <th>Count</th>
              <th>Symbols</th>
            </tr>
          </thead>
          <tbody>
            {rules.map(([rule, syms]) => (
              <tr key={rule}>
                <td>{SIGNAL_LABEL[rule] ?? rule}</td>
                <td>{syms.length}</td>
                <td className={`tech-members ${syms.length === 0 ? "muted" : ""}`}>
                  {syms.length === 0 ? "—" : syms.join(", ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="muted">A match is a pattern on the chart, not a trade idea.</p>
    </section>
  );
}

export function TechnicalsCards({ t }: { t: TechnicalsReport | null | undefined }) {
  // Absent, not just null: a server older than this bundle omits the field entirely.
  if (!t) {
    return <p className="muted">No technicals report before this session.</p>;
  }
  return (
    <>
      <MoversCard t={t} />
      <BreadthCard t={t} />
      <StagesCard t={t} />
      <div className="cards cards-wide">
        <RotationCard t={t} />
        <LeadersCard t={t} />
      </div>
      <SignalsCard t={t} />
    </>
  );
}
