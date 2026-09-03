import { useQuery } from "@tanstack/react-query";
import type { ExperimentGuide, ExperimentGuideEntry, GuideNote, TradingMode } from "@console/shared";
import { Card } from "./DataTable";

/**
 * What each experiment arm (flies) or risk profile (MEIC) is, and why it isn't one of the others.
 *
 * Everything shown is read from the module rather than written about it: the descriptions are the
 * config's own `_note`/`_history_note` entries, and "what makes it different" is derived from the
 * settings themselves. So a retuned arm re-describes itself and this page cannot quietly go stale —
 * which matters more here than anywhere else in the console, since an arm's description is the thing
 * every result about it gets interpreted against.
 */

function useGuide(url: string, mode: TradingMode) {
  return useQuery<ExperimentGuide>({
    queryKey: ["experiment-guide", url, mode],
    queryFn: async () => {
      const res = await fetch(`${url}?mode=${mode}`);
      if (!res.ok) throw new Error(`HTTP ${String(res.status)}`);
      return (await res.json()) as ExperimentGuide;
    },
    // Config and ledger history — it changes when someone edits an arm, not on a timer.
    staleTime: 60_000,
  });
}

/** Config values read as config, not as prose: windows as ranges, lists inline. */
function fmtValue(v: unknown): string {
  if (Array.isArray(v)) {
    if (v.length > 0 && v.every((x) => Array.isArray(x) && x.length === 2)) {
      return (v as Array<[string, string]>).map(([a, b]) => `${a}–${b}`).join(", ");
    }
    return v.length === 0 ? "(none)" : v.map((x) => (typeof x === "object" ? JSON.stringify(x) : String(x))).join(", ");
  }
  if (typeof v === "object" && v !== null) {
    return Object.entries(v as Record<string, unknown>)
      .map(([k, x]) => `${k} ${String(x)}`)
      .join(", ");
  }
  return String(v);
}

/** A note's key as a heading — `history_note` reads as "history". */
function noteLabel(key: string): string {
  return key.replace(/_note$/, "").replace(/_/g, " ");
}

function Notes({ notes }: { notes: GuideNote[] }) {
  const lead = notes.find((n) => n.key === "note");
  const rest = notes.filter((n) => n.key !== "note");
  return (
    <>
      {lead !== undefined && <p className="arm-lead">{lead.text}</p>}
      {rest.map((n) => (
        <div className="arm-note" key={n.key}>
          <span className="arm-note-label">{noteLabel(n.key)}</span>
          <p>{n.text}</p>
        </div>
      ))}
    </>
  );
}

function EntryCard({ entry, unit }: { entry: ExperimentGuideEntry; unit: string }) {
  const distinguishing = entry.overrides.filter((o) => !o.sharedByMostArms && !o.matchesDefault);
  const common = entry.overrides.filter((o) => o.sharedByMostArms || o.matchesDefault);

  return (
    <Card
      title={
        <span className="arm-title">
          {entry.name}
          {entry.removed ? (
            <span className="chip chip-warn" title={`no longer in the config — its ${unit} definition is gone`}>
              gone from config
            </span>
          ) : entry.enabled ? (
            <span className="chip chip-ok">running</span>
          ) : entry.positions > 0 ? (
            <span className="chip" title="disabled, but it has sessions in the book">
              retired
            </span>
          ) : (
            <span className="chip chip-missing">never run</span>
          )}
        </span>
      }
      collapseKey={`guide-${unit}-${entry.name}`}
      defaultCollapsed
    >
      <div className="arm-facts muted">
        {entry.positions > 0 ? (
          <>
            {entry.positions.toLocaleString()} position{entry.positions === 1 ? "" : "s"} · {entry.firstSession} →{" "}
            {entry.lastSession}
          </>
        ) : (
          <>no positions in this book</>
        )}
      </div>

      {entry.removed ? (
        <p className="muted arm-diff-none">
          Still in the ledger but no longer defined in the config, so there is nothing left to describe
          it. It is listed here because the History tab will still show its rows, and a name you cannot
          look up anywhere is worse than one marked as gone.
        </p>
      ) : (
        <div className="arm-diff">
          <span className="fine-label">what makes it different</span>

          {/* Derived facts first — they are the ones a config diff cannot show, and for flies the
              centring rule is the primary axis every comparison is built on. */}
          {entry.derived.map((d) => (
            <p className="arm-centring" key={d.label}>
              {d.label} <strong>{d.value}</strong>
              {d.detail !== null && <span className="muted"> — {d.detail}</span>}
            </p>
          ))}

          {distinguishing.length === 0 ? (
            <p className="muted arm-diff-none">
              Nothing further — every other setting it states is the shared base value or the value
              most of its siblings use.
            </p>
          ) : (
            <table className="data-table arm-diff-table">
              <tbody>
                {distinguishing.map((o) => (
                  <tr key={o.key}>
                    <td className="arm-diff-key">{o.key.replace(/_/g, " ")}</td>
                    <td className="arm-diff-value">{fmtValue(o.value)}</td>
                    <td className="arm-diff-was muted">
                      {o.inDefaults ? `base ${fmtValue(o.fallback)}` : "no shared base value"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {common.length > 0 && (
            <details className="arm-common-details">
              <summary className="muted">
                also states {common.length} setting{common.length === 1 ? "" : "s"} shared with its siblings
              </summary>
              <table className="data-table arm-diff-table">
                <tbody>
                  {common.map((o) => (
                    <tr key={o.key}>
                      <td className="arm-diff-key">{o.key.replace(/_/g, " ")}</td>
                      <td className="arm-diff-value">{fmtValue(o.value)}</td>
                      <td className="arm-diff-was muted">{o.matchesDefault ? "the base value" : "as most do"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          )}
        </div>
      )}

      <Notes notes={entry.notes} />
    </Card>
  );
}

export function ExperimentGuideView({ url, mode, intro }: { url: string; mode: TradingMode; intro: string }) {
  const { data, isLoading, isError } = useGuide(url, mode);

  if (isLoading) return <p className="muted">reading the definitions…</p>;
  if (isError || data?.configMissing === true) {
    return (
      <p className="stale-note">
        Could not read the module's config, so the definitions are unavailable. The numbers on the
        other tabs come from the ledger and are unaffected.
      </p>
    );
  }

  const unit = data?.unit ?? "arm";
  const entries = data?.entries ?? [];
  const running = entries.filter((e) => e.enabled && !e.removed);
  const finished = entries.filter((e) => !e.enabled && !e.removed);
  const gone = entries.filter((e) => e.removed);

  return (
    <div className="arm-help">
      <p className="arm-intro muted">{intro}</p>

      {(data?.breaks ?? []).length > 0 && (
        <div className="cards cards-wide">
          <Card title="Measurement breaks" collapseKey={`guide-breaks-${unit}`} defaultCollapsed>
            <p className="muted arm-diff-none">
              Sessions either side of these dates are not comparable. The module records them itself, so
              a comparison spanning one is a known-bad reading rather than a surprise.
            </p>
            {(data?.breaks ?? []).map((b) => (
              <div className="arm-note" key={`${b.date}-${b.kind}`}>
                <span className="arm-note-label">
                  {b.date} · {b.kind}
                  {b.scope !== "*" && ` · ${b.scope}`}
                </span>
                <p>{b.reason}</p>
              </div>
            ))}
          </Card>
        </div>
      )}

      {(data?.groupNotes ?? []).length > 0 && (
        <div className="cards cards-wide">
          <Card title={`About these ${unit}s`} collapseKey={`guide-about-${unit}`} defaultCollapsed>
            <Notes notes={data?.groupNotes ?? []} />
          </Card>
        </div>
      )}

      <div className="cards cards-wide">
        {running.map((e) => (
          <EntryCard key={e.name} entry={e} unit={unit} />
        ))}
      </div>

      {finished.length > 0 && (
        <>
          <h2 className="arm-section-head">Not currently running</h2>
          <div className="cards cards-wide">
            {finished.map((e) => (
              <EntryCard key={e.name} entry={e} unit={unit} />
            ))}
          </div>
        </>
      )}

      {gone.length > 0 && (
        <>
          <h2 className="arm-section-head">In the book, gone from the config</h2>
          <div className="cards cards-wide">
            {gone.map((e) => (
              <EntryCard key={e.name} entry={e} unit={unit} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
