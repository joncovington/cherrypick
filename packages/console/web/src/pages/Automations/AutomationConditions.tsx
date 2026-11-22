import type { AutomationConditions, ConditionsLabel } from "@console/shared";
import { Card } from "../../components/DataTable";
import { HelpSection, type CardHelpContent } from "../../components/CardHelp";
import { Section } from "../../components/Section";
import { TodayConditionsCard } from "../../components/RegimeCutsTab";
import { useAutomationConditions } from "../../lib/api";

/**
 * "Today's conditions" on one automation's page: its record in the kind of market each underlying is
 * in today. The automations package labels every closed trade's entry session by its underlying's
 * daily-bar trend and volatility and `core.regimecuts` assembles and stamps the cut (`cli conditions`);
 * the card is MEIC's, fed the same document shape. The page words the labels and computes nothing.
 */

const HELP: CardHelpContent = {
  title: "Today's conditions",
  body: (
    <>
      <HelpSection title="Which reading">
        <p>
          Each underlying&apos;s daily bars up to the last full session: its trend (price against a rising or falling average, confirmed by higher
          or lower highs and lows) and its volatility (its average true range against its own past year, high or low). Today&apos;s own bar is never
          read, and each past trade is labelled the same way from the bars before the session it entered.
        </p>
      </HelpSection>
      <HelpSection title="The automation in that bucket">
        <p>
          Its net, sessions and mean trade among the trades it entered in the same kind of market, over the module&apos;s current era. A figure shows
          only where the cut can carry it: a dimension with too few sessions, one where every session falls in one bucket, and a bucket with too few
          sessions of its own are named with the reason. Context for the day, not a forecast and not a verdict.
        </p>
      </HelpSection>
    </>
  ),
};

const DIM_WORDS: Record<string, string> = { trend: "trend", vol: "volatility" };

/** One underlying's label in words, or why it has none. */
export function labelWords(symbol: string, l: ConditionsLabel): string {
  if (l.status === "unmeasured") return `${symbol} has no label today: ${l.reason ?? "its daily bars cannot be read"}`;
  return `${symbol} ${l.trend ?? "?"}, ${l.vol ?? "?"} volatility`;
}

/** The card's lead: today's label for each underlying and the bar it reads through. */
export function conditionsLead(name: string, data: AutomationConditions): string {
  const entries = Object.entries(data.labels);
  if (entries.length === 0) return `No underlying of ${name} has daily bars to label today.`;
  const through = entries.map(([, l]) => l.through).find((t) => typeof t === "string") ?? null;
  const words = entries.map(([s, l]) => labelWords(s, l)).join("; ");
  return `Today, from daily bars${through !== null ? ` through ${through}` : ""}: ${words}. How ${name} has done when it entered in the same kind of market:`;
}

export function ConditionsBody({ name, data }: { name: string; data: AutomationConditions | undefined }) {
  if (data === undefined) {
    return (
      <Card title="today's conditions" collapseKey="automations-automation-conditions" className="lv-12" help={HELP}>
        <p className="muted">Reading today&apos;s conditions…</p>
      </Card>
    );
  }
  if (data.error !== null || data.cuts === null) {
    return (
      <Card title="today's conditions" collapseKey="automations-automation-conditions" className="lv-12" help={HELP}>
        <p className="error">{data.error ?? "today's conditions could not be read"}</p>
      </Card>
    );
  }
  return (
    <TodayConditionsCard
      cuts={data.cuts}
      arm={name}
      lead={conditionsLead(name, data)}
      help={HELP}
      dimWords={DIM_WORDS}
      none="No underlying has a label today, so there is no bucket to read its record in."
    />
  );
}

export function ConditionsSection({ name }: { name: string }) {
  const q = useAutomationConditions(name);
  return (
    <Section id="automation-conditions" concept="room" tag="today's conditions" question="How has it done in markets like today's?">
      <ConditionsBody name={name} data={q.data} />
    </Section>
  );
}
