import { Link } from "react-router-dom";
import type { TradingMode } from "@console/shared";
import { AnalysisSlide } from "../../components/analysis/AnalysisSlide";
import { AttemptsTab } from "../../components/AttemptsTab";
import { ChangesSection } from "../../components/changes/ChangesSlide";
import { ModuleCosts } from "../../components/costs/ModuleCosts";
import { PerformanceSlide } from "../../components/performance/PerformanceSlide";
import { RegimeCutsTab } from "../../components/RegimeCutsTab";
import { MeicHistoryTable } from "./MeicTables";

/**
 * One MEIC arm's page (`/meic/arm/<name>`, framed as the arms tab): the arm's whole record in the
 * order every arm page uses. Against control first (its rows from the changes reader, read per trade
 * over MEIC's current measurement era), then what it did (its performance card and running net with
 * control beside it, its exit reasons and breaks), today (its attempts; the `?arm=` view on the arms
 * tab keeps the payoff at expiry), its costs, its regime cuts, its analysis and its history. Every
 * section is the tab it comes from with the arm fixed, so a figure here is the tab's figure; the header's
 * mode and era still apply, the arm picker does not.
 */

export const MEIC_ARM_JUMPS = [
  { id: "meic-arm-control", label: "Against control" },
  { id: "meic-arm-record", label: "What it did" },
  { id: "meic-arm-today", label: "Today" },
  { id: "meic-arm-costs", label: "Costs" },
  { id: "meic-arm-regime", label: "Regime cuts" },
  { id: "meic-arm-analysis", label: "Analysis" },
  { id: "meic-arm-history", label: "History" },
] as const;

export function meicArmHref(name: string): string {
  return `/meic/arm/${encodeURIComponent(name)}`;
}

export function MeicArmPage({
  name,
  mode,
  era,
  resolvedEra,
  symbol,
  day,
  reasons,
}: {
  name: string;
  mode: TradingMode;
  /** The header's era as picked (null: the default), which the costs read takes. */
  era: string | null;
  /** The era the header resolves to, which the history and attempts read take. */
  resolvedEra: string | null;
  symbol: string | null;
  day: string | null;
  reasons: string[];
}) {
  const scope = { day, symbol, profile: name, era: resolvedEra };
  return (
    <div className="pf-layout meic-arm-page">
      <nav className="grp-crumbs" aria-label="Where you are">
        <Link to="/meic/arms">arms</Link>
        {" / "}
        <b>{name}</b>
        {" · "}
        <Link to={`/meic/arms?arm=${encodeURIComponent(name)}`}>today&apos;s payoff at expiry ›</Link>
      </nav>
      <nav className="ar-jump" aria-label="On this page">
        {MEIC_ARM_JUMPS.map((j) => (
          <a key={j.id} href={`#${j.id}`}>
            {j.label}
          </a>
        ))}
      </nav>
      <div id="meic-arm-control">
        {name === "control" ? (
          <p className="muted">This is the control: every other arm is read against it.</p>
        ) : (
          <ChangesSection module="meic" arm={name} />
        )}
      </div>
      <div id="meic-arm-record">
        <PerformanceSlide module="meic" mode={mode} arm={name} pairs={false} />
      </div>
      <div id="meic-arm-today">
        <AttemptsTab module="meic" mode={mode} date={day} scope={{ arm: name, symbol, era: resolvedEra }} />
      </div>
      <div id="meic-arm-costs">
        <ModuleCosts module="meic" mode={mode} era={era} arm={name} />
      </div>
      <div id="meic-arm-regime">
        <RegimeCutsTab module="meic" arm={name} />
      </div>
      <div id="meic-arm-analysis">
        <AnalysisSlide module="meic" mode={mode} arm={name} />
      </div>
      <div id="meic-arm-history">
        <MeicHistoryTable mode={mode} scope={scope} reasons={reasons} />
      </div>
    </div>
  );
}
