import { useQuery } from "@tanstack/react-query";
import type { BridgedResult, StudyDigest } from "@console/shared";

/**
 * The studies tab's This week (`?group=week`, orchestrator `study-report --since`): every study with
 * a result written or a queue step finished since Monday, its verdict counts, and which gave no
 * reading. The counts are the orchestrator's (`studyreport.digest`); this prints them.
 */

/** The Monday on or before `d`, as YYYY-MM-DD in `d`'s own calendar fields. */
export function mondayOf(d: Date): string {
  const back = (d.getUTCDay() + 6) % 7;
  const m = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() - back));
  return m.toISOString().slice(0, 10);
}

/** This week's digest, asked for only while the view is open (`since` null otherwise). */
export function useStudyDigest(since: string | null) {
  return useQuery<BridgedResult<StudyDigest>>({
    queryKey: ["bridged", "backtest", "study-digest", since],
    enabled: since !== null,
    refetchInterval: 300_000,
    queryFn: async () => {
      const url = `/api/backtest/studies/digest?since=${since ?? ""}`;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`${url}: HTTP ${String(res.status)}`);
      return (await res.json()) as BridgedResult<StudyDigest>;
    },
  });
}

export function WeekSummary({
  digest,
  loading,
  onOpen,
}: {
  digest: BridgedResult<StudyDigest> | undefined;
  loading: boolean;
  onOpen: (study: string) => void;
}) {
  if (loading) return <p className="muted">Reading this week's studies…</p>;
  if (!digest?.ok || digest.data === null) return <p className="muted">This week could not be read: {digest?.error ?? "no answer"}</p>;
  const d = digest.data;
  const judged = d.totals.pass + d.totals.fail + d.totals.noVerdict;
  const quiet = d.studies.filter((s) => d.noReading.includes(s.id));
  return (
    <section className="study-callout study-week">
      <strong>
        Since {d.since}: {d.studies.length} {d.studies.length === 1 ? "study" : "studies"} moved, {judged} {judged === 1 ? "rule" : "rules"} judged
      </strong>
      <div className="history-controls">
        <span className="chip chip-ok">{d.totals.pass} passed</span>
        <span className="chip chip-bad">{d.totals.fail} failed</span>
        <span className="chip chip-warn">{d.totals.noVerdict} no verdict</span>
      </div>
      {quiet.length > 0 ? (
        <p className="muted">
          No reading yet:{" "}
          {quiet.map((s, i) => (
            <span key={s.study}>
              {i > 0 ? ", " : ""}
              <button type="button" className="link-button" onClick={() => onOpen(s.study)}>
                item {s.id}
              </button>
              {s.failedSteps.length > 0 ? ` (${s.failedSteps.join(", ")} failed)` : ""}
            </span>
          ))}
          . Open one and read its Report tab for what to do next.
        </p>
      ) : null}
      <p className="muted">Counts are the result files' own words, read in the declared order; the register's outcome decides where it has one.</p>
    </section>
  );
}
