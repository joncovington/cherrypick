import TastytradeClient from "@tastytrade/api";
import { loadCredentials } from "../auth/credentials.js";

/**
 * The console's broker session: the OAuth application's shared client secret
 * paired with the console's own read-only refresh token (scope rides on the
 * refresh token). Access tokens are minted/refreshed by the SDK's HTTP client.
 * Market data and reads only — this module never touches order endpoints.
 */

let client: TastytradeClient | null = null;

export function getClient(): TastytradeClient {
  if (client !== null) return client;
  const creds = loadCredentials();
  if (creds === null) {
    throw new Error(
      "no console broker credential — run: python run.py credentials set",
    );
  }
  client = new TastytradeClient({
    ...TastytradeClient.ProdConfig,
    clientSecret: creds.clientSecret,
    refreshToken: creds.refreshToken,
    oauthScopes: ["read"],
  } as ConstructorParameters<typeof TastytradeClient>[0]);
  return client;
}

export function hasCredential(): boolean {
  return loadCredentials() !== null;
}
