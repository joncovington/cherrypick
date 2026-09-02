import { useEffect, useRef, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { useQuote } from "../lib/useQuote";

function fmt(v: number | undefined, digits: number): string {
  return v === undefined ? "—" : v.toFixed(digits);
}

/** One live symbol row: last + bid/ask, tick-flash + roll on change. */
export function LiveQuoteRow({
  symbol,
  digits = 2,
  trailing,
  symbolTo,
}: {
  symbol: string;
  digits?: number;
  trailing?: ReactNode;
  /** Route the symbol cell links to (e.g. the builder); plain text when unset. */
  symbolTo?: string;
}) {
  const q = useQuote(symbol);
  const lastRef = useRef<HTMLTableCellElement>(null);
  const prevTs = useRef<number>(0);

  useEffect(() => {
    if (q === undefined || q.ts === prevTs.current) return;
    prevTs.current = q.ts;
    const el = lastRef.current;
    if (el === null || q.direction === null || q.source !== "dxlink") return;
    el.classList.remove("flash-up", "flash-down", "roll");
    void el.offsetWidth; // restart the animation
    el.classList.add(q.direction === "up" ? "flash-up" : "flash-down", "roll");
  }, [q]);

  const mid =
    q?.last ?? (q?.bid !== undefined && q?.ask !== undefined ? (q.bid + q.ask) / 2 : undefined);

  return (
    <tr>
      <td>
        {symbolTo !== undefined ? (
          <Link to={symbolTo} className="link">
            {symbol}
          </Link>
        ) : (
          symbol
        )}
      </td>
      <td ref={lastRef} className={q?.direction === "down" ? "pnl-neg" : q?.direction === "up" ? "pnl-pos" : ""}>
        {fmt(mid, digits)}
      </td>
      <td className="muted">{fmt(q?.bid, digits)}</td>
      <td className="muted">{fmt(q?.ask, digits)}</td>
      <td className="muted">{q === undefined ? "" : q.source === "dxlink" ? "live" : "cached"}</td>
      {trailing}
    </tr>
  );
}
