// MEIC live against paper (`GET /api/live/meic/gap`, server/src/readers/meicLiveGap.ts): each live
// condor beside the paper arm that trades what live trades (`live-shadow`, `follows_live`), paired by
// session and entry order. Money is whole-position dollars; net is gross less fees (settlement
// included), as on the MEIC page.

export interface MeicLiveGapSide {
  entry: string | null;
  symbol: string | null;
  putStrike: number | null;
  callStrike: number | null;
  width: number | null;
  /** Net credit per share: the broker's fill on live, the modelled price on paper. */
  credit: number | null;
  status: string | null;
  gross: number | null;
  fees: number | null;
  /** Null while open, and on a row that never recorded its fees. */
  net: number | null;
}

export interface MeicLiveGapRow {
  session: string;
  live: MeicLiveGapSide | null;
  paper: MeicLiveGapSide | null;
  /** live net − paper net, + in live's favour; null unless both are settled. */
  gap: number | null;
}

export interface MeicLiveGapTotals {
  /** Condor pairs settled on both sides: these rows add up to the three nets. */
  paired: number;
  liveNet: number;
  paperNet: number;
  gap: number;
  /** Paper condors with no live one (live not armed, or refused): what live sat out. */
  paperOnly: number;
  paperOnlyNet: number;
  /** Live condors with no paper twin. */
  liveOnly: number;
}

export interface MeicLiveGapPayload {
  /** The paper arm compared against. */
  twin: string;
  /** "absent" when either ledger is missing on this machine. */
  ledger: "ok" | "absent";
  /** The first live session: paper is read from here on. */
  since: string | null;
  /** Newest session first. */
  rows: MeicLiveGapRow[];
  totals: MeicLiveGapTotals;
}
