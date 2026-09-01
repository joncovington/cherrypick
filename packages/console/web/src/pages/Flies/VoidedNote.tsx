import { useQuery } from "@tanstack/react-query";
import type { TradingMode } from "@console/shared";
import { fmtMoney } from "../../components/DataTable";
import { fliesQuery, type FliesFilter } from "../../lib/api";

interface VoidedRows {
  total: number;
  pnl: number;
  byReason: Array<{ reason: string; arm: string; entryMode: string | null; rows: number; pnl: number }>;
}

/**
 * What every total on this page left out, said out loud.
 *
 * The reads here all filter `void_reason IS NULL`, and said nothing about what that removed. The
 * module's own rule is that an exclusion is stated rather than inferred from a gap in a total, and
 * this surface was the one not honouring it.
 *
 * A void row is one whose DECISIONS rest on a defect a later fix proved wrong — the bwb roll priced
 * the wrong legs until 2026-08-07, so those positions were opened and rolled on a spread that was
 * never the trade. It is not a losing row and not one a filter dropped; those stay in every table.
 * The reason travels with the count precisely so this cannot be read as "excluded because they
 * lost", which is the claim the module refuses to make.
 */
export function VoidedNote({ mode, filter }: { mode: TradingMode; filter: FliesFilter }) {
  const { data } = useQuery<VoidedRows>({
    queryKey: ["flies-voided", mode, filter.date, filter.symbol, filter.era, filter.arm],
    queryFn: async () => {
      const res = await fetch(`/api/flies/voided?${fliesQuery(mode, filter)}`);
      if (!res.ok) throw new Error(`voided: HTTP ${res.status}`);
      return (await res.json()) as VoidedRows;
    },
    refetchInterval: 300_000,
  });

  if (data === undefined || data.total === 0) return null;

  return (
    <p className="integrity-note" role="note">
      <strong>{data.total} row{data.total === 1 ? "" : "s"} held back as void</strong> ({fmtMoney(data.pnl)}),
      excluded from every total on this page.{" "}
      {data.byReason.map((r) => `${String(r.rows)}× ${r.arm}/${r.entryMode ?? "—"}: ${r.reason}`).join(" · ")}.{" "}
      <span className="muted">
        A void row is one whose decisions rested on a defect a later fix proved wrong — not a losing
        row, and not one a filter dropped. Those stay in every table.
      </span>
    </p>
  );
}
