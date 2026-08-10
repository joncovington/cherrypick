import { useCollectors } from "../lib/api";

/**
 * Passive status line for the background data collectors: candles warm on a
 * cadence, chain snapshots capture daily after 15:30 ET, quote sweeps ride
 * tab polling — nothing here is a button. The banner only appears while
 * something is updating or genuinely unavailable.
 */
export function CollectorBanner({ chain = false }: { chain?: boolean }) {
  const { data } = useCollectors();
  if (data === undefined) return null;
  const lines: Array<{ text: string; tone: "info" | "warn" }> = [];
  if (data.dx === "error") {
    lines.push({ text: "market data feed error — reconnecting automatically", tone: "warn" });
  }
  if (data.candles.running && data.candles.progress !== null) {
    lines.push({
      text: `updating daily candles… ${data.candles.progress.done}/${data.candles.progress.total}`,
      tone: "info",
    });
  }
  if (chain) {
    if (data.chain.running && data.chain.progress !== null) {
      lines.push({
        text: `capturing option-chain snapshot… ${data.chain.progress.done}/${data.chain.progress.total}`,
        tone: "info",
      });
    } else if (data.chain.latest === null) {
      lines.push({
        text: "no chain snapshot yet — captures automatically after 15:30 ET on trading days; the builder captures selected symbols on demand",
        tone: "warn",
      });
    } else if (data.chain.latest.tradeDate !== data.etDate) {
      lines.push({
        text: `chain data as of ${data.chain.latest.tradeDate} — next capture runs automatically after 15:30 ET on trading days`,
        tone: "info",
      });
    }
  }
  if (lines.length === 0) return null;
  return (
    <div className="collector-banner">
      {lines.map((l) => (
        <span key={l.text} className={l.tone === "warn" ? "chip chip-warn" : "chip"}>
          {l.text}
        </span>
      ))}
    </div>
  );
}
