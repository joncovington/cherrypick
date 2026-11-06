import type { ManageTimeSpreads, TimeSpreadStructure } from "@console/shared";

/**
 * The time-spreads tab's pure part: join the scan's structures to the account's sizing, filter them and
 * group them by name. It formats nothing and decides nothing about a trade; every figure is the scan's
 * or manage's. NO TESTED EDGE (item 54).
 */

/** Where a scan structure stands against the account's evaluation. */
export type FitState =
  /** No evaluation to compare with. */
  | "unmarked"
  /** Sized: `units` fit. */
  | "fits"
  /** Itemised as too big; `binding` names the cap. */
  | "stopped"
  /** The evaluation lists only some of the rows it stopped; this is one it did not itemise. */
  | "stopped-unlisted"
  /** The evaluation holds time spreads, but not this row. */
  | "absent";

export interface TimeSpreadView {
  s: TimeSpreadStructure;
  state: FitState;
  units: number | null;
  binding: string | null;
  heldName: boolean;
  heldSector: boolean;
}

function key(s: TimeSpreadStructure): string {
  return `${s.underlying}|${s.label}`;
}

function legs(s: TimeSpreadStructure): string | null {
  return s.longLeg?.option && s.shortLeg?.option ? `${s.longLeg.option}|${s.shortLeg.option}` : null;
}

/** Each scan structure with its standing: matched on underlying + label, else on the two option symbols. */
export function joinAccount(structures: TimeSpreadStructure[], account: ManageTimeSpreads | null): TimeSpreadView[] {
  if (account === null) {
    return structures.map((s) => ({ s, state: "unmarked", units: null, binding: null, heldName: s.heldName, heldSector: s.heldSector }));
  }
  const byLabel = new Map<string, { row: TimeSpreadStructure; fits: boolean }>();
  const byLegs = new Map<string, { row: TimeSpreadStructure; fits: boolean }>();
  const add = (rows: TimeSpreadStructure[], fits: boolean) => {
    for (const row of rows) {
      byLabel.set(key(row), { row, fits });
      const l = legs(row);
      if (l !== null) byLegs.set(l, { row, fits });
    }
  };
  add(account.tooBig, false);
  add(account.fits, true);
  const unlisted = account.tooBigTotal > account.tooBig.length;
  return structures.map((s) => {
    const l = legs(s);
    const hit = byLabel.get(key(s)) ?? (l !== null ? byLegs.get(l) : undefined);
    if (!hit) {
      return { s, state: unlisted ? "stopped-unlisted" : "absent", units: null, binding: null, heldName: s.heldName, heldSector: s.heldSector };
    }
    const { row, fits } = hit;
    return {
      s,
      state: fits && (row.units ?? 0) > 0 ? "fits" : "stopped",
      units: row.units,
      binding: row.binding,
      heldName: row.heldName,
      heldSector: row.heldSector,
    };
  });
}

export type StructureKind = "calendar" | "diagonal";
export type Right = "put" | "call";
export type Direction = "bullish" | "neutral" | "bearish";

export interface TimeSpreadFilters {
  structure: StructureKind | null;
  right: Right | null;
  direction: Direction | null;
  /** Largest debit at the mid, per share. */
  maxDebit: number | null;
  fitsOnly: boolean;
}

export const NO_FILTERS: TimeSpreadFilters = { structure: null, right: null, direction: null, maxDebit: null, fitsOnly: false };

export function structureOfRow(s: TimeSpreadStructure): StructureKind | null {
  const k = s.kind.toLowerCase();
  return k.includes("diagonal") ? "diagonal" : k.includes("calendar") ? "calendar" : null;
}

export function rightOfRow(s: TimeSpreadStructure): Right | null {
  const r = s.longLeg?.right ?? s.shortLeg?.right ?? null;
  if (r !== null) return r === "P" ? "put" : "call";
  const k = s.kind.toLowerCase();
  return k.includes("put") ? "put" : k.includes("call") ? "call" : null;
}

export function applyFilters(rows: TimeSpreadView[], f: TimeSpreadFilters): TimeSpreadView[] {
  return rows.filter(
    (r) =>
      (f.structure === null || structureOfRow(r.s) === f.structure) &&
      (f.right === null || rightOfRow(r.s) === f.right) &&
      (f.direction === null || r.s.direction === f.direction) &&
      (f.maxDebit === null || (r.s.debitMid !== null && r.s.debitMid <= f.maxDebit)) &&
      (!f.fitsOnly || r.state === "fits"),
  );
}

export interface TimeSpreadGroup {
  underlying: string;
  ratio: number | null;
  ivRank: number | null;
  spot: number | null;
  earningsDate: string | null;
  fitCount: number;
  rows: TimeSpreadView[];
}

/** One group a name, in the order each name first appears (the scan's: by ratio). */
export function groupByName(rows: TimeSpreadView[]): TimeSpreadGroup[] {
  const out = new Map<string, TimeSpreadGroup>();
  for (const r of rows) {
    let g = out.get(r.s.underlying);
    if (!g) {
      g = {
        underlying: r.s.underlying,
        ratio: r.s.ratio,
        ivRank: r.s.ivRank,
        spot: r.s.spot,
        earningsDate: r.s.earningsDate,
        fitCount: 0,
        rows: [],
      };
      out.set(r.s.underlying, g);
    }
    g.rows.push(r);
    if (r.state === "fits") g.fitCount += 1;
  }
  return [...out.values()];
}
