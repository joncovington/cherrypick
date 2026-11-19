/**
 * A leg in words, for the Builder, the shape and the chain. A leg added in the Builder is named by its
 * ID (L1, L2, …), given once and never reused; its words follow its action and side ("Long Put", "Short
 * Call ×2"). An automation saved before IDs keeps its own names, shown title-cased ("short_put" reads
 * "Short Put").
 *
 * The page's words and the stored keys differ: the page's **action** is the order (Buy to open, Sell to
 * open; an adjustment's Buy to close, Sell to close), stored as the leg's `side`, its contracts signed
 * (+ bought, − sold); the page's **side** is Put or Call, stored as `right`.
 */

export type LegLike = { name?: unknown; side?: unknown; right?: unknown };

const ID = /^L(\d+)$/;

export function isLegId(name: unknown): boolean {
  return typeof name === "string" && ID.test(name);
}

/** The order that opens a leg: "Sell to open" for a sold one (`side` < 0), "Buy to open" for a bought one. */
export function openVerb(side: number): string {
  return side < 0 ? "Sell to open" : "Buy to open";
}

/** The order that closes a leg held: "Buy to close" a sold one, "Sell to close" a bought one. */
export function closeVerb(side: number): string {
  return side < 0 ? "Buy to close" : "Sell to close";
}

/** "Long Put", "Short Call ×2": the leg's action, side and contracts. */
export function legWords(leg: LegLike): string {
  const side = Number(leg.side ?? 0);
  const kind = side < 0 ? "Short" : "Long";
  const right = leg.right === "C" ? "Call" : "Put";
  const n = Math.abs(side);
  return `${kind} ${right}${n > 1 ? ` ×${n}` : ""}`;
}

/** An older leg name in words: "short_put" reads "Short Put". */
export function titleCase(name: string): string {
  return name
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/** How a leg is named on the page: "L2 · Short Put ×2" for an ID, else its own name in words. */
export function legLabel(leg: LegLike): string {
  const name = typeof leg.name === "string" ? leg.name : "";
  if (isLegId(name)) return `${name} · ${legWords(leg)}`;
  return name ? titleCase(name) : legWords(leg);
}

/** The next leg's ID: one past the highest these legs hold (or past their count, for older names) and
 *  past `given`, the highest the page has handed out, so an ID removed this session is not reused. */
export function nextLegId(legs: readonly LegLike[], given = 0): string {
  const used = legs.map((l) => (typeof l.name === "string" ? ID.exec(l.name) : null)).filter((m): m is RegExpExecArray => m !== null);
  const top = used.length ? used.reduce((m, x) => Math.max(m, Number(x[1])), 0) : legs.length;
  return `L${Math.max(top, given) + 1}`;
}

/** A leg named in a sentence, without its contracts: "L2 · Short Put", or "Short Put" for an older name. */
export function legPlain(leg: LegLike): string {
  const name = typeof leg.name === "string" ? leg.name : "";
  const words = legWords({ ...leg, side: Math.sign(Number(leg.side ?? 0)) || -1 });
  return isLegId(name) ? `${name} · ${words}` : name ? titleCase(name) : words;
}

function dist(points: unknown, from: string): string {
  const p = Number(points ?? 0);
  if (!Number.isFinite(p) || p === 0) return `at ${from}`;
  return `${Math.abs(p)} ${p < 0 ? "below" : "above"} ${from}`;
}

/** Where a leg's strike rule puts it, in words, naming any leg it is placed from. */
export function placeWords(strike: Record<string, unknown>, byName: Record<string, LegLike>): string {
  const ref = (n: unknown) => (typeof n === "string" && byName[n] ? `the ${legPlain(byName[n])}` : `leg ${String(n)}`);
  const pct = strike["pct"] !== undefined ? `${Math.abs(Number(strike["pct"]))}% ${Number(strike["pct"]) < 0 ? "below" : "above"}` : null;
  switch (strike["rule"]) {
    case "spot":
      return pct ? `${pct} the spot` : Number(strike["points"] ?? 0) === 0 ? "at the strike nearest the spot" : dist(strike["points"], "the spot");
    case "prev_close":
      return pct ? `${pct} the previous close` : dist(strike["points"], "the previous close");
    case "open":
      return pct ? `${pct} the open` : dist(strike["points"], "the open");
    case "from_leg":
      return pct ? `${pct} ${ref(strike["leg"])}` : dist(strike["points"], ref(strike["leg"]));
    case "same_as":
      return `at the same strike as ${ref(strike["leg"])}`;
    case "match_width":
      return `from ${ref(strike["from"])} by the width between ${(Array.isArray(strike["width_of"]) ? strike["width_of"] : []).map(ref).join(" and ")}`;
    case "delta":
      return `at ${String(strike["target"] ?? "?")} delta`;
    case "premium":
      return `priced near $${String(strike["target"] ?? "?")}`;
    case "expected_move":
      return `${String(strike["times"] ?? "?")} expected moves from the spot`;
    case "std_dev":
      return `${String(strike["sd"] ?? "?")} standard deviations from the spot`;
    case "opening_range":
      return `at the opening range's ${String(strike["level"] ?? "?")}`;
    case "entry_level":
      return "at the spot when the entry fired";
    default:
      return `by ${String(strike["rule"] ?? "?").replaceAll("_", " ")}`;
  }
}

/** One entry leg as a sentence: "Sell to open a Short Put 5 below the spot", "Sell to open 2 × L2 · Short
 *  Put 5 below the L1 · Long Put". */
export function legSentence(leg: LegLike & { strike?: unknown }, byName: Record<string, LegLike>): string {
  const side = Number(leg.side ?? 0);
  const n = Math.abs(side) || 1;
  const strike = typeof leg.strike === "object" && leg.strike !== null ? (leg.strike as Record<string, unknown>) : {};
  return `${openVerb(side)} ${n > 1 ? `${n} ×` : "a"} ${legPlain(leg)} ${placeWords(strike, byName)}`;
}

/** Each leg's label by its name, for a picker of legs. */
export function legLabels(legs: readonly LegLike[]): Record<string, string> {
  return Object.fromEntries(legs.filter((l) => typeof l.name === "string").map((l) => [String(l.name), legLabel(l)]));
}
