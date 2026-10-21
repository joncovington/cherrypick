import type { SkateBand, SkateRecordBand, SkateRecordGroup, SkateRecordPayload, SkateRecordTest } from "@console/shared";
import { useSkateRecord } from "../../lib/api";

/**
 * The skate screen's forward record on the Skate tab (docs/skate-plan.md step 4). `technicals.skate_record`
 * keeps the rows the tab showed, settles them at expiry and writes its reading as `record.json`; this
 * shows that reading and decides nothing. Whether the record is decision-grade and whether a band
 * passes are the file's `decision_grade` and `passes`, never worked out here from the z scores, and
 * the declared test is stated from the file's own thresholds.
 *
 * No tone before the record is decision-grade: a z score or a rate above implied is shown plainly,
 * because twenty expiries is what the test was declared against and an early lead is noise.
 */

const DASH = "—";
const BANDS: { id: SkateBand; label: string }[] = [
  { id: "fixed", label: "fixed" },
  { id: "byExpiry", label: "by expiry" },
];

function pct(v: number | null): string {
  return v === null ? DASH : `${(v * 100).toFixed(1)}%`;
}

function count(v: number | null): string {
  return v === null ? DASH : v.toLocaleString("en-US");
}

function zText(v: number | null): string {
  if (v === null) return DASH;
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(2)}`;
}

/** A gap threshold in rate units as points: 0.05 -> "5 points", 0 -> "zero". */
function gapWords(v: number | null): string {
  if (v === null) return "the declared gap";
  if (v === 0) return "zero";
  // Rounded to a tenth first: 0.08 * 100 is 8.000000000000002.
  const p = Math.round(v * 1000) / 10;
  return `${Number.isInteger(p) ? p.toFixed(0) : p.toFixed(1)} points`;
}

/** The declared test in words, from the file's thresholds. */
export function testWords(t: SkateRecordTest | null): string {
  if (t === null) return "The record states no test thresholds.";
  return (
    `The declared test, judged per band: once ${t.minExpiries ?? DASH} distinct expiries have settled, rows with history minus ` +
    `implied above ${gapWords(t.gapHigh)} must skate more often than their own implied odds, and more often than rows with it ` +
    `below ${gapWords(t.gapLow)}, each by a one-sided z above ${t.zOneSided ?? DASH}. A row that skated but touched the strike ` +
    `on the way counts as skated.`
  );
}

function GroupCells({ g }: { g: SkateRecordGroup | null }) {
  return (
    <>
      <td className="num">{count(g?.n ?? null)}</td>
      <td className="num">
        {pct(g?.rate ?? null)}
        <span className="muted"> vs {pct(g?.implied ?? null)}</span>
      </td>
      <td className="num">{zText(g?.z ?? null)}</td>
    </>
  );
}

/** The file's verdict for one band. Only a decision-grade record with a stated boolean shows pass or fail. */
function VerdictCell({ b, grade, expiries, need }: { b: SkateRecordBand | null; grade: boolean; expiries: string; need: string }) {
  if (!grade) {
    return (
      <td className="muted" title="The test is judged only once the record is decision-grade">
        not yet decision-grade ({expiries} of {need} expiries)
      </td>
    );
  }
  if (b === null || b.passes === null) {
    return (
      <td className="muted" title="The record is decision-grade but states no verdict for this band">
        no verdict stated
      </td>
    );
  }
  return b.passes ? (
    <td title="The record's verdict: the declared test passes on this band">
      <span className="chip chip-ok">pass</span>
    </td>
  ) : (
    <td title="The record's verdict: the declared test does not pass on this band">
      <span className="chip chip-warn">fail</span>
    </td>
  );
}

function etStamp(iso: string | null): string | null {
  if (iso === null) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(d);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")} ${get("hour")}:${get("minute")} ET`;
}

export function SkateRecordView({ data, isLoading, isError }: { data: SkateRecordPayload | undefined; isLoading: boolean; isError: boolean }) {
  const written = etStamp(data?.writtenAt ?? null);
  const t = data?.test ?? null;
  const expiries = count(data?.expiries ?? null);
  const need = count(t?.minExpiries ?? null);
  const added = data ? Object.values(data.added).reduce((a, b) => a + b, 0) : 0;
  return (
    <div style={{ marginTop: 16 }}>
      <div className="card-head">
        <h3>Forward record</h3>
        {written && <span className="card-asof">written {written}</span>}
      </div>
      {isError && <p className="muted">Could not read the forward record.</p>}
      {isLoading && <p className="muted">Reading the forward record…</p>}
      {data?.status === "none" && (
        <p className="muted">
          No forward record written yet: the technicals-skate job writes it each morning, from the rows this tab showed. Until it
          exists there is nothing to count, which is not the same as a count of zero.
        </p>
      )}
      {data?.status === "unreadable" && (
        <p className="chip chip-warn" style={{ whiteSpace: "normal" }}>
          The forward record (record.json) could not be read, so nothing is shown. This is a failed read, not an empty record.
        </p>
      )}
      {data?.status === "ok" && (
        <>
          <p>
            {count(data.recorded)} rows recorded · {count(data.settled)} settled · {expiries} of {need} distinct expiries settled ·{" "}
            {data.decisionGrade ? (
              <strong>decision-grade</strong>
            ) : (
              <span className="muted">
                not yet decision-grade ({expiries} of {need} expiries)
              </span>
            )}
          </p>
          <p className="muted">
            Last run: {added} rows added
            {data.settledNow
              ? `, ${count(data.settledNow.skated)} skated, ${count(data.settledNow.tested)} tested and ${count(data.settledNow.through)} through settled, ${count(data.settledNow.waiting)} waiting on their expiry`
              : ""}
            .
          </p>
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Band</th>
                  <th className="num" title={`Settled rows with history minus implied above ${gapWords(t?.gapHigh ?? null)}`}>
                    High gap n
                  </th>
                  <th className="num" title="Skated rate (tested rows included) against the group's mean implied rate">
                    Skated vs implied
                  </th>
                  <th className="num" title="Skates over the summed implied odds, in Poisson-binomial standard deviations">
                    z
                  </th>
                  <th className="num" title={`Settled rows with history minus implied below ${gapWords(t?.gapLow ?? null)}`}>
                    Below-zero n
                  </th>
                  <th className="num">Skated vs implied</th>
                  <th className="num">z</th>
                  <th className="num" title="Two-proportion z of the high-gap skate rate over the below-zero rate">
                    z high over low
                  </th>
                  <th className="num" title="Settled rows that skated but touched the strike on the way">
                    Tested
                  </th>
                  <th title="The record's own verdict, passed through">Verdict</th>
                </tr>
              </thead>
              <tbody>
                {BANDS.map(({ id, label }) => {
                  const b = data.bands[id];
                  return (
                    <tr key={id} data-band={id}>
                      <td>{label}</td>
                      <GroupCells g={b?.highGap ?? null} />
                      <GroupCells g={b?.belowZero ?? null} />
                      <td className="num">{zText(b?.zHighOverLow ?? null)}</td>
                      <td className="num">{count(b?.tested ?? null)}</td>
                      <VerdictCell b={b} grade={data.decisionGrade} expiries={expiries} need={need} />
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
      <p className="muted">{data?.status === "ok" ? testWords(t) : "The declared test is stated here once the record is read."}</p>
    </div>
  );
}

export function SkateRecordSection() {
  const { data, isLoading, isError } = useSkateRecord();
  return <SkateRecordView data={data} isLoading={isLoading} isError={isError} />;
}
