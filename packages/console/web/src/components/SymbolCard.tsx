import { useSymbolCard } from "../lib/api";

function money(v: number | null): string {
  return v === null ? "—" : `$${v.toFixed(2)}`;
}

function compact(v: number | null): string {
  if (v === null) return "—";
  const abs = Math.abs(v);
  if (abs >= 1e12) return `${(v / 1e12).toFixed(1)}T`;
  if (abs >= 1e9) return `${(v / 1e9).toFixed(0)}B`;
  if (abs >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return v.toFixed(0);
}

const LIQUIDITY_LABELS = ["poor", "low", "fair", "liquid", "very liquid"];

function fmtEarnings(iso: string | null): string {
  if (iso === null) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const days = Math.round((t - Date.now()) / 86_400_000);
  const date = new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
  return days >= 0 ? `${date} (${days}d)` : date;
}

function trendClass(v: string | null): string {
  if (v === null) return "";
  if (v.includes("bull")) return "pnl-pos";
  if (v.includes("bear")) return "pnl-neg";
  return "";
}

/** Compact per-symbol summary for the builder: quote + EOD stats + metrics. */
export function SymbolCard({ symbol }: { symbol: string }) {
  const { data, isError } = useSymbolCard(symbol);
  if (isError || data === undefined) return null;

  const price = data.last ?? data.eodClose;
  const tiles: Array<{ label: string; value: string; cls?: string }> = [
    {
      label: "last / chg",
      value: `${money(price)} ${data.eodChangePct !== null ? `${data.eodChangePct >= 0 ? "+" : ""}${data.eodChangePct.toFixed(2)}%` : ""}`,
      cls: data.eodChangePct !== null && data.eodChangePct < 0 ? "pnl-neg" : "pnl-pos",
    },
    {
      label: "52w range",
      value: data.yearLow !== null && data.yearHigh !== null ? `${money(data.yearLow)} – ${money(data.yearHigh)}` : "—",
    },
    { label: "iv rank", value: data.ivRank !== null ? `${data.ivRank.toFixed(0)}/100` : "—" },
    { label: "iv index", value: data.ivIndex !== null ? `${data.ivIndex.toFixed(1)}%` : "—" },
    {
      label: "liquidity",
      value: data.liquidity !== null ? (LIQUIDITY_LABELS[Math.round(data.liquidity)] ?? String(data.liquidity)) : "—",
    },
    { label: "p/e", value: data.pe !== null ? data.pe.toFixed(1) : "—" },
    { label: "div yield", value: data.divYield !== null ? `${data.divYield.toFixed(2)}%` : "—" },
    { label: "mkt cap", value: compact(data.marketCap) },
    { label: "volume", value: compact(data.volume) },
    { label: "earnings", value: fmtEarnings(data.earningsDate) },
    { label: "1m trend", value: data.trend1m ?? "—", cls: trendClass(data.trend1m) },
    { label: "6m trend", value: data.trend6m ?? "—", cls: trendClass(data.trend6m) },
  ];

  return (
    <section className="card">
      <div className="stats-grid">
        {tiles.map((t) => (
          <div key={t.label} className="stat-tile">
            <span className="stat-label">{t.label}</span>
            <span className={`stat-value ${t.cls ?? ""}`}>{t.value}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
