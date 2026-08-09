import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { mutateJson } from "../../lib/api";
import { useQuote } from "../../lib/useQuote";
import { PayoffChart } from "./PayoffChart";
import { ChainPanel } from "./ChainPanel";
import { fmtMoney } from "../../components/DataTable";

interface LegDraft {
  id: number;
  kind: "call" | "put" | "stock";
  quantity: number;
  strike: string;
  price: string;
  /** Carried from the chain when the leg was picked there — the strike-selection read. */
  delta: number | null;
}

interface PayoffResult {
  curve: Array<{ spot: number; pnl: number }>;
  breakevens: number[];
  maxProfit: { value: number | null; unbounded: boolean };
  maxLoss: { value: number | null; unbounded: boolean };
  netGreeks: Record<string, number | null>;
  slopes: { below: number; above: number };
  pnlAtSpot: number | null;
  pop: number | null;
  expectedMove: number | null;
}

let nextId = 1;

const DEFAULT_LEGS: LegDraft[] = [
  { id: nextId++, kind: "put", quantity: -1, strike: "", price: "", delta: null },
  { id: nextId++, kind: "put", quantity: 1, strike: "", price: "", delta: null },
];

function extremum(e: { value: number | null; unbounded: boolean }): string {
  return e.unbounded ? "unbounded" : fmtMoney(e.value);
}

export function BuilderPage() {
  const [symbol, setSymbol] = useState("SPX");
  const [legs, setLegs] = useState<LegDraft[]>(DEFAULT_LEGS);
  const [iv, setIv] = useState("20");
  const [dte, setDte] = useState("30");
  const [expiration, setExpiration] = useState<string | null>(null);
  const quote = useQuote(symbol);
  const spot =
    quote?.last ?? (quote?.bid !== undefined && quote?.ask !== undefined ? (quote.bid + quote.ask) / 2 : null);

  const validLegs = legs
    .filter((l) => (l.kind === "stock" || l.strike !== "") && l.price !== "" && l.quantity !== 0)
    .map((l) => ({
      kind: l.kind,
      quantity: l.quantity,
      price: Number(l.price),
      strike: l.kind === "stock" ? null : Number(l.strike),
      delta: l.delta,
    }));

  const { data, isFetching } = useQuery<PayoffResult>({
    queryKey: ["payoff", validLegs, spot, iv, dte],
    queryFn: () =>
      mutateJson<PayoffResult>("/api/payoff", "POST", {
        legs: validLegs,
        spot,
        sigma: Number(iv) / 100,
        dte: Number(dte),
      }),
    enabled: validLegs.length > 0,
    placeholderData: (prev) => prev,
  });

  const update = (id: number, patch: Partial<LegDraft>) =>
    setLegs((ls) => ls.map((l) => (l.id === id ? { ...l, ...patch } : l)));

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>Builder</h1>
        <input
          className="text-input"
          value={symbol}
          onChange={(e) => setSymbol(e.target.value.toUpperCase())}
          aria-label="underlying symbol"
          style={{ width: "6rem" }}
        />
        {spot !== null && (
          <span className={`chip ${quote?.source === "dxlink" ? "chip-live" : ""}`}>
            spot {spot.toFixed(2)} {quote?.source === "dxlink" ? "live" : "cached"}
          </span>
        )}
      </div>

      <div className="cards cards-wide">
        <section className="card">
          <div className="panel-head-row">
            <h2>Legs</h2>
            <label className="muted lbl">
              IV %{" "}
              <input className="text-input num-input" value={iv} onChange={(e) => setIv(e.target.value)} />
            </label>
            <label className="muted lbl">
              DTE{" "}
              <input className="text-input num-input" value={dte} onChange={(e) => setDte(e.target.value)} />
            </label>
          </div>
          <table className="data-table">
            <thead>
              <tr>
                <th>kind</th>
                <th>qty (− short)</th>
                <th>strike</th>
                <th>price/share</th>
                <th>Δ</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {legs.map((l) => (
                <tr key={l.id}>
                  <td>
                    <select
                      className="text-input"
                      value={l.kind}
                      onChange={(e) => update(l.id, { kind: e.target.value as LegDraft["kind"] })}
                    >
                      <option value="call">call</option>
                      <option value="put">put</option>
                      <option value="stock">stock</option>
                    </select>
                  </td>
                  <td>
                    <input
                      className="text-input num-input"
                      type="number"
                      value={l.quantity}
                      onChange={(e) => update(l.id, { quantity: Number(e.target.value) })}
                    />
                  </td>
                  <td>
                    <input
                      className="text-input num-input"
                      value={l.strike}
                      disabled={l.kind === "stock"}
                      placeholder={l.kind === "stock" ? "n/a" : "strike"}
                      onChange={(e) => update(l.id, { strike: e.target.value })}
                    />
                  </td>
                  <td>
                    <input
                      className="text-input num-input"
                      value={l.price}
                      placeholder="price"
                      onChange={(e) => update(l.id, { price: e.target.value })}
                    />
                  </td>
                  <td className="muted">{l.delta !== null ? l.delta.toFixed(2) : "—"}</td>
                  <td>
                    <button
                      type="button"
                      className="btn btn-quiet"
                      onClick={() => setLegs((ls) => ls.filter((x) => x.id !== l.id))}
                      aria-label="remove leg"
                    >
                      ✕
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <button
            type="button"
            className="btn"
            style={{ marginTop: "0.6rem" }}
            onClick={() =>
              setLegs((ls) => [
                ...ls,
                { id: nextId++, kind: "call", quantity: -1, strike: "", price: "", delta: null },
              ])
            }
          >
            + add leg
          </button>
        </section>

        <ChainPanel
          symbol={symbol}
          expiration={expiration}
          onExpiration={setExpiration}
          spot={spot}
          onPick={({ kind, strike, price, delta }: { kind: "call" | "put"; strike: number; price: number | null; delta?: number | null }) =>
            setLegs((ls) => [
              ...ls,
              {
                id: nextId++,
                kind,
                quantity: -1,
                strike: String(strike),
                price: price !== null ? price.toFixed(2) : "",
                delta: delta ?? null,
              },
            ])
          }
        />

        <section className={`card ${isFetching ? "" : ""}`}>
          <h2>Payoff at expiry</h2>
          {data ? (
            <>
              <PayoffChart
                curve={data.curve}
                slopes={data.slopes}
                breakevens={data.breakevens}
                spot={spot}
              />
              <div className="stat-row">
                <span className="chip">max profit {extremum(data.maxProfit)}</span>
                <span className="chip">max loss {extremum(data.maxLoss)}</span>
                <span className="chip">
                  breakevens {data.breakevens.length > 0 ? data.breakevens.map((b) => b.toFixed(2)).join(" / ") : "—"}
                </span>
                {data.pop !== null && <span className="chip chip-ok">POP {(data.pop * 100).toFixed(1)}%</span>}
                {data.expectedMove !== null && (
                  <span className="chip">±1σ {data.expectedMove.toFixed(2)}</span>
                )}
                {data.netGreeks["delta"] != null && (
                  <span className="chip">net Δ {data.netGreeks["delta"].toFixed(1)}</span>
                )}
                {data.pnlAtSpot !== null && (
                  <span className={`chip ${data.pnlAtSpot >= 0 ? "chip-ok" : "chip-missing"}`}>
                    at spot {fmtMoney(data.pnlAtSpot)}
                  </span>
                )}
              </div>
            </>
          ) : (
            <p className="muted">fill in legs (strike + price) to compute</p>
          )}
        </section>
      </div>
    </div>
  );
}
