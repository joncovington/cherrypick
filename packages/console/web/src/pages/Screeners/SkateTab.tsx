import type { SkateBand, SkateConfig, SkateSide } from "@console/shared";
import { HelpSection, HelpWarn, type CardHelpContent } from "../../components/CardHelp";
import { FilterGroup, FilterSwitch, SortSection, sortRows, useSortChoice, type SortChoice, type SortOption } from "../../components/FilterSort";
import { Pager, usePage } from "../../components/ScopeBar";
import { PremiumSlider, defaultFloor, useMinPremium } from "../../components/PremiumSlider";
import { useViewChoice } from "../../components/ViewSwitch";
import { writePref } from "../../lib/prefs";
import { useSkate } from "../../lib/api";
import { TradeCards, type TradeCardModel } from "../Live/TradeCard";
import { PanelChoice, ScreenerCard, ScreenerLayout, SessionSelect, SymbolSearch, Tabs, useParamState } from "./ScreenerParts";
import { Section } from "../../components/Section";
import { SkateRecordSection } from "./SkateRecord";
import { beatsMonthly, otherBand, skateCounts, skateViews, type SkateView } from "./skateRows";

/**
 * The Skate tab (2026-10-19; docs/skate-plan.md step 3): covered calls above a name's recent range
 * and cash-secured puts below it, where the seller does not want assignment. Read-only, and it
 * decides nothing: `fetch_screener_greeks.py skate` quotes the band strikes in regular hours and
 * `technicals.skate` scores them -- the history and implied rates, the gap between them, the yields,
 * the hard and soft verdicts and each band's best expiry are all the run file's.
 *
 * As the plan's decisions set it (2026-10-18): the hard filters hide a row (open interest, the
 * spread; earnings are dropped upstream, by the band file), the soft thresholds are a marker with a
 * "passing only" toggle, and the default sort is history minus implied. A band selector picks which
 * band's strikes drive the rows; the other band's strike and premium a day sit beside them, quieter.
 *
 * Redesign (2026-11-06): each name-side is a trade card (rows on request); side stays as tabs, and the
 * band, passing only, the premium floor, the sort and the symbol search sit behind filter & sort. The
 * two footers that said "not a tested edge" are one amber flag, the method behind the ?.
 */

const SIDES: { id: "calls" | "puts"; side: SkateSide; label: string }[] = [
  { id: "calls", side: "C", label: "calls" },
  { id: "puts", side: "P", label: "puts" },
];
const BAND_LABEL: Record<SkateBand, string> = { fixed: "fixed", byExpiry: "by expiry" };
const PAGE_SIZES = [24, 48, 96, 192] as const;

const DASH = "—";

function pct(v: number | null, digits = 1): string {
  return v === null ? DASH : `${(v * 100).toFixed(digits)}%`;
}

/** A rate difference in points: +4.2 pts. */
function points(v: number | null): string {
  if (v === null) return DASH;
  const p = v * 100;
  return `${p > 0 ? "+" : p < 0 ? "−" : ""}${Math.abs(p).toFixed(1)} pts`;
}

/** The strike against the spot at the quote, as a fraction: a display figure, judged by nothing here. */
function distance(v: SkateView): number | null {
  return v.row.spot === null || v.row.spot === 0 ? null : v.pick.strike / v.row.spot - 1;
}

function dollars(v: number | null, digits = 2): string {
  return v === null ? DASH : `$${v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

/** "2026-11-13" -> "Nov 13". */
function shortDate(iso: string | null): string {
  if (iso === null) return DASH;
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

/** A UTC ISO stamp as New York wall time: "2026-10-19 15:05 ET". */
function etStamp(iso: string | null): string | null {
  if (iso === null) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(d);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")} ${get("hour")}:${get("minute")} ET`;
}

/** The haircut schedule in words, from the run's own config. */
function bandsLine(c: SkateConfig | null): string {
  if (c === null || c.haircut === null) return "";
  const steps = c.byExpiry.map(([days, cut]) => `${cut} at ${days} days or fewer`).join(", ");
  return `Fixed is a ${c.haircut} haircut at every expiry; by expiry is ${steps || "the same"}${steps ? `, ${c.haircut} beyond` : ""}.`;
}

function hardLine(c: SkateConfig | null): string {
  if (c === null) return "open interest and the bid-ask spread";
  const h = c.hard;
  return (
    `open interest of ${h.minOpenInterest ?? DASH} or more, and a spread within ${h.maxSpreadPct === null ? DASH : `${Math.round(h.maxSpreadPct * 100)}%`} ` +
    `of mid or ${h.maxSpreadAbs === null ? DASH : dollars(h.maxSpreadAbs)} a share`
  );
}

function softLine(c: SkateConfig | null): string {
  if (c === null) return "the skate rate over the name's full history and the last 3 years, and skate yield";
  const s = c.soft;
  return (
    `skate rate ${pct(s.skate, 0)} or more over the name's full history (from 2015, as little as a year for a young name) ` +
    `and ${pct(s.skateRecent, 0)} over the last 3 years, gross skate yield on the bid ${pct(s.yield, 0)} or more`
  );
}

/** Windows of history as years, ~252 a year: "6.0y". */
function years(windows: number | null): string | null {
  return windows === null ? null : `${(windows / 252).toFixed(1)}y`;
}

/** What a soft miss missed, in words (the chip's hover). */
function softMisses(v: SkateView, c: SkateConfig | null): string {
  const s = v.pick.soft;
  return [
    s.skate ? null : `full-history skate rate under ${pct(c?.soft.skate ?? null, 0)}`,
    s.skateRecent ? null : `3-year skate rate under ${pct(c?.soft.skateRecent ?? null, 0)}`,
    s.yield ? null : `gross skate yield under ${pct(c?.soft.yield ?? null, 0)}`,
  ]
    .filter((x): x is string => x !== null)
    .join("; ");
}

/** The other band's best, in a line: "by expiry band: 105 · Nov 20 · $5.50/d", or "same". */
function otherLine(v: SkateView): string {
  const o = v.other;
  const name = BAND_LABEL[otherBand(v.band)];
  if (o.best === null) return `${name} band: no pick passes`;
  const same = o.best.expiry === v.best.expiry && o.best.strike === v.best.strike;
  return same ? `${name} band: same` : `${name} band: ${o.best.strike}${o.best.expiry !== v.best.expiry ? ` · ${shortDate(o.best.expiry)}` : ""} · ${dollars(o.best.perDayBid)}/d`;
}

/**
 * One name-side as a trade card: the strike's premium net of the fee as the headline (the gross where
 * the run predates the fee figures), the skate rate against its implied odds as the bar, the yield and
 * the soft verdict as chips. Money follows the suite layout: whole contract, a credit +.
 */
export function skateModel(v: SkateView, c: SkateConfig | null): TradeCardModel {
  const p = v.pick;
  const bid = p.quote.bid;
  const net = p.netPremiumBid !== null && p.feeOpen !== null;
  const cash = net ? p.netPremiumBid : bid === null ? null : bid * 100;
  const under = net && (p.netPremiumBid ?? 0) <= 0;
  const right = v.row.side;
  const word = right === "C" ? "covered call" : "cash-secured put";
  const q = [bid === null && p.quote.ask === null ? "" : `${bid === null ? DASH : bid.toFixed(2)} / ${p.quote.ask === null ? DASH : p.quote.ask.toFixed(2)}`, p.delta === null ? "" : `Δ ${Math.abs(p.delta).toFixed(2)}`]
    .filter(Boolean)
    .join(" · ");
  const d = distance(v);
  return {
    key: `${v.row.symbol}-${v.row.side}`,
    symbol: v.row.symbol,
    symbolHref: `/charts/technicals?symbol=${encodeURIComponent(v.row.symbol)}`,
    spot: v.row.spot === null ? null : v.row.spot.toFixed(2),
    shape: `${word} · ${shortDate(v.best.expiry)} · ${p.days} d`,
    chips: (
      <>
        {p.soft.all ? (
          <span className="chip chip-ok" title="Passes every soft threshold">
            soft pass
          </span>
        ) : (
          <span className="chip chip-warn" title={`A marker, not a filter. Misses: ${softMisses(v, c)}`}>
            soft miss
          </span>
        )}
        {under ? (
          <span className="chip chip-warn" title="The premium does not cover the fee to open: a loss even if it skates">
            under cost
          </span>
        ) : null}
        {v.row.fund ? (
          <span className="chip" title="A fund: no earnings">
            fund
          </span>
        ) : null}
      </>
    ),
    cash,
    side: [
      net ? `net of the ${dollars(p.feeOpen)} fee` : "gross: this run predates the fee figures",
      `${net ? "net " : ""}yield ${pct(net ? p.netYieldBid : p.yieldBid)}`,
    ],
    bar: p.skate === null ? null : p.skate * 100,
    barTitle: `the share of past days the band held at this strike's offset, over ${years(p.historyWindows) ?? "the name's history"}; the model's odds are ${pct(p.impliedSkate)}`,
    label: `skated ${pct(p.skate, 0)} of past days · ${pct(p.impliedSkate, 0)} implied · ${points(p.gap)}${p.historyWindows === null ? "" : ` · ${years(p.historyWindows)} of history`}`,
    open: { date: "proposed", price: bid === null ? DASH : `${bid.toFixed(2)} bid` },
    legs: [{ buy: false, text: `1 ${v.best.expiry} ${p.strike}${right}${bid === null ? "" : ` @ ${bid.toFixed(2)}`}`, quote: q || null }],
    after: [
      `${p.delta === null ? "Δ —" : `Δ ${Math.abs(p.delta).toFixed(2)}`} · OI ${p.quote.openInterest === null ? DASH : p.quote.openInterest.toLocaleString("en-US")}`,
      `${d === null ? DASH : `${d > 0 ? "+" : d < 0 ? "−" : ""}${Math.abs(d * 100).toFixed(1)}%`} to the strike`,
      `through ${pct(p.testedPerMonth, 0)} a month`,
    ],
    foot: [
      `${BAND_LABEL[v.band]} band${v.best.vsMonthly === null ? "" : ` · ${v.best.vsMonthly.toFixed(2)}× the monthly${beatsMonthly(v.best) ? ", more a day" : ""}`} · ${otherLine(v)}`,
      v.row.fund ? "a fund: no earnings" : `earnings ${shortDate(v.row.earnings)}`,
    ],
  };
}

/** The skate sorts: history minus implied (the scan's order), name, timing and delta. */
export const SKATE_SORTS: SortOption<SkateView>[] = [
  { id: "gap", label: "history − implied", hint: "most first (the scan's order)", key: (v) => (v.pick.gap === null ? null : -v.pick.gap) },
  { id: "symbol", label: "symbol", hint: "A to Z", key: (v) => v.row.symbol },
  { id: "expiry", label: "expiry", hint: "soonest first", key: (v) => v.pick.days },
  { id: "delta", label: "Δ", hint: "lowest first", key: (v) => (v.pick.delta === null ? null : Math.abs(v.pick.delta)) },
];

/** The ? behind the skate card: the two footers (not a tested edge), the card, the money. */
export function skateHelp(c: SkateConfig | null): CardHelpContent {
  return {
    title: "Reading skate",
    body: (
      <>
        <HelpWarn title="Not a tested edge">
          <p>
            The best expiry ranks premium a day; it is not a tested edge. A 14-day put against the monthly, held, halved or rolled,
            reached no verdict over history (edge-hypotheses item 25), so the monthly stays the default.
          </p>
          <p>
            History minus implied is a history estimate: the share of past days the band held, against the model's odds today. It is not
            yet a tested edge; the forward record will say whether rows with a wide gap skate more than priced, once it is decision-grade.
            Closer strikes tend to top that sort, so skate yield and the skate rate stay beside it.
          </p>
        </HelpWarn>
        <HelpSection title="A card">
          <p>
            One name-side: the band's own strike on its best expiry, the one paying the most a day on the bid among strikes that pass the
            hard filters ({hardLine(c)}); expiries spanning an earnings report are dropped before quoting. A row whose pick fails them is
            hidden. The soft thresholds ({softLine(c)}) are a marker, never a filter, unless passing only is on.
          </p>
        </HelpSection>
        <HelpSection title="The bands">
          <p>{bandsLine(c) || "Fixed and by expiry are two haircuts on the recent range."}</p>
        </HelpSection>
        <HelpSection title="The money">
          <p>
            Premium is per contract (100 shares) at the bid; the bid and ask are the one per-share figure. Net of fee takes off what opening
            one short contract costs (commission and pass-through fees; the bid already concedes the spread), and a premium that does not
            cover it is marked. The best expiry, the ranking and the forward record all stay on the gross.
          </p>
        </HelpSection>
      </>
    ),
  };
}

/** What the filter & sort panel holds: the sort, the band, passing only, the premium floor (the slider
 *  lives here, not on the card) and the symbol search. */
export function SkatePanel({
  sort,
  onSort,
  band,
  passingOnly,
  minPremium,
  hidden,
  q,
  config,
  set,
}: {
  sort: SortChoice;
  onSort: (c: SortChoice) => void;
  band: SkateBand;
  passingOnly: boolean;
  minPremium: number;
  hidden: number;
  q: string;
  config: SkateConfig | null;
  set: (key: string, value: string | null) => void;
}) {
  return (
    <>
      <SortSection sorts={SKATE_SORTS} choice={sort} fallback="gap" onChange={onSort} />
      <FilterGroup title="band" note="which band's strikes drive the cards">
        <PanelChoice
          label="Band"
          value={band}
          options={(["fixed", "byExpiry"] as const).map((b) => ({ id: b, label: BAND_LABEL[b], title: `Cards and ranking from the ${BAND_LABEL[b]} band's strikes. ${bandsLine(config)}` }))}
          onChange={(b) => set("band", b === "fixed" ? null : "by-expiry")}
        />
      </FilterGroup>
      <FilterGroup title="filters">
        <FilterSwitch
          label="Passing only"
          description={`Only picks passing every soft threshold: ${softLine(config)}. Off, they are marked, not hidden.`}
          on={passingOnly}
          onChange={(on) => set("passing", on ? "1" : null)}
        />
      </FilterGroup>
      <FilterGroup title="premium floor" note="one contract at the bid, before costs">
        <PremiumSlider
          list="skate"
          value={minPremium}
          onChange={(v) => set("premium", v === 0 ? null : String(v))}
          hidden={hidden}
          title="Hide a pick whose premium for one contract at the bid, before costs, is under this. A pick with no bid stays shown. Remembered for next time."
        />
      </FilterGroup>
      <FilterGroup title="symbol">
        <SymbolSearch value={q} onChange={(v) => set("q", v === "" ? null : v)} />
      </FilterGroup>
    </>
  );
}

export function SkateTab() {
  const { params, set, clear } = useParamState();
  const { data, isLoading, isError } = useSkate(params.get("day"));
  const [sort, setSort] = useSortChoice("skate", "gap");
  const [view, setView] = useViewChoice("skate");
  const sideId = params.get("side") === "puts" ? "puts" : "calls";
  const side = SIDES.find((s) => s.id === sideId)!;
  const band: SkateBand = params.get("band") === "by-expiry" ? "byExpiry" : "fixed";
  const passingOnly = params.get("passing") === "1";
  const q = (params.get("q") ?? "").trim().toUpperCase();
  const latest = data?.days[0] ?? null;

  const all = data?.rows ?? [];
  const minPremium = useMinPremium("skate", params.get("premium"));
  const matched = skateViews(all, side.side, band, passingOnly).filter((v) => q === "" || v.row.symbol.includes(q));
  // The premium floor's figure: one contract at the bid, gross. A pick with no bid stays shown.
  const floored = matched.filter((v) => minPremium === 0 || v.pick.quote.bid === null || v.pick.quote.bid * 100 >= minPremium);
  const small = matched.length - floored.length;
  const views = sortRows(floored, SKATE_SORTS.find((s) => s.id === sort.id), sort.reversed);
  const counts = skateCounts(all, side.side, band);
  const measured = etStamp(data?.measuredAt ?? null);
  const { page, setOffset, setLimit } = usePage([data?.day, sideId, band, passingOnly, q, minPremium, sort.id, sort.reversed], 48);
  const shown = views.slice(page.offset, page.offset + page.limit);

  const sortChanged = sort.id !== "gap" || sort.reversed;
  const floorChanged = minPremium !== defaultFloor("skate");
  const changed = (sortChanged ? 1 : 0) + (band !== "fixed" ? 1 : 0) + (passingOnly ? 1 : 0) + (floorChanged ? 1 : 0) + (q !== "" ? 1 : 0);
  const reset = () => {
    void setSort({ id: "gap", reversed: false });
    void writePref("minPremium.skate", undefined);
    clear(["band", "passing", "premium", "q"]);
  };
  const sortLabel = SKATE_SORTS.find((s) => s.id === sort.id)?.label ?? sort.id;

  return (
    <ScreenerLayout>
      <Section concept="income" tag="candidates" question="Which strikes pay most to sit through?">
        <ScreenerCard
          title="skate"
          help={skateHelp(data?.config ?? null)}
          flag="not a tested edge"
          extra={data ? <SessionSelect days={data.days} day={data.day} latest={latest} label="Skate session" onChange={(d) => set("day", d)} /> : null}
          tabs={
            <Tabs
              label="Side"
              value={sideId}
              options={SIDES.map((s) => ({
                id: s.id,
                label: s.label,
                title: s.side === "C" ? "Covered calls above the range band" : "Cash-secured puts below the range band",
              }))}
              onChange={(id) => set("side", id === "calls" ? null : id)}
            />
          }
          view={{ value: view, onChange: setView }}
          filter={{
            changed,
            parts: [side.label, `${BAND_LABEL[band]} band`, ...(passingOnly ? ["passing only"] : []), minPremium === 0 ? "any premium" : `≥ $${minPremium.toLocaleString()}`, `by ${sortLabel}${sort.reversed ? ", reversed" : ""}`],
            note:
              data?.status === "ok"
                ? `${matched.length - small} shown${small > 0 ? ` · ${small} under $${minPremium.toLocaleString()} hidden` : ""} · ${counts.read - counts.tradable} with no pick passing the hard filters hidden`
                : null,
            onReset: reset,
            panel: (
              <SkatePanel
                sort={sort}
                onSort={setSort}
                band={band}
                passingOnly={passingOnly}
                minPremium={minPremium}
                hidden={small}
                q={params.get("q") ?? ""}
                config={data?.config ?? null}
                set={set}
              />
            ),
          }}
        >
          {data?.day && (
            <p className="muted pf-caption" style={{ marginTop: 0 }}>
              session {data.day}
              {measured ? ` · measured ${measured}` : ""}
              {data.bandDay ? ` · bands on the ${data.bandDay} close` : ""}
              {data.runs.length > 1 ? ` · newest of ${data.runs.length} runs` : ""}
              {data.status === "ok" ? ` · ${counts.read} ${side.label} read · ${counts.passing} of ${counts.tradable} pass the soft thresholds` : ""}
            </p>
          )}
          {isError && <p className="muted">Could not read the skate screen.</p>}
          {isLoading && <p className="muted">Reading the skate run…</p>}
          {data && data.status === "none" && (
            <p className="muted">No skate run written{data.day ? ` for ${data.day}` : " yet"}: the quotes are taken at 12:00 and 15:05 ET on a session day.</p>
          )}
          {data && data.status === "unreadable" && (
            <p className="chip chip-warn" style={{ whiteSpace: "normal" }}>
              The {data.run} run for {data.day} could not be read, so nothing is shown. This is a failed read, not an empty screen.
            </p>
          )}
          {data && data.status === "ok" && !data.complete && (
            <p className="chip chip-warn" style={{ whiteSpace: "normal" }}>
              This run did not complete: the rows are what it scored before it stopped, not the whole screen.
            </p>
          )}
          {data && data.status === "ok" && data.problem !== null && (
            <p className="chip chip-warn" style={{ whiteSpace: "normal" }}>
              The run reports a problem: {data.problem}
            </p>
          )}
          {data && data.status === "ok" && all.length === 0 && <p className="muted">The run scored no name-sides.</p>}
          {data && data.status === "ok" && all.length > 0 && views.length === 0 && (
            <p className="muted">
              {passingOnly
                ? `No ${side.label} on the ${BAND_LABEL[band]} band pass the soft thresholds${q ? ` matching ${q}` : ""}.`
                : `No ${side.label} on the ${BAND_LABEL[band]} band pass the hard filters${q ? ` matching ${q}` : ""}.`}
            </p>
          )}

          {data && views.length > 0 && <TradeCards models={shown.map((v) => skateModel(v, data.config))} />}
          {views.length > PAGE_SIZES[0] && (
            <Pager offset={page.offset} limit={page.limit} total={views.length} pageSizes={PAGE_SIZES} onOffset={setOffset} onLimit={setLimit} />
          )}
        </ScreenerCard>
      </Section>
      <SkateRecordSection />
    </ScreenerLayout>
  );
}
