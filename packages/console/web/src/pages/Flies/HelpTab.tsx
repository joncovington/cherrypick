import { useQuery } from "@tanstack/react-query";
import type { FliesArmGuide, FliesArmGuideEntry, FliesArmNote, TradingMode } from "@console/shared";
import { Card } from "../../components/DataTable";

/**
 * What each arm is, and why it isn't one of the others.
 *
 * Everything here is read from the module rather than written about it: the descriptions are the
 * deployed config's own `_note`/`_history_note` entries, and "what makes it different" is derived
 * from the arm's own settings. So a retuned arm re-describes
 * itself, and this page cannot quietly go stale — which matters more here than anywhere else in the
 * console, since an arm's description is the thing a result gets interpreted against.
 */

function useArmGuide(mode: TradingMode) {
  return useQuery<FliesArmGuide>({
    queryKey: ["flies-arms", mode],
    queryFn: async () => {
      const res = await fetch(`/api/flies/arms?mode=${mode}`);
      if (!res.ok) throw new Error(`HTTP ${String(res.status)}`);
      return (await res.json()) as FliesArmGuide;
    },
    // Config and ledger history — it changes when someone edits an arm, not on a timer.
    staleTime: 60_000,
  });
}

/** Config values read as config, not as prose: windows as ranges, lists inline. */
function fmtValue(v: unknown): string {
  if (Array.isArray(v)) {
    if (v.every((x) => Array.isArray(x) && x.length === 2)) {
      return (v as Array<[string, string]>).map(([a, b]) => `${a}–${b}`).join(", ");
    }
    return v.map((x) => (typeof x === "object" ? JSON.stringify(x) : String(x))).join(", ");
  }
  if (typeof v === "object" && v !== null) return JSON.stringify(v);
  return String(v);
}

/** A note's key as a heading — `history_note` reads as "history". */
function noteLabel(key: string): string {
  return key.replace(/_note$/, "").replace(/_/g, " ");
}

function Notes({ notes }: { notes: FliesArmNote[] }) {
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

function ArmCard({ entry }: { entry: FliesArmGuideEntry }) {
  const distinguishing = entry.overrides.filter((o) => !o.sharedByMostArms && !o.matchesDefault);
  const common = entry.overrides.filter((o) => o.sharedByMostArms || o.matchesDefault);
  return (
    <Card
      title={
        <span className="arm-title">
          {entry.arm}
          {entry.enabled ? (
            <span className="chip chip-ok">running</span>
          ) : entry.retired ? (
            <span className="chip" title="disabled, but it has sessions in the book">
              retired
            </span>
          ) : (
            <span className="chip chip-missing">never run</span>
          )}
        </span>
      }
      collapseKey={`flies-arm-${entry.arm}`}
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

      {/* Distinguishing settings only. A value most arms share differs from `defaults` but from
          almost no sibling, so listing it here buries the one or two that carry the hypothesis. */}
      <div className="arm-diff">
        <span className="fine-label">what makes it different</span>
        {/* Centring first, and always shown. It is the primary axis every flies comparison is built
            on, and it is invisible in a config diff for the arms that take it from their own name. */}
        <p className="arm-centring">
          centres on <strong>{entry.centring === "gex" ? "GEX" : "ATM"}</strong>
          <span className="muted">
            {entry.centringFromName ? " — from the arm's name, no center_rule set" : " — set by center_rule"}
          </span>
        </p>
        {distinguishing.length === 0 ? (
          <p className="muted arm-diff-none">
            Nothing further — every other setting it states is the shared default or the value most
            other arms use.
          </p>
        ) : (
          <table className="data-table arm-diff-table">
            <tbody>
              {distinguishing.map((o) => (
                <tr key={o.key}>
                  <td className="arm-diff-key">{o.key.replace(/_/g, " ")}</td>
                  <td className="arm-diff-value">{fmtValue(o.value)}</td>
                  <td className="arm-diff-was muted">
                    {o.inDefaults ? `default ${fmtValue(o.fallback)}` : "no shared default"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {common.length > 0 && (
          <p className="muted arm-diff-common">
            Also states{" "}
            {common.map((o, i) => (
              <span key={o.key}>
                {i > 0 && ", "}
                <code>
                  {o.key.replace(/_/g, " ")} {fmtValue(o.value)}
                </code>
                {o.matchesDefault ? " (the default)" : " (as most arms do)"}
              </span>
            ))}
            .
          </p>
        )}
      </div>

      <Notes notes={entry.notes} />
    </Card>
  );
}

export function HelpTab({ mode }: { mode: TradingMode }) {
  const { data, isLoading, isError } = useArmGuide(mode);

  if (isLoading) return <p className="muted">reading the arm definitions…</p>;
  if (isError || data?.configMissing === true) {
    return (
      <p className="stale-note">
        Could not read the deployed flies config, so the arm definitions are unavailable. The numbers
        on the other tabs come from the ledger and are unaffected.
      </p>
    );
  }

  const running = (data?.arms ?? []).filter((a) => a.enabled);
  const finished = (data?.arms ?? []).filter((a) => !a.enabled);

  return (
    <div className="arm-help">
      <p className="arm-intro muted">
        Every arm is an independent portfolio trading the same market with the same money, so the only
        thing separating them is which entries their rules allow. Each description below is the
        module's own — read from the deployed config — and "what makes it different" is derived from
        its settings: the values it does not share with the defaults or with most of its siblings.
      </p>

      {(data?.breaks ?? []).length > 0 && (
        <Card title="Measurement breaks" collapseKey="flies-arm-breaks">
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
      )}

      <div className="cards cards-wide">
        {running.map((a) => (
          <ArmCard key={a.arm} entry={a} />
        ))}
      </div>

      {finished.length > 0 && (
        <>
          <h2 className="arm-section-head">Not currently running</h2>
          {(data?.groupNotes ?? []).map((n) => (
            <div className="arm-note arm-group-note" key={n.key}>
              <span className="arm-note-label">{noteLabel(n.key)}</span>
              <p>{n.text}</p>
            </div>
          ))}
          <div className="cards cards-wide">
            {finished.map((a) => (
              <ArmCard key={a.arm} entry={a} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
