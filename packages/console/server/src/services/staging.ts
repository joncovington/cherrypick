/**
 * Order dry-run validation and staged-ticket persistence — the builder's
 * "Stage ticket" action.
 *
 * **This is the single broker-order call site in the whole package**, ported
 * from scout's staging.py with the same invariant: the ONLY order-shaped SDK
 * method this package ever calls is `postOrderDryRun` — buying-power effect,
 * fees, warnings, no order created. It is written here as a literal method
 * call, never threaded through as a variable or parameter, and a source-scan
 * test asserts no code path in this package can reach any order-creation or
 * order-mutation SDK method. Dry-run is still an authenticated
 * write-shaped call: button-triggered only, never on a timer or page load.
 *
 * Staging must not depend on validation succeeding: `stageTicket` always
 * saves, recording a dry-run failure (no credential, read-only-scope 403,
 * network hiccup) in the ticket's dryRun field instead of blocking the save.
 */

import { getClient, hasCredential } from "../market/session.js";

export interface TicketLeg {
  /** OCC option symbol from the chain. */
  symbol: string;
  /** Signed: positive = buy to open, negative = sell to open. */
  quantity: number;
  /** Per share. */
  price: number;
}

function maskAccount(value: unknown): string {
  const s = String(value ?? "");
  return s.length >= 4 ? `****${s.slice(-4)}` : "****";
}

function maskAccounts(obj: unknown): unknown {
  if (Array.isArray(obj)) return obj.map(maskAccounts);
  if (typeof obj === "object" && obj !== null) {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(obj)) {
      out[k] = k === "account-number" || k === "account_number" ? maskAccount(v) : maskAccounts(v);
    }
    return out;
  }
  return obj;
}

export function buildOrderSpec(legs: TicketLeg[]): Record<string, unknown> {
  const orderLegs = legs.map((leg) => ({
    "instrument-type": "Equity Option",
    symbol: leg.symbol,
    action: leg.quantity > 0 ? "Buy to Open" : "Sell to Open",
    quantity: Math.abs(leg.quantity),
  }));
  const spec: Record<string, unknown> = {
    "time-in-force": "Day",
    "order-type": "Limit",
    legs: orderLegs,
  };
  const net = -legs.reduce((s, leg) => s + leg.quantity * leg.price, 0);
  if (net !== 0) {
    spec["price"] = Math.round(Math.abs(net) * 100) / 100;
    spec["price-effect"] = net >= 0 ? "Credit" : "Debit";
  }
  return spec;
}

export interface DryRunResult {
  ok: boolean;
  error?: string;
  account?: string;
  result?: unknown;
}

/** Never throws — every failure becomes {ok:false, error}. */
export async function dryRunOrder(legs: TicketLeg[]): Promise<DryRunResult> {
  if (legs.length === 0) return { ok: false, error: "no legs to validate" };
  if (!hasCredential()) return { ok: false, error: "no console broker credential" };
  try {
    const client = getClient();
    const accounts = (await client.accountsAndCustomersService.getCustomerAccounts()) as Array<
      Record<string, unknown>
    >;
    const first = accounts?.[0]?.["account"] as Record<string, unknown> | undefined;
    const accountNumber = String(first?.["account-number"] ?? "");
    if (accountNumber === "") return { ok: false, error: "no accounts on this login" };
    const spec = buildOrderSpec(legs);
    // The single order-shaped broker call in this package. Dry-run only.
    const result: unknown = await client.orderService.postOrderDryRun(accountNumber, spec);
    return { ok: true, account: maskAccount(accountNumber), result: maskAccounts(result) };
  } catch (err) {
    const e = err as { response?: { status?: number; data?: unknown }; message?: string };
    const status = e.response?.status;
    const detail = e.response?.data !== undefined ? JSON.stringify(e.response.data).slice(0, 400) : e.message;
    return { ok: false, error: `${status !== undefined ? `HTTP ${status}: ` : ""}${detail ?? "unknown error"}` };
  }
}
