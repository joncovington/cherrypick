import { useState, type ReactNode } from "react";
import type { SystemLevel, SupervisorJob } from "@console/shared";
import {
  useLogs,
  useLogSources,
  useSystemData,
  useSystemEnvironment,
  useSystemHealth,
  useSystemModules,
  useSystemSupervisor,
} from "../../lib/api";
import { ModuleCellLink } from "../../components/ModuleLink";

/**
 * The System page's tabs. Every figure is the server's reading of a file the suite wrote, and every
 * colour is a level the server attached from the suite's own verdict (see `readers/system.ts`) —
 * this file decides layout, never health.
 *
 * Built from the console's own primitives (`card`, `stats-grid`/`stat-tile`, `data-table`, `chip`,
 * `mode-toggle`) so it reads like every other page.
 */

// --------------------------------------------------------------------------- formatting

function ago(s: number | null | undefined): string {
  if (s === null || s === undefined) return "—";
  if (s < 90) return `${String(Math.round(s))}s`;
  if (s < 5400) return `${String(Math.round(s / 60))}m`;
  if (s < 172_800) return `${String(Math.round(s / 3600))}h`;
  return `${String(Math.round(s / 86_400))}d`;
}

function agoIso(iso: string | null | undefined): string {
  if (iso === null || iso === undefined) return "—";
  const t = Date.parse(iso);
  return Number.isNaN(t) ? "—" : ago(Math.max(0, (Date.now() - t) / 1000));
}

/** "in 4m" for a time ahead, "2m ago" for one behind. */
function relative(iso: string | null | undefined): string {
  if (iso === null || iso === undefined) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "—";
  const d = (t - Date.now()) / 1000;
  return d >= 0 ? `in ${ago(d)}` : `${ago(-d)} ago`;
}

/** What every time the suite prints means: ET. */
function et(iso: string | null | undefined): string {
  if (iso === null || iso === undefined) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  return `${new Date(t).toLocaleString("en-US", {
    timeZone: "America/New_York",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  })} ET`;
}

function bytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  if (n < 1024) return `${String(n)} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]!}`;
}

const LEVEL_CHIP: Record<SystemLevel, string> = {
  ok: "chip chip-ok",
  warn: "chip chip-warn",
  critical: "chip chip-missing",
  unknown: "chip",
};

function statusLevel(status: string | null | undefined): SystemLevel {
  if (status === "OK") return "ok";
  if (status === "WARN") return "warn";
  if (status === "CRITICAL" || status === "ERROR") return "critical";
  return "unknown";
}

function Loading({ isError }: { isError: boolean }) {
  return isError ? <p className="muted">Could not read this from the console server.</p> : <span className="skeleton skeleton-text" style={{ width: "40%" }} />;
}

function Toggle<T extends string>({ value, options, onChange, label }: { value: T; options: readonly T[]; onChange: (v: T) => void; label: string }) {
  return (
    <div className="mode-toggle" style={{ marginLeft: "auto" }} role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o} type="button" className={value === o ? "mode-btn active" : "mode-btn"} onClick={() => onChange(o)}>
          {o}
        </button>
      ))}
    </div>
  );
}

function Section({ title, controls, children, note }: { title: string; controls?: ReactNode; children: ReactNode; note?: ReactNode }) {
  return (
    <section className="card">
      <div className="card-head">
        <h2>{title}</h2>
        {controls}
      </div>
      {children}
      {note !== undefined && <p className="muted system-note">{note}</p>}
    </section>
  );
}

// --------------------------------------------------------------------------- health

export function HealthTab() {
  const { data, isError } = useSystemHealth();
  const [scope, setScope] = useState<"not OK" | "all">("not OK");
  if (data === undefined) return <Loading isError={isError} />;
  const notOk = data.watchdog.findings.filter((f) => f.status !== "OK");
  const findings = scope === "all" || notOk.length === 0 ? data.watchdog.findings : notOk;

  return (
    <div className="system-page">
      <div className="system-checks">
        {data.checks.map((c) => (
          <div key={c.key} className={`stat-tile system-check system-${c.level}`} title={`level from: ${c.source}`}>
            <span className="stat-label">{c.label}</span>
            <span className="system-check-value">{c.value}</span>
            {c.lines.map((l, i) => (
              <span key={i} className="system-check-line muted">
                {l}
              </span>
            ))}
          </div>
        ))}
      </div>

      <Section
        title={`watchdog findings · ${data.watchdog.overall ?? "no report"} · ${String(notOk.length)} not OK of ${String(data.watchdog.findings.length)}`}
        controls={<Toggle value={scope} options={["not OK", "all"] as const} onChange={setScope} label="findings scope" />}
        note={`Last run ${ago(data.watchdog.ageSeconds)} ago (${et(data.watchdog.at)}), every ${String(data.watchdog.intervalMinutes ?? "?")} min; a problem renotifies every ${String(data.watchdog.renotifyMinutes ?? "?")} min. "Since" is how long the finding has held that status.`}
      >
        {notOk.length === 0 && scope === "not OK" && <p className="muted">Every finding is OK; showing all of them.</p>}
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>status</th>
                <th>check</th>
                <th>message</th>
                <th>since</th>
              </tr>
            </thead>
            <tbody>
              {findings.map((f) => (
                <tr key={f.key}>
                  <td>
                    <span className={LEVEL_CHIP[statusLevel(f.status)]}>{f.status}</span>
                  </td>
                  <td title={f.key}>{f.title}</td>
                  <td className="system-wrap">{f.message}</td>
                  <td className="muted" title={f.since ?? undefined}>
                    {f.since !== null ? agoIso(f.since) : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <div className="cards cards-wide">
        <Section title="live trading posture">
          <p>
            <span className={data.live.halted ? "chip chip-missing" : "chip chip-ok"}>{data.live.halted ? "LIVE HALTED (flag present)" : "halt flag clear"}</span>{" "}
            {data.live.alertDaemon !== null && <span className="chip">alert daemon {data.live.alertDaemon.state}</span>}
          </p>
          <table className="data-table">
            <thead>
              <tr>
                <th>module</th>
                <th>live gate</th>
                <th>armed today</th>
              </tr>
            </thead>
            <tbody>
              {data.live.modules.map((m) => (
                <tr key={m.id}>
                  <td>
                    <ModuleCellLink id={m.id}>{m.id}</ModuleCellLink>
                  </td>
                  <td className={m.liveEnabled === true ? "pnl-neg" : "muted"}>{m.liveEnabled === null ? "—" : m.liveEnabled ? "ENABLED" : "paper only"}</td>
                  <td className={m.armedToday ? "pnl-neg" : "muted"}>{m.armedToday ? "ARMED" : m.armDate !== null ? `last ${m.armDate}` : "no"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
        <Section title="notifications" note={data.notify.webhookNote}>
          <dl className="system-dl">
            <dt>channels</dt>
            <dd>{data.notify.channels.join(" · ") || "none"}</dd>
            <dt>trade channels</dt>
            <dd>{data.notify.tradeChannels.join(" · ") || "none"}</dd>
            <dt>last sent</dt>
            <dd>
              {data.notify.last === null ? (
                "nothing in notify.log"
              ) : (
                <>
                  <span className={`log-level lvl-${data.notify.last.level}`}>{data.notify.last.level}</span> {data.notify.last.title}{" "}
                  <span className="muted">({agoIso(data.notify.last.at)} ago)</span>
                </>
              )}
            </dd>
          </dl>
        </Section>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- supervisor

const JOB_FILTERS = ["problems", "running", "all"] as const;

function jobMatches(j: SupervisorJob, f: (typeof JOB_FILTERS)[number]): boolean {
  if (f === "all") return true;
  if (f === "running") return j.runningPid !== null;
  return j.level === "warn" || j.level === "critical";
}

function lastExit(j: SupervisorJob): ReactNode {
  if (j.lastExitCode === null) return <span className="muted">—</span>;
  const cls = j.lastExitCode === 0 || j.lastExitRequested ? "muted" : "pnl-neg";
  return (
    <span className={cls} title={et(j.lastExitAt)}>
      {j.lastExitRequested ? "requested" : `exit ${String(j.lastExitCode)}`} · {agoIso(j.lastExitAt)} ago
    </span>
  );
}

export function SupervisorTab() {
  const { data, isError } = useSystemSupervisor();
  const problems = data?.jobs.filter((j) => jobMatches(j, "problems")).length ?? 0;
  const [filter, setFilter] = useState<(typeof JOB_FILTERS)[number]>("all");
  if (data === undefined) return <Loading isError={isError} />;
  const hb = data.heartbeat;
  const jobs = data.jobs.filter((j) => jobMatches(j, filter));
  const deriveErrors = Object.entries(data.registry.deriveErrors);

  return (
    <div className="system-page">
      <div className="stats-grid">
        <div className={`stat-tile system-check system-${hb.alive ? "ok" : "critical"}`}>
          <span className="stat-label">supervisor</span>
          <span className="system-check-value">{hb.alive ? "alive" : hb.present ? "NOT alive" : "no heartbeat"}</span>
          <span className="system-check-line muted">
            heartbeat {ago(hb.ageSeconds)} ago · pid {String(hb.pid ?? "—")}
            {hb.pidAlive === false && " (gone)"}
          </span>
        </div>
        <div className="stat-tile">
          <span className="stat-label">up since</span>
          <span className="system-check-value">{et(hb.startedAt)}</span>
          <span className="system-check-line muted">{agoIso(hb.startedAt)} · loop {String(hb.loopSeq ?? "—")}</span>
        </div>
        <div className="stat-tile">
          <span className="stat-label">jobs</span>
          <span className="system-check-value">
            {String(data.jobs.length)} · {String(data.jobs.filter((j) => j.runningPid !== null).length)} running
          </span>
          <span className="system-check-line muted">
            {String(hb.residentChildren ?? "—")} resident · {String(hb.rssMb ?? "—")} MB
          </span>
        </div>
        <div className={`stat-tile system-check system-${statusLevel(data.anchor.status)}`}>
          <span className="stat-label">anchor task</span>
          <span className="system-check-value">{data.anchor.message ?? "no watchdog report"}</span>
          <span className="system-check-line muted">relaunches the supervisor every 2 min · probe failures {String(data.anchor.probeFailures ?? "n/r")}</span>
        </div>
        <div className="stat-tile">
          <span className="stat-label">job registry</span>
          <span className="system-check-value">written {ago(data.registry.ageSeconds)} ago</span>
          <span className="system-check-line muted">{deriveErrors.length === 0 ? "no derive errors" : `${String(deriveErrors.length)} derive error(s)`}</span>
        </div>
      </div>

      {(deriveErrors.length > 0 || data.holds.length > 0 || data.restartRequests.length > 0) && (
        <Section title="needs attention">
          <ul className="system-list">
            {deriveErrors.map(([k, v]) => (
              <li key={k}>
                <span className="chip chip-missing">derive error</span> {k}: {v}
              </li>
            ))}
            {data.holds.map((h) => (
              <li key={h.name}>
                <span className="chip chip-warn">held</span> {h.name} — by {h.by ?? "?"} {h.at !== null ? `${agoIso(h.at)} ago` : ""} · <code>run.py start {h.name}</code> releases it
              </li>
            ))}
            {data.restartRequests.map((r) => (
              <li key={r.job}>
                <span className="chip">restart pending</span> {r.job} — requested {agoIso(r.requestedAt)} ago by {r.by ?? "?"}
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title="daemons">
        <table className="data-table">
          <thead>
            <tr>
              <th>daemon</th>
              <th>pid</th>
              <th>launched</th>
              <th>heartbeat</th>
              <th>watchdog</th>
            </tr>
          </thead>
          <tbody>
            {data.daemons.map((d) => (
              <tr key={d.id}>
                <td>{d.id}</td>
                <td className={d.alive === false ? "pnl-neg" : ""}>
                  {d.pid ?? "—"}
                  {d.alive === false && " (gone)"}
                </td>
                <td className="muted" title={et(d.launchedAt)}>
                  {d.launchedAt !== null ? `${agoIso(d.launchedAt)} ago` : "—"}
                </td>
                <td className="muted">{d.heartbeatAgeSeconds !== null ? `${ago(d.heartbeatAgeSeconds)} ago` : "—"}</td>
                <td>{d.watchdog !== null ? <span className={LEVEL_CHIP[statusLevel(d.watchdog.status)]} title={d.watchdog.note}>{d.watchdog.note.split(" [")[0]}</span> : <span className="muted">no report</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <Section
        title={`jobs · ${String(problems)} need attention`}
        controls={<Toggle value={filter} options={JOB_FILTERS} onChange={setFilter} label="job filter" />}
        note="The supervisor's own registry, rewritten every pass. A daily or monthly job's next fire is not recorded, only its schedule and the last day it fired. Hover a state for why a job is disabled, an error for its full text."
      >
        <div className="table-scroll">
          <table className="data-table system-jobs">
            <thead>
              <tr>
                <th>job</th>
                <th>state</th>
                <th>schedule</th>
                <th>pid</th>
                <th>last start</th>
                <th>last exit</th>
                <th>fails</th>
                <th>next</th>
                <th>last error</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j) => (
                <tr key={j.id}>
                  <td>{j.id}</td>
                  <td>
                    <span className={LEVEL_CHIP[j.level]} title={j.enabledReason ?? (j.backoffUntil !== null ? `backoff until ${et(j.backoffUntil)}` : undefined)}>
                      {j.state}
                    </span>
                  </td>
                  <td className="muted">{j.schedule ?? j.kind ?? "—"}</td>
                  <td title={j.pidStartedAt !== null ? `started ${et(j.pidStartedAt)}` : undefined}>{j.runningPid ?? <span className="muted">—</span>}</td>
                  <td className="muted" title={et(j.lastStart)}>
                    {j.lastStart !== null ? `${agoIso(j.lastStart)} ago` : "never"}
                  </td>
                  <td>{lastExit(j)}</td>
                  <td className={j.consecutiveFailures > 0 ? "pnl-neg" : "muted"}>{j.consecutiveFailures}</td>
                  {/* A disabled job's recorded next run is the one it would have had: not a schedule. */}
                  <td className="muted">{!j.enabled ? "—" : j.nextRun !== null ? relative(j.nextRun) : j.lastFireDay !== null ? `fired ${j.lastFireDay.slice(5)}` : "—"}</td>
                  <td className="system-error" title={j.lastError ?? undefined}>
                    {j.lastError ?? ""}
                    {j.stderrLog !== null && j.lastError !== null && <span className="muted"> · {j.stderrLog}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
    </div>
  );
}

// --------------------------------------------------------------------------- modules

export function ModulesTab() {
  const { data, isError } = useSystemModules();
  if (data === undefined) return <Loading isError={isError} />;
  return (
    <div className="system-page">
      <Section
        title="modules"
        note="Heartbeat is the module's own state/<module>.heartbeat; the ledger is its paper_trades.db; the stream request is what it asked the streamer for (state/stream_requests). Watchdog chips are its task and freshness findings."
      >
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>module</th>
                <th>enabled</th>
                <th>live gate</th>
                <th>jobs</th>
                <th>heartbeat</th>
                <th>ledger</th>
                <th>stream request</th>
                <th>watchdog</th>
              </tr>
            </thead>
            <tbody>
              {data.modules.map((m) => (
                <tr key={m.id}>
                  <td>
                    <ModuleCellLink id={m.id}>{m.id}</ModuleCellLink>
                    {m.kind !== null && <span className="muted"> · {m.kind}</span>}
                  </td>
                  <td className={m.enabled ? "" : "muted"}>{m.enabled ? "yes" : "no"}</td>
                  <td className={m.liveEnabled === true ? "pnl-neg" : "muted"}>
                    {m.liveEnabled === null ? "—" : m.liveEnabled ? (m.armedToday ? "ENABLED · ARMED" : "ENABLED") : "paper only"}
                  </td>
                  <td>
                    {m.jobs.length === 0 ? (
                      <span className="muted">—</span>
                    ) : (
                      m.jobs.map((j) => (
                        <span key={j.id} className={`${LEVEL_CHIP[j.level]} system-mini`} title={j.id}>
                          {j.id.slice(m.id.length + 1) || j.id} {j.state}
                        </span>
                      ))
                    )}
                  </td>
                  <td className="muted">{m.heartbeatAgeSeconds !== null ? `${ago(m.heartbeatAgeSeconds)} ago` : "—"}</td>
                  <td className="muted" title={m.ledger?.path}>
                    {m.ledger !== null ? `${bytes(m.ledger.sizeBytes)} · ${ago(m.ledger.ageSeconds)} ago` : "—"}
                  </td>
                  <td className="muted">
                    {m.streamRequest !== null
                      ? `${String(m.streamRequest.symbols)} sym · ${String(m.streamRequest.legs)} legs · ${ago(m.streamRequest.ageSeconds)} ago`
                      : "none"}
                  </td>
                  <td>
                    {m.findings.map((f) => (
                      <span key={f.key} className={`${LEVEL_CHIP[statusLevel(f.status)]} system-mini`} title={f.message}>
                        {f.key.slice(m.id.length + 1)}
                      </span>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
    </div>
  );
}

// --------------------------------------------------------------------------- data

export function DataTab() {
  const { data, isError } = useSystemData();
  if (data === undefined) return <Loading isError={isError} />;
  const totalStores = data.stores.reduce((s, x) => s + (x.sizeBytes ?? 0), 0);
  return (
    <div className="system-page">
      <Section title="freshness" note="When each scheduled artifact last landed, and whether its own run said ok.">
        <table className="data-table">
          <thead>
            <tr>
              <th>artifact</th>
              <th>landed</th>
              <th>result</th>
              <th>detail</th>
            </tr>
          </thead>
          <tbody>
            {data.artifacts.map((a) => (
              <tr key={a.key}>
                <td>{a.label}</td>
                <td className="muted" title={et(a.at)}>
                  {a.at !== null ? `${ago(a.ageSeconds)} ago` : "never"}
                </td>
                <td>{a.ok === null ? <span className="muted">—</span> : <span className={a.ok ? "chip chip-ok" : "chip chip-missing"}>{a.ok ? "ok" : "failed"}</span>}</td>
                <td className="muted system-wrap">{a.detail ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      {data.stream !== null && (
        <Section
          title="stream cache"
          note="The shared cache every module reads, written by the streamer alone. Row counts and the newest write per table."
        >
          <div className="stats-grid">
            <div className="stat-tile">
              <span className="stat-label">last event</span>
              <span className="system-check-value">{ago(data.stream.lastEventAgeSeconds)} ago</span>
              <span className="system-check-line muted">pid {String(data.stream.pid ?? "—")}</span>
            </div>
            <div className="stat-tile">
              <span className="stat-label">connected since</span>
              <span className="system-check-value">{et(data.stream.connectedSince)}</span>
              <span className="system-check-line muted">{String(data.stream.reconnects ?? "—")} reconnects</span>
            </div>
            <div className="stat-tile">
              <span className="stat-label">subscribed</span>
              <span className="system-check-value">{(data.stream.subscribedSymbols ?? 0).toLocaleString()} symbols</span>
            </div>
          </div>
          <table className="data-table">
            <thead>
              <tr>
                <th>table</th>
                <th>rows</th>
                <th>newest write</th>
              </tr>
            </thead>
            <tbody>
              {data.stream.tables.map((t) => (
                <tr key={t.table}>
                  <td>{t.table}</td>
                  <td>{t.rows.toLocaleString()}</td>
                  <td className="muted">{t.newestAgeSeconds !== null ? `${ago(t.newestAgeSeconds)} ago` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}

      <div className="cards cards-wide">
        <Section
          title="storage"
          note={data.storesMeasuredAt !== null ? `Sizes measured ${agoIso(data.storesMeasuredAt)} ago; the walk repeats every 15 minutes.` : "Measuring sizes in the background — this fills in within a minute."}
        >
          {data.disk !== null && (
            <div className="system-disk">
              <div className="system-disk-bar">
                <div style={{ width: `${String(Math.round((1 - data.disk.freeBytes / data.disk.totalBytes) * 100))}%` }} />
              </div>
              <span className="muted">
                {bytes(data.disk.freeBytes)} free of {bytes(data.disk.totalBytes)} on the volume holding ~/.cherrypick
              </span>
            </div>
          )}
          <table className="data-table">
            <thead>
              <tr>
                <th>store</th>
                <th>size</th>
                <th>share</th>
              </tr>
            </thead>
            <tbody>
              {data.stores.map((s) => (
                <tr key={s.label}>
                  <td>{s.label}</td>
                  <td>{bytes(s.sizeBytes)}</td>
                  <td className="muted">{totalStores > 0 && s.sizeBytes !== null ? `${((s.sizeBytes / totalStores) * 100).toFixed(1)}%` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
        <Section title="reader failures" note="Stores whose reads have thrown since the console started. A failing store is served as empty, so this is the only place it shows.">
          {data.readerFailures.length === 0 ? (
            <p className="muted">None since the console started.</p>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>store</th>
                  <th>error</th>
                  <th>count</th>
                  <th>last</th>
                </tr>
              </thead>
              <tbody>
                {data.readerFailures.map((f) => (
                  <tr key={f.path}>
                    <td className="system-wrap">{f.path}</td>
                    <td className="pnl-neg system-wrap">{f.error}</td>
                    <td>{f.count}</td>
                    <td className="muted">{agoIso(f.lastAt)} ago</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Section>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------- environment

export function EnvironmentTab() {
  const { data, isError } = useSystemEnvironment();
  if (data === undefined) return <Loading isError={isError} />;
  const m = data.machine;
  const r = data.runtime;
  const g = data.git;
  const stale = data.processes.filter((p) => p.stale === true);
  return (
    <div className="system-page">
      <div className="stats-grid">
        <div className="stat-tile">
          <span className="stat-label">machine</span>
          <span className="system-check-value">{m.hostname}</span>
          <span className="system-check-line muted">
            {m.platform} · {m.release}
          </span>
          <span className="system-check-line muted">
            up {ago(m.uptimeSeconds)} · {String(m.cpus)} cpus · {bytes(m.freeMemBytes)} free of {bytes(m.totalMemBytes)}
          </span>
        </div>
        <div className={`stat-tile system-check system-${r.python.error !== null ? "warn" : "ok"}`}>
          <span className="stat-label">python</span>
          <span className="system-check-value">{r.python.version ?? "unavailable"}</span>
          <span className="system-check-line muted system-wrap">{r.python.executable ?? r.python.error ?? ""}</span>
        </div>
        <div className="stat-tile">
          <span className="stat-label">console</span>
          <span className="system-check-value">node {r.node}</span>
          <span className="system-check-line muted">
            started {agoIso(r.consoleStartedAt)} ago · built {r.consoleBuiltAt !== null ? `${agoIso(r.consoleBuiltAt)} ago` : "—"}
          </span>
        </div>
        <div className="stat-tile" title="Local receive time minus the exchange's event time over live futures prints in the last five minutes: clock skew, network latency and the streamer's ~1s flush, together.">
          <span className="stat-label">feed lag</span>
          <span className="system-check-value">{data.feedLag?.medianMs != null ? `${(data.feedLag.medianMs / 1000).toFixed(2)}s` : "—"}</span>
          <span className="system-check-line muted">
            {data.feedLag !== null && data.feedLag.samples > 0 ? `median of ${String(data.feedLag.samples)} futures prints` : "no live futures prints in 5 min"}
          </span>
        </div>
      </div>

      <div className="cards cards-wide">
        <Section title="code" note={g.lastFetchAt !== null ? `Ahead/behind is against the last fetch (${agoIso(g.lastFetchAt)} ago); nothing here asks the network.` : "Ahead/behind is against the last fetch; nothing here asks the network."}>
          {g.error !== null ? (
            <p className="pnl-neg">{g.error}</p>
          ) : (
            <dl className="system-dl">
              <dt>branch</dt>
              <dd>
                {g.branch}
                {g.upstream !== null && <span className="muted"> → {g.upstream}</span>}
              </dd>
              <dt>head</dt>
              <dd>
                <code>{g.head}</code> {g.headSubject} <span className="muted">({agoIso(g.headAt)} ago)</span>
              </dd>
              <dt>sync</dt>
              <dd className={g.ahead !== null && g.ahead > 0 ? "pnl-neg" : ""}>
                {g.ahead === null ? "no upstream" : `${String(g.ahead)} ahead · ${String(g.behind)} behind`}
                {g.ahead !== null && g.ahead > 0 && " (unpushed)"}
              </dd>
              <dt>working tree</dt>
              <dd className={g.dirtyFiles !== null && g.dirtyFiles > 0 ? "pnl-neg" : ""}>{g.dirtyFiles === 0 ? "clean" : `${String(g.dirtyFiles)} uncommitted file(s)`}</dd>
            </dl>
          )}
        </Section>
        <Section title="python packages">
          <table className="data-table">
            <tbody>
              {Object.entries(r.python.packages).map(([k, v]) => (
                <tr key={k}>
                  <td>{k}</td>
                  <td className={v === null ? "muted" : ""}>{v ?? "not installed"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      </div>

      <Section
        title={`processes · ${stale.length === 0 ? "all current" : `${String(stale.length)} running older code`}`}
        note="A process loads its code when it starts, so one started before the newest commit to its own packages runs the older code until it restarts. The console runs its build: it is current only if built after that commit and started after the build."
      >
        <table className="data-table">
          <thead>
            <tr>
              <th>process</th>
              <th>started</th>
              <th>code</th>
              <th>newest commit to it</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {data.processes.map((p) => (
              <tr key={p.name}>
                <td>{p.name}</td>
                <td className="muted" title={et(p.startedAt)}>
                  {p.startedAt !== null ? `${agoIso(p.startedAt)} ago` : "—"}
                </td>
                <td className="muted">{p.packages.map((x) => x.replace("packages/", "")).join(" + ")}</td>
                <td className="muted">
                  {p.latestCommit !== null ? (
                    <>
                      <code>{p.latestCommit}</code> {agoIso(p.latestCommitAt)} ago
                    </>
                  ) : (
                    "—"
                  )}
                </td>
                <td>{p.stale === null ? <span className="muted">—</span> : p.stale ? <span className="chip chip-warn">restart to update</span> : <span className="chip chip-ok">current</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
    </div>
  );
}

// --------------------------------------------------------------------------- logs

const LOG_LEVELS = ["ALL", "CRITICAL", "WARN", "INFO", "NOTIFY", "OK"] as const;
const LOG_LIMITS = ["100", "250", "1000"] as const;

export function LogsTab() {
  const [source, setSource] = useState<string>("");
  const [level, setLevel] = useState<(typeof LOG_LEVELS)[number]>("ALL");
  const [limit, setLimit] = useState<(typeof LOG_LIMITS)[number]>("250");
  const { data: sources } = useLogSources();
  const { data } = useLogs(source === "" ? null : source, Number(limit));
  const lines = (data?.lines ?? []).filter((l) => {
    if (level === "ALL") return true;
    if (level === "CRITICAL") return l.level === "CRITICAL" || l.level === "ERROR";
    if (level === "WARN") return l.level === "WARN" || l.level === "WARNING";
    return l.level === level;
  });
  return (
    <div className="system-page">
      <Section
        title="logs"
        controls={
          <div className="system-log-controls">
            <select value={source} onChange={(e) => setSource(e.target.value)} aria-label="log source">
              <option value="">merged: watchdog · notify · trading loops</option>
              {sources?.sources.map((s) => (
                <option key={s.id} value={s.id} disabled={!s.exists}>
                  {s.id}
                  {s.exists ? "" : " (no file)"}
                </option>
              ))}
            </select>
            <Toggle value={limit} options={LOG_LIMITS} onChange={setLimit} label="line count" />
            <Toggle value={level} options={LOG_LEVELS} onChange={setLevel} label="log level filter" />
          </div>
        }
        note="Newest first. Times are converted to ET from whatever each writer used (UTC, ET, or this machine's local time)."
      >
        {data === undefined ? (
          <span className="skeleton skeleton-text" style={{ width: "60%" }} />
        ) : lines.length === 0 ? (
          <p className="muted">no log lines found</p>
        ) : (
          <div className="system-log">
            {[...lines].reverse().map((l, i) => (
              <div key={i} className="log-line">
                <span className={`log-level lvl-${l.level}`}>{l.level}</span>
                <span className="log-source">{l.source}</span>
                <span className="log-text" title={l.text}>
                  <span className="muted">{l.ts !== null ? `${et(l.ts).replace(" ET", "")} ` : ""}</span>
                  {l.text}
                </span>
              </div>
            ))}
          </div>
        )}
      </Section>
    </div>
  );
}
