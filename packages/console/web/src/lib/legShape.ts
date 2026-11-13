/**
 * An order's legs in the shape of the structure: puts before calls, each from the lowest strike up, so an
 * iron condor reads bought / sold / sold / bought, a fly bought / sold / bought, and a vertical low to
 * high. Equal strikes go front expiry first (a calendar's short above its long). In a roll the legs it
 * closes come before the ones it opens, each set in shape. A leg with no strike to read (stock, an
 * exercise line) keeps its place after the options, in the order it came.
 */

const STRIKE = /(?:^|\s)(\d+(?:\.\d+)?)([PC])(?=\s|$)/;
const EXPIRY = /\b(\d{4}-\d{2}-\d{2})\b/;

interface Placed {
  closes: boolean;
  right: number;
  strike: number;
  expiry: string;
  index: number;
  option: boolean;
}

function place(action: string | null | undefined, text: string, index: number): Placed {
  const m = STRIKE.exec(text);
  return {
    closes: action === "BTC" || action === "STC",
    right: m?.[2] === "C" ? 1 : 0,
    strike: m ? Number(m[1]) : 0,
    expiry: EXPIRY.exec(text)?.[1] ?? "",
    index,
    option: m !== null,
  };
}

function compare(a: Placed, b: Placed): number {
  if (a.closes !== b.closes) return a.closes ? -1 : 1;
  if (a.option !== b.option) return a.option ? -1 : 1;
  if (!a.option) return a.index - b.index;
  return a.right - b.right || a.strike - b.strike || a.expiry.localeCompare(b.expiry) || a.index - b.index;
}

/** `legs` in shape order; `read` gives each leg's action (BTO/STO/BTC/STC, or null) and its text. */
export function shapeOrder<T>(legs: readonly T[], read: (leg: T) => { action: string | null | undefined; text: string }): T[] {
  return legs
    .map((leg, i) => {
      const r = read(leg);
      return { leg, at: place(r.action, r.text, i) };
    })
    .sort((a, b) => compare(a.at, b.at))
    .map((x) => x.leg);
}
