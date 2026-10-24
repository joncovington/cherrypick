import type { ReactNode } from "react";
import type { ResearchQueue, ResearchTask } from "@console/shared";
import { Card } from "../../components/DataTable";
import { useBridged } from "../../lib/bridged";

/**
 * The Backtest page's queue tab (2026-10-24): the research tasks the supervisor's `research-queue`
 * job runs one at a time, read through `/api/backtest/queue` (`services/researchQueueBridge.ts`, the
 * orchestrator's `queue-list`). Read-only: rows are added with `run.py queue-add` and cancelled with
 * `run.py queue-cancel`. Why a queued row waits is the orchestrator's own reason, shown as given.
 */

const STATUS_CHIP: Record<ResearchTask["status"], string> = {
  queued: "chip",
  running: "chip chip-ok",
  done: "chip chip-ok",
  failed: "chip chip-warn",
  stopped: "chip",
  cancelled: "chip muted",
};

/** "2026-10-26T18:30:05" -> "Oct 26 18:30" (ET, as the orchestrator records it). */
export function stamp(iso: string | null): string {
  if (iso === null || iso === "") return "—";
  const [d, t] = iso.split("T");
  const day = new Date(`${d}T00:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
  return t ? `${day} ${t.slice(0, 5)}` : day;
}

/** The task's arguments in one line: a file's name rather than its full path, other values as given. */
export function argsLine(args: Record<string, unknown>): string {
  return Object.entries(args)
    .map(([k, v]) => {
      if (typeof v === "string" && /[\\/]/.test(v)) return `${k} ${v.split(/[\\/]/).pop() ?? v}`;
      if (Array.isArray(v)) return `${k} ${v.join(" ")}`;
      return `${k} ${String(v)}`;
    })
    .join(" · ");
}

function StateCell({ t }: { t: ResearchTask }) {
  const detail = t.status === "queued" ? (t.waits === "ready" ? "ready: starts at the next tick" : t.waits) : t.note;
  return (
    <td>
      <span className={STATUS_CHIP[t.status]}>{t.status}</span>
      {detail ? <span className="muted"> {detail}</span> : null}
    </td>
  );
}

export function QueueTable({ q }: { q: ResearchQueue }) {
  if (q.tasks.length === 0) {
    return <p className="muted">The queue is empty.</p>;
  }
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th className="num">#</th>
            <th>Task</th>
            <th>Kind</th>
            <th title="What the task was given: a file is shown by its name">Arguments</th>
            <th>State</th>
            <th className="num" title="How many times it has started: a fetch stopped for the nightly window resumes as a new run">
              Runs
            </th>
            <th title="ET">Queued</th>
            <th title="ET">Started</th>
            <th title="ET">Finished</th>
            <th className="num">Exit</th>
          </tr>
        </thead>
        <tbody>
          {q.tasks.map((t) => (
            <tr key={t.id}>
              <td className="num">{t.id}</td>
              <td>
                {t.label ?? "—"}
                {t.needs.includes("thetadata") ? (
                  <span className="chip" title="Needs the vendor account's one ThetaData session">
                    ThetaData
                  </span>
                ) : null}
                {t.notBefore ? <span className="muted"> · not before {stamp(t.notBefore)}</span> : null}
                {t.stopBy ? <span className="muted"> · stop by {stamp(t.stopBy)}</span> : null}
              </td>
              <td>{t.kind}</td>
              <td className="muted">{argsLine(t.args)}</td>
              <StateCell t={t} />
              <td className="num">{t.runs}</td>
              <td>{stamp(t.createdAt)}</td>
              <td>{stamp(t.startedAt)}</td>
              <td>{stamp(t.finishedAt)}</td>
              <td className={t.exitCode !== null && t.exitCode !== 0 ? "num pnl-neg" : "num"}>{t.exitCode ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function BacktestQueueTab() {
  const { data, isLoading, isError, error, dataUpdatedAt } = useBridged<ResearchQueue>(["backtest", "queue"], "/api/backtest/queue", 30_000);
  let body: ReactNode;
  if (isLoading) body = <span className="skeleton skeleton-text" style={{ width: "40%" }} />;
  else if (isError) body = <p className="muted">{(error as Error).message}</p>;
  else if (data === undefined || !data.ok || data.data === null) body = <p className="muted">{data?.error ?? "research queue read unavailable"}</p>;
  else {
    const q = data.data;
    body = (
      <>
        <p className="muted" style={{ margin: "0 0 8px" }}>
          ThetaData session {q.sessionHeld ? "held by another process" : "free"}
          {q.blackout ? ` · no ThetaData task runs ${q.blackout[0]}–${q.blackout[1]} ET on trading days (the nightly backtest-fetch)` : ""}
        </p>
        <QueueTable q={q} />
      </>
    );
  }
  return (
    <Card title="Research queue" collapseKey="backtest-queue" updatedAt={dataUpdatedAt} isError={isError}>
      {body}
      <p className="muted" style={{ marginTop: 8 }}>
        The supervisor runs one task at a time, oldest ready first: an allow-listed fetch (fetch-chains, fetch-flow) or a
        script checked into <code>scripts/</code>. A fetch stopped by the nightly window resumes after it. Add one with{" "}
        <code>run.py queue-add --task &lt;kind&gt; --task-args &apos;&#123;…&#125;&apos;</code>; cancel a queued one with{" "}
        <code>run.py queue-cancel --task-id N</code>. Read-only here.
      </p>
    </Card>
  );
}
