import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type {
  AdvisorApplyStatus,
  AdvisorCheckpoint,
  AdvisorExperiment,
  AdvisorPair,
  AdvisorProposal,
} from "@console/shared";
import { dismissAdvisorProposal, killAdvisorExperiment, useAdvisor } from "../../lib/api";
import { otherFields, paramRows, scalar } from "./proposalPayload";
import { TabStrip } from "../../components/ScopeBar";

/**
 * The AI advisor. Renders what it observed, proposed and ran — and judges none of it here.
 *
 * Every verdict on this page was computed in `packages/advisor`, through the suite's own chain
 * (ledger readers → compare_profiles → qualify_readings). The model's keep/kill/promote sits
 * BESIDE those numbers, labelled as a recommendation, never in place of them.
 *
 * Three things this page must keep visible that a tidier rendering would drop:
 *
 * - **Rejections, with their reason.** A refused proposal that disappears gets re-proposed
 *   forever. They are shown next to the admitted ones, not hidden behind a filter.
 * - **Underpowered is not failed.** An experiment below the promotion gate's sample and day
 *   thresholds has not been measured. That is a third state and it renders as one.
 * - **Written and applied are two facts.** The advisor writing an artifact and a module's loop
 *   applying it come apart in ordinary operation; the banner says which of the two happened.
 *
 * Two actions, both narrowing: kill an experiment, dismiss a proposal. Both POST to the advisor's
 * own CLI through the server — this page holds no advisor logic, the same shape as Config.
 */

function count(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : v.toLocaleString();
}

function money(v: unknown): string {
  return typeof v === "number" && Number.isFinite(v)
    ? v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : "—";
}

function pct(v: unknown): string {
  return typeof v === "number" && Number.isFinite(v) ? `${(v * 100).toFixed(1)}%` : "—";
}

function pnlClass(v: unknown): string {
  if (typeof v !== "number" || !Number.isFinite(v) || v === 0) return "";
  return v > 0 ? "pnl-pos" : "pnl-neg";
}

type Tab = "today" | "proposals" | "experiments" | "history";

const TABS: Tab[] = ["today", "proposals", "experiments", "history"];

// --------------------------------------------------------------------------- apply-status banner

function ApplyBanner({ status }: { status: AdvisorApplyStatus[] }) {
  return (
    <section className="card">
      <div className="card-head">
        <h2>Advice for the next session</h2>
        <span className="card-asof">{status[0]?.nextSession ?? "nothing issued yet"}</span>
      </div>
      <div className="table-scroll">
        <table className="data-table data-table-labelled">
          <thead>
            <tr>
              <th>Module</th>
              <th>Artifact</th>
              <th>Admitted</th>
              <th>Rejected</th>
              <th>The loop&apos;s own decision</th>
            </tr>
          </thead>
          <tbody>
            {status.map((s) => (
              <tr key={s.module}>
                <td>{s.module}</td>
                <td>
                  {s.disabledReason !== null ? (
                    <span className="chip chip-missing" title={s.disabledReason}>
                      not accepting advice
                    </span>
                  ) : s.artifactWritten ? (
                    <span className="chip">written</span>
                  ) : (
                    <span className="chip chip-missing">none</span>
                  )}
                </td>
                <td>
                  {s.artifactProposals.length === 0 ? (
                    <span className="muted">—</span>
                  ) : (
                    s.artifactProposals.map((p) => (
                      <div key={p.param}>
                        <code>{p.param}</code> = {String(p.value)}
                      </div>
                    ))
                  )}
                </td>
                <td className={s.artifactRejected.length > 0 ? "pnl-neg" : "muted"}>
                  {s.artifactRejected.length === 0
                    ? "—"
                    : s.artifactRejected.map((r) => (
                        <div key={String(r.param)} title={r.reason}>
                          {String(r.param)}
                        </div>
                      ))}
                </td>
                <td className="muted">
                  {/* The module froze this at session start and replays it all day. It is the only
                      evidence that advice actually reached a loop rather than merely being written. */}
                  {s.consumerDecision === null
                    ? "not read yet"
                    : `${String(s.consumerDecision["day"] ?? "?")} · ${String(
                        s.consumerDecision["reason"] ?? "applied",
                      )}`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted">
        An artifact that carries nothing is not a failure: reject-all is silent from the loop&apos;s
        side, so a written artifact with zero admitted params runs the baseline — exactly as an
        absent one would — and the rejections above are the only record that the advisor tried.
      </p>
    </section>
  );
}

// --------------------------------------------------------------------------- today

function CheckpointCard({ c }: { c: AdvisorCheckpoint }) {
  return (
    <section className="card">
      <div className="card-head">
        <h2>{c.slot}</h2>
        {c.ok ? <span className="chip">ok</span> : <span className="chip chip-warn">failed</span>}
        <span className="card-asof">{c.model ?? "model not recorded"}</span>
      </div>
      {!c.ok && <p className="pnl-neg">{c.error}</p>}
      {c.flags.map((f, i) => (
        <p className="review-caveat" key={i}>
          <span className={`dot ${f.severity === "critical" ? "status-err" : "status-warn"}`} />{" "}
          <strong>{f.module}</strong> {f.text}
          {/* Live-relevant concerns arrive through this same channel, deliberately: the output
              contract gains no new kind for something the advisor cannot act on anyway. */}
          {f.module === "live" && <span className="chip chip-warn">live — propose-only</span>}
        </p>
      ))}
      {c.observations.length === 0 && c.ok && <p className="muted">nothing worth noting</p>}
      <ul>
        {c.observations.map((o, i) => (
          <li key={i}>{o}</li>
        ))}
      </ul>
    </section>
  );
}

// --------------------------------------------------------------------------- proposals

/** Payload entries no card field covers — shown verbatim so nothing is silently lost. */
function OtherFields({ payload }: { payload: Record<string, unknown> }) {
  const rest = otherFields(payload);
  if (rest === null) {
    return <pre className="spec-block">{JSON.stringify(payload, null, 2)}</pre>;
  }
  if (rest.length === 0) return null;
  return (
    <div className="table-scroll">
      <table className="data-table advisor-params">
        <tbody>
          {rest.map(([k, v]) => (
            <tr key={k}>
              <td>
                <code>{k}</code>
              </td>
              <td className="muted">{scalar(v)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ProposalCard({
  p,
  onDismiss,
  busy,
}: {
  p: AdvisorProposal;
  onDismiss: (id: number) => void;
  busy: boolean;
}) {
  const rows = paramRows(p.payload["params"] ?? null);
  const spec = p.payload["spec_json"];
  // What the proposal is ABOUT, which is not always its module: an experiment_spec names the
  // experiment it would start, and a tune or a verdict names the one it addresses. Same shape the
  // experiment cards use, so a proposal and the experiment it became read as the same subject.
  const subject =
    typeof p.payload["name"] === "string"
      ? p.payload["name"]
      : typeof p.payload["experiment_id"] === "string"
        ? p.payload["experiment_id"]
        : null;
  const recommendation = p.payload["recommendation"];
  const sessions = p.payload["sessions"];

  return (
    <section className="card">
      <div className="card-head">
        <h2>
          {p.kind}
          {p.module !== null && <span className="muted"> · {p.module}</span>}
          {subject !== null && <span className="muted"> · {subject}</span>}
        </h2>
        <span className={`chip ${p.status === "rejected" ? "chip-warn" : p.status === "dismissed" ? "chip-missing" : ""}`}>
          {p.status}
        </span>
        {/* A verdict IS its recommendation — a card that shows only the rationale makes the reader
            infer the call from the prose. Labelled as the model's, per this page's whole posture:
            the numbers it argues over were computed in `packages/advisor`, and they are on the
            experiment's own card. */}
        {typeof recommendation === "string" && recommendation !== "" && (
          <span className={`chip ${recommendation === "kill" ? "chip-warn" : ""}`}>
            model recommends {recommendation}
          </span>
        )}
        <span className="card-asof">
          {p.slot ?? "—"}
          {typeof sessions === "number" ? ` · ${sessions} sessions` : ""} · #{p.id}
        </span>
      </div>

      {typeof p.payload["title"] === "string" && <p>{String(p.payload["title"])}</p>}
      {typeof p.payload["hypothesis"] === "string" && (
        <p className="muted">{String(p.payload["hypothesis"])}</p>
      )}
      {typeof p.payload["text"] === "string" && <p>{String(p.payload["text"])}</p>}
      {typeof p.payload["rationale"] === "string" && (
        <p className="muted">{String(p.payload["rationale"])}</p>
      )}
      {typeof p.payload["success_metric"] === "string" && (
        <p className="muted">success metric: {String(p.payload["success_metric"])}</p>
      )}

      {rows !== null && rows.length > 0 && (
        <div className="table-scroll">
          <table className="data-table advisor-params">
            <thead>
              <tr>
                <th>Param</th>
                <th>Proposed</th>
                <th>Why</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={i}>
                  <td>
                    <code>{row.param}</code>
                  </td>
                  <td>{scalar(row.value)}</td>
                  <td className="muted">{row.rationale ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* A creative idea is only actionable if it arrives ready to paste — so it is shown whole,
          selectable, and never applied by anything on this page. */}
      {spec !== undefined && spec !== null && (
        <pre className="spec-block">{JSON.stringify(spec, null, 2)}</pre>
      )}

      <OtherFields payload={p.payload} />

      {p.rejectReason !== null && (
        <p className="review-caveat">
          <span className="dot status-warn" /> {p.rejectReason}
        </p>
      )}

      {p.status !== "dismissed" && (
        <button type="button" className="btn" disabled={busy} onClick={() => onDismiss(p.id)}>
          Dismiss
        </button>
      )}
    </section>
  );
}

// --------------------------------------------------------------------------- experiments

function PairTable({ pairs }: { pairs: AdvisorPair[] }) {
  return (
    <div className="table-scroll">
      <table className="data-table data-table-labelled">
        <thead>
          <tr>
            <th>Book</th>
            <th>Net</th>
            <th>Win rate</th>
            <th>Trades</th>
            <th>Sessions</th>
            <th>Qualified</th>
          </tr>
        </thead>
        <tbody>
          {pairs.flatMap((pair) =>
            [
              { tag: pair.advisedTag, reading: pair.advised },
              { tag: pair.baseTag, reading: pair.base },
            ].map(({ tag, reading }) => {
              const q = (pair.qualification[tag] ?? null) as { qualified?: boolean } | null;
              return (
                <tr key={tag}>
                  <td>{tag}</td>
                  <td className={pnlClass(reading?.["net_pnl"])}>{money(reading?.["net_pnl"])}</td>
                  <td>{pct(reading?.["win_rate"])}</td>
                  <td>{count(reading?.["sample"] as number | null)}</td>
                  <td>{count(reading?.["days"] as number | null)}</td>
                  <td>
                    {reading === null ? (
                      <span className="muted">no rows</span>
                    ) : q?.qualified === true ? (
                      <span className="chip">yes</span>
                    ) : (
                      <span className="chip chip-missing">no</span>
                    )}
                  </td>
                </tr>
              );
            }),
          )}
        </tbody>
      </table>
    </div>
  );
}

function ExperimentCard({
  e,
  onKill,
  busy,
}: {
  e: AdvisorExperiment;
  onKill: (id: string) => void;
  busy: boolean;
}) {
  const running = e.status === "active" || e.status === "queued";
  const enacted = e.journal.filter((j) => j.event === "enacted");
  const lastEnact = enacted[enacted.length - 1];

  return (
    <section className="card">
      <div className="card-head">
        <h2>
          {e.module} <span className="muted">· {e.name ?? e.id}</span>
        </h2>
        <span className={`chip ${e.status === "active" ? "" : "chip-missing"}`}>{e.status}</span>
        {e.verdict?.underpowered === true && (
          <span
            className="chip chip-warn"
            title="Below the promotion gate's sample and day thresholds — not measured, which is neither a pass nor a fail"
          >
            underpowered
          </span>
        )}
        <span className="card-asof">
          {e.sessionsRun} / {e.expiresAfter} sessions
        </span>
      </div>

      {e.hypothesis !== null && e.hypothesis !== "" && <p>{e.hypothesis}</p>}
      {e.successMetric !== null && e.successMetric !== "" && (
        <p className="muted">success metric: {e.successMetric}</p>
      )}

      <div className="table-scroll">
        <table className="data-table data-table-labelled">
          <thead>
            <tr>
              <th>Param</th>
              <th>Value</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(e.params).map(([k, v]) => (
              <tr key={k}>
                <td>
                  <code>{k}</code>
                </td>
                <td>{String(v)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {e.verdict !== null && (
        <>
          <div className="review-subhead">
            Advised against control
            <span className="muted"> · same sessions, same underlying — a paired comparison</span>
          </div>
          <PairTable pairs={e.verdict.pairs} />
          {e.verdict.recommendation !== null && (
            <p className="review-caveat">
              <span className="dot" /> the model recommends{" "}
              <strong>{e.verdict.recommendation.value}</strong> — {e.verdict.recommendation.rationale}
            </p>
          )}
        </>
      )}

      <p className="muted">
        {lastEnact === undefined
          ? "no advice issued for it yet"
          : `last enacted ${lastEnact.session ?? "?"} → ${String(lastEnact.detail?.["target"] ?? "?")}`}
      </p>

      {running && (
        <button type="button" className="btn" disabled={busy} onClick={() => onKill(e.id)}>
          Kill experiment
        </button>
      )}
    </section>
  );
}

// --------------------------------------------------------------------------- page

export function AdvisorPage() {
  const [tab, setTab] = useState<Tab>("today");
  const [session, setSession] = useState<string | undefined>(undefined);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const { data, isLoading, isError } = useAdvisor(session);

  async function act(run: () => Promise<unknown>) {
    setBusy(true);
    setActionError(null);
    try {
      await run();
      await queryClient.invalidateQueries({ queryKey: ["advisor"] });
    } catch (err) {
      setActionError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (isLoading) return <div className="page">loading…</div>;
  if (isError || data === undefined) return <div className="page">the advisor store could not be read</div>;

  const active = data.experiments.filter((e) => e.status === "active" || e.status === "queued");
  const concluded = data.experiments.filter((e) => e.status !== "active" && e.status !== "queued");

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>Advisor</h1>
        <TabStrip tabs={TABS} value={tab} onChange={setTab} ariaLabel="advisor tabs" />
        <select
          className="text-input"
          value={session ?? ""}
          onChange={(e) => setSession(e.target.value === "" ? undefined : e.target.value)}
          aria-label="session"
        >
          <option value="">latest session</option>
          {data.sessions
            .slice()
            .reverse()
            .map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
        </select>
      </div>

      {actionError !== null && <p className="pnl-neg">{actionError}</p>}

      {!data.storePresent && (
        <section className="card">
          <div className="card-head">
            <h2>The advisor has not run yet</h2>
          </div>
          <p className="muted">
            No store at <code>data/advisor/advisor.db</code>. Nothing is scheduled until{" "}
            <code>advisor.enabled</code> is set in the suite config, and no module accepts advice
            until its own config declares an <code>advice</code> block. Both are off by default.
          </p>
        </section>
      )}

      <ApplyBanner status={data.applyStatus} />

      {tab === "today" &&
        (data.latest.length === 0 ? (
          <section className="card">
            <p className="muted">no checkpoints recorded for {data.session ?? "this session"}</p>
          </section>
        ) : (
          data.latest.map((c) => <CheckpointCard key={c.slot} c={c} />)
        ))}

      {tab === "proposals" &&
        (data.proposals.length === 0 ? (
          <section className="card">
            <p className="muted">nothing proposed this session — a quiet day is a real answer</p>
          </section>
        ) : (
          data.proposals.map((p) => (
            <ProposalCard
              key={p.id}
              p={p}
              busy={busy}
              onDismiss={(id) => void act(() => dismissAdvisorProposal(id))}
            />
          ))
        ))}

      {tab === "experiments" && (
        <>
          {active.length === 0 && (
            <section className="card">
              <p className="muted">nothing running</p>
            </section>
          )}
          {active.map((e) => (
            <ExperimentCard key={e.id} e={e} busy={busy} onKill={(id) => void act(() => killAdvisorExperiment(id))} />
          ))}
          {concluded.length > 0 && <div className="review-subhead">Concluded</div>}
          {concluded.map((e) => (
            <ExperimentCard key={e.id} e={e} busy={busy} onKill={(id) => void act(() => killAdvisorExperiment(id))} />
          ))}
        </>
      )}

      {tab === "history" && (
        <section className="card">
          <div className="card-head">
            <h2>Checkpoints</h2>
            <span className="card-asof">
              {data.checkpoints.filter((c) => c.ok).length} ok of {data.checkpoints.length}
            </span>
          </div>
          <div className="table-scroll">
            <table className="data-table data-table-labelled">
              <thead>
                <tr>
                  <th>Session</th>
                  <th>Slot</th>
                  <th>Model</th>
                  <th>Result</th>
                  <th>Observations</th>
                </tr>
              </thead>
              <tbody>
                {data.checkpoints.map((c) => (
                  <tr key={`${c.session}-${c.slot}`}>
                    <td>{c.session}</td>
                    <td>{c.slot}</td>
                    <td className="muted">{c.model ?? "—"}</td>
                    <td className={c.ok ? "" : "pnl-neg"}>{c.ok ? "ok" : (c.error ?? "failed")}</td>
                    <td>{count(c.observations.length)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
