import { useRef, useState, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { EARNINGS_WEEK_DAYS, type EarningsCalendarRow, type EarningsTradeLeg } from "@console/shared";
import { useEarningsCalendar } from "../../lib/api";
import { Card } from "../../components/DataTable";
import { HelpSection, HelpWarn, type CardHelpContent } from "../../components/CardHelp";
import { PremiumSlider, defaultFloor, useMinPremium } from "../../components/PremiumSlider";
import {
  FilterGroup,
  FilterSortButton,
  FilterSortPanel,
  FilterSummary,
  FilterSwitch,
  SortSection,
  sortRows,
  useSortChoice,
  type SortOption,
} from "../../components/FilterSort";
import { ListViewContext, ViewSwitch, useViewChoice } from "../../components/ViewSwitch";
import { writePref } from "../../lib/prefs";
import { TradeCards, dayMonth, type TradeCardLeg, type TradeCardModel } from "../Live/TradeCard";
import { ScreenerLayout } from "./ScreenerParts";
import { Section } from "../../components/Section";

/**
 * The earnings calendar (2026-10-17; docs/history/earnings-screener-plan.md): every announcement in the
 * next 30 days, grouped by day. Read-only, and it decides nothing: `python -m cherrypick.technicals
 * earnings-calendar` joins every figure each evening.
 *
 * 2026-11-06: one row an announcement (rows by default, cards by the switch), each row opening its
 * card in place with what used to be hover-only: company, sector, IV rank, the close, the expected
 * move and its expiry, and the spread's legs with bid and ask, open interest, volume and whether they
 * pass. Nine filters and the sort sit behind filter & sort; the window stays as tabs. The no-edge note
 * is an amber flag, its text behind the ?.
 *
 * The direction read and strength are OURS (Bearing, from our bars, the PREVIOUS session's close);
 * the vendor's are kept in the file and never shown or filtered on. IV rank is the broker's, and it
 * picks the spread's side. The expected move is the broker's earnings rule, 0.85 x the ATM straddle
 * on the first expiry after the reaction.
 */

type Win = "today" | "7" | "30";
const WINDOWS: { key: Win; label: string }[] = [
  { key: "today", label: "Today" },
  { key: "7", label: "Next 7 days" },
  { key: "30", label: "Next 30 days" },
];
const STRONG_BULL = "sb";
const STRONG_BEAR = "sbe";
const DEFAULT_DIRS = [STRONG_BULL, STRONG_BEAR];
const LIST = "earnings-calendar";

function money(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** "2026-10-20" -> "Tuesday, Oct 20". */
function dayHeading(iso: string): string {
  const d = new Date(`${iso}T00:00:00Z`);
  return d.toLocaleDateString("en-US", { weekday: "long", month: "short", day: "numeric", timeZone: "UTC" });
}

function shortDate(iso: string | null): string {
  if (iso === null) return "—";
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

function addDays(iso: string, n: number): string {
  return new Date(Date.parse(`${iso}T00:00:00Z`) + n * 86_400_000).toISOString().slice(0, 10);
}

/** Today in New York, as an ISO date. */
function todayNy(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" }).format(new Date());
}

const readNote = (r: EarningsCalendarRow) =>
  r.readSession ? `Ours (Bearing), from our bars as of the ${shortDate(r.readSession)} close.` : "We hold no bars for this name, so no read.";

/** The report's time of day as a chip: confirmed, estimated from last quarter's, or unknown. */
function timingChip(r: EarningsCalendarRow): ReactNode {
  if (r.timing === null && r.usualTiming !== null) {
    return (
      <span
        key="t"
        className="chip"
        title={`Not confirmed: no source gives this date's time. ${r.usualTiming === "bmo" ? "Before the open" : "After the close"} is when the company reported last quarter, which held 97% of the time historically. No entry session is worked out from it.`}
      >
        {r.usualTiming.toUpperCase()} est.
      </span>
    );
  }
  if (r.timing === null) {
    return (
      <span key="t" className="chip" title="No source gives the time of day for this date, and no earlier report to estimate it from; never assumed">
        time unknown
      </span>
    );
  }
  return (
    <span key="t" className="chip" title={r.timing === "bmo" ? "Before the open" : "After the close"}>
      {r.timing.toUpperCase()}
    </span>
  );
}

function dateFlags(r: EarningsCalendarRow): ReactNode[] {
  const flags: ReactNode[] = [];
  if (r.dateConfirmed === false) {
    flags.push(
      <span key="est" className="chip chip-warn" title="The broker marks this date estimated, and Dolt has none">
        est.
      </span>,
    );
  }
  if (r.dateGapDays !== null && r.dateGapDays > 1) {
    flags.push(
      <span key="gap" className="chip chip-warn" title={`The broker's date and Dolt's are ${r.dateGapDays} days apart; the ${r.dateSource === "broker" ? "broker's" : "Dolt's"} is shown`}>
        dates differ
      </span>,
    );
  }
  return flags;
}

function legQuote(l: EarningsTradeLeg): string {
  const q = l.bid === null && l.ask === null ? "unquoted" : `${money(l.bid)} / ${money(l.ask)}`;
  return `${q} · OI ${l.openInterest === null ? "—" : l.openInterest.toLocaleString()} · vol ${l.volume === null ? "—" : l.volume.toLocaleString()}${l.passes === false ? " · fails" : ""}`;
}

/**
 * One announcement as a trade card, from the same row the calendar was built from: the read and the
 * spread's shape on the head, IV rank, close and the expected move on the side, strength as the bar,
 * and the spread's legs as the order chain (a trade read in regular hours has quotes; listed strikes
 * and target prices say which they are). Every figure is the calendar's; nothing is computed here but
 * the signs and the words.
 */
export function earningsModel(r: EarningsCalendarRow): TradeCardModel {
  const s = r.suggestion;
  const t = r.trade;
  const right = s?.right === "C" ? "C" : "P";
  const word = s ? `${s.kind} ${right === "C" ? "call" : "put"}` : null;
  const credit = s?.kind === "credit";
  let legs: TradeCardLeg[] = [];
  let strikes = "";
  let expiry: string | null = s?.expiry ?? null;
  let price = "no spread suggested";
  let cash: number | null = null;
  if (t !== null && s !== null) {
    expiry = t.expiry;
    const [first, second] = t.kind === "credit" ? [t.legs[1], t.legs[0]] : [t.legs[0], t.legs[1]];
    strikes = `${first?.strike}/${second?.strike}`;
    legs = (first && second ? [first, second] : t.legs).map((l) => ({
      buy: l.role === "long",
      text: `1 ${t.expiry} ${l.strike}${t.right}${l.bid !== null && l.ask !== null ? ` @ ${((l.bid + l.ask) / 2).toFixed(2)}` : ""}`,
      quote: legQuote(l),
    }));
    price = t.mid === null ? "unpriced" : `${t.mid.toFixed(2)} ${t.kind === "credit" ? "cr" : "db"}`;
    cash = t.mid === null ? null : (t.kind === "credit" ? 1 : -1) * t.mid * 100;
  } else if (s !== null && r.listed !== null) {
    const [long, short] = r.listed.strikes;
    const order = credit ? [short, long] : [long, short];
    const buys = credit ? [false, true] : [true, false];
    expiry = r.listed.expiry;
    strikes = `${order[0]}/${order[1]}`;
    legs = order.map((k, i) => ({ buy: buys[i] ?? false, text: `1 ${r.listed?.expiry} ${k}${right}`, quote: null }));
    price = "listed, not priced yet";
  } else if (s !== null) {
    const order = credit ? [s.short, s.long] : [s.long, s.short];
    const buys = credit ? [false, true] : [true, false];
    strikes = `≈${money(order[0])}/≈${money(order[1])}`;
    legs = order.map((k, i) => ({ buy: buys[i] ?? false, text: `1 ≈ ${money(k)}${right} (target)`, quote: null }));
    price = "target prices, not strikes";
  }
  const moveWord =
    r.emMovePct === null || r.emMove === null
      ? null
      : `±${r.emMovePct.toFixed(1)}%`;
  const strong = r.edgeLabel ?? "no read";
  const longMove = r.eventExpiryDays !== null && r.eventExpiryDays > EARNINGS_WEEK_DAYS;
  const sideMove =
    moveWord === null
      ? "no implied vol held for the event expiry"
      : `move ${moveWord} (±$${money(r.emMove)})${r.eventExpiry ? ` to the ${dayMonth(r.eventExpiry)} expiry${r.eventExpiryDays === null ? "" : ` +${r.eventExpiryDays}d`}` : ""} · 0.85 × straddle${longMove ? " · a longer move than the announcement's" : ""}`;
  const entry = r.entrySession ? `enter by the ${shortDate(r.entrySession)} close · reacts ${shortDate(r.reactionDay)}` : null;
  const liquid =
    t === null
      ? s === null
        ? "no spread suggested"
        : "liquidity read once the spread is priced"
      : t.passes === true
        ? "liquid: every leg passes"
        : t.passes === false
          ? "thin: the spread fails the liquidity bars"
          : "liquidity not read";
  const after = [liquid];
  if (t !== null && t.afterReport === true) after.push("read after the report: not an entry price");
  else if (expiry !== null && r.reactionDay) after.push(`expires ${dayMonth(expiry)}`);
  const width = t !== null && t.shareOfWidth !== null ? `${(100 * t.shareOfWidth).toFixed(0)}% of width` : null;
  const maxLoss = t !== null && t.maxLoss !== null ? `max loss $${money(t.maxLoss)}` : null;
  return {
    key: `${r.date}-${r.symbol}`,
    symbol: r.symbol,
    symbolHref: `/charts/technicals?symbol=${encodeURIComponent(r.symbol)}`,
    spot: r.company,
    shape: [strong, word && `${word} ${strikes}`, moveWord && `exp. move ${moveWord}`].filter(Boolean).join(" · "),
    chips: (
      <>
        {timingChip(r)}
        {dateFlags(r)}
        {r.edgeLabel !== null ? (
          <span
            className={r.edge === STRONG_BULL ? "chip chip-ok" : r.edge === STRONG_BEAR ? "chip chip-bad" : "chip"}
            title={`${r.edgeLabel}. ${readNote(r)}`}
          >
            {r.edgeLabel}
          </span>
        ) : null}
      </>
    ),
    cash,
    side: [
      `IV rank ${r.ivRank === null ? "—" : `${Math.round(r.ivRank)}%`} · close ${r.price === null ? "—" : `$${money(r.price)}`}`,
      sideMove,
      ...(entry ? [entry] : []),
    ],
    bar: r.strength === null ? null : r.strength * 10,
    barTitle: r.strength === null ? readNote(r) : `Strength ${r.strength}/10. ${readNote(r)}`,
    label: `${r.strength === null ? "no read" : `strength ${r.strength}/10`} · ${r.sector ?? "no sector"}`,
    open: { date: "proposed", price },
    legs,
    after,
    foot: [
      [word, width, maxLoss].filter(Boolean).join(" · ") || "no spread",
      s === null ? "not a strong read" : "the spread has no edge in history",
    ],
  };
}

/** The sorts: timing, size and fit, in the calendar's own day groups; never the spread's credit. */
export const CALENDAR_SORTS: SortOption<EarningsCalendarRow>[] = [
  { id: "symbol", label: "symbol", hint: "A to Z", key: (r) => r.symbol },
  { id: "time", label: "report time", hint: "before the open first", key: (r) => ((r.timing ?? r.usualTiming) === "bmo" ? 0 : (r.timing ?? r.usualTiming) === "amc" ? 1 : null) },
  { id: "strength", label: "strength of the read", hint: "strongest first", key: (r) => (r.strength === null ? null : -r.strength) },
  { id: "move", label: "expected move", hint: "largest first", key: (r) => (r.emMovePct === null ? null : -r.emMovePct) },
];

export const calendarHelp = (ivRankCut: number): CardHelpContent => ({
  title: "Reading the calendar",
  body: (
    <>
      <HelpWarn title="No edge in history">
        <p>
          The suggested spread has no edge in history. Replayed over 2020-26 (3,587 priced announcements), it lost 4.3% on risk on the
          defaults and in every variant tried, and the direction read called the reaction 48-49% of the time: a coin. It is shown
          because the screen shows it, not as advice (docs/history/earnings-screener-plan.md).
        </p>
      </HelpWarn>
      <HelpSection title="Direction and strength">
        <p>
          Ours (Bearing), from our bars as of the previous session's close; a name we hold no bars for has none. The vendor's reads are
          kept in the file and never shown or filtered on.
        </p>
      </HelpSection>
      <HelpSection title="IV rank">
        <p>The broker's. It picks the spread's side: a debit under the cut, a credit at or over it. The cut is {ivRankCut}.</p>
      </HelpSection>
      <HelpSection title="The expected move">
        <p>
          0.85 × the at-the-money straddle on the first LISTED expiry on or after the reaction (the broker's rule for an earnings move),
          with that expiry and its days past the reaction. Where only monthlies are listed it can be weeks out: past {EARNINGS_WEEK_DAYS}{" "}
          days it prices a fortnight's or a month's move, not the announcement's alone. One standard deviation is about 1.5 times it.
        </p>
      </HelpSection>
      <HelpSection title="The spread">
        <p>
          Listed strikes once the evening's chain listing has matched them, and its price once a regular-hours reading has (11:30 and 14:30
          ET); until then the target prices, marked as targets. A spread read as thin is hidden by default: judged as one order, its cost
          to trade within 10% of its price (or a nickel), 100 open a leg.
        </p>
      </HelpSection>
    </>
  ),
});

export function EarningsCalendarTab() {
  const [params, setParams] = useSearchParams();
  const { data, isLoading, isError } = useEarningsCalendar(params.get("day"));
  const set = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value === null) next.delete(key);
    else next.set(key, value);
    setParams(next, { replace: true });
  };

  const [view, setView] = useViewChoice(LIST, "rows");
  const [sortChoice, setSortChoice] = useSortChoice(LIST, "symbol");
  const [panel, setPanel] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);

  const win = (params.get("win") as Win | null) ?? "7";
  const time = params.get("time"); // null | "bmo" | "amc"
  const dirParam = params.get("dir");
  const dirs = dirParam === "all" ? null : dirParam ? dirParam.split(",") : DEFAULT_DIRS;
  const allLiquidity = params.get("liq") === "all";
  const somewhat = params.get("liq") === "1,2";
  const allOptions = params.get("opt") === "all";
  const allRegions = params.get("region") === "all";
  const allTrades = params.get("trade") === "all";
  const sector = params.get("sector");
  const q = (params.get("q") ?? "").trim().toUpperCase();
  const minPremium = useMinPremium(LIST, params.get("premium"));
  // One spread's price at mid, whole contract, credit or debit. A spread with no price yet stays shown.
  const premiumOk = (r: EarningsCalendarRow) => minPremium === 0 || r.trade?.mid == null || r.trade.mid * 100 >= minPremium;

  const all = data?.rows ?? [];
  const latest = data?.days[0] ?? null;
  // Windows count from today (New York) on the newest calendar; from its own session on an older one.
  const ref = data?.day && data.day !== latest ? data.day : todayNy();
  const inWindow = (r: EarningsCalendarRow, w: Win) =>
    r.date >= ref && (w === "30" || r.date <= (w === "today" ? ref : addDays(ref, 7)));
  const passes = (r: EarningsCalendarRow) =>
    (dirs === null || (r.edge !== null && dirs.includes(r.edge))) &&
    (allLiquidity || r.liquidityRank === 1 || (somewhat && r.liquidityRank === 2)) &&
    (allOptions || r.optionable === true) &&
    (allRegions || r.region === "US") &&
    // A spread read as thin is hidden; one not read yet stays, its targets shown.
    (allTrades || r.trade?.passes !== false) &&
    premiumOk(r) &&
    (sector === null || r.sector === sector) &&
    (q === "" || r.symbol.includes(q));
  const filtered = all.filter(passes);
  const shown = filtered.filter((r) => inWindow(r, win) && (time === null || (r.timing ?? r.usualTiming) === time));
  const hiddenByFloor = minPremium === 0 ? 0 : all.filter((r) => inWindow(r, win) && !premiumOk(r)).length;
  const groups = new Map<string, EarningsCalendarRow[]>();
  for (const r of shown) groups.set(r.date, [...(groups.get(r.date) ?? []), r]);
  const sortOption = CALENDAR_SORTS.find((s) => s.id === sortChoice.id);
  const sectors = [...new Set(all.map((r) => r.sector).filter((s): s is string => s !== null))].sort();
  const toggleDir = (code: string) => {
    const cur = new Set(dirs ?? []);
    if (cur.has(code)) cur.delete(code);
    else cur.add(code);
    const list = [...cur].sort();
    set("dir", list.length === 0 ? "all" : list.join(",") === [...DEFAULT_DIRS].sort().join(",") ? null : list.join(","));
  };
  const othersOn = dirs === null;
  const timeCount = (t: "bmo" | "amc") => filtered.filter((r) => inWindow(r, win) && (r.timing ?? r.usualTiming) === t).length;

  const dirsDefault = dirs !== null && dirs.length === DEFAULT_DIRS.length && DEFAULT_DIRS.every((d) => dirs.includes(d));
  const changed =
    (time !== null ? 1 : 0) +
    (dirsDefault ? 0 : 1) +
    (allLiquidity || somewhat ? 1 : 0) +
    (allOptions ? 1 : 0) +
    (allRegions ? 1 : 0) +
    (allTrades ? 1 : 0) +
    (minPremium !== defaultFloor(LIST) ? 1 : 0) +
    (sector !== null ? 1 : 0) +
    (q !== "" ? 1 : 0) +
    (sortChoice.id !== "symbol" || sortChoice.reversed ? 1 : 0);
  const reset = () => {
    const next = new URLSearchParams(params);
    for (const k of ["time", "dir", "liq", "opt", "region", "trade", "sector", "q", "premium"]) next.delete(k);
    setParams(next, { replace: true });
    void writePref(`minPremium.${LIST}`, undefined);
    setSortChoice({ id: "symbol", reversed: false });
  };
  const dirWords = othersOn
    ? "all directions"
    : (dirs ?? []).includes(STRONG_BULL) && (dirs ?? []).includes(STRONG_BEAR)
      ? "strong reads"
      : (dirs ?? []).includes(STRONG_BULL)
        ? "strong bull"
        : "strong bear";
  const parts = [
    WINDOWS.find((w) => w.key === win)?.label.toLowerCase() ?? win,
    dirWords,
    ...(time ? [time === "bmo" ? "before market" : "after market"] : []),
    ...(!allLiquidity ? [somewhat ? "very + somewhat liquid" : "very liquid"] : []),
    ...(!allOptions ? [] : ["not only optionable"]),
    ...(!allRegions ? [] : ["non-US too"]),
    ...(!allTrades ? ["liquid trade"] : []),
    ...(minPremium > 0 ? [`≥ $${minPremium.toLocaleString()}`] : []),
    ...(sector ? [sector] : []),
    ...(q ? [`"${q}"`] : []),
    ...(sortChoice.id !== "symbol" || sortChoice.reversed ? [`by ${sortOption?.label ?? sortChoice.id}${sortChoice.reversed ? ", reversed" : ""}`] : []),
  ];

  const stale = data?.day && data.inputs.closeDate !== null && data.inputs.closeDate < data.day;

  return (
    <ScreenerLayout>
      <Section concept="stand" tag="the calendar" question="What reports soon?">
        <Card
          title="earnings calendar"
          help={calendarHelp(data?.ivRankCut ?? 50)}
          flag="no edge in history"
          controls={
            <>
              {data && data.days.length > 0 && (
                <select
                  className="chip review-session-select"
                  aria-label="Calendar day"
                  value={data.day ?? ""}
                  onChange={(e) => set("day", e.target.value === latest ? null : e.target.value)}
                >
                  {data.days.map((d) => (
                    <option key={d} value={d}>
                      {d}
                    </option>
                  ))}
                </select>
              )}
              <div className="mode-toggle" role="group" aria-label="window">
                {WINDOWS.map((w) => (
                  <button
                    key={w.key}
                    type="button"
                    className={win === w.key ? "mode-btn active" : "mode-btn"}
                    aria-pressed={win === w.key}
                    onClick={() => set("win", w.key === "7" ? null : w.key)}
                  >
                    {w.label} ({all.filter((r) => inWindow(r, w.key)).length})
                  </button>
                ))}
              </div>
              <ViewSwitch view={view} onChange={setView} />
              <FilterSortButton changed={changed} open={panel} onOpen={() => setPanel(!panel)} anchor={anchor} />
            </>
          }
        >
          <FilterSummary parts={parts} note={`${shown.length} of ${all.length} announcements`} changed={changed} onOpen={() => setPanel(true)} onReset={reset} />
          {data?.day && (
            <p className="muted pf-caption" style={{ margin: "0 0 8px" }}>
              built {data.day} · prices from the {data.inputs.closeDate ?? "—"} close · moves from the {data.inputs.expiryIv ?? "—"} IVs
              {stale ? (
                <>
                  {" "}
                  <span
                    className="chip chip-warn"
                    title={`Dolt did not yet hold the ${data.day} close when this calendar was built, so the price, the expected move as a percentage and the spread targets use the ${data.inputs.closeDate} close`}
                  >
                    prices from {data.inputs.closeDate}, not {data.day}
                  </span>
                </>
              ) : null}
            </p>
          )}

          {isError && <p className="muted">Could not read the earnings calendar.</p>}
          {isLoading && <p className="muted">Reading the calendar…</p>}
          {data && data.day === null && <p className="muted">No calendar written yet: the earnings-calendar job runs at 19:10 ET on trading days.</p>}

          <ListViewContext.Provider value={view}>
            {[...groups.entries()].map(([day, rows]) => (
              <div key={day} className="ec-day">
                <div className="ec-dayhead">
                  {dayHeading(day)} · {rows.length}
                </div>
                <TradeCards models={sortRows(rows, sortOption, sortChoice.reversed).map(earningsModel)} />
              </div>
            ))}
          </ListViewContext.Provider>
          {data && data.day !== null && all.length > 0 && shown.length === 0 && <p className="muted">Nothing matches these filters.</p>}
        </Card>
        <FilterSortPanel open={panel} onClose={() => setPanel(false)} onReset={reset} anchor={anchor}>
          <SortSection sorts={CALENDAR_SORTS} choice={sortChoice} fallback="symbol" onChange={setSortChoice} />
          <FilterGroup title="timing" note="the report's time of day; an estimate counts">
            <FilterSwitch
              label="Before market"
              description={`${timeCount("bmo")} reporting before the open`}
              on={time === "bmo"}
              onChange={(on) => set("time", on ? "bmo" : null)}
            />
            <FilterSwitch
              label="After market"
              description={`${timeCount("amc")} reporting after the close`}
              on={time === "amc"}
              onChange={(on) => set("time", on ? "amc" : null)}
            />
          </FilterGroup>
          <FilterGroup title="direction" note="our read, from our bars">
            <FilterSwitch
              label="Strong bull"
              description="Strong bull direction reads"
              on={!othersOn && (dirs ?? []).includes(STRONG_BULL)}
              onChange={() => toggleDir(STRONG_BULL)}
            />
            <FilterSwitch
              label="Strong bear"
              description="Strong bear direction reads"
              on={!othersOn && (dirs ?? []).includes(STRONG_BEAR)}
              onChange={() => toggleDir(STRONG_BEAR)}
            />
            <FilterSwitch label="All directions" description="Every read, the mixed ones included" on={othersOn} onChange={(on) => set("dir", on ? "all" : null)} />
          </FilterGroup>
          <FilterGroup title="liquidity">
            <FilterSwitch
              label="Very liquid"
              description="Options rated very liquid (rank 1) only; off shows every rating"
              on={!allLiquidity}
              onChange={(on) => set("liq", on ? null : "all")}
            />
            <FilterSwitch
              label="Also somewhat liquid"
              description="Add the names rated rank 2"
              on={somewhat}
              disabled={allLiquidity}
              onChange={(on) => set("liq", on ? "1,2" : null)}
            />
            <FilterSwitch label="Optionable" description="Names with listed options" on={!allOptions} onChange={(on) => set("opt", on ? null : "all")} />
            <FilterSwitch
              label="US listings"
              description="Depository receipts left out, as the vendor's US region filter does"
              on={!allRegions}
              onChange={(on) => set("region", on ? null : "all")}
            />
            <FilterSwitch
              label="Liquid trade"
              description="Hide a spread read in regular hours that fails the bars; one not read yet stays"
              on={!allTrades}
              onChange={(on) => set("trade", on ? null : "all")}
            />
          </FilterGroup>
          <FilterGroup title="premium floor" note="one whole spread at mid">
            <PremiumSlider
              list={LIST}
              value={minPremium}
              onChange={(v) => set("premium", v === 0 ? null : String(v))}
              title="Hide a name whose spread, read in regular hours, prices under this at mid for one whole spread (credit or debit). A spread with no price yet stays shown. Remembered for next time."
              hidden={hiddenByFloor}
            />
          </FilterGroup>
          <FilterGroup title="sector and symbol">
            <select className="chip review-session-select" aria-label="Sector" value={sector ?? ""} onChange={(e) => set("sector", e.target.value || null)}>
              <option value="">all sectors</option>
              {sectors.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <input
              className="chip review-session-select"
              value={params.get("q") ?? ""}
              placeholder="symbol"
              aria-label="Filter by symbol"
              onChange={(e) => set("q", e.target.value === "" ? null : e.target.value)}
            />
          </FilterGroup>
        </FilterSortPanel>
      </Section>
    </ScreenerLayout>
  );
}
