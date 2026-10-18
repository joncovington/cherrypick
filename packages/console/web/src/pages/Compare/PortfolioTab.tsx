import { useState } from "react";
import type { PnlDay, PortfolioReading } from "@console/shared";
import { Card, PnlCell } from "../../components/DataTable";
import { TileGrid } from "../../components/performance/TileGrid";
import { Tile } from "../../components/performance/Tile";
import { fmtMoney, fmtPct } from "../../lib/format";
import { usePnlCalendar, usePortfolio } from "../../lib/api";
import { useMode } from "../../lib/useMode";
import { useFeatures } from "../../lib/useFeatures";
import { isModuleVisible } from "../../lib/visibility";

/**
 * Compare's portfolio tab (docs/strategy-analysis-plan.md Phase 5): one chosen arm per module held
 * together, read by core.portfolio. The arms within a module are alternatives, not holdings, so the
 * picker takes one each. Its default -- `control`, else the module's first arm that is not an
 * advised experiment -- is a starting point, not a judgement of which arm is the strategy.
 *
 * Every figure says how many periods it rests on, and one under core's `min_periods` is marked thin.
 */

/** The arms each module traded in the calendar's window, by module. */
export function armsByModule(days: PnlDay[], visible: (m: string) => boolean): Map<string, string[]> {
  const out = new Map<string, Set<string>>();
  for (const d of days) {
    if (!visible(d.module)) continue;
    const set = out.get(d.module) ?? new Set<string>();
    set.add(d.tag);
    out.set(d.module, set);
  }
  return new Map([...out.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([m, s]) => [m, [...s].sort()]));
}

/** `control` if the module has one, else its first arm that is not an advised experiment. */
export function defaultArm(tags: string[]): string | null {
  if (tags.includes("control")) return "control";
  return tags.find((t) => !t.startsWith("advised:") && !t.includes("@")) ?? null;
}

function corrTone(r: number | null): string | undefined {
  if (r === null) return undefined;
  return r >= 0.5 ? "pnl-neg" : r <= -0.2 ? "pnl-pos" : undefined;
}

function fmtR(r: number | null): string {
  return r === null ? "—" : r.toFixed(2);
}

export function PortfolioView({ p }: { p: PortfolioReading }) {
  const n = p.shared.arms?.length ?? p.arms.filter((a) => a.periods_traded > 0).length;
  const tooNew = p.shared.too_new ?? [];
  const unit = p.period === "week" ? "weeks" : "sessions";
  const thin = (periods: number) => periods < p.min_periods;
  return (
    <>
      {p.failures.length > 0 && (
        <p style={{ fontSize: 12 }}>
          {p.failures.map((f) => (
            <span key={f.key ?? f.error} className="chip chip-warn" style={{ marginRight: 6 }} title={f.error}>
              {f.key} not read
            </span>
          ))}
        </p>
      )}
      {tooNew.length > 0 && (
        <p className="muted" style={{ fontSize: 12 }}>
          too new to count in effective arms (under {String(p.min_periods)} {p.period === "week" ? "weeks" : "sessions"} since
          their first close): {tooNew.join(", ")} — in every other figure
        </p>
      )}
      {p.no_closes.length > 0 && (
        <p className="muted" style={{ fontSize: 12 }}>
          no closes in the window, so nothing to hold together: {p.no_closes.join(", ")}
        </p>
      )}
      <TileGrid count={6}>
        <Tile
          label="effective arms"
          value={p.effective_arms === null ? "—" : `${p.effective_arms.toFixed(2)} of ${String(n)}`}
          n={p.shared.periods}
          nUnit={unit}
          underpowered={p.shared.thin}
          title="(Σ each arm's spread / the spread of their sum)²: N for independent arms of equal size, 1 for one bet; over the window every chosen arm spans"
        />
        <Tile
          label="drawdown, combined"
          value={fmtMoney(p.drawdown.combined)}
          tone="neg"
          n={p.window.combined_periods}
          nUnit={unit}
          title={p.drawdown.episode === null ? "" : `worst from ${p.drawdown.episode.from ?? "the start"} to ${p.drawdown.episode.to}`}
        />
        <Tile label="drawdown, arms summed" value={fmtMoney(p.drawdown.sum_of_arms)} tone="dim" title="each arm's own worst drawdown, added up" />
        <Tile
          label="combining saves"
          value={p.drawdown.benefit === null ? "—" : fmtPct(p.drawdown.benefit * 100)}
          tone={p.drawdown.benefit === null ? "dim" : "pos"}
          title="1 − combined drawdown / the arms' drawdowns summed"
        />
        <Tile label="combined net" value={fmtMoney(p.combined_net)} tone={p.combined_net >= 0 ? "pos" : "neg"} afterFees />
        <Tile
          label="weighting"
          value={p.weights === "equal_risk" ? "equal risk" : "as booked"}
          tone="dim"
          title={p.weights === "equal_risk" ? "each arm scaled to the median arm's spread per period; see the scale column" : "dollars as each book booked them"}
        />
      </TileGrid>

      <div className="card-subhead">correlation, two ways</div>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>arm</th>
              <th>arm</th>
              <th title="every period inside both spans, a period an arm closed nothing counted as zero">every period</th>
              <th>n</th>
              <th title="only periods both arms closed something">when both traded</th>
              <th>n</th>
            </tr>
          </thead>
          <tbody>
            {[...p.correlation]
              .sort((x, y) => Math.abs(y.zero_filled ?? 0) - Math.abs(x.zero_filled ?? 0))
              .map((c) => (
                <tr key={`${c.a}|${c.b}`} className={thin(c.zero_filled_periods) ? "muted" : undefined}>
                  <td className="mono">{c.a}</td>
                  <td className="mono">{c.b}</td>
                  <td className={thin(c.zero_filled_periods) ? undefined : corrTone(c.zero_filled)}>{fmtR(c.zero_filled)}</td>
                  <td>{String(c.zero_filled_periods)}</td>
                  <td className={thin(c.similarity_periods) ? undefined : corrTone(c.similarity)}>{fmtR(c.similarity)}</td>
                  <td>
                    {String(c.similarity_periods)}
                    {c.thin && (
                      <span className="chip" style={{ marginLeft: 6, fontSize: 9 }} title={`fewer than ${String(p.min_periods)} shared ${unit}`}>
                        thin
                      </span>
                    )}
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>

      <div className="card-subhead">each arm</div>
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>arm</th>
              <th>first close</th>
              <th title={`${unit} with a close`}>traded</th>
              <th>net, booked</th>
              <th title="its net in the combined book, after weighting">in the book</th>
              <th>share</th>
              <th title="its net during the combined book's worst drawdown">in the worst drawdown</th>
              <th title="the factor its nets were scaled by (1 = as booked)">scale</th>
            </tr>
          </thead>
          <tbody>
            {p.arms
              .filter((a) => a.periods_traded > 0)
              .map((a) => {
                const c = p.contribution.find((x) => x.key === a.key);
                return (
                  <tr key={a.key}>
                    <td className="mono">{a.key}</td>
                    <td>{a.first ?? "—"}</td>
                    <td>{String(a.periods_traded)}</td>
                    <td><PnlCell v={a.net} /></td>
                    <td><PnlCell v={c?.net ?? null} /></td>
                    <td>{c?.share === null || c?.share === undefined ? "—" : fmtPct(c.share * 100)}</td>
                    <td><PnlCell v={c?.in_worst_drawdown ?? null} /></td>
                    <td className="mono">{a.scale === null ? "—" : `×${a.scale.toPrecision(3)}`}</td>
                  </tr>
                );
              })}
          </tbody>
        </table>
      </div>

      {p.worst_periods.length > 0 && (
        <>
          <div className="card-subhead">worst {unit}</div>
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>{p.period === "week" ? "week of" : "session"}</th>
                  <th>combined</th>
                  <th>arms lost / closed</th>
                  <th>by arm</th>
                </tr>
              </thead>
              <tbody>
                {p.worst_periods.map((w) => (
                  <tr key={w.period}>
                    <td>{w.period}</td>
                    <td><PnlCell v={w.net} /></td>
                    <td>{`${String(w.arms_lost)} / ${String(w.arms_traded)}`}</td>
                    <td className="muted">
                      {Object.entries(w.by_arm)
                        .sort((a, b) => a[1] - b[1])
                        .map(([k, v]) => `${k} ${fmtMoney(v)}`)
                        .join(" · ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  );
}

export function PortfolioTab() {
  const [mode] = useMode();
  const calendar = usePnlCalendar(mode);
  const features = useFeatures();
  const visible = (module: string) => (features === undefined ? true : isModuleVisible(module, features));
  const modules = armsByModule(calendar.data?.days ?? [], visible);
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const [period, setPeriod] = useState<"day" | "week">("day");
  const [weights, setWeights] = useState<"equal_risk" | "booked">("equal_risk");
  const pick = (module: string, tags: string[]) => (module in chosen ? chosen[module] ?? "" : (defaultArm(tags) ?? ""));
  const arms = [...modules.entries()].map(([m, tags]) => [m, pick(m, tags)] as const).filter(([, t]) => t !== "").map(([m, t]) => `${m}:${t}`);
  const { data, isLoading, isError, dataUpdatedAt } = usePortfolio(arms, period, weights, mode);
  return (
    <div className="cards cards-wide">
      <Card title="portfolio — one arm per module, held together" updatedAt={dataUpdatedAt} isError={isError}>
        <p className="muted" style={{ fontSize: 12 }}>
          Each module&apos;s arms are alternatives, so pick one each (or none). Correlation is shown two ways: over every{" "}
          {period === "week" ? "week" : "session"} in both arms&apos; spans with a no-close period counted as zero (do they lose
          together), and over periods both closed something (when both trade, do they move together). No ledger says
          whether an arm was running on a day it closed nothing, so zero is assumed only from its first close. Multi-day
          modules book P&amp;L on the close, so a daily figure partly measures their exit calendars; weekly mostly removes
          that.
        </p>
        <div style={{ display: "flex", gap: "0.6rem", flexWrap: "wrap", alignItems: "center", marginBottom: "0.6rem" }}>
          {[...modules.entries()].map(([m, tags]) => (
            <label key={m} style={{ fontSize: 12 }}>
              {m}{" "}
              <select className="text-input" style={{ maxWidth: "15rem" }} value={pick(m, tags)} onChange={(e) => setChosen({ ...chosen, [m]: e.target.value })}>
                <option value="">none</option>
                {tags.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </label>
          ))}
          <select className="text-input" value={period} onChange={(e) => setPeriod(e.target.value === "week" ? "week" : "day")}>
            <option value="day">daily</option>
            <option value="week">weekly</option>
          </select>
          <select className="text-input" value={weights} onChange={(e) => setWeights(e.target.value === "booked" ? "booked" : "equal_risk")}>
            <option value="equal_risk">equal risk</option>
            <option value="booked">as booked</option>
          </select>
        </div>
        {arms.length === 0 ? (
          <p className="muted">choose at least one arm</p>
        ) : isLoading || data === undefined ? (
          <span className="skeleton skeleton-text" style={{ width: "50%" }} />
        ) : !data.ok ? (
          <p className="muted">{data.error ?? "the portfolio read is unavailable"}</p>
        ) : (
          <PortfolioView p={data} />
        )}
      </Card>
    </div>
  );
}
