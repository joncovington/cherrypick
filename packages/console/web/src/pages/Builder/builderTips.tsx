import { useState } from "react";
import type { BuilderSuggestion } from "@console/shared";

/**
 * The Builder's help for someone new to it: a short note behind a ? on each part a newcomer would
 * ask about (`TIPS`, closed until clicked), whether the data we hold can backtest the automation
 * (`Coverage`, from the module's Check), and whether a time entry meets the one daily snapshot our
 * monthly history holds (`holdsCloseSnapshot`).
 */

/** Whether a time entry's window holds 15:59, the one moment the daily chain history has. Other kinds say nothing here. */
export function holdsCloseSnapshot(entry: Record<string, unknown>): boolean {
  if (entry["kind"] !== "time" || typeof entry["at"] !== "string") return true;
  const [h, m] = entry["at"].split(":").map(Number);
  if (h === undefined || m === undefined || Number.isNaN(h) || Number.isNaN(m)) return true;
  const start = h * 60 + m;
  const span = typeof entry["window"] === "number" ? entry["window"] : 5; // the engine's default
  return start <= 959 && 959 < start + span; // the engine's own test: at <= minute < at + window
}

/** What each part of the Builder means, for someone new to it. */
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
  pay: "The credit is what a credit structure takes in when it opens. Max loss is the most it can lose: for a spread, the gap between the strikes less the credit. Buying power is the cash your account sets aside to hold it. All three change daily with the market; Today, live shows them on today's chain.",
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

export function Tip({ id, open, onToggle, label, word }: { id: string; open: string | null; onToggle: (id: string) => void; label: string; word?: string }) {
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

export function TipText({ id, open }: { id: string; open: string | null }) {
  return open === id ? (
    <div className="wiz-tip" role="note">
      {TIPS[id]}
    </div>
  ) : null;
}

/** The backtest coverage: covered and over what, or each short need with why, and, where our stores
 *  would cover monthlies only, that offer. */
export function Coverage({ s, onMonthly }: { s: BuilderSuggestion | null | undefined; onMonthly?: () => void }) {
  if (!s) return <p className="muted">Check reads whether our data covers a backtest.</p>;
  const short = s.needs.filter((n) => !n.covered);
  const monthly = short.find((n) => n.monthly)?.monthly;
  return (
    <>
      <span className={s.possible ? "wiz-ok" : "wiz-warn"}>
        {s.possible
          ? `Covered by ${s.licensed ? "the licensed vendor store" : "our own data"}, ${s.window[0]} to ${s.window[1]}`
          : "Not covered by the data we hold yet"}
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

/** One ? open at a time: `open` is its id, `toggle` opens one or closes it. */
export function useTips(): { open: string | null; toggle: (id: string) => void } {
  const [open, setOpen] = useState<string | null>(null);
  return { open, toggle: (id) => setOpen(open === id ? null : id) };
}
