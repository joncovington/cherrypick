import { Link } from "react-router-dom";
import type { DeskEntriesRow, DeskExposureRow } from "@console/shared";
import { Card, SkeletonRows } from "../../components/DataTable";
import { ModeToggle } from "../../components/ModeToggle";
import { fmtMoney, ageLabel } from "../../lib/format";
import { useDesk, useDeskLive } from "../../lib/api";
import type { BookRotation } from "../../lib/useBookRotation";

/**
 * The two desk cards rotate between the paper book and the live one on a shared clock
 * (`useBookRotation`), held still while the pointer is over either. The toggle in each header says
 * which book is up and switches it; the paper-only modules read "no live path" on the live side.
 */
function useBook(rotation: BookRotation) {
  const paper = useDesk();
  const live = useDeskLive();
  const q = rotation.book === "live" ? live : paper;
  return { book: q.data, isLoading: q.isLoading, dataUpdatedAt: q.dataUpdatedAt };
}

function BookControls({ rotation }: { rotation: BookRotation }) {
  return (
    <div style={{ marginLeft: "auto" }} title="rotates paper ⇄ live every 15s; hover to hold">
      <ModeToggle mode={rotation.book} onChange={rotation.show} />
    </div>
  );
}

export function ExposureCard({ rotation }: { rotation: BookRotation }) {
  const { book: data, isLoading, dataUpdatedAt } = useBook(rotation);
  const rows = data?.exposure ?? [];
  const totalOpen = rows.reduce<number | null>((s, r) => (r.open !== null ? (s ?? 0) + r.open : s), null);
  return (
    <Card
      title={`Open exposure — right now · ${rotation.book}`}
      collapseKey="Open exposure — right now"
      updatedAt={dataUpdatedAt}
      className="desk-card"
      controls={<BookControls rotation={rotation} />}
      onHoverChange={rotation.setPaused}
    >
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
        Counts and capital at risk are honest sums; unrealised is not — the books differ in scale
        by more than an order of magnitude. A module name opens its slides.
      </p>
    </Card>
  );
}

export function EntriesCard({ rotation }: { rotation: BookRotation }) {
  const { book: data, isLoading, dataUpdatedAt } = useBook(rotation);
  const rows = data?.entries ?? [];
  const totalFilled = rows.reduce((s, r) => s + r.filled, 0);
  const totalRefused = rows.reduce((s, r) => s + r.refused, 0);
  const totalNoFill = rows.reduce((s, r) => s + r.noFill, 0);
  return (
    <Card
      title={`Today's entries — filled and refused · ${rotation.book}`}
      collapseKey="Today's entries — filled and refused"
      updatedAt={dataUpdatedAt}
      className="desk-card"
      controls={<BookControls rotation={rotation} />}
      onHoverChange={rotation.setPaused}
    >
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
                      <td className="muted" style={{ textAlign: "left" }}>
                        {r.note ?? "—"}
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
