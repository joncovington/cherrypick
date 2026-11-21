import { Link, useSearchParams } from "react-router-dom";
import type {
  AutomationsGroupsRead,
  AutomationsGroupUnit,
  AutomationsLine,
} from "@console/shared";
import { Card, DataCard } from "../../components/DataTable";
import { HelpSection, type CardHelpContent } from "../../components/CardHelp";
import { Section } from "../../components/Section";
import { FliesTile } from "../Flies/FliesSession";
import { useGroupUnits } from "../../lib/api";
import { fmtMoney } from "../../lib/format";
import { automationHref } from "./automationsModels";

/**
 * A group read as one unit, and the "by automation / by group" switch on performance and costs. Every
 * figure is the automations package's (`cli groups-read`): the members' line stacked, net and drawdown
 * against the allocation, the most risk held at once against the cap and each member's against its
 * share, the sessions the cap or a share refused, and how the members move together (`core.portfolio`).
 * The page only lays them out.
 */

const HELP: CardHelpContent = {
  title: "The group as a unit",
  body: (
    <>
      <HelpSection title="Against its allocation">
        <p>
          The members' closed trades stacked as sized: the net and the largest
          fall of the running net, each also as a share of the group's
          allocation.
        </p>
      </HelpSection>
      <HelpSection title="Risk at once">
        <p>
          Each position's maximum loss at the mid, the measure the cap reads,
          summed over what was open on a session. It is read a session at a
          time, so a position closed earlier in the day another entered counts
          with it: an upper bound. Each member's is read against its share of
          the cap.
        </p>
      </HelpSection>
      <HelpSection title="Refused">
        <p>
          Sessions a member missed because the group's cap, or its own share of
          it, had no room left. A session counts by its last try in the window.
        </p>
      </HelpSection>
      <HelpSection title="Moving together">
        <p>
          Each pair's net per session, correlated two ways. Counting a session
          either closed nothing as zero asks whether they lose together;
          counting only sessions both closed something asks whether, when both
          trade, they move together. Effective members is how many independent
          automations the group behaves like. Under twenty sessions a figure is
          thin: shown, never a finding.
        </p>
      </HelpSection>
    </>
  ),
};

const pct = (v: number | null | undefined): string =>
  v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`;
const corr = (v: number | null): string => (v === null ? "—" : v.toFixed(2));

export function GroupUnitBody({ g }: { g: AutomationsGroupUnit }) {
  const l = g.line;
  const t = g.together;
  return (
    <>
      <FliesTile
        title="net P&L"
        value={l.trades === 0 ? "—" : fmtMoney(l.net)}
        tone={l.net > 0 ? "pos" : l.net < 0 ? "neg" : "dim"}
        foot={
          g.allocation
            ? `${pct(g.against_allocation?.net)} of its ${fmtMoney(g.allocation)} allocation`
            : "no allocation set"
        }
        help={HELP}
      />
      <FliesTile
        title="drawdown"
        value={l.trades === 0 ? "—" : fmtMoney(-l.drawdown)}
        tone={l.drawdown > 0 ? "neg" : "dim"}
        foot={
          g.allocation
            ? `${pct(g.against_allocation?.drawdown)} of its allocation`
            : "largest fall of the members' running net"
        }
      />
      <FliesTile
        title="most risk at once"
        value={g.risk === null ? "—" : fmtMoney(g.risk.peak)}
        tone={
          g.risk?.of_cap !== null &&
          g.risk?.of_cap !== undefined &&
          g.risk.of_cap > 1
            ? "neg"
            : "dim"
        }
        foot={
          g.risk === null
            ? "nothing held yet"
            : g.max_risk
              ? `${pct(g.risk.of_cap)} of its ${fmtMoney(g.max_risk)} cap, on ${g.risk.session}`
              : `on ${g.risk.session}; no cap set`
        }
      />
      <FliesTile
        title="refused"
        value={String(g.refused.cap + g.refused.share + g.refused.unknown)}
        tone="dim"
        foot={`${String(g.refused.cap)} by the cap, ${String(g.refused.share)} by a member's share`}
      />
      <Card
        title="how its members move together"
        collapseKey={`automations-group-together-${g.name}`}
        className="lv-12"
        help={HELP}
      >
        {t === null ? (
          <p className="muted">
            Fewer than two of its members have closed a trade, so there is
            nothing to set side by side yet.
          </p>
        ) : (
          <>
            <dl className="ar-facts">
              <dt>effective members</dt>
              <dd>
                {t.effective_members === null
                  ? "—"
                  : t.effective_members.toFixed(2)}{" "}
                of {t.members_counted.length} counted
                {t.thin && <span className="chip grp-thin">thin</span>}
              </dd>
              <dt>drawdown</dt>
              <dd>
                {fmtMoney(-t.drawdown.combined)} together against{" "}
                {fmtMoney(-t.drawdown.sum_of_arms)} for its members apart
                {t.drawdown.benefit !== null &&
                  `, ${pct(t.drawdown.benefit)} less`}
              </dd>
              <dt>sessions</dt>
              <dd>
                {t.shared_periods} shared; thin under {t.min_periods}
              </dd>
            </dl>
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>members</th>
                    <th className="num">lose together</th>
                    <th className="num">move together when both trade</th>
                  </tr>
                </thead>
                <tbody>
                  {t.pairs.map((p) => (
                    <tr key={`${p.a}|${p.b}`}>
                      <td>
                        {p.a} · {p.b}
                        {p.thin && <span className="chip grp-thin">thin</span>}
                      </td>
                      <td className="num">
                        {corr(p.zero_filled)}{" "}
                        <span className="muted">({p.zero_filled_periods})</span>
                      </td>
                      <td className="num">
                        {corr(p.similarity)}{" "}
                        <span className="muted">({p.similarity_periods})</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Card>
      <DataCard
        title="each member's risk against its share"
        className="lv-12"
        headers={["member", "share", "most at once", "its limit"]}
        rowCount={g.member_risk.length}
        loading={false}
        numFrom={1}
        empty="no members"
        help={HELP}
      >
        {g.member_risk.map((m) => (
          <tr key={m.name}>
            <td>
              <Link to={automationHref(m.name)}>{m.name}</Link>
            </td>
            <td>{m.share === null ? "—" : `${String(m.share)}%`}</td>
            <td>{m.peak === null ? "—" : fmtMoney(m.peak)}</td>
            <td
              className={
                m.of_limit !== null && m.of_limit > 1 ? "pnl-neg" : undefined
              }
            >
              {m.limit === null
                ? "—"
                : `${fmtMoney(m.limit)} · ${pct(m.of_limit)}`}
            </td>
          </tr>
        ))}
      </DataCard>
      {g.refused.latest.length > 0 && (
        <DataCard
          title="its latest refusals"
          className="lv-12"
          headers={["session", "automation", "refused by"]}
          rowCount={g.refused.latest.length}
          loading={false}
          empty="none"
          help={HELP}
        >
          {g.refused.latest.map((r) => (
            <tr key={`${r.session}|${r.automation}`}>
              <td>{r.session}</td>
              <td>
                <Link to={automationHref(r.automation)}>{r.automation}</Link>
              </td>
              <td>
                {r.by === "cap"
                  ? "the group's cap"
                  : r.by === "share"
                    ? "its share"
                    : "the cap (which not recorded)"}
              </td>
            </tr>
          ))}
        </DataCard>
      )}
    </>
  );
}

/** The group's own view in Groups: the unit beneath its controls and members. */
export function GroupUnitSection({ group }: { group: string }) {
  const q = useGroupUnits();
  const g = q.data?.groups.find((x) => x.name === group);
  return (
    <Section
      concept="income"
      tag="as a unit"
      question={`How is ${group} doing as one book?`}
    >
      {g === undefined ? (
        <Card
          title="as a unit"
          collapseKey="automations-group-unit"
          className="lv-12"
          help={HELP}
        >
          <p className={q.isError || q.data?.error ? "error" : "muted"}>
            {q.data?.error ??
              (q.isError
                ? "Could not read the group."
                : q.data === undefined
                  ? "Reading the group…"
                  : `No group named ${group} in the read.`)}
          </p>
        </Card>
      ) : (
        <GroupUnitBody g={g} />
      )}
    </Section>
  );
}

export type By = "automation" | "group";

/** The performance and costs pages' switch, kept in the address (`?by=group`). */
export function useBy(): [By, (b: By) => void] {
  const [params, setParams] = useSearchParams();
  const by: By = params.get("by") === "group" ? "group" : "automation";
  const set = (b: By) =>
    setParams(
      (p) => {
        const next = new URLSearchParams(p);
        if (b === "group") next.set("by", "group");
        else next.delete("by");
        return next;
      },
      { replace: true },
    );
  return [by, set];
}

export function ByToggle({
  by,
  onChange,
}: {
  by: By;
  onChange: (b: By) => void;
}) {
  return (
    <div className="grp-by">
      <span
        className="mode-toggle"
        role="group"
        aria-label="Rows by automation or by group"
      >
        {(["automation", "group"] as const).map((b) => (
          <button
            key={b}
            type="button"
            className={by === b ? "mode-btn active" : "mode-btn"}
            aria-pressed={by === b}
            onClick={() => onChange(b)}
          >
            by {b}
          </button>
        ))}
      </span>
    </div>
  );
}

export const NO_GROUP_LABEL = "no group";

/** Each group's line and the automations in none, as the pages' rows (the module's lines, unchanged). */
export function groupRows(
  read: AutomationsGroupsRead | undefined,
): Array<[string, AutomationsLine]> {
  if (read === undefined) return [];
  const rows: Array<[string, AutomationsLine]> = read.groups.map((g) => [
    g.name,
    g.line,
  ]);
  if (read.ungrouped.members.length > 0)
    rows.push([NO_GROUP_LABEL, read.ungrouped.line]);
  return rows;
}

/** Where a group's name leads: its own view in Groups. */
export function groupHref(name: string): string {
  return name === NO_GROUP_LABEL
    ? "/automations/groups?group=_none"
    : `/automations/groups?group=${encodeURIComponent(name)}`;
}
