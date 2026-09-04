import { Link } from "react-router-dom";
import type { DeskEntriesRow, DeskEvidenceRow, DeskExposureRow } from "@console/shared";
import { Card, SkeletonRows } from "../../components/DataTable";
import { fmtMoney, ageLabel } from "../../lib/format";
import { useDesk } from "../../lib/api";
import { ModuleChipLink } from "../../components/ModuleLink";

export function ExposureCard() {
  const { data, isLoading, dataUpdatedAt } = useDesk();
  const rows = data?.exposure ?? [];
  const totalOpen = rows.reduce<number | null>((s, r) => (r.open !== null ? (s ?? 0) + r.open : s), null);
  return (
    <Card title="Open exposure — right now" updatedAt={dataUpdatedAt} className="desk-card">
      <div className="table-scroll desk-table-scroll">
        <table className="data-table num-from-1">
          <thead>
            <tr>
              <th>module</th>
              <th>open</th>
              <th>at risk</th>
              <th>unrealised</th>
              <th>mark age</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <SkeletonRows n={7} cols={5} />
            ) : (
              rows.map((r: DeskExposureRow) => (
                <tr key={r.module}>
                  <td>
                    {r.available ? <Link to={`/${r.module}`} className="module-link">{r.module}</Link> : r.module}
                  </td>
                  <td>{r.available ? (r.open ?? "—") : <span className="muted">{r.note}</span>}</td>
                  <td className={r.available ? "" : "muted"} title={r.available ? r.atRiskLabel : undefined}>
                    {r.available ? fmtMoney(r.atRisk) : ""}
                  </td>
                  <td className={r.unrealisedNet !== null ? (r.unrealisedNet >= 0 ? "pnl-pos" : "pnl-neg") : "muted"}>
                    {r.available ? fmtMoney(r.unrealisedNet) : ""}
                  </td>
                  <td className="muted">{r.available ? ageLabel(r.markAgeSeconds) : ""}</td>
                </tr>
              ))
            )}
            {!isLoading && rows.length > 0 && (
              <tr className="total">
                <td>suite</td>
                <td>{totalOpen ?? "—"}</td>
                <td className="muted">not summed</td>
                <td />
                <td />
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <p className="muted" style={{ fontSize: 11, marginTop: "0.5rem", marginBottom: 0 }}>
        Counts and capital at risk are honest sums; unrealised is not, for the same reason the
        equity card draws no combined line — the books differ in scale by more than an order of
        magnitude. A module name opens its slides.
      </p>
    </Card>
  );
}

export function EntriesCard() {
  const { data, isLoading, dataUpdatedAt } = useDesk();
  const rows = data?.entries ?? [];
  const totalFilled = rows.reduce((s, r) => s + r.filled, 0);
  const totalRefused = rows.reduce((s, r) => s + r.refused, 0);
  const totalNoFill = rows.reduce((s, r) => s + r.noFill, 0);
  return (
    <Card title="Today's entries — filled and refused" updatedAt={dataUpdatedAt} className="desk-card">
      <div className="table-scroll desk-table-scroll">
        <table className="data-table num-from-1">
          <thead>
            <tr>
              <th>module</th>
              <th>filled</th>
              <th>refused</th>
              <th>no fill</th>
              <th>session net</th>
              <th style={{ textAlign: "left" }}>top refusal</th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <SkeletonRows n={7} cols={6} />
            ) : (
              rows.map((r: DeskEntriesRow) => (
                <tr key={r.module}>
                  <td>
                    {r.available ? <Link to={`/${r.module}`} className="module-link">{r.module}</Link> : r.module}
                  </td>
                  {r.available ? (
                    <>
                      <td>{r.filled}</td>
                      <td>{r.refused}</td>
                      <td>{r.noFill}</td>
                      <td className={r.sessionNet !== null ? (r.sessionNet >= 0 ? "pnl-pos" : "pnl-neg") : "muted"}>
                        {fmtMoney(r.sessionNet)}
                      </td>
                      <td className="muted desk-top-refusal" style={{ textAlign: "left" }} title={r.topRefusal ?? undefined}>
                        {r.topRefusal ?? "—"}
                      </td>
                    </>
                  ) : (
                    <>
                      <td className="muted">—</td>
                      <td className="muted">—</td>
                      <td className="muted">—</td>
                      <td className="muted">—</td>
                      <td className="muted" style={{ textAlign: "left" }} title={r.note ?? undefined}>
                        —
                      </td>
                    </>
                  )}
                </tr>
              ))
            )}
            {!isLoading && rows.length > 0 && (
              <tr className="total">
                <td>suite</td>
                <td>{totalFilled}</td>
                <td>{totalRefused}</td>
                <td>{totalNoFill}</td>
                <td />
                <td />
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <p className="muted" style={{ fontSize: 11, marginTop: "0.5rem", marginBottom: 0 }}>
        Every arm sees the same market with the same money, so the refusals are the primary
        signal: a quiet module was either refused by a rule or had nothing to trade, and this says
        which.
      </p>
    </Card>
  );
}

export function EvidenceClockRow() {
  const { data } = useDesk();
  const rows = data?.evidence ?? [];
  return (
    <div className="evidence-row">
      <span className="fine-label">evidence clock</span>
      {rows.length === 0 ? (
        <span className="muted" style={{ fontSize: 11 }}>
          no era data yet
        </span>
      ) : (
        rows.map((r: DeskEvidenceRow) => (
          <ModuleChipLink
            key={r.module}
            id={r.module}
            className="chip"
            title={r.lastBreakDate !== null ? `last break ${r.lastBreakDate}: ${r.lastBreakReason ?? ""}` : "no measurement break recorded"}
          >
            {r.module} {r.sessionsSince ?? "—"} {r.lastBreakDate !== null ? `since ${r.lastBreakDate}` : "· no break"}
          </ModuleChipLink>
        ))
      )}
      <span className="muted evidence-note">
        sessions since the last measurement break · hover for the break · results either side are never pooled
      </span>
    </div>
  );
}
