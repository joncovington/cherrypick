import type { SessionProfiles } from "../Intraday/profileOverlay";
import type { Level } from "./LevelsChart";

/**
 * SPX's Market Profile levels for the GEX panel's chart, beside the walls: the same profiles the
 * intraday chart draws (`sessionProfiles` over the live 15-minute candles, SPX's fixed row size) and
 * the same untested POCs (recorded bars plus the live ones). Display only: the panel compares nothing
 * and nothing reads it.
 *
 * While today's session is under way it is drawn as POC, and the session before it as the prior
 * (pPOC, pVAH, pVAL). Outside the session the newest finished session is the prior one, because the
 * panel then shows the next session's chain.
 */
export function profileLevels(profiles: SessionProfiles, untested: ReadonlyArray<readonly [string, number]>): Level[] {
  const { latest, prior, developing } = profiles;
  const out: Level[] = [];
  const priorSession = developing ? prior : latest;
  if (developing && latest !== null) {
    out.push({ price: latest.poc + latest.rowSize / 2, title: "POC", token: "--amber-line", style: "solid" });
  }
  if (priorSession !== null) {
    const row = priorSession.rowSize;
    out.push({ price: priorSession.poc + row / 2, title: "pPOC", token: "--amber-line", style: "dotted" });
    out.push({ price: priorSession.vah + row, title: "pVAH", token: "--accent", style: "dotted" });
    out.push({ price: priorSession.val, title: "pVAL", token: "--accent", style: "dotted" });
  }
  const row = (latest ?? prior)?.rowSize ?? 0;
  for (const [session, poc] of untested) {
    if (session === priorSession?.session) continue;
    out.push({ price: poc + row / 2, title: `nPOC ${session.slice(5)}`, token: "--amber-line", style: "dashed" });
  }
  return out;
}
