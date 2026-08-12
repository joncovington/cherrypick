import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { mutateJson } from "../../lib/api";
import { useQuote } from "../../lib/useQuote";
import { PayoffChart } from "./PayoffChart";
import { ChainPanel } from "./ChainPanel";
import { fmtMoney } from "../../components/DataTable";
import { SymbolCard } from "../../components/SymbolCard";
import { StrategyCards, type ApiLeg } from "../../components/StrategyCards";
import { CollectorBanner } from "../../components/CollectorBanner";
import { StrategyReadout } from "./StrategyReadout";

interface LegDraft {
  id: number;
  kind: "call" | "put" | "stock";
  quantity: number;
  strike: string;
  price: string;
  delta: number | null;
  expiration: string | null;
  /** OCC symbol from the chain — required to stage a ticket. */
  occSymbol: string | null;
  /** The two-sided quote this leg was picked from, for the NET combo spread. */
  bid: number | null;
  ask: number | null;
}

interface CheckItem {
  name: string;
  status: "pass" | "warn" | "fail";
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

  // The describe.py half: strategy-card numbers and their prose.
  direction: "bullish" | "bearish" | "neutral" | null;
  credit: number;
  annualizedReturn: number | null;
  probWorthless: number | null;
  probableRisk2sd: number | null;
  score: number | null;
  /** A defined-risk score is externally validated; an undefined-risk one is our own estimate. */
  scoreIsEstimated: boolean;
  comboSpreadPct: number | null;
  hasWeeklyCadence: boolean | null;
  explanation: string | null;
  greeksText: string | null;
  checklist: CheckItem[];
  checklistDirectional: CheckItem[] | null;
}

let nextId = 1;

function extremum(e: { value: number | null; unbounded: boolean }): string {
  return e.unbounded ? "unbounded" : fmtMoney(e.value);
}

function dteOf(expiration: string | null): number | null {
  if (expiration === null) return null;
  const t = Date.parse(expiration);
  if (Number.isNaN(t)) return null;
  return Math.max(0, Math.round((t - Date.now()) / 86_400_000));
}

function fmtExpiry(expiration: string | null): string {
  if (expiration === null) return "—";
  const t = Date.parse(expiration);
  if (Number.isNaN(t)) return expiration;
  return new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

export function BuilderPage() {
  const [search] = useSearchParams();
  const [symbol, setSymbol] = useState(() => {
    const s = search.get("symbol")?.trim().toUpperCase() ?? "";
    return s !== "" ? s : "SPX";
  });
  const [legs, setLegs] = useState<LegDraft[]>([]);
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
      bid: l.bid,
      ask: l.ask,
    }));

  const { data } = useQuery<PayoffResult>({
    queryKey: ["payoff", validLegs, spot, iv, dte],
    queryFn: () =>
      mutateJson<PayoffResult>("/api/payoff", "POST", {
        legs: validLegs,
        spot,
        sigma: Number(iv) / 100,
        dte: Number(dte),
        symbol,
        expiration,
        // Two-sided quotes drive the NET combo spread the liquidity row grades — per-leg widths
        // are not what it measures.
        quotedLegs: validLegs.map((l) => ({ quantity: l.quantity, bid: l.bid, ask: l.ask })),
      }),
    enabled: validLegs.length > 0,
    placeholderData: (prev) => prev,
  });

  const update = (id: number, patch: Partial<LegDraft>) =>
    setLegs((ls) => ls.map((l) => (l.id === id ? { ...l, ...patch } : l)));

  const qc = useQueryClient();
  const status = useQuery<{ credentialScope: "read" | "trade" | null }>({
    queryKey: ["status"],
    queryFn: async () => (await fetch("/api/status")).json() as Promise<{ credentialScope: "read" | "trade" | null }>,
    staleTime: 60_000,
  });
  const readOnly = status.data?.credentialScope === "read";
  const stageable = legs.length > 0 && legs.every((l) => l.occSymbol !== null && l.price !== "" && l.quantity !== 0);
  const stage = useMutation({
    mutationFn: () =>
      mutateJson<{ ticket: { id: string; dryRun: { ok: boolean; error?: string; skipped?: boolean } } }>("/api/orders/stage", "POST", {
        symbol,
        strategy: null,
        legs: legs.map((l) => ({ symbol: l.occSymbol, quantity: l.quantity, price: Number(l.price) })),
        credit: data?.maxProfit.value ?? null,
        maxRisk: data?.maxLoss.value ?? null,
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["staged"] }),
  });

  return (
    <div className="page">
      <CollectorBanner chain />
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
        <label className="muted lbl">
          IV % <input className="text-input num-input" value={iv} onChange={(e) => setIv(e.target.value)} />
        </label>
        <label className="muted lbl">
          DTE <input className="text-input num-input" value={dte} onChange={(e) => setDte(e.target.value)} />
        </label>
      </div>

      <div className="cards">
        <SymbolCard symbol={symbol} />
        <StrategyCards
          symbol={symbol}
          onApply={(apiLegs: ApiLeg[], exp: string) => {
          setLegs(
            apiLegs.map((l) => ({
              id: nextId++,
              kind: l.kind,
              quantity: l.quantity,
              strike: l.strike !== null ? String(l.strike) : "",
              price: l.price.toFixed(2),
              delta: l.delta,
              expiration: l.expiration,
              occSymbol: l.occSymbol,
              // A suggested strategy carries no two-sided quote, so the liquidity row warns
              // rather than grading a spread nobody measured.
              bid: null,
              ask: null,
            })),
          );
            if (exp !== "") {
              setExpiration(exp);
              const d = dteOf(exp);
              if (d !== null) setDte(String(d));
            }
          }}
        />
      </div>

      <div className="cards cards-wide">
        <section className="card">
          <div className="panel-head-row">
            <h2>Order details</h2>
            <button
              type="button"
              className="btn"
              style={{ marginLeft: "auto" }}
              disabled={!stageable || stage.isPending}
              title={
                !stageable
                  ? "legs must come from the chain (need OCC symbols) with prices"
                  : readOnly
                    ? "read-only credential — the ticket saves WITHOUT broker dry-run validation"
                    : "validate via broker dry-run (no order created) and save the ticket"
              }
              onClick={() => stage.mutate()}
            >
              {stage.isPending ? "staging…" : readOnly ? "Stage ticket (no validation)" : "Stage ticket (dry-run)"}
            </button>
          </div>
          {stage.data && (
            <p className={stage.data.ticket.dryRun.ok ? "muted" : "stale-note"} style={{ marginTop: 0 }}>
              {stage.data.ticket.dryRun.ok
                ? "ticket staged — dry-run validated, no order created"
                : stage.data.ticket.dryRun.skipped === true
                  ? "ticket staged — dry-run skipped (read-only credential)"
                  : `ticket staged; dry-run failed: ${stage.data.ticket.dryRun.error ?? "unknown"}`}
            </p>
          )}
          {legs.length === 0 ? (
            <p className="muted">click bid (sell) or ask (buy) in the chain below to add legs</p>
          ) : (
            <table className="data-table legs-table">
              <tbody>
                {legs.map((l) => {
                  const legDte = dteOf(l.expiration);
                  const short = l.quantity < 0;
                  return (
                    <tr key={l.id}>
                      <td>
                        <input
                          className="text-input qty-input"
                          type="number"
                          value={l.quantity}
                          onChange={(e) => update(l.id, { quantity: Number(e.target.value) })}
                          aria-label="quantity (negative = short)"
                        />
                      </td>
                      <td>{fmtExpiry(l.expiration)}</td>
                      <td className="muted">{legDte !== null ? `${legDte}d` : "—"}</td>
                      <td>
                        <input
                          className="text-input num-input"
                          value={l.strike}
                          disabled={l.kind === "stock"}
                          onChange={(e) => update(l.id, { strike: e.target.value })}
                          aria-label="strike"
                        />
                      </td>
                      <td className="leg-kind">{l.kind === "stock" ? "S" : l.kind === "call" ? "C" : "P"}</td>
                      <td>
                        <span className={`chain-badge ${short ? "chain-badge-short" : "chain-badge-long"}`}>
                          {short ? "STO" : "BTO"}
                        </span>
                      </td>
                      <td>
                        <input
                          className="text-input num-input"
                          value={l.price}
                          onChange={(e) => update(l.id, { price: e.target.value })}
                          aria-label="price (mid)"
                        />
                      </td>
                      <td className="muted">{l.delta !== null ? `Δ ${l.delta.toFixed(2)}` : ""}</td>
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
                  );
                })}
              </tbody>
            </table>
          )}
        </section>

        {data && (
          <section className="card">
            <h2>Payoff at expiry</h2>
            <PayoffChart curve={data.curve} slopes={data.slopes} breakevens={data.breakevens} spot={spot} />
            <div className="stat-row">
              <span className="chip">max profit {extremum(data.maxProfit)}</span>
              <span className="chip">max loss {extremum(data.maxLoss)}</span>
              <span className="chip">
                breakevens {data.breakevens.length > 0 ? data.breakevens.map((b) => b.toFixed(2)).join(" / ") : "—"}
              </span>
              {data.pop !== null && <span className="chip chip-ok">POP {(data.pop * 100).toFixed(1)}%</span>}
              {data.expectedMove !== null && <span className="chip">±1σ {data.expectedMove.toFixed(2)}</span>}
              {data.netGreeks["delta"] != null && (
                <span className="chip">net Δ {data.netGreeks["delta"].toFixed(1)}</span>
              )}
              {data.pnlAtSpot !== null && (
                <span className={`chip ${data.pnlAtSpot >= 0 ? "chip-ok" : "chip-missing"}`}>
                  at spot {fmtMoney(data.pnlAtSpot)}
                </span>
              )}
            </div>
          </section>
        )}

        {data && <StrategyReadout data={data} symbol={symbol} expiration={expiration} />}

        <ChainPanel
          symbol={symbol}
          expiration={expiration}
          onExpiration={setExpiration}
          spot={spot}
          legs={legs
            .filter((l) => l.kind !== "stock" && l.strike !== "")
            .map((l) => ({ kind: l.kind as "call" | "put", strike: Number(l.strike), quantity: l.quantity }))}
          onPick={({ kind, strike, quantity, price, delta, expiration: exp, occSymbol, atmIv, bid, ask }) => {
            // Auto-fill the POP inputs from the chain: DTE from the picked
            // expiration, IV from the ATM call. Manual edits still win after.
            const d = dteOf(exp);
            if (d !== null) setDte(String(d));
            if (atmIv !== null) setIv((atmIv * 100).toFixed(1));
            setLegs((ls) => [
              ...ls,
              {
                id: nextId++,
                kind,
                quantity,
                strike: String(strike),
                price: price !== null ? price.toFixed(2) : "",
                delta,
                expiration: exp,
                occSymbol,
                bid,
                ask,
              },
            ]);
          }}
        />
      </div>
    </div>
  );
}
