import { useQuery } from "@tanstack/react-query";
import type { TradingMode } from "@console/shared";
import { powerNote } from "../../lib/power";
import { Card, DataCard, PnlCell } from "../../components/DataTable";
import { HelpSection, type CardHelpContent } from "../../components/CardHelp";
import { Section } from "../../components/Section";
import { CalendarHeatmap } from "../../components/CalendarHeatmap";

interface BreakdownRow {
  bucket: string;
  trades: number;
  sessions: number;
  winPct: number | null;
  avgNet: number | null;
}

interface DeepAnalytics {
  calendar: Array<{ date: string; net: number; trades: number }>;
  nlv: Array<{ date: string; nlv: number }>;
  byDelta: BreakdownRow[];
  byWing: BreakdownRow[];
  bySymbol: BreakdownRow[];
  byWeekday: BreakdownRow[];
  byHour: BreakdownRow[];
}

function useDeep(mode: TradingMode, symbol: string | null, profile: string | null, era: string | null) {
  const params = new URLSearchParams({ mode });
  if (symbol !== null) params.set("symbol", symbol);
  if (profile !== null) params.set("profile", profile);
  if (era !== null) params.set("era", era);
  return useQuery<DeepAnalytics>({
    queryKey: ["meic-deep", mode, symbol, profile, era],
    queryFn: async () => {
      const res = await fetch(`/api/meic/deep?${params.toString()}`);
      if (!res.ok) throw new Error(`meic deep: HTTP ${res.status}`);
      return (await res.json()) as DeepAnalytics;
    },
    refetchInterval: 60_000,
  });
}

function NlvChart({ points }: { points: Array<{ date: string; nlv: number }> }) {
  if (points.length < 2) return <p className="muted">no EOD summaries yet</p>;
  const width = 720;
  const height = 160;
  const m = { l: 8, r: 8, t: 8, b: 8 };
  const ys = points.map((p) => p.nlv);
  const y0 = Math.min(...ys);
  const y1 = Math.max(...ys);
  const rising = ys[ys.length - 1]! >= ys[0]!;
  const sx = (i: number) => m.l + (i / (points.length - 1)) * (width - m.l - m.r);
  const sy = (v: number) => m.t + ((y1 - v) / Math.max(y1 - y0, 1)) * (height - m.t - m.b);
  return (
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label="NLV over time" style={{ width: "100%", height: "auto", display: "block" }}>
      <polyline
        points={points.map((p, i) => `${sx(i).toFixed(1)},${sy(p.nlv).toFixed(1)}`).join(" ")}
        fill="none"
        stroke={rising ? "var(--pos)" : "var(--neg)"}
        strokeWidth={1.6}
      />
    </svg>
  );
}

function BreakdownCard({ title, rows, loading, className }: { title: string; rows: BreakdownRow[] | undefined; loading: boolean; className?: string }) {
  const note = rows !== undefined && !loading ? powerNote(rows) : null;
  if (note !== null) {
    return (
      <Card title={title} className={className}>
        <p className="muted" style={{ fontSize: 12 }}>
          {note}
        </p>
      </Card>
    );
  }
  return (
    <DataCard
      title={title}
      className={className}
      headers={["bucket", "trades", "sessions", "win %", "avg net"]}
      numFrom={1}
      loading={loading}
      rowCount={rows?.length ?? 0}
    >
      {rows?.map((r) => {
        // A trailing "*" marks a bucket outside the regular session — rendered
        // as a marker so one long label can't widen the whole column.
        const off = r.bucket.endsWith(" *");
        return (
        <tr key={r.bucket}>
          <td>
            {off ? r.bucket.slice(0, -2) : r.bucket}
            {off && (
              <span className="muted" title="outside 09:00–16:00 ET — paper replay or practice, not a session entry">
                {" *"}
              </span>
            )}
          </td>
          <td>{r.trades}</td>
          <td className="muted">{r.sessions}</td>
          <td>{r.winPct !== null ? `${r.winPct.toFixed(0)}%` : "—"}</td>
          <td>{r.avgNet !== null ? <PnlCell v={r.avgNet} /> : "—"}</td>
        </tr>
        );
      })}
    </DataCard>
  );
}

const NLV_HELP: CardHelpContent = {
  title: "Account value (NLV)",
  body: (
    <HelpSection title="Why it is unscoped">
      <p>
        closing_nlv is a broker balance for the whole account, and the daily summary carries no arm column to group it by, so the page's
        symbol, arm and era filters do not narrow this line. It is written only by the end-of-day report, so paper mode has none.
      </p>
    </HelpSection>
  ),
};

export function MeicDeepCards({
  mode,
  symbol = null,
  profile = null,
  era = null,
}: {
  mode: TradingMode;
  symbol?: string | null;
  profile?: string | null;
  era?: string | null;
}) {
  const { data, isLoading } = useDeep(mode, symbol, profile, era);
  return (
    <>
      <Section concept="hist" tag="calendar" question="Which days made money?">
        <Card title="Daily net P&L (after fees) — calendar" className="lv-12">
          {isLoading ? (
            <span className="skeleton skeleton-text" style={{ width: "40%" }} />
          ) : (
            <CalendarHeatmap days={(data?.calendar ?? []).map((d) => ({ date: d.date, net: d.net, count: d.trades }))} />
          )}
        </Card>
      </Section>

      {/* Paper mode has no NLV and cannot have one: `closing_nlv` is written only by
          `db.cmd_save_daily_summary`, reachable solely from the agent-driven /eod-report, and it is
          a BROKER fact rather than something derivable from ic_trades -- the session roll-up
          deliberately does not invent it. Rendering an empty chart in paper was reporting an
          absence as a flat line. */}
      {mode === "live" && (
        <Section concept="stand" tag="account" question="What is the account worth?">
          <Card title="Account value (NLV) over time" className="lv-12" help={NLV_HELP} flag="account-level · unscoped">
            {isLoading ? <span className="skeleton skeleton-text" style={{ width: "40%" }} /> : <NlvChart points={data?.nlv ?? []} />}
          </Card>
        </Section>
      )}

      <Section concept="edge" tag="breakdowns" question="Where does the net come from?">
        <BreakdownCard title="By short-call delta" rows={data?.byDelta} loading={isLoading} className="lv-4" />
        <BreakdownCard title="By wing width" rows={data?.byWing} loading={isLoading} className="lv-4" />
        <BreakdownCard title="By symbol" rows={data?.bySymbol} loading={isLoading} className="lv-4" />
        <BreakdownCard title="By weekday" rows={data?.byWeekday} loading={isLoading} className="lv-4" />
        <BreakdownCard title="By entry hour (ET) — * is off-session" rows={data?.byHour} loading={isLoading} className="lv-8" />
      </Section>
    </>
  );
}
