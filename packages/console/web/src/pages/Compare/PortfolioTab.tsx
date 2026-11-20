import { useState } from "react";
import { BACKTEST_ARM_PREFIX, portfolioArmLabel, type PnlDay, type PortfolioOpenRisk, type PortfolioReading, type PortfolioSource } from "@console/shared";
import { Card, PnlCell } from "../../components/DataTable";
import { HelpSection, type CardHelpContent } from "../../components/CardHelp";
import { FilterGroup } from "../../components/FilterSort";
import { Section } from "../../components/Section";
import { useFilterPanel } from "../useFilterPanel";
import { TileGrid } from "../../components/performance/TileGrid";
import { Tile } from "../../components/performance/Tile";
import { fmtMoney, fmtPct } from "../../lib/format";
import { usePnlCalendar, usePortfolio, usePortfolioSources } from "../../lib/api";
import { useMode } from "../../lib/useMode";
import { useFeatures } from "../../lib/useFeatures";
import { isModuleVisible } from "../../lib/visibility";

/**
 * Compare's portfolio tab (packages/core/docs/module-map.md): one chosen arm per module held
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

/** One backtest arm the picker offers: its key, what it reads as, and the run behind it. */
export interface BacktestOption {
  key: string;
  label: string;
  licensed: boolean;
  window: [string, string] | null;
}

/** Every study arm, and each Builder automation's arms from its newest run only (older runs of the
 *  same automation are its history, not another strategy). */
export function backtestOptions(sources: PortfolioSource[]): BacktestOption[] {
  const newest = new Map<string, PortfolioSource>();
  const out: BacktestOption[] = [];
  for (const s of sources) {
    if (s.kind !== "builder") continue;
    const name = s.arms.join(",");
    const held = newest.get(name);
    if (held === undefined || (s.generated_at ?? s.id) > (held.generated_at ?? held.id)) newest.set(name, s);
  }
  for (const s of [...newest.values(), ...sources.filter((x) => x.kind === "study")]) {
    for (const arm of s.arms) {
      const span = s.window === null ? "" : `, ${s.window[0]} → ${s.window[1]}`;
      out.push({
        key: `${BACKTEST_ARM_PREFIX}${s.id}#${arm}`,
        label: s.kind === "builder" ? `${arm} (Builder run${span})` : `${arm} (${s.label}${span})`,
        licensed: s.licensed,
        window: s.window,
      });
    }
  }
  return out.sort((a, b) => a.label.localeCompare(b.label));
}

function corrTone(r: number | null): string | undefined {
  if (r === null) return undefined;
  return r >= 0.5 ? "pnl-neg" : r <= -0.2 ? "pnl-pos" : undefined;
}

function fmtR(r: number | null): string {
  return r === null ? "—" : r.toFixed(2);
}

/** Max loss held at once: each session's peak of the summed open max loss, and the sessions it peaked. */
export function OpenRiskCard({ r }: { r: PortfolioOpenRisk }) {
  const unmeasured = Object.entries(r.unmeasured);
  const whole = Object.entries(r.whole_session);
  const share = (v: number | undefined) => (v === undefined ? "max loss open at once; give an account to read it as a share" : `${fmtPct(v * 100)} of the account`);
  return (
    <Card title="risk held at once" help={OPEN_RISK_HELP}>
      {r.floor && (
        <p style={{ fontSize: 12 }}>
          <span className="chip chip-warn" style={{ marginRight: 6 }}>a floor</span>
          trades with no recorded max loss are left out:{" "}
          {unmeasured.map(([k, n]) => `${portfolioArmLabel(k)} (${String(n)})`).join(", ")}
        </p>
      )}
      {whole.length > 0 && (
        <p className="muted" style={{ fontSize: 12 }}>
          no entry or exit time recorded, so held for their whole sessions: {whole.map(([k, n]) => `${portfolioArmLabel(k)} (${String(n)})`).join(", ")}
        </p>
      )}
      {r.sessions === 0 ? (
        <p className="muted">no trade with a recorded max loss in the window</p>
      ) : (
        <>
          <TileGrid count={3}>
            <Tile label="peak, median session" value={fmtMoney(r.peaks.median)} tone="dim" n={r.sessions} nUnit="sessions" title={share(r.of_account?.median)} />
            <Tile label="peak, 95th percentile" value={fmtMoney(r.peaks.p95)} tone="neg" title={share(r.of_account?.p95)} />
            <Tile label="peak, worst session" value={fmtMoney(r.peaks.max)} tone="neg" title={share(r.of_account?.max)} />
          </TileGrid>
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>session</th>
                  <th title="the most max loss open at one moment that session">held at once</th>
                  <th>by arm, at that moment</th>
                </tr>
              </thead>
              <tbody>
                {r.worst_sessions.map((w) => (
                  <tr key={w.session}>
                    <td>{w.session}</td>
                    <td>{fmtMoney(w.peak)}</td>
                    <td className="muted">
                      {Object.entries(w.by_arm)
                        .sort((a, b) => b[1] - a[1])
                        .map(([k, v]) => `${portfolioArmLabel(k)} ${fmtMoney(v)}`)
                        .join(" · ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Card>
  );
}

const OPEN_RISK_HELP: CardHelpContent = {
  title: "Risk held at once",
  body: (
    <HelpSection title="The most the arms could lose together">
      <p>
        Each trade's defined maximum loss, held from its entry to its exit, summed across the chosen arms. Each session reads its peak, and
        the tiles are those peaks over the sessions anything was open. A position held overnight counts from every session it spans. A trade
        with no recorded maximum loss is named and left out, so the reading is then a floor; a trade whose source records no time is held for
        its whole sessions.
      </p>
    </HelpSection>
  ),
};

export function PortfolioView({ p }: { p: PortfolioReading }) {
  const n = p.shared.arms?.length ?? p.arms.filter((a) => a.periods_traded > 0).length;
  const tooNew = p.shared.too_new ?? [];
  const unit = p.period === "week" ? "weeks" : "sessions";
  const thin = (periods: number) => periods < p.min_periods;
  return (
    <>
      <Section concept="risk" question="Do the arms move together?">
      <Card title="held together" help={HELD_HELP}>
      {p.failures.length > 0 && (
        <p style={{ fontSize: 12 }}>
          {p.failures.map((f) => (
            <span key={f.key ?? f.error} className="chip chip-warn" style={{ marginRight: 6 }} title={f.error}>
              {f.key} not read
            </span>
          ))}
        </p>
      )}
      {p.licensed === true && (
        <p className="muted" style={{ fontSize: 12 }}>
          <span className="chip chip-warn" style={{ marginRight: 6 }}>licensed</span>
          holds a backtest derived from licensed vendor data: this reading stays on this machine
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
      </Card>

      <Card title="correlation, two ways" help={CORRELATION_HELP}>
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
                  <td className="mono" title={c.a}>{portfolioArmLabel(c.a)}</td>
                  <td className="mono" title={c.b}>{portfolioArmLabel(c.b)}</td>
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
      </Card>
      {p.open_risk !== undefined && <OpenRiskCard r={p.open_risk} />}
      </Section>

      <Section concept="stand" tag="contribution" question="What does each arm add?">
      <Card title="each arm" className="lv-12">
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>arm</th>
              <th title="its first close; a backtest arm counts from its run's first session">from</th>
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
                    <td className="mono" title={a.key}>{portfolioArmLabel(a.key)}</td>
                    <td>{a.span_from ?? a.first ?? "—"}</td>
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
      </Card>

      {p.worst_periods.length > 0 && (
        <Card title={`worst ${unit}`} className="lv-12">
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
                        .map(([k, v]) => `${portfolioArmLabel(k)} ${fmtMoney(v)}`)
                        .join(" · ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
      </Section>
    </>
  );
}

const HELD_HELP: CardHelpContent = {
  title: "Holding the arms together",
  body: (
    <HelpSection title="What the tiles say">
      <p>
        Effective arms is N for independent arms of equal size and 1 for one bet. Combining saves is how much smaller the combined drawdown
        is than the arms' own drawdowns added up. Every figure says how many periods it rests on, and one under the minimum is marked thin.
      </p>
    </HelpSection>
  ),
};

const CORRELATION_HELP: CardHelpContent = {
  title: "Correlation, two ways",
  body: (
    <HelpSection title="Every period, and when both traded">
      <p>
        The first counts every period inside both arms' spans, a period an arm closed nothing counted as zero: do they lose together. The
        second counts only periods both closed something: when both trade, do they move together.
      </p>
    </HelpSection>
  ),
};

const PICK_HELP: CardHelpContent = {
  title: "Which arms are held together",
  body: (
    <>
      <HelpSection title="One arm per module">
        <p>
          Each module's arms are alternatives, so pick one each (or none). The default is control, else the module's first arm that is not an
          advised experiment: a starting point, not a judgement of which arm is the strategy.
        </p>
      </HelpSection>
      <HelpSection title="Backtests beside paper">
        <p>
          Add any number of backtest arms: each Builder automation's newest run, and the arms of a study result. A backtest arm counts from its
          run's first session, so its quiet opening days read as zeros, and the window then starts at the earliest chosen run. Each arm is
          labelled backtest; it is never summed with a paper arm into one. A result derived from licensed vendor data is listed only while
          the results gate is open, and the reading says it holds one.
        </p>
      </HelpSection>
      <HelpSection title="How the correlation is read">
        <p>
          Correlation is shown two ways: over every session or week in both arms' spans with a no-close period counted as zero, and over
          periods both closed something. No ledger says whether an arm was running on a day it closed nothing, so zero is assumed only from its
          first close. Multi-day modules book P&amp;L on the close, so a daily figure partly measures their exit calendars; weekly mostly
          removes that.
        </p>
      </HelpSection>
    </>
  ),
};

export function PortfolioTab() {
  const [mode] = useMode();
  const calendar = usePnlCalendar(mode);
  const features = useFeatures();
  const visible = (module: string) => (features === undefined ? true : isModuleVisible(module, features));
  const modules = armsByModule(calendar.data?.days ?? [], visible);
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const [period, setPeriod] = useState<"day" | "week">("day");
  const [weights, setWeights] = useState<"equal_risk" | "booked">("equal_risk");
  const [backtests, setBacktests] = useState<string[]>([]);
  const sources = usePortfolioSources();
  const options = sources.data?.ok === true ? backtestOptions(sources.data.sources) : [];
  const pick = (module: string, tags: string[]) => (module in chosen ? chosen[module] ?? "" : (defaultArm(tags) ?? ""));
  const paperArms = [...modules.entries()].map(([m, tags]) => [m, pick(m, tags)] as const).filter(([, t]) => t !== "").map(([m, t]) => `${m}:${t}`);
  const arms = [...paperArms, ...backtests];
  const { data, isLoading, isError, dataUpdatedAt } = usePortfolio(arms, period, weights, mode);
  const changedPicks = [...modules.entries()].filter(([m, tags]) => pick(m, tags) !== (defaultArm(tags) ?? "")).length;
  const changed = changedPicks + backtests.length + (period !== "day" ? 1 : 0) + (weights !== "equal_risk" ? 1 : 0);
  const filter = useFilterPanel({
    parts: [
      arms.length === 0 ? "no arm chosen" : arms.map(portfolioArmLabel).join(", "),
      period === "week" ? "weekly" : "daily",
      weights === "booked" ? "as booked" : "equal risk",
    ],
    changed,
    reset: () => {
      setChosen({});
      setBacktests([]);
      setPeriod("day");
      setWeights("equal_risk");
    },
    children: (
      <>
        {modules.size > 0 && (
        <FilterGroup title="arms" note="one per module">
          {[...modules.entries()].map(([m, tags]) => (
            <label key={m} style={{ fontSize: 12, display: "block", marginBottom: "0.35rem" }}>
              {m}{" "}
              <select className="chip review-session-select" aria-label={`${m} arm`} value={pick(m, tags)} onChange={(e) => setChosen({ ...chosen, [m]: e.target.value })}>
                <option value="">none</option>
                {tags.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </label>
          ))}
        </FilterGroup>
        )}
        <FilterGroup title="backtests" note="any number, beside the paper arms">
          {backtests.map((k) => (
            <span key={k} className="chip" style={{ fontSize: 11, marginRight: 6, display: "inline-block", marginBottom: 4 }} title={k}>
              {portfolioArmLabel(k)}{" "}
              <button type="button" className="link-button" aria-label={`remove ${portfolioArmLabel(k)}`} onClick={() => setBacktests(backtests.filter((x) => x !== k))}>
                ×
              </button>
            </span>
          ))}
          {sources.data !== undefined && !sources.data.ok ? (
            <p className="muted" style={{ fontSize: 12 }}>{sources.data.error ?? "backtest results unavailable"}</p>
          ) : (
            <select
              className="chip review-session-select"
              aria-label="add a backtest arm"
              value=""
              onChange={(e) => {
                if (e.target.value !== "") setBacktests([...backtests, e.target.value]);
              }}
            >
              <option value="">{options.length === 0 ? "no backtest results" : "add a backtest arm…"}</option>
              {options
                .filter((o) => !backtests.includes(o.key))
                .map((o) => (
                  <option key={o.key} value={o.key}>
                    {o.label}
                    {o.licensed ? " · licensed" : ""}
                  </option>
                ))}
            </select>
          )}
        </FilterGroup>
        <FilterGroup title="period">
          <select className="chip review-session-select" aria-label="period" value={period} onChange={(e) => setPeriod(e.target.value === "week" ? "week" : "day")}>
            <option value="day">daily</option>
            <option value="week">weekly</option>
          </select>
        </FilterGroup>
        <FilterGroup title="weighting">
          <select className="chip review-session-select" aria-label="weighting" value={weights} onChange={(e) => setWeights(e.target.value === "booked" ? "booked" : "equal_risk")}>
            <option value="equal_risk">equal risk</option>
            <option value="booked">as booked</option>
          </select>
        </FilterGroup>
      </>
    ),
  });
  const pickable = modules.size > 0 || options.length > 0;
  const ready = !calendar.isLoading && pickable && arms.length > 0 && !isLoading && data !== undefined && data.ok;
  return (
    <div className="pf-layout">
      <Section concept="stand" tag="the arms" question="Which arms am I holding together?">
      <Card
        title="one arm per module, and backtests"
        updatedAt={dataUpdatedAt}
        isError={isError}
        help={PICK_HELP}
        controls={pickable ? filter.button : null}
      >
        {pickable ? filter.body : null}
        {calendar.isLoading ? (
          <span className="skeleton skeleton-text" style={{ width: "50%" }} />
        ) : !pickable ? (
          <p className="muted">{calendar.isError ? "the arms could not be read" : `no arm has closed a ${mode} trade yet, and no backtest result is listed`}</p>
        ) : arms.length === 0 ? (
          <p className="muted">choose at least one arm</p>
        ) : isLoading || data === undefined ? (
          <span className="skeleton skeleton-text" style={{ width: "50%" }} />
        ) : !data.ok ? (
          <p className="muted">{data.error ?? "the portfolio read is unavailable"}</p>
        ) : (
          <p className="muted">
            {data.arms.filter((a) => a.periods_traded > 0).length} arms held together, {data.period === "week" ? "weekly" : "daily"}.
          </p>
        )}
      </Card>
      </Section>
      {ready && data !== undefined ? <PortfolioView p={data} /> : null}
    </div>
  );
}
