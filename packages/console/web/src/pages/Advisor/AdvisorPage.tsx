import { useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type {
  AdvisorApplyStatus,
  AdvisorCheckpoint,
  AdvisorEvent,
  AdvisorExperiment,
  AdvisorPair,
  AdvisorProposal,
} from "@console/shared";
import { dismissAdvisorProposal, killAdvisorExperiment, useAdvisor } from "../../lib/api";
import { otherFields, paramRows, scalar } from "./proposalPayload";
import { TabStrip } from "../../components/ScopeBar";
import { pushToast } from "../../lib/toast";

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

/**
 * A card that starts CLOSED and remembers nothing.
 *
 * Deliberately local state rather than the shared `Card`'s `useCollapsed`, which is
 * localStorage-backed and defaults to open. This page's sections are dense and numerous — several
 * checkpoints, a card per proposal, a card per experiment — and the reader needs the tab's summary
 * first and the detail on request. The page body is keyed by tab, so switching tabs remounts these
 * and every section closes again: "collapsed on entering the tab", while an expansion sticks for as
 * long as you stay on it.
 */
function CollapsibleCard({
  head,
  children,
  className = "",
}: {
  head: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const [collapsed, setCollapsed] = useState(true);
  return (
    <section className={`card ${className}`}>
      <div className="card-head">
        <button
          type="button"
          className="btn btn-quiet collapse-toggle"
          onClick={() => setCollapsed(!collapsed)}
          aria-expanded={!collapsed}
          aria-label={collapsed ? "expand" : "collapse"}
        >
          {collapsed ? "▸" : "▾"}
        </button>
        {head}
      </div>
      {!collapsed && children}
    </section>
  );
}

/**
 * What this tab is, in plain English, above the detail.
 *
 * It explains the sections below and states the safety facts that are easy to lose in a wall of
 * cards — nothing here has touched a live loop, and both buttons on the page only ever make the
 * advisor do LESS. The counts are reads of data already fetched; no verdict is computed here, which
 * is the same rule the rest of the page follows (`packages/advisor` decides, this renders).
 */
function TabSummary({
  tab,
  session,
  checkpoints,
  proposals,
  activeExperiments,
  concludedExperiments,
  totalCheckpoints,
  okCheckpoints,
}: {
  tab: Tab;
  session: string | null;
  checkpoints: number;
  proposals: number;
  activeExperiments: number;
  concludedExperiments: number;
  totalCheckpoints: number;
  okCheckpoints: number;
}) {
  const when = session ?? "this session";
  const body: Record<Tab, ReactNode> = {
    today: (
      <>
        <p>
          Each card below is one <strong>checkpoint</strong> — a moment in the trading day when the
          advisor read a fact pack of the suite's own numbers and replied. The card shows what it
          observed, anything it flagged, and what it proposed.
        </p>
        <p className="muted">
          {checkpoints === 0
            ? `No checkpoints recorded for ${when}. The advisor is off by default twice over: the suite must schedule it, and each module must declare its own advice bounds.`
            : `${checkpoints} checkpoint${checkpoints === 1 ? "" : "s"} recorded for ${when}. A checkpoint that failed costs a checkpoint and nothing else — modules run their baseline whenever no advice is admitted.`}
        </p>
      </>
    ),
    proposals: (
      <>
        <p>
          A <strong>proposal</strong> is a parameter change the model suggested, after the
          deterministic validator in <code>packages/advisor</code> accepted or refused it against the
          bounds the module itself declared. One out-of-bounds value refuses the whole artifact.
        </p>
        <p className="muted">
          {proposals === 0
            ? `Nothing proposed for ${when} — a quiet day is a real answer, not a failure.`
            : `${proposals} proposal${proposals === 1 ? "" : "s"} for ${when}. Nothing here has been applied to a live loop: an admitted proposal runs as paper, on a synthetic advised book beside the module's unchanged control. Dismissing one stops it being offered again.`}
        </p>
      </>
    ),
    experiments: (
      <>
        <p>
          Each card is a paper <strong>A/B</strong>: the admitted parameters running as an{" "}
          <code>advised:</code> book beside the module's own control, entered from the same plan, so
          the comparison is exactly paired and any difference is the parameters and nothing else.
        </p>
        <p className="muted">
          {activeExperiments} running, {concludedExperiments} concluded. Verdicts come from the
          suite's own promotion chain, not from the model — an{" "}
          <em>underpowered</em> chip means the sample has not reached the gate, which is neither a
          pass nor a fail. Killing an experiment journals a reason and frees its slot; it never
          touches the control book.
        </p>
      </>
    ),
    history: (
      <>
        <p>
          Every checkpoint the advisor has run, newest session first — <strong>including the ones
          that failed</strong>, which is the point of keeping them.
        </p>
        <p className="muted">
          {okCheckpoints} ok of {totalCheckpoints}. The model is invoked outside every package by a
          scheduled script, so a failed checkpoint can never damage a ledger or a loop — the modules
          simply run their baseline.
        </p>
      </>
    ),
  };
  return (
    <section className="card">
      <div className="card-head">
        <h2>What you are looking at</h2>
        <span className="card-asof">{tab}</span>
      </div>
      {body[tab]}
    </section>
  );
}

// --------------------------------------------------------------------------- apply-status banner

/** One module's answer to "did the artifact issued for this session actually reach the loop?" */
export function EnactmentCell({ status }: { status: AdvisorApplyStatus }) {
  const e = status.enactment;
  if (e === null) {
    // No stored reconciliation: the advisor has not run a slot for this session. Not a failure —
    // an unscored session and a dropped artifact are different facts and must not share a chip.
    return <span className="muted">not scored yet</span>;
  }
  if (e.status === "no_artifact") return <span className="muted">nothing issued</span>;
  if (e.status === "enacted") return <span className="chip">applied</span>;
  if (e.status === "carried") {
    // Not a dropped artifact and not a fresh decision: the module had nothing to decide this
    // session, and the params it already applied are frozen on positions it still holds. Its own
    // chip rather than "applied", so a reader can tell a session that DECIDED from one that
    // inherited -- the experiment is charged for the first and not the second.
    return (
      <>
        <span className="chip">carried</span>
        {e.detail !== null && <div className="muted">{e.detail}</div>}
      </>
    );
  }
  return (
    <>
      <span className="chip chip-warn">not applied</span>
      {e.detail !== null && <div className="muted">{e.detail}</div>}
    </>
  );
}

export function ApplyBanner({ status }: { status: AdvisorApplyStatus[] }) {
  // The card is collapsed by default, so the head is the only thing most readers ever see. A
  // dropped artifact has to be legible THERE: on 2026-08-25 meic and earnings both sat inside this
  // card reading "written" beside "advice_disabled", and nothing on the closed head said so.
  const dropped = status.filter((s) => s.enactment?.status === "not_enacted");
  return (
    <CollapsibleCard
      head={
        <>
          <h2>Advice: written, and whether it landed</h2>
          {dropped.length > 0 ? (
            <span className="chip chip-warn" title={dropped.map((s) => s.module).join(", ")}>
              {dropped.length} not applied
            </span>
          ) : (
            <span className="chip">all applied</span>
          )}
          <span className="card-asof">{status[0]?.nextSession ?? "nothing issued yet"}</span>
        </>
      }
    >
      <div className="table-scroll">
        <table className="data-table advisor-apply">
          <thead>
            <tr>
              <th>Module</th>
              <th>Queued for next session</th>
              <th>Admitted</th>
              <th>Rejected</th>
              <th>Did this session&apos;s artifact land?</th>
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
                <td>
                  {/* The advisor's own reconciliation of the CHOSEN session, not this reader's.
                      Previously this column showed the loop's decision for today beside an artifact
                      for tomorrow — two different sessions, which can never agree, which is exactly
                      why "written" next to "advice_disabled" read as normal for two whole days. */}
                  <EnactmentCell status={s} />
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
      <p className="muted">
        <strong>Not applied</strong> is different, and it is not free: the advisor wrote an artifact
        and the loop never ran under it, so the session bought that experiment no evidence. Those
        sessions are excluded from its <code>sessions run</code> count for the same reason.
      </p>
    </CollapsibleCard>
  );
}

// --------------------------------------------------------------------------- today

function CheckpointCard({ c }: { c: AdvisorCheckpoint }) {
  return (
    <CollapsibleCard
      head={
        <>
          <h2>{c.slot}</h2>
          {c.ok ? <span className="chip">ok</span> : <span className="chip chip-warn">failed</span>}
          <span className="card-asof">{c.model ?? "model not recorded"}</span>
        </>
      }
    >
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
    </CollapsibleCard>
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
    <CollapsibleCard
      head={
        <>
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
        </>
      }
    >

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
    </CollapsibleCard>
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

/**
 * Why this experiment's session count is what it is.
 *
 * Split out of the card so it can be rendered on its own: `CollapsibleCard` draws no children while
 * collapsed, which is every card's default, so anything asserted only through the card is asserted
 * against an empty body.
 *
 * Without this the number on the head is bare — an experiment reading 2/10 after four evenings
 * looks stalled when it was actually starved, which is the reading that nearly fired earnings'
 * kill-at-session-6 rule on sessions its parameter was never applied to.
 */
export function CountingCaveat({ journal }: { journal: AdvisorEvent[] }) {
  const dropped = journal.filter((j) => j.event === "counted" && j.detail?.["enacted"] === false);
  const recount = journal.filter((j) => j.event === "recounted").slice(-1)[0];
  if (dropped.length === 0 && recount === undefined) return null;
  return (
    <p className="review-caveat">
      {dropped.length > 0 && (
        <>
          {`${dropped.length} session${dropped.length === 1 ? "" : "s"} did not count`}: an artifact
          was issued and the loop never ran under it
          {` (${dropped.map((j) => j.session ?? "?").join(", ")}). `}
        </>
      )}
      {recount !== undefined && (
        <>
          {"Counts were re-derived from what the loops recorded: "}
          {`${String(recount.detail?.["sessions_run_recorded"] ?? "?")} → ${String(
            recount.detail?.["sessions_run_derived"] ?? "?",
          )}.`}
        </>
      )}
    </p>
  );
}

export function ExperimentCard({
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
    <CollapsibleCard
      head={
        <>
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
        </>
      }
    >

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
          {e.verdict.recommendation != null && (
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

      <CountingCaveat journal={e.journal} />

      {running && (
        <button type="button" className="btn" disabled={busy} onClick={() => onKill(e.id)}>
          Kill experiment
        </button>
      )}
    </CollapsibleCard>
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

  async function act(run: () => Promise<unknown>, successTitle: string) {
    setBusy(true);
    setActionError(null);
    try {
      await run();
      await queryClient.invalidateQueries({ queryKey: ["advisor"] });
      // These two buttons are this page's only write actions and previously gave no confirmation
      // at all on success -- only the inline error banner existed, so a successful kill/dismiss
      // was silent.
      pushToast({ tone: "info", title: successTitle });
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

      {/* Summary first, then the detail. The body is keyed by tab so every CollapsibleCard below
          remounts closed when you switch tabs — "collapsed on entering the tab" — while an
          expansion sticks for as long as you stay on that tab. */}
      <TabSummary
        tab={tab}
        session={data.session}
        checkpoints={data.latest.length}
        proposals={data.proposals.length}
        activeExperiments={active.length}
        concludedExperiments={concluded.length}
        totalCheckpoints={data.checkpoints.length}
        okCheckpoints={data.checkpoints.filter((c) => c.ok).length}
      />

      <div key={tab}>
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
              onDismiss={(id) => void act(() => dismissAdvisorProposal(id), "Proposal dismissed")}
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
            <ExperimentCard key={e.id} e={e} busy={busy} onKill={(id) => void act(() => killAdvisorExperiment(id), "Experiment killed")} />
          ))}
          {concluded.length > 0 && <div className="review-subhead">Concluded</div>}
          {concluded.map((e) => (
            <ExperimentCard key={e.id} e={e} busy={busy} onKill={(id) => void act(() => killAdvisorExperiment(id), "Experiment killed")} />
          ))}
        </>
      )}

      {tab === "history" && (
        <CollapsibleCard
          head={
            <>
              <h2>Checkpoints</h2>
              <span className="card-asof">
                {data.checkpoints.filter((c) => c.ok).length} ok of {data.checkpoints.length}
              </span>
            </>
          }
        >
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
        </CollapsibleCard>
      )}
      </div>
    </div>
  );
}
