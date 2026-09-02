import TastytradeClient from "@tastytrade/api";
import { loadCredentials } from "../auth/credentials.js";

/**
 * The console's broker session: the OAuth application's shared client secret
 * paired with the console's own read-only refresh token (scope rides on the
 * refresh token). Access tokens are minted/refreshed by the SDK's HTTP client.
 * Market data and reads only — this module never touches order endpoints.
 */

import { getScope } from "../auth/credentials.js";

let client: TastytradeClient | null = null;
let clientScopeKey = "";

export function getClient(): TastytradeClient {
  // The OAuth refresh grant narrows access tokens to the requested scopes,
  // so the client asks for what the probe detected: read-only stays read
  // (a token request for trade would fail), trade-capable gets both so the
  // dry-run validation path isn't capped. Rebuilt if the scope changes.
  const detected = getScope().scope;
  const scopes = detected === "trade" ? ["read", "trade"] : ["read"];
  const key = scopes.join(" ");
  if (client !== null && key === clientScopeKey) return client;
  const creds = loadCredentials();
  if (creds === null) {
    throw new Error("no suite broker credential — set one with: python -m cherrypick.core.auth setup");
  }
  client = new TastytradeClient({
    ...TastytradeClient.ProdConfig,
    clientSecret: creds.clientSecret,
    refreshToken: creds.refreshToken,
    oauthScopes: scopes,
  } as ConstructorParameters<typeof TastytradeClient>[0]);
  clientScopeKey = key;
  return client;
}

export function hasCredential(): boolean {
  return loadCredentials() !== null;
}

/**
 * Drop the cached client so the next getClient() builds a fresh one — the
 * recovery path when the DXLink socket dies underneath the SDK (it can go
 * silent without ever flipping its own state). Old streamer is disconnected
 * best-effort; REST calls mint their own tokens, so nothing else is lost.
 */
export function resetClient(): void {
  try {
    (client?.quoteStreamer as { disconnect?: () => void } | undefined)?.disconnect?.();
  } catch {
    /* already dead */
  }
  client = null;
  clientScopeKey = "";
}
