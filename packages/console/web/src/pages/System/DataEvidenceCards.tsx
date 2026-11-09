import type { ImpliedVarCheck, ImpliedVarEvidence, QuoteHistoryCoverage, QuoteHistorySession } from "@console/shared";
import { useQuery } from "@tanstack/react-query";
import { Card } from "../../components/DataTable";
import { fmtNum } from "../../lib/format";
import { useBridged } from "../../lib/bridged";
import { HelpSection, type CardHelpContent } from "../../components/CardHelp";
import { Section } from "../../components/Section";
import { useOpenParam } from "../Backtest/CardLightbox";
import { BoxOpen, FliesBoxHost, type FliesBox } from "../Flies/FliesBoxes";

/**
 * System › data, the two reads of `cherrypick.core` (`services/dataEvidenceBridge.ts`):
 *
 * - **Quote-history coverage**: per session, whether the gex recorder kept the chain's quotes, the
 *   sample count, first and last sample, the gaps inside the session and samples with no spot.
 *   Read through `core.quotehistory.session()` only -- this console holds no SQL against the table.
 *   A session with nothing kept is drawn as missing, never left out.
 * - **The implied-variance check**: `core.impliedvar check` for VIX9D and VIX, now. A refusal such
 *   as `not_bracketed` (the cache holds no pair of expirations around the target, its normal state)
 *   is the reading and is shown as such; `truncated` reads low by construction and is no verdict.
 */

function Strip({ sessions }: { sessions: QuoteHistorySession[] }) {
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 3, margin: "0.4rem 0" }} aria-label="sessions recorded">
      {sessions.map((s) => (
        <span
          key={s.session}
          className={s.recorded ? "chip chip-ok" : "chip chip-missing"}
          style={{ fontSize: 10, padding: "1px 5px" }}
          title={
            s.recorded
              ? `${s.session}: ${String(s.samples)} samples ${s.first ?? ""}–${s.last ?? ""}, ${String(s.gaps.length)} gap(s)`
              : `${s.session}: nothing recorded`
          }
        >
          {s.session.slice(5)}
        </span>
      ))}
    </div>
  );
}

function coverageHelp(d: QuoteHistoryCoverage | null): CardHelpContent {
  return {
    title: "Quote-history coverage",
    body: (
      <HelpSection title="What is read">
        <p>
          The chain&apos;s near-spot quotes, kept every recorder tick{d === null ? "" : <> from <strong>{d.recorded_from}</strong></>}, read through{" "}
          <code>core.quotehistory.session()</code>. A gap is any stretch over {String(d?.gap_seconds ?? 60)}s between samples inside a
          session; &quot;no spot&quot; is a sample whose spot could not be joined at or before it. A session with nothing kept is drawn
          as missing, never left out.
        </p>
      </HelpSection>
    ),
  };
}

/** One symbol's per-session table, the long secondary table behind the card's "sessions" button. */
function SessionsTable({ sessions }: { sessions: QuoteHistorySession[] }) {
  return (
    <div className="table-scroll">
      <table className="data-table num-from-1">
        <thead>
          <tr>
            <th>session</th>
            <th>samples</th>
            <th>first</th>
            <th>last</th>
            <th title="stretches longer than the gap threshold between consecutive samples">gaps</th>
            <th>longest gap</th>
            <th title="samples whose spot could not be joined">no spot</th>
            <th title="median strikes (puts + calls) per sample">strikes</th>
          </tr>
        </thead>
        <tbody>
          {sessions.map((r) => (
            <tr key={r.session}>
              <td>{r.session}</td>
              <td>{r.recorded ? String(r.samples) : <span className="muted">none</span>}</td>
              <td className="muted">{r.first ?? "—"}</td>
              <td className="muted">{r.last ?? "—"}</td>
              <td title={r.gaps.map((g) => `${g.from}–${g.to} (${String(g.seconds)}s)`).join("\n") || undefined}>
                {r.recorded ? String(r.gaps.length) : "—"}
              </td>
              <td>{r.max_gap_s === null ? "—" : `${String(r.max_gap_s)}s`}</td>
              <td title={Object.entries(r.no_spot_reasons).map(([k, n]) => `${k} ${String(n)}`).join(", ") || undefined}>
                {r.recorded ? String(r.no_spot) : "—"}
              </td>
              <td className="muted">{r.strikes_median ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CoverageCard() {
  const { data, isLoading, dataUpdatedAt } = useBridged<QuoteHistoryCoverage>(["quote-history"], "/api/system/quote-history", 300_000);
  const d = data?.data ?? null;
  const lb = useOpenParam("qh");
  const boxes: FliesBox[] = Object.entries(d?.symbols ?? {}).map(([symbol, s]) => ({
    id: symbol,
    kicker: "quote history",
    title: `${symbol} · ${String(s.recorded)} of ${String(s.sessions.length)} sessions recorded`,
    tabs: [{ id: "sessions", label: `${String(s.sessions.length)} sessions` }],
    body: () => <SessionsTable sessions={s.sessions} />,
  }));
  return (
    <Card title="quote history — gex recorder" isError={data !== undefined && !data.ok} updatedAt={dataUpdatedAt} help={coverageHelp(d)}>
      {isLoading ? (
        <span className="skeleton skeleton-text" style={{ width: "40%" }} />
      ) : data === undefined || !data.ok || d === null ? (
        <p className="muted">{data?.error ?? "quote-history coverage unavailable"}</p>
      ) : (
        <>
          <FliesBoxHost lb={lb} boxes={boxes} />
          {!d.exists && <p className="muted">The history store does not exist on this machine.</p>}
          {Object.keys(d.symbols).length === 0 ? (
            <p className="muted">Nothing to read yet: recording starts {d.recorded_from}.</p>
          ) : (
            Object.entries(d.symbols).map(([symbol, s]) => (
              <div key={symbol}>
                <div className="card-subhead">
                  {symbol} · {String(s.recorded)} of {String(s.sessions.length)} sessions recorded
                  {s.missing.length > 0 && <span className="muted"> · missing {s.missing.join(", ")}</span>}
                </div>
                <Strip sessions={s.sessions} />
                <div className="pf-mores">
                  <BoxOpen lb={lb} id={symbol}>
                    session table
                  </BoxOpen>
                </div>
              </div>
            ))
          )}
        </>
      )}
    </Card>
  );
}

function verdictCell(c: ImpliedVarCheck) {
  if (!c.ok) return <span className="chip">{c.reason ?? "refused"}</span>;
  const cls = c.verdict === "agrees" ? "chip chip-ok" : c.verdict === "disagrees" ? "chip chip-warn" : "chip";
  return <span className={cls}>{c.verdict}</span>;
}

const IMPLIEDVAR_HELP: CardHelpContent = {
  title: "The implied-variance check",
  body: (
    <HelpSection title="What is compared">
      <p>
        The suite&apos;s model-free implied variance, interpolated to each index&apos;s constant maturity from the two cached expirations
        around it, against the index&apos;s own print, now. <code>not_bracketed</code> means the cache holds no such pair (its normal state:
        it declares only what modules trade); <code>truncated</code> is a strip the cache&apos;s strike window cut, which reads low by
        construction. Off hours the quotes are stale, so expect a refusal.
      </p>
    </HelpSection>
  ),
};

function ImpliedVarCard() {
  const { data, isLoading, dataUpdatedAt } = useQuery<ImpliedVarEvidence>({
    queryKey: ["bridged", "impliedvar"],
    queryFn: async () => {
      const res = await fetch("/api/system/impliedvar");
      if (!res.ok) throw new Error(`impliedvar: HTTP ${String(res.status)}`);
      return (await res.json()) as ImpliedVarEvidence;
    },
    refetchInterval: 120_000,
  });
  return (
    <Card title="implied variance check — core.impliedvar" updatedAt={dataUpdatedAt} help={IMPLIEDVAR_HELP}>
      {isLoading || data === undefined ? (
        <span className="skeleton skeleton-text" style={{ width: "40%" }} />
      ) : (
        <>
          <div className="table-scroll">
          <table className="data-table num-from-1">
            <thead>
              <tr>
                <th>index</th>
                <th>published</th>
                <th>computed</th>
                <th title="computed − published, vol points">diff</th>
                <th>verdict / reason</th>
                <th title="the expirations offered or used">expirations</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(data.checks).map(([index, r]) => {
                const c = r.data;
                if (!r.ok || c === null) {
                  return (
                    <tr key={index}>
                      <td>{index}</td>
                      <td colSpan={5} className="muted">{r.error ?? "unavailable"}</td>
                    </tr>
                  );
                }
                const exps =
                  c.near?.expiration !== undefined
                    ? `${c.near.expiration}${c.near.complete === false ? " (cut)" : ""} · ${c.next?.expiration ?? "—"}${c.next?.complete === false ? " (cut)" : ""}`
                    : (c.offered ?? []).map(([e, days]) => `${e} (${String(days)}d)`).join(", ") || "—";
                return (
                  <tr key={index}>
                    <td>{index}</td>
                    <td>{fmtNum(c.published)}</td>
                    <td>{fmtNum(c.computed ?? null)}</td>
                    <td>{fmtNum(c.diff ?? null)}</td>
                    <td>{verdictCell(c)}</td>
                    <td className="muted" style={{ fontSize: 11 }}>{exps}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          </div>
        </>
      )}
    </Card>
  );
}

/** Rendered under the data tab's own cards. */
export function DataEvidenceCards() {
  return (
    <div className="pf-layout">
      <Section concept="hist" tag="evidence" question="What does the recorded data cover?">
        <CoverageCard />
        <ImpliedVarCard />
      </Section>
    </div>
  );
}
