import TastytradeClient from "@tastytrade/api";
import type { ConsoleCredentials } from "./credentials.js";

export interface ProbeResult {
  ok: boolean;
  error?: string;
  scope?: "read" | "trade";
  account?: string;
}

function isInsufficientScope(err: unknown): boolean {
  const e = err as { response?: { status?: number; data?: unknown } };
  if (e.response?.status !== 403) return false;
  return JSON.stringify(e.response.data ?? "").toLowerCase().includes("scope");
}

/**
 * Validate a credential live and detect its scope. Reads are proven with the
 * customer resource + API quote token; write capability is probed with the
 * broker's DRY-RUN preflight (no order is ever created — the probe order is
 * also deliberately empty, so even a trade-scoped grant only reaches
 * validation). A 403 insufficient-scopes on the dry-run means the refresh
 * token is read-only: write-oriented functions must disable themselves.
 */
export async function probeCredentials(creds: ConsoleCredentials): Promise<ProbeResult> {
  const client = new TastytradeClient({
    ...TastytradeClient.ProdConfig,
    clientSecret: creds.clientSecret,
    refreshToken: creds.refreshToken,
    oauthScopes: ["read"],
  } as ConstructorParameters<typeof TastytradeClient>[0]);

  let accountNumber: string;
  try {
    const accounts = (await client.accountsAndCustomersService.getCustomerAccounts()) as Array<
      Record<string, unknown>
    >;
    const first = accounts?.[0]?.["account"] as Record<string, unknown> | undefined;
    accountNumber = String(first?.["account-number"] ?? "");
    if (accountNumber === "") return { ok: false, error: "credential works but the login has no accounts" };
    await client.accountsAndCustomersService.getApiQuoteToken();
  } catch (err) {
    const e = err as { response?: { status?: number }; message?: string };
    return { ok: false, error: `read validation failed${e.response?.status !== undefined ? ` (HTTP ${e.response.status})` : ""}: ${e.message ?? "unknown"}` };
  }

  const masked = `****${accountNumber.slice(-4)}`;
  try {
    // Dry-run only, and an empty order at that: a trade-scoped token gets a
    // validation error (order shape), a read-scoped one gets the 403 first.
    await client.orderService.postOrderDryRun(accountNumber, { "time-in-force": "Day", "order-type": "Limit", legs: [] });
    return { ok: true, scope: "trade", account: masked };
  } catch (err) {
    if (isInsufficientScope(err)) return { ok: true, scope: "read", account: masked };
    // Any non-scope rejection (422 shape validation etc.) means the scope gate let us through.
    return { ok: true, scope: "trade", account: masked };
  }
}
