export type WatchdogStatus = "OK" | "WARN" | "ERROR" | string;

export interface WatchdogFinding {
  key: string;
  status: WatchdogStatus;
  title: string;
  message: string;
}

export interface WatchdogSnapshot {
  ts: string | null;
  et: string | null;
  overall: WatchdogStatus | null;
  inSession: boolean;
  isTradingDay: boolean;
  findings: WatchdogFinding[];
  /** Age of the snapshot in seconds, computed server-side; null when the file is missing. */
  ageSeconds: number | null;
}

export interface ServiceEntry {
  id: string;
  enabled: boolean;
}

export interface OverviewPayload {
  watchdog: WatchdogSnapshot;
  services: ServiceEntry[];
  timezone: string | null;
}
