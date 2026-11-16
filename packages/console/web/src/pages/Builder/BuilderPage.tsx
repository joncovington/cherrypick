import type { BuilderAutomation, BuilderBlock, BuilderSuggestion } from "@console/shared";
import { useAutomationsBuilder } from "../../lib/api";

/** What each block label says, in words. */
export const STATE_TEXT: Record<BuilderBlock["state"], string> = {
  backtested: "backtested",
  changed: "changed since its backtest",
  not_backtested: "not backtested",
};

const STATE_CHIP: Record<BuilderBlock["state"], string> = {
  backtested: "chip chip-ok",
  changed: "chip chip-warn",
  not_backtested: "chip",
};

function value(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

function Block({ b }: { b: BuilderBlock }) {
  const keys = Object.entries(b.keys);
  return (
    <div className="builder-block" data-block={b.block}>
      <div className="builder-block-head">
        <strong>{b.block}</strong> <span className={STATE_CHIP[b.state]}>{STATE_TEXT[b.state]}</span>
      </div>
      {keys.length === 0 ? (
        <div className="muted">none set</div>
      ) : (
        <dl className="builder-keys">
          {keys.map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>
                <code>{value(v)}</code>
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

/** "Our data covers a backtest" or which need falls short, and the nearest store. */
export function SuggestionLine({ s }: { s: BuilderSuggestion }) {
  if (s.possible) {
    return (
      <div className="builder-suggestion" data-possible="yes">
        Our own data covers a backtest of {s.blocks.join(", ")} from {s.window[0]} to {s.window[1]}.
      </div>
    );
  }
  const short = s.needs.filter((n) => !n.covered);
  return (
    <div className="builder-suggestion" data-possible="no">
      No backtest yet: our own data does not cover {s.window[0]} to {s.window[1]} for{" "}
      {short.map((n) => `${n.field}${n.expiry ? ` (${n.expiry})` : ""} at ${n.cadence}`).join(", ")}.
      {short.map((n) =>
        n.nearest ? (
          <div key={n.field} className="muted">
            nearest for {n.field}: {n.nearest.source} {n.nearest.first} to {n.nearest.last}
            {n.nearest.why ? ` (${n.nearest.why})` : ""}
          </div>
        ) : null,
      )}
    </div>
  );
}

export function BuilderCard({ a }: { a: BuilderAutomation }) {
  return (
    <section className="card builder-card" data-automation={a.name}>
      <header className="builder-card-head">
        <h3>{a.name}</h3>
        <span className="chip">{a.symbol}</span>
        {a.group && <span className="chip">group {a.group}</span>}
        <span className={a.enabled ? "chip chip-ok" : "chip"}>{a.enabled ? "on" : "off"}</span>
        {a.register && (
          <span className="chip">
            item {a.register.item}: {a.register.verdict.replace("_", " ")}
          </span>
        )}
      </header>
      {a.note && <p className="muted">{a.note}</p>}
      <p className="builder-summary">{a.summary}</p>
      <div className="builder-enrolment">
        <span>start {a.start ?? "not set"}</span>
        <span>forward check {a.forward ? `${value(a.forward["measure"])} of ${value(a.forward["field"])}, ${value(a.forward["min_obs"])} to read` : "not declared"}</span>
      </div>
      {a.suggestion && <SuggestionLine s={a.suggestion} />}
      <div className="builder-blocks">
        {a.blocks.map((b) => (
          <Block key={b.block} b={b} />
        ))}
      </div>
    </section>
  );
}

export function BuilderPage() {
  const q = useAutomationsBuilder();
  if (q.isLoading) return <div className="muted">Loading the automations…</div>;
  if (q.error) return <div className="error">Could not read the automations: {String(q.error)}</div>;
  const data = q.data;
  if (!data) return null;
  if (data.error) return <div className="error">{data.error}</div>;
  return (
    <div className="builder-page">
      <p className="muted">
        Each automation block by block. A block is backtested while it stands as the study ran it; a changed or new
        block is not, and a paper result on it is descriptive until its forward check is read.
      </p>
      {data.automations.map((a) => (
        <BuilderCard key={a.name} a={a} />
      ))}
    </div>
  );
}
