import type { ReactNode } from "react";
import type { SkateBand, SkateRecordBand, SkateRecordGroup, SkateRecordPayload, SkateRecordTest } from "@console/shared";
import { HelpSection, HelpWarn, type CardHelpContent } from "../../components/CardHelp";
import { TileGrid } from "../../components/performance/TileGrid";
import { useSkateRecord } from "../../lib/api";
import { ScreenerCard } from "./ScreenerParts";
import { Section } from "../../components/Section";

/**
 * The skate screen's forward record on the Skate tab. `technicals.skate_record`
 * keeps the rows the tab showed, settles them at expiry and writes its reading as `record.json`; this
 * shows that reading and decides nothing. Whether the record is decision-grade and whether a band
 * passes are the file's `decision_grade` and `passes`, never worked out here from the z scores, and
 * the declared test is stated from the file's own thresholds (behind the card's ?).
 *
 * No tone before the record is decision-grade: a z score or a rate above implied is shown plainly,
 * because twenty expiries is what the test was declared against and an early lead is noise. The z
 * meters are neutral for the same reason: they show how far a z is from the one the test needs, and
 * say nothing about pass or fail.
 *
 * Redesign (2026-11-06): its own card in its own section, a block of tiles a band with z meters and
 * the file's verdict as a chip; the declared test is behind the ?.
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

/** The ? behind the record's card: the declared test, and how the record is kept. */
export function recordHelp(t: SkateRecordTest | null, ok: boolean): CardHelpContent {
  return {
    title: "The forward record",
    body: (
      <>
        <HelpSection title="The declared test">
          <p>{ok ? testWords(t) : "The declared test is stated here once the record is read."}</p>
        </HelpSection>
        <HelpSection title="How it is kept">
          <p>
            The technicals-skate job records the rows the skate card showed, settles them at expiry and writes its reading each morning.
            Whether the record is decision-grade and whether a band passes are the file's own statements, never worked out here.
          </p>
        </HelpSection>
        <HelpWarn title="No tone before decision-grade">
          <p>A z score or a rate above implied is shown plainly until the record has the expiries the test was declared against: an early lead is noise.</p>
        </HelpWarn>
      </>
    ),
  };
}

/** A tile: label, figure, and under it a line; a z tile carries a meter against the z the test needs. */
function RecordTile({ label, value, sub, z, need }: { label: string; value: string; sub?: string; z?: number | null; need?: number | null }) {
  const fill = z === undefined ? null : z === null || need === null || need === undefined || need <= 0 ? 0 : Math.max(0, Math.min(1, z / need)) * 100;
  return (
    <div className="stat-tile">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
      {sub ? (
        <span className="muted" style={{ fontSize: 10, display: "block" }}>
          {sub}
        </span>
      ) : null}
      {fill !== null ? (
        <span className="pos-bar sc-zbar" title={need ? `z against the ${need} the test needs: no tone, it is not a verdict` : "the test's z is not stated"}>
          <span className="pos-bar-fill tc-bar-cap" style={{ width: `${fill}%` }} />
        </span>
      ) : null}
    </div>
  );
}

function groupTiles(prefix: string, g: SkateRecordGroup | null, need: number | null): ReactNode {
  return (
    <>
      <RecordTile label={`${prefix} · n`} value={count(g?.n ?? null)} />
      <RecordTile label={`${prefix} · skated`} value={pct(g?.rate ?? null)} sub={`vs ${pct(g?.implied ?? null)} implied`} />
      <RecordTile label={`${prefix} · z`} value={zText(g?.z ?? null)} z={g?.z ?? null} need={need} />
    </>
  );
}

/** The file's verdict for one band. Only a decision-grade record with a stated boolean shows pass or fail. */
function Verdict({ b, grade, expiries, need }: { b: SkateRecordBand | null; grade: boolean; expiries: string; need: string }) {
  if (!grade) {
    return (
      <span className="muted" title="The test is judged only once the record is decision-grade">
        not yet decision-grade ({expiries} of {need} expiries)
      </span>
    );
  }
  if (b === null || b.passes === null) {
    return (
      <span className="muted" title="The record is decision-grade but states no verdict for this band">
        no verdict stated
      </span>
    );
  }
  return b.passes ? (
    <span className="chip chip-ok" title="The record's verdict: the declared test passes on this band">
      pass
    </span>
  ) : (
    <span className="chip chip-warn" title="The record's verdict: the declared test does not pass on this band">
      fail
    </span>
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
  const ok = data?.status === "ok";
  return (
    <Section concept="hist" tag="forward record" question="Is it holding up?">
      <ScreenerCard
        title="forward record"
        help={recordHelp(t, ok)}
        extra={
          ok ? (
            data.decisionGrade ? (
              <span className="chip">decision-grade</span>
            ) : (
              <span className="chip chip-warn">
                not yet decision-grade · {expiries} of {need}
              </span>
            )
          ) : null
        }
      >
        {written && (
          <p className="muted pf-caption" style={{ marginTop: 0 }}>
            written {written}
          </p>
        )}
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
        {ok && (
          <>
            <p style={{ marginTop: 0 }}>
              {count(data.recorded)} rows recorded · {count(data.settled)} settled · {expiries} of {need} distinct expiries settled ·{" "}
              {data.decisionGrade ? (
                <strong>decision-grade</strong>
              ) : (
                <span className="muted">
                  not yet decision-grade ({expiries} of {need} expiries)
                </span>
              )}
            </p>
            <p className="muted pf-caption">
              Last run: {added} rows added
              {data.settledNow
                ? `, ${count(data.settledNow.skated)} skated, ${count(data.settledNow.tested)} tested and ${count(data.settledNow.through)} through settled, ${count(data.settledNow.waiting)} waiting on their expiry`
                : ""}
              .
            </p>
            {BANDS.map(({ id, label }) => {
              const b = data.bands[id];
              return (
                <div key={id} className="sc-band" data-band={id}>
                  <div className="sc-band-head">
                    <b>{label}</b>
                    <Verdict b={b} grade={data.decisionGrade} expiries={expiries} need={need} />
                  </div>
                  <TileGrid count={4}>
                    {groupTiles("high gap", b?.highGap ?? null, t?.zOneSided ?? null)}
                    {groupTiles("below zero", b?.belowZero ?? null, t?.zOneSided ?? null)}
                    <RecordTile label="z, high over low" value={zText(b?.zHighOverLow ?? null)} z={b?.zHighOverLow ?? null} need={t?.zOneSided ?? null} />
                    <RecordTile label="tested" value={count(b?.tested ?? null)} sub="skated, but touched the strike" />
                  </TileGrid>
                </div>
              );
            })}
          </>
        )}
      </ScreenerCard>
    </Section>
  );
}

export function SkateRecordSection() {
  const { data, isLoading, isError } = useSkateRecord();
  return <SkateRecordView data={data} isLoading={isLoading} isError={isError} />;
}
