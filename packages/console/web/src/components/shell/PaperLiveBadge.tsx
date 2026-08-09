import type { TradingMode } from "@console/shared";

/** Mode always comes from the API payload's source DB — never inferred client-side. */
export function PaperLiveBadge({ mode }: { mode: TradingMode }) {
  return <span className={`badge badge-${mode}`}>{mode === "live" ? "LIVE" : "PAPER"}</span>;
}
