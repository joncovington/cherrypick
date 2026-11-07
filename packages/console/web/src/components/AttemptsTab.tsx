import type { TradingMode } from "@console/shared";
import { ArmRail, AttemptTimeline, NO_ATTEMPTS_SCOPE, type AttemptsModule, type AttemptsScope } from "./Attempts";
import { OccupancyMap } from "./OccupancyMap";
import { Section } from "./Section";

/**
 * The attempts tab every module with entry attempts shares (2026-11-07): the arm cards, the timeline
 * and, for the modules that hold strikes, the occupancy map, each under the question it answers.
 * One place, so flies, MEIC and pmcc read alike.
 */
export function AttemptsTab({
  module,
  mode,
  date = null,
  scope = NO_ATTEMPTS_SCOPE,
}: {
  module: AttemptsModule;
  mode: TradingMode;
  date?: string | null;
  scope?: AttemptsScope;
}) {
  return (
    <div className="pf-layout">
      <Section concept="add" tag="entries" question="Which arms are entering, and which are held back?">
        <ArmRail module={module} mode={mode} date={date} scope={scope} />
      </Section>
      <Section concept="risk" tag="refusals" question="When was each entry tried, and what stopped it?">
        <AttemptTimeline module={module} mode={mode} date={date} scope={scope} />
      </Section>
      {(module === "meic" || module === "flies") && (
        <Section concept="room" tag="strikes" question="What does each arm hold, and where would a new leg be refused?">
          <OccupancyMap module={module} mode={mode} date={date} scope={scope} />
        </Section>
      )}
    </div>
  );
}
