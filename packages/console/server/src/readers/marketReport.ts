import path from "node:path";
import type { ConsoleConfig } from "../config.js";

/** The market report's data dir: the configured one, else `market-report` beside technicals'. */
export function marketReportDir(config: ConsoleConfig): string {
  return config.paths.marketReportDir ?? path.join(path.dirname(config.paths.technicalsDir), "market-report");
}
