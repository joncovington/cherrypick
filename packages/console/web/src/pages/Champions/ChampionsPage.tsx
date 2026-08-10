import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Card, DataCard, PnlCell, fmtMoney, fmtNum } from "../../components/DataTable";
import { TabStrip } from "../../components/ScopeBar";

interface Check {
  value: number | null;
  threshold: number | string;
  pass: boolean;
}

interface Reading {
  sample: number;
  winRate: number | null;
  days: number;
  netPnl: number;
  netPnl2xSlippage: number;
  slippageCoverage: number;
  returnOnCapital: number | null;
  capitalCoverage: number;
  sharpe: number | null;
  maxDrawdown: number;
  sampleProgress: { n: number; nextTarget: number | null; progress: number };
}

interface Tag {
  tag: string;
  reading: Reading;
  qualification: { qualified: boolean; checks: Record<string, Check> };
  role: string;
}

interface ModuleCalibration {
  module: string;
  champion: string | null;
  tags: Tag[];
  verdict: { eligible: boolean; recommendation: string; reason: string } | null;
}

interface Calibration {
  modules: ModuleCalibration[];
}

const CHECK_LABEL: Record<string, string> = { sample: "sample", win_rate: "win rate", days: "days" };

/** How far a failing check still has to go, in the check's own units. */
function shortfall(name: string, c: Check): string | null {
  if (c.pass || typeof c.threshold !== "number") return null;
  const v = c.value ?? 0;
  if (name === "win_rate") return `${((c.threshold - v) * 100).toFixed(0)} pts short`;
  const missing = Math.max(0, Math.ceil(c.threshold - v));
  return `${missing} more`;
}

function CheckBar({ name, c }: { name: string; c: Check }) {
  const pct = c.value !== null && typeof c.threshold === "number" ? Math.min(1, c.value / c.threshold) * 100 : 0;
  const shown =
    c.value === null
      ? "—"
      : name === "win_rate"
        ? `${(c.value * 100).toFixed(0)}% / ${(Number(c.threshold) * 100).toFixed(0)}%`
        : `${c.value} / ${c.threshold}`;
  return (
    <div className="check-row">
      <span className="check-label">{CHECK_LABEL[name] ?? name}</span>
      <div className="check-track">
        <div className="check-fill" style={{ width: `${pct}%`, background: c.pass ? "var(--ok)" : "var(--warn)" }} />
      </div>
      <span className="check-value">{shown}</span>
    </div>
  );
}

/**
 * Net per arm as a diverging bar list. A column chart would label only its
 * first and last category, which is useless when the categories are the arm
 * names — here every row carries its own label and the zero line is shared.
 */
function NetBars({ tags }: { tags: Tag[] }) {
  const maxAbs = Math.max(...tags.map((t) => Math.abs(t.reading.netPnl)), 1);
  return (
    <div className="netbars">
      {tags.map((t) => {
        const v = t.reading.netPnl;
        const half = (Math.abs(v) / maxAbs) * 50;
        return (
          <div key={t.tag} className="netbar-row">
            <span className="netbar-label" title={t.tag}>{t.tag}</span>
            <div className="netbar-track">
              <div className="netbar-zero" />
              <div
                className="netbar-fill"
                style={
                  v >= 0
                    ? { left: "50%", width: `${half}%`, background: "var(--ok)" }
                    : { right: "50%", width: `${half}%`, background: "var(--err)" }
                }
              />
            </div>
            <span className={`netbar-value ${v >= 0 ? "pnl-pos" : "pnl-neg"}`}>{fmtMoney(v)}</span>
          </div>
        );
      })}
    </div>
  );
}

function RoleBadge({ role }: { role: string }) {
  const cls =
    role === "champion"
      ? "chain-badge-long"
      : role === "beats champion"
        ? "chain-badge-long"
        : role === "qualified"
          ? "chain-badge-long"
          : "";
  return <span className={`chain-badge ${cls}`}>{role}</span>;
}

/**
 * What a module's promotion state actually is. A module with no declared
 * champion runs no comparison at all — its arms are judged against the rule
 * one at a time and never against each other, which is a different claim from
 * "nothing has won yet" and needs to read that way.
 */
function VerdictBanner({ m }: { m: ModuleCalibration }) {
  if (m.champion === null) {
    return (
      <p className="verdict verdict-neutral">
        No champion declared for <strong>{m.module}</strong>. Its {m.tags.length} arms are qualified independently
        against the rule and never promoted against each other — nothing here is a comparison.
      </p>
    );
  }
  if (m.verdict === null) return null;
  return (
    <p className={`verdict ${m.verdict.eligible ? "verdict-promote" : "verdict-neutral"}`}>
      {m.verdict.eligible ? "Promotion available — " : ""}
      {m.verdict.reason}
    </p>
  );
}

function ModuleTab({ m }: { m: ModuleCalibration }) {
  const qualified = m.tags.filter((t) => t.qualification.qualified);
  // Clearing the rule means the evidence is sufficient, not that the arm makes
  // money. Say so where it happens, since the badge alone reads as approval.
  const qualifiedButLosing = qualified.filter((t) => t.reading.netPnl < 0);
  const rule = m.tags[0]?.qualification.checks;

  return (
    <div className="cards cards-wide">
      <Card title={`${m.module} — promotion state`}>
        <VerdictBanner m={m} />
        <div className="stats-grid">
          <div className="stat-tile">
            <span className="stat-label">arms</span>
            <span className="stat-value">{m.tags.length}</span>
          </div>
          <div className="stat-tile">
            <span className="stat-label">qualified</span>
            <span className={`stat-value ${qualified.length > 0 ? "pnl-pos" : ""}`}>{qualified.length}</span>
          </div>
          <div className="stat-tile">
            <span className="stat-label">champion</span>
            <span className="stat-value">{m.champion ?? "—"}</span>
          </div>
          <div className="stat-tile">
            <span className="stat-label">best net</span>
            <span className={`stat-value ${(m.tags[0]?.reading.netPnl ?? 0) >= 0 ? "pnl-pos" : "pnl-neg"}`}>
              {m.tags.length > 0 ? fmtMoney(m.tags[0]!.reading.netPnl) : "—"}
            </span>
          </div>
        </div>
        {rule !== undefined && (
          <p className="muted rule-line">
            Qualifies at ≥{rule["sample"]?.threshold} trades, ≥{rule["days"]?.threshold} sessions, and a win rate of
            ≥{(Number(rule["win_rate"]?.threshold ?? 0) * 100).toFixed(0)}%.
          </p>
        )}
        {qualifiedButLosing.length > 0 && (
          <p className="stale-note">
            {qualifiedButLosing.length === 1 ? "One arm has" : `${qualifiedButLosing.length} arms have`} enough evidence
            to qualify while still losing money ({qualifiedButLosing.map((t) => t.tag).join(", ")}). Qualifying means the
            sample is big enough to believe, not that the result is good.
          </p>
        )}
      </Card>

      <Card title="Net P&L by arm (after fees, all-time)">
        <NetBars tags={m.tags} />
      </Card>

      <DataCard
        title={`All arms — ${m.tags.length} ranked by net`}
        headers={["arm", "role", "trades", "sessions", "win %", "net", "2× slip", "RoC", "Sharpe", "max DD"]}
        numFrom={2}
        tableClass="data-table-labelled"
        loading={false}
        rowCount={m.tags.length}
        empty="no tagged trades for this module"
      >
        {m.tags.map((t) => (
          <tr key={t.tag}>
            <td>{t.tag}</td>
            <td><RoleBadge role={t.role} /></td>
            <td>{t.reading.sample}</td>
            <td className="muted">{t.reading.days}</td>
            <td>{t.reading.winRate !== null ? `${(t.reading.winRate * 100).toFixed(0)}%` : "—"}</td>
            <td><PnlCell v={t.reading.netPnl} /></td>
            <td className="muted">
              {t.reading.slippageCoverage > 0 ? fmtMoney(t.reading.netPnl2xSlippage) : "—"}
            </td>
            <td>{t.reading.returnOnCapital !== null ? `${(t.reading.returnOnCapital * 100).toFixed(1)}%` : "—"}</td>
            <td>{t.reading.sharpe !== null ? t.reading.sharpe.toFixed(2) : "—"}</td>
            <td className="pnl-neg">{fmtMoney(-t.reading.maxDrawdown)}</td>
          </tr>
        ))}
      </DataCard>

      <Card title="Qualification progress — every arm, and what each still needs">
        <div className="cards" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(21rem, 1fr))", gap: "0.6rem" }}>
          {m.tags.map((t) => (
            <div key={t.tag} className={`arm-card ${t.qualification.qualified ? "arm-card-qualified" : ""}`}>
              <div className="arm-head">
                <strong>{t.tag}</strong>
                <RoleBadge role={t.role} />
                <span className={`arm-net ${t.reading.netPnl >= 0 ? "pnl-pos" : "pnl-neg"}`}>
                  {fmtMoney(t.reading.netPnl)}
                </span>
              </div>
              {Object.entries(t.qualification.checks).map(([name, c]) => (
                <CheckBar key={name} name={name} c={c} />
              ))}
              <div className="arm-foot muted">
                {Object.entries(t.qualification.checks)
                  .map(([name, c]) => {
                    const s = shortfall(name, c);
                    return s === null ? null : `${CHECK_LABEL[name] ?? name}: ${s}`;
                  })
                  .filter((s): s is string => s !== null)
                  .join(" · ") || "all checks clear"}
              </div>
              <div className="arm-foot muted">
                RoC {t.reading.returnOnCapital !== null ? `${(t.reading.returnOnCapital * 100).toFixed(1)}%` : "—"}
                {" · "}Sharpe {t.reading.sharpe !== null ? t.reading.sharpe.toFixed(2) : "—"}
                {" · "}max DD {fmtMoney(t.reading.maxDrawdown)}
                {t.reading.capitalCoverage < t.reading.sample && (
                  <> {" · "}capital on {fmtNum((t.reading.capitalCoverage / Math.max(1, t.reading.sample)) * 100, 0)}% of trades</>
                )}
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

export function ChampionsPage() {
  const { data, isLoading, dataUpdatedAt } = useQuery<Calibration>({
    queryKey: ["calibration"],
    queryFn: async () => {
      const res = await fetch("/api/calibration");
      if (!res.ok) throw new Error(`calibration: HTTP ${res.status}`);
      return (await res.json()) as Calibration;
    },
    refetchInterval: 120_000,
  });

  const modules = data?.modules ?? [];
  const [tab, setTab] = useState<string | null>(null);
  // Tabs come from the payload, so a module added to calibrate shows up here
  // without this page needing to know its name.
  const active = modules.find((m) => m.module === tab) ?? modules[0];

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>Champions &amp; challengers</h1>
        {modules.length > 0 && (
          <TabStrip
            tabs={modules.map((m) => m.module)}
            value={active?.module ?? modules[0]!.module}
            onChange={setTab}
          />
        )}
        <span className="card-asof" style={{ marginLeft: "auto" }}>
          {dataUpdatedAt > 0 ? `as of ${new Date(dataUpdatedAt).toLocaleTimeString()}` : ""}
        </span>
      </div>

      {isLoading ? (
        <div className="cards cards-wide">
          <Card title="loading">
            <span className="skeleton skeleton-text" style={{ width: "50%" }} />
          </Card>
        </div>
      ) : active === undefined ? (
        <p className="muted">No calibration data — no module has tagged closed trades yet.</p>
      ) : (
        <ModuleTab m={active} />
      )}
    </div>
  );
}
