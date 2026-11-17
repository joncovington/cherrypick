import { useEffect, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { BuilderAutomation, BuilderSchema, BuilderSuggestion, BuilderToday } from "@console/shared";
import { builderBacktest, builderDraft, builderSave, builderToday } from "../../lib/api";
import { EntryFields, ExitFields, GateFields, LegFields, SizeFields, UnderlyingFields, entryText, type Spec } from "./BuilderForm";
import { TodayView } from "./BuilderToday";

/**
 * The Builder's wizard (#1180, the approved mockup's eight artboards): a new automation built one
 * decision at a time, for someone without much backtesting behind them. The steps are fixed and
 * listed with the current one marked and finished ones ticked; each step shows a few fields, an
 * "About this step" panel beside them and a ? on every part that opens a short plain-language note
 * below it. Leaving a step asks the automations module's own Check and shows only that step's (and
 * earlier steps') problems. Every field writes the same spec the full form does, which is one click
 * away. Saving is the form's save: always off.
 */

export const STEPS = [
  { id: "start", label: "Start" },
  { id: "underlying", label: "Underlying and expiry" },
  { id: "legs", label: "Legs" },
  { id: "entry", label: "Entry" },
  { id: "gates", label: "Gates", optional: true },
  { id: "exits", label: "Exits and size" },
  { id: "review", label: "Review" },
] as const;
export type StepId = (typeof STEPS)[number]["id"];

/** The spec keys each step writes: a problem about one of them belongs to that step. */
export const STEP_KEYS: Record<StepId, string[]> = {
  start: ["name"],
  underlying: ["symbol", "expiry", "weekdays", "skip_events"],
  legs: ["legs"],
  entry: ["entry"],
  gates: ["gates"],
  exits: ["exit", "size", "limits"],
  review: [],
};

/** The spec key a module problem is about, or null when it names none. */
export function problemKey(name: string, problem: string): string | null {
  if (problem.startsWith("name:")) return "name";
  if (problem.startsWith(`${name}.`)) return problem.slice(name.length + 1).split(/[.[:\s]/)[0] ?? null;
  const quoted = /^[^:]+: (?:missing|unknown key) '([^']+)'/.exec(problem);
  return quoted ? quoted[1]! : null;
}

/** The problems that stop leaving `step`: those about its keys or an earlier step's. */
export function problemsUpTo(step: StepId, name: string, problems: string[]): string[] {
  const upto = STEPS.slice(0, STEPS.findIndex((s) => s.id === step) + 1).flatMap((s) => STEP_KEYS[s.id]);
  return problems.filter((p) => {
    const k = problemKey(name, p);
    return k !== null && upto.includes(k);
  });
}

/** Whether a time entry's window holds 15:59, the one moment the daily chain history has. Other kinds say nothing here. */
export function holdsCloseSnapshot(entry: Record<string, unknown>): boolean {
  if (entry["kind"] !== "time" || typeof entry["at"] !== "string") return true;
  const [h, m] = entry["at"].split(":").map(Number);
  if (h === undefined || m === undefined || Number.isNaN(h) || Number.isNaN(m)) return true;
  const start = h * 60 + m;
  const span = typeof entry["window"] === "number" ? entry["window"] : 5; // the engine's default
  return start <= 959 && 959 < start + span; // the engine's own test: at <= minute < at + window
}

type Obj = Record<string, unknown>;
const obj = (v: unknown): Obj => (typeof v === "object" && v !== null && !Array.isArray(v) ? (v as Obj) : {});
const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"];

/** The automation block by block, in plain English, as the review step reads it. */
export function plainBlocks(spec: Spec): Record<"structure" | "entry" | "gates" | "exit" | "size", string> {
  const e = obj(spec["expiry"]);
  const expiry =
    e["kind"] === "between"
      ? `the listed ${e["monthly"] ? "monthly " : ""}expiry ${String(e["min"])}-${String(e["max"])} days out, ${e["prefer"] === "last" ? "the farthest" : "the nearest"} first`
      : e["kind"] === "at_least"
        ? `the nearest listed ${e["monthly"] ? "monthly " : ""}expiry at least ${String(e["days"])} days out`
        : e["kind"] === "market_days"
          ? `the expiry ${String(e["days"])} trading days out`
          : `the expiry ${String(e["days"] ?? "?")} days out`;
  const legs = list(spec["legs"]).map(obj);
  const legText = legs
    .map((l) => {
      const s = obj(l["strike"]);
      const how = s["rule"] === "delta" ? `nearest ${String(s["target"])} delta` : `by ${String(s["rule"] ?? "?").replaceAll("_", " ")}`;
      return `${Number(l["side"]) < 0 ? "sell" : "buy"} the ${l["right"] === "C" ? "call" : "put"} ${how}`;
    })
    .join(", ");
  const days = list(spec["weekdays"]).map(Number);
  const skip = list(obj(spec["skip_events"])["labels"]).map(String);
  const gates = list(spec["gates"]).map(obj);
  const gateText = gates.length
    ? gates
        .map((g) => {
          const lo = g["min"] !== undefined ? ` at least ${String(g["min"])}` : "";
          const hi = g["max"] !== undefined ? ` at most ${String(g["max"])}` : "";
          return `${String(g["gate"]).replaceAll("_", " ")}${lo}${hi}`;
        })
        .join("; ")
    : "None: every eligible day is entered.";
  const x = obj(spec["exit"]);
  const exits: string[] = [];
  if (x["profit_target_pct"] !== undefined) exits.push(`take ${String(x["profit_target_pct"])}% of the most it can make`);
  if (x["stop_credit_multiple"] !== undefined) exits.push(`stop at a loss of ${String(x["stop_credit_multiple"])}x the credit`);
  if (x["close_at_dte"] !== undefined) exits.push(`close at ${String(x["close_at_dte"])} days to expiry`);
  const others = Object.keys(x).filter((k) => !["profit_target_pct", "stop_credit_multiple", "close_at_dte"].includes(k));
  if (others.length) exits.push(`also ${others.join(", ").replaceAll("_", " ")}`);
  const size = obj(spec["size"]);
  const lim = obj(spec["limits"]);
  const sizeText = size["contracts"] !== undefined ? `${String(size["contracts"])} contract${size["contracts"] === 1 ? "" : "s"}` : `sized by ${Object.keys(size)[0] ?? "?"}`;
  return {
    structure: `${String(spec["symbol"] ?? "?")}. ${expiry[0]!.toUpperCase()}${expiry.slice(1)}. ${legText ? legText[0]!.toUpperCase() + legText.slice(1) : "No legs yet"}.`,
    entry: `${entryText(obj(spec["entry"]))} ${days.length === 5 ? "Monday to Friday" : days.map((d) => DAYS[d]).join(", ")}${skip.length ? `, skipping ${skip.join(", ")} days` : ""}.`,
    gates: gateText,
    exit: exits.length ? `${exits.join("; ")[0]!.toUpperCase()}${exits.join("; ").slice(1)}.` : "No exit set: positions are held to expiry.",
    size: `${sizeText}; at most ${String(lim["max_open"] ?? "?")} open and ${String(lim["per_day"] ?? "?")} a day.`,
  };
}

/** What each part of each step means, for someone new to it. */
export const TIPS: Record<string, string> = {
  name: "A short name for this automation, used everywhere it shows: the console, the paper ledger and its backtest runs. Lower case letters, digits and hyphens.",
  shape:
    "A shape is the set of option legs the automation trades. A credit spread sells one option and buys a cheaper one further from the price: you collect money up front, and the option you bought caps how much you can lose. Undefined risk (the short put) means no bought option caps the loss, so it needs more cash set aside.",
  copy: "Starts from another automation's settings under a new name. The copy counts as new: none of its parts are treated as backtested, whatever the original showed.",
  sym: "The underlying: the stock, fund or index the options are written on. SPY is the S&P 500 fund; SPX is the index itself, cash-settled. The symbol decides which option chains and which data the automation reads.",
  exp: 'Every option has an expiry date. Days to expiry is how far away it is. Selling 45-60 days out collects more premium per trade and gives the trade time; selling close to expiry collects less but decays faster. "Between" picks the listed expiry inside the window. "Monthly" keeps to the standard monthly expiries (the third Friday): our own daily history holds those, not the weeklies.',
  ev: "Some days carry scheduled news: a Fed decision (FOMC), a jobs report (NFP), a producer-price print (PPI). Prices can jump on them. Skipping them means the automation does not open a trade that day. A day the calendar has no data for passes, recorded as unknown.",
  cov: "A backtest replays the automation over past market data to see how it would have done. It can only replay days we hold the right data for: the option chain for its expiries, at the time it would trade. Coverage tells you, before you build the rest, whether that data exists. A backtest is a description of the past, never a promise.",
  legs: 'Each leg is one option. Side: sell (you collect its price) or buy (you pay it). Right: a put gains as the price falls, a call as it rises. "Strike by" picks the strike on the day it enters, since the right strike moves with the market. Match says which listed strike to take when the exact target is not listed.',
  delta: "Delta is roughly the chance the option ends in the money, so a 0.20-delta put has about a 1 in 5 chance of finishing below its strike. A lower delta sits farther from the price: safer, but it collects less.",
  pay: "The credit is what a credit structure takes in when it opens. Max loss is the most it can lose: for a spread, the gap between the strikes less the credit. Buying power is the cash your account sets aside to hold it. All three change daily with the market; Today, live on the review step shows them on today's chain.",
  kind: 'Start with "time" if you are new: it is the easiest to read and to backtest. An opening range (orb) waits for the price to break out of its first minutes. A trigger waits for a signal you choose, such as a moving-average cross; each signal is another assumption a backtest has to support.',
  cad: 'A backtest can only act on moments it has prices for. For monthly expiries our history holds one snapshot of the option chain per day, at 15:59. An entry earlier in the day never meets a snapshot, so the backtest counts it "needs intraday" rather than pretend it filled at the close. Paper uses live prices and is not affected.',
  gates: "A gate is an optional check that skips the day when it fails, and the day records which gate stopped it and the value it read. Market and chain gates run before the legs are chosen; price gates run after.",
  more: "Each gate is a rule you have chosen, and with enough rules any backtest can be made to look good on the past while saying nothing about the future. That is called overfitting. Add a gate only for a reason you believe before you see a result, and expect each one to cut the number of trades.",
  rules:
    "Taking half the credit banks most of the gain early. The stop limits a bad trade to about what it could have earned. Closing at 21 days avoids the last weeks, when a small move near the strike can swing the price sharply. The engine has no fallback stop for a spread whose credit is more than half its width. A backtest shows what these rules did, not that they will keep working.",
  size: "How many to trade each time. Start with 1 contract until the paper record has run a while. A risk budget sizes to the largest count whose max loss fits a dollar amount; a percent of an allocation does the same against a share of your capital.",
  lim: "Safety caps the automation must state: how many positions it may hold at once (max open) and how many it may open in a day (per day, always 1). The engine never assumes a default, so a mistake cannot run without a cap.",
  cost: "Every fill costs a little beyond the quoted mid: that is slippage, modelled here from the bid-ask spread. Fees are the broker's and exchange's charges. Both come off every trade, so P&L is shown after them, never before.",
  label:
    '"Not backtested" means no study has run this part, so nothing yet says it works. It can still go on paper to collect a live record, but read that record as a description until its forward check is done.',
  check: "The Check asks the automations module whether the paper loop would accept this automation: every setting known, every value of the right kind, every leg placeable. It says the automation will run. It says nothing about whether it will make money.",
  today: "The engine decides this automation right now, the way the paper loop would, on today's prices, and shows every check it read. It writes nothing and places nothing: a preview of the rules, not a trade.",
  bt: "A backtest replays the rules over past prices. Read it as one possible past, not a forecast: a good result may be luck or a rule fitted to that history. The suite judges a result only through a research item whose test and pass bar are written down before it runs.",
  off: "A new automation is always saved off, so building one never starts trading by accident. Turning it on puts it on paper (simulated trades, no real money) and is a deliberate separate step: a hand edit or its group's switch.",
  run: "An explore run backtests the saved automation on the history our data covers and shows how it would have done, trade by trade. It is a look, not a verdict: every version you try is another look, and the more you look, the more likely a good result is luck. A real test is a research item that writes the rule down first and counts the looks.",
};

/** Each step's question and the line under it. */
const HEAD: Record<StepId, [string, string]> = {
  start: ["What shape will it trade?", "Pick a shape to start from. Its legs, exits and limits are filled in for you, and every one can be changed in the steps that follow."],
  underlying: ["What does it trade, and which expiry?", "The symbol and the expiry rule. The coverage below says whether our own data can backtest this, before you build the rest."],
  legs: ["Which strikes?", "The legs come filled in from the shape. Each leg is placed by a rule, mostly by delta; change the rule or the target, or add a leg."],
  entry: ["When does it enter?", "At a time each eligible day, on the first break of the opening range, or when a signal fires."],
  gates: [
    "Anything that should stop an entry?",
    "Gates are optional checks; the first one that fails skips the day, and the day records which gate and the value it read. Every gate you add is another condition to backtest, so add only what you mean to test.",
  ],
  exits: ["How does it get out, and how big?", 'A credit trade starts from the credit-trade rules. Keep them, or switch on "customise" to set each exit yourself.'],
  review: ["", "Read it as the engine will. Edit any line to go back to its step; your other answers are kept."],
};

/** The step's own explanation, in the panel beside it. */
const ABOUT: Record<StepId, string> = {
  start: "A shape is a starting point, not a strategy: it sets the legs, and every one can be changed in the steps that follow. A copy starts from another automation's settings.",
  underlying: "What it trades and which expiry. The coverage below says whether our own data can backtest this, before you build the rest.",
  legs: "The legs come from the shape. Each is placed by a rule, here mostly by delta; change the rule or the target, or add a leg.",
  entry: "When it opens a trade: at a time, on an opening-range break, or when a signal fires.",
  gates: "Optional checks that skip a day. Every gate is another condition to backtest, so add only what you mean to test.",
  exits: "How a trade ends and how big it is. A credit shape starts from the credit-trade rules.",
  review: "Read it as the engine will. Edit any block to go back to its step; your other answers are kept.",
};

function Tip({ id, open, onToggle, label, word }: { id: string; open: string | null; onToggle: (id: string) => void; label: string; word?: string }) {
  const q = (
    <button type="button" className={open === id ? "wiz-q is-open" : "wiz-q"} aria-label={label} aria-expanded={open === id} onClick={() => onToggle(id)}>
      ?
    </button>
  );
  return word ? (
    <span className="wiz-qw">
      {word}
      {q}
    </span>
  ) : (
    q
  );
}

function TipText({ id, open }: { id: string; open: string | null }) {
  return open === id ? (
    <div className="wiz-tip" role="note">
      {TIPS[id]}
    </div>
  ) : null;
}

/** The backtest coverage: covered and over what, or each short need with why, and, where our stores
 *  would cover monthlies only, that offer. */
export function Coverage({ s, onMonthly }: { s: BuilderSuggestion | null | undefined; onMonthly?: () => void }) {
  if (!s) return <p className="muted">Coverage is read when you leave the first step.</p>;
  const short = s.needs.filter((n) => !n.covered);
  const monthly = short.find((n) => n.monthly)?.monthly;
  return (
    <>
      <span className={s.possible ? "wiz-ok" : "wiz-warn"}>
        {s.possible
          ? `Covered by ${s.licensed ? "the licensed vendor store" : "our own data"}, ${s.window[0]} to ${s.window[1]}`
          : "Not covered by our own data yet"}
      </span>
      {short.map((n) => (
        <p key={n.field} className="muted">
          {n.field}
          {n.expiry ? ` (${n.expiry})` : ""} at {n.cadence}
          {n.nearest
            ? `: nearest ${n.nearest.source} ${n.nearest.first ?? ""} to ${n.nearest.last ?? ""}${n.nearest.why ? `, ${n.nearest.why}` : ""}`
            : n.why
              ? `: ${n.why}`
              : ""}
        </p>
      ))}
      {monthly && (
        <p className="wiz-row">
          Monthlies only would be covered by {monthly.source}
          {monthly.first ? ` from ${monthly.first}` : ""}.
          {onMonthly && (
            <button type="button" className="btn wiz-inline-btn" onClick={onMonthly}>
              Use monthlies only
            </button>
          )}
        </p>
      )}
    </>
  );
}

const HIDDEN = new Set(["name", "register", "enabled"]);
const editable = (s: Obj): Spec => Object.fromEntries(Object.entries(s).filter(([k]) => !HIDDEN.has(k)));

export function BuilderWizard({
  schema,
  automations,
  copyFrom,
  onCancel,
  onAnother,
  onOpenForm,
  onSaved,
  openAt = "start",
}: {
  schema: BuilderSchema;
  automations: BuilderAutomation[];
  copyFrom?: string | null;
  onCancel: () => void;
  onAnother: () => void;
  onOpenForm: (name: string, spec: Spec) => void;
  onSaved: (name: string) => void;
  /** The step it opens on, every earlier one counted as reached. The page always opens at the start. */
  openAt?: StepId;
}) {
  const qc = useQueryClient();
  const firstShape = Object.keys(schema.shapes).includes("put_credit_spread") ? "put_credit_spread" : Object.keys(schema.shapes)[0]!;
  const opening = STEPS.findIndex((s) => s.id === openAt);
  const [step, setStep] = useState(opening);
  const [reached, setReached] = useState(opening);
  const [name, setName] = useState(copyFrom ? `${copyFrom}-copy` : "my-automation");
  const [shape, setShape] = useState<string | null>(copyFrom ? null : firstShape);
  const [source, setSource] = useState<string>(copyFrom ?? "");
  const [spec, setSpec] = useState<Spec>(() => (copyFrom ? {} : structuredClone(schema.shapes[firstShape]!.spec)));
  const [tip, setTip] = useState<string | null>(null);
  const [customise, setCustomise] = useState(false);
  const [problems, setProblems] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [suggestion, setSuggestion] = useState<BuilderSuggestion | null>(null);
  const [today, setToday] = useState<BuilderToday | null>(null);
  const [saved, setSaved] = useState<{ added: boolean } | null>(null);
  const [queued, setQueued] = useState<string | null>(null);
  const current = STEPS[step]!;
  const toggle = (id: string) => setTip((t) => (t === id ? null : id));

  useEffect(() => {
    if (!source) return;
    let live = true;
    void builderDraft(source).then((r) => {
      if (live && r.ok && r.automation) setSpec(editable(r.automation));
      else if (live) setError(r.error ?? `${source} could not be read`);
    });
    return () => {
      live = false;
    };
  }, [source]);

  useEffect(() => {
    if (current.id !== "review") return;
    let live = true;
    void builderToday(name, spec).then((r) => {
      if (live) setToday(r.error ? { name, state: "not_decided", reason: r.error } : r);
    });
    return () => {
      live = false;
    };
    // Read once on arriving at the review; the spec cannot change there.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current.id]);

  const go = (to: number) => {
    setStep(to);
    setReached((r) => Math.max(r, to));
    setTip(null);
    setProblems([]);
    setError(null);
  };

  /** Ask the module, and move forward to `to` only when every step before it is clean: Next, Skip
   * gates and a forward click in the steps list all pass through here, so the review's Check holds. */
  const advance = async (to: number = step + 1) => {
    setBusy(true);
    const r = await builderDraft(name, spec);
    setBusy(false);
    if (!r.ok) {
      setError(r.error ?? "the check failed");
      return;
    }
    const mine = problemsUpTo(STEPS[to - 1]!.id, name, r.problems ?? []);
    if (mine.length) {
      setProblems(mine);
      return;
    }
    const sug = (r.view as { suggestion?: BuilderSuggestion | null } | undefined)?.suggestion;
    if (sug !== undefined) setSuggestion(sug);
    go(to);
  };

  /** Switch the expiry rule to monthlies only and read the coverage again for the new rule. */
  const useMonthly = async () => {
    const next = { ...spec, expiry: { ...obj(spec["expiry"]), monthly: true } };
    setSpec(next);
    const r = await builderDraft(name, next);
    const sug = (r.view as { suggestion?: BuilderSuggestion | null } | undefined)?.suggestion;
    if (r.ok && sug !== undefined) setSuggestion(sug);
  };

  const save = async () => {
    setBusy(true);
    const r = await builderSave(name, spec);
    setBusy(false);
    if (!r.ok) {
      setProblems(r.problems ?? []);
      setError(r.problems?.length ? null : (r.error ?? "the save failed"));
      return;
    }
    setSaved({ added: r.added === true });
    void qc.invalidateQueries({ queryKey: ["automations-builder"] });
  };

  const explore = async () => {
    setBusy(true);
    const r = await builderBacktest(name);
    setBusy(false);
    setQueued(r.ok ? `Queued ${r.run} (task ${r.task ?? "?"}), ${r.window?.[0]} to ${r.window?.[1]}.` : `Not queued: ${r.error}`);
  };

  if (saved) {
    return (
      <div className="wiz-done" data-wizard="saved">
        <div className="wiz-mark" aria-hidden="true">
          ✓
        </div>
        <h2 className="wiz-h">Saved {name}, off</h2>
        <p className="wiz-lead">
          It is in the automations config, written through the suite's config editor with a backup. Nothing trades it: it is off until you turn it on.
          <Tip id="off" open={tip} onToggle={toggle} label="Why is it saved off?" />
        </p>
        <TipText id="off" open={tip} />
        <div className="wiz-row">
          <span className="builder-lbl">What next</span>
          <Tip id="run" open={tip} onToggle={toggle} label="What is an explore run?" />
        </div>
        <TipText id="run" open={tip} />
        <div className="builder-editor-actions">
          <button type="button" className="btn btn-primary" disabled={busy || !suggestion?.possible} title={suggestion?.possible ? undefined : "our own data does not cover it"} onClick={() => void explore()}>
            Explore run
          </button>
          <button type="button" className="btn" onClick={() => onSaved(name)}>
            Open in the form
          </button>
          <button type="button" className="btn" onClick={onAnother}>
            Build another
          </button>
        </div>
        {!suggestion?.possible && <p className="muted">Explore run waits until our own data covers every block; the coverage on step 2 says what is missing.</p>}
        {queued && (
          <p className="builder-check" role="status">
            {queued}
          </p>
        )}
      </div>
    );
  }

  const blocks = plainBlocks(spec);
  const sectionProps = { schema, spec, onChange: (s: Spec) => setSpec(s) };
  const body: Record<StepId, ReactNode> = {
    start: (
      <>
        <div className="wiz-field">
          <div className="wiz-row">
            <label className="builder-lbl" htmlFor="wiz-name">
              Name
            </label>
            <Tip id="name" open={tip} onToggle={toggle} label="What is the name for?" />
          </div>
          <input id="wiz-name" className="builder-input" value={name} onChange={(e) => setName(e.currentTarget.value)} />
          <TipText id="name" open={tip} />
        </div>
        <div className="wiz-row">
          <span className="builder-lbl">Shape</span>
          <Tip id="shape" open={tip} onToggle={toggle} label="What is a shape?" />
        </div>
        <TipText id="shape" open={tip} />
        <div className="wiz-shapes" role="radiogroup" aria-label="Shape">
          {Object.entries(schema.shapes).map(([k, s]) => (
            <button
              key={k}
              type="button"
              role="radio"
              aria-checked={shape === k}
              className={shape === k ? "wiz-shape is-on" : "wiz-shape"}
              onClick={() => {
                setShape(k);
                setSource("");
                setSpec(structuredClone(s.spec));
              }}
            >
              <span className="wiz-shape-t">{s.label}</span>
              <span className="wiz-shape-d">{s.about}</span>
              <span className={s.risk.startsWith("undefined") ? "wiz-warn" : "muted"}>{s.risk}</span>
            </button>
          ))}
        </div>
        <div className="wiz-field">
          <div className="wiz-row">
            <label className="builder-lbl" htmlFor="wiz-copy">
              or copy an existing automation
            </label>
            <Tip id="copy" open={tip} onToggle={toggle} label="What does copying do?" />
          </div>
          <select
            id="wiz-copy"
            className="builder-input"
            value={source}
            onChange={(e) => {
              const v = e.currentTarget.value;
              setSource(v);
              if (v) {
                setShape(null);
                setName(`${v}-copy`);
              }
            }}
          >
            <option value="">choose one…</option>
            {automations.map((a) => (
              <option key={a.name} value={a.name}>
                {a.name}
              </option>
            ))}
          </select>
          <TipText id="copy" open={tip} />
        </div>
      </>
    ),
    underlying: (
      <>
        <div className="wiz-row">
          <span className="builder-lbl">Symbol, expiry and days</span>
          <Tip id="sym" open={tip} onToggle={toggle} label="What is the symbol?" word="symbol" />
          <Tip id="exp" open={tip} onToggle={toggle} label="What is the expiry rule?" word="expiry" />
          <Tip id="ev" open={tip} onToggle={toggle} label="Why skip event days?" word="event days" />
        </div>
        <TipText id="sym" open={tip} />
        <TipText id="exp" open={tip} />
        <TipText id="ev" open={tip} />
        <UnderlyingFields {...sectionProps} />
        <div className="wiz-cov" role="status">
          <div className="wiz-row">
            <span className="builder-lbl">Backtest coverage, our own data</span>
            <Tip id="cov" open={tip} onToggle={toggle} label="What is a backtest, and why does coverage matter?" />
          </div>
          <TipText id="cov" open={tip} />
          <Coverage s={suggestion} onMonthly={() => void useMonthly()} />
        </div>
      </>
    ),
    legs: (
      <>
        <div className="wiz-row">
          <span className="builder-lbl">Legs</span>
          <Tip id="legs" open={tip} onToggle={toggle} label="What are side, right, strike rule and match?" word="the fields" />
          <Tip id="delta" open={tip} onToggle={toggle} label="What does delta mean?" word="delta" />
          <Tip id="pay" open={tip} onToggle={toggle} label="What are credit, max loss and buying power?" word="credit and max loss" />
        </div>
        <TipText id="legs" open={tip} />
        <TipText id="delta" open={tip} />
        <TipText id="pay" open={tip} />
        <LegFields {...sectionProps} />
        <div className="builder-plain">
          <span className="builder-lbl">in plain English</span>
          <p>{blocks.structure}</p>
        </div>
      </>
    ),
    entry: (
      <>
        <div className="wiz-row">
          <span className="builder-lbl">How it enters</span>
          <Tip id="kind" open={tip} onToggle={toggle} label="Which kind of entry should I choose?" word="which kind" />
          <Tip id="cad" open={tip} onToggle={toggle} label="Which entry times can be backtested?" word="backtestable times" />
        </div>
        <TipText id="kind" open={tip} />
        <TipText id="cad" open={tip} />
        <EntryFields {...sectionProps} />
        {!holdsCloseSnapshot(obj(spec["entry"])) && (
          <p className="wiz-warn" role="status">
            This entry never meets the 15:59 daily snapshot, so a backtest on monthly expiries counts its days as needing intraday data. Paper is not affected.
          </p>
        )}
      </>
    ),
    gates: (
      <>
        <div className="wiz-row">
          <span className="builder-lbl">Market and chain gates · before the legs</span>
          <Tip id="gates" open={tip} onToggle={toggle} label="What is a gate?" word="what a gate is" />
          <Tip id="more" open={tip} onToggle={toggle} label="Should I add more gates?" word="how many" />
        </div>
        <TipText id="gates" open={tip} />
        <TipText id="more" open={tip} />
        <GateFields {...sectionProps} stages={["signal", "chain"]} prefix="wiz-gate" />
        <span className="builder-lbl">Price gates · after the legs</span>
        <GateFields {...sectionProps} stages={["structure"]} prefix="wiz-sgate" />
      </>
    ),
    exits: (
      <>
        <div className="wiz-preset">
          <div className="wiz-row">
            <strong>{shape && schema.shapes[shape]?.credit ? "Your credit-trade rules" : "Exits"}</strong>
            <Tip id="rules" open={tip} onToggle={toggle} label="Why these rules?" />
            <label className="wiz-toggle">
              <input type="checkbox" checked={customise} onChange={(e) => setCustomise(e.currentTarget.checked)} /> customise
            </label>
          </div>
          <TipText id="rules" open={tip} />
          {customise ? <ExitFields {...sectionProps} /> : <p>{blocks.exit}</p>}
        </div>
        <div className="wiz-row">
          <span className="builder-lbl">Size and limits</span>
          <Tip id="size" open={tip} onToggle={toggle} label="How should I size it?" word="size" />
          <Tip id="lim" open={tip} onToggle={toggle} label="What are the limits?" word="limits" />
          <Tip id="cost" open={tip} onToggle={toggle} label="What are slippage and fees?" word="costs" />
        </div>
        <TipText id="size" open={tip} />
        <TipText id="lim" open={tip} />
        <TipText id="cost" open={tip} />
        <SizeFields {...sectionProps} />
        <p className="muted">Costs are not set here: fills at mid with the suite's slippage model, the suite's fees, P&amp;L net of both.</p>
      </>
    ),
    review: (
      <>
        <div className="wiz-row">
          <span className="builder-lbl">Each block, as the engine will read it</span>
          <Tip id="label" open={tip} onToggle={toggle} label="What does not backtested mean?" />
        </div>
        <TipText id="label" open={tip} />
        {(
          [
            ["Structure", "structure", 2],
            ["Entry", "entry", 3],
            ["Gates", "gates", 4],
            ["Exit", "exit", 5],
            ["Size", "size", 5],
          ] as const
        ).map(([label, key, to]) => (
          <div key={key} className="wiz-blk">
            <span className="builder-lbl">{label}</span>
            <span>{blocks[key]}</span>
            <span className="chip">not backtested</span>
            <button type="button" className="link-button" onClick={() => go(to)}>
              Edit
            </button>
          </div>
        ))}
        <div className="wiz-cards">
          <div className="wiz-card">
            <div className="wiz-row">
              <span className="builder-lbl">Check</span>
              <Tip id="check" open={tip} onToggle={toggle} label="What does the Check prove?" />
            </div>
            <TipText id="check" open={tip} />
            <span className="wiz-ok">✓ No problems</span>
            <p className="muted">The automations module would load it as it stands.</p>
          </div>
          <div className="wiz-card">
            <div className="wiz-row">
              <span className="builder-lbl">Backtest</span>
              <Tip id="bt" open={tip} onToggle={toggle} label="How should I read a backtest?" />
            </div>
            <TipText id="bt" open={tip} />
            <Coverage s={suggestion} />
          </div>
          <div className="wiz-card wiz-card-wide">
            <div className="wiz-row">
              <span className="builder-lbl">Today, live</span>
              <Tip id="today" open={tip} onToggle={toggle} label="What is Today, live?" />
            </div>
            <TipText id="today" open={tip} />
            {today ? <TodayView t={today} /> : <p className="muted">Reading…</p>}
          </div>
        </div>
      </>
    ),
  };

  return (
    <div className="wiz" data-wizard={current.id}>
      <div className="wiz-top">
        <span>
          New automation{name ? ` · ${name}` : ""}
        </span>
        <span className="chip">
          step {step + 1} of {STEPS.length}
        </span>
        <span className="chip">not saved</span>
        <span className="builder-actions-gap" />
        <button type="button" className="link-button" onClick={() => onOpenForm(name, spec)}>
          Open in the full form
        </button>
      </div>
      <div className="wiz-grid">
        <nav className="wiz-steps" aria-label="Steps">
          {STEPS.map((s, i) => {
            const done = i < step || (i <= reached && i !== step);
            const cls = i === step ? "wiz-st is-current" : done ? "wiz-st is-done" : "wiz-st";
            return (
              <button key={s.id} type="button" className={cls} disabled={i > reached || busy} aria-current={i === step ? "step" : undefined} onClick={() => (i <= step ? go(i) : void advance(i))}>
                <span className="wiz-st-n">{done ? "✓" : i + 1}</span>
                {s.label}
                {"optional" in s && s.optional ? <span className="wiz-st-opt">optional</span> : null}
              </button>
            );
          })}
        </nav>
        <main className="wiz-body">
          <p className="wiz-kick">
            Step {step + 1} · {current.label}
            {"optional" in current && current.optional ? " · optional" : ""}
          </p>
          <h2 className="wiz-h">{current.id === "review" ? `${name}, in plain English` : HEAD[current.id][0]}</h2>
          <p className="wiz-lead">{HEAD[current.id][1]}</p>
          {body[current.id]}
          {problems.length > 0 && (
            <div className="error" role="alert">
              Fix these before going on:
              <ul>
                {problems.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            </div>
          )}
          {error && (
            <div className="error" role="alert">
              {error}
            </div>
          )}
        </main>
        <aside className="wiz-side" aria-label="About this step">
          <h4>About this step</h4>
          <p>{ABOUT[current.id]}</p>
          <h4>So far</h4>
          <p className="wiz-so">
            {name} · {blocks.structure}
          </p>
        </aside>
      </div>
      <div className="wiz-foot">
        <button type="button" className="btn btn-quiet" onClick={onCancel}>
          Cancel
        </button>
        <span className="builder-actions-gap" />
        <button type="button" className="btn" disabled={step === 0} onClick={() => go(step - 1)}>
          Back
        </button>
        {current.id === "gates" && (
          <button type="button" className="btn btn-quiet" disabled={busy} onClick={() => void advance()}>
            Skip gates
          </button>
        )}
        {current.id === "review" ? (
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void save()}>
            Save (off)
          </button>
        ) : (
          <button type="button" className="btn btn-primary" disabled={busy || (step === 0 && !shape && !source)} onClick={() => void advance()}>
            Next: {STEPS[step + 1]!.label}
          </button>
        )}
      </div>
    </div>
  );
}
