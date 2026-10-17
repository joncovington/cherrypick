import type { ArmRiskRow, ArmsRiskResult } from "@console/shared";
import { Link } from "react-router-dom";
import { Card, SortTh, sortRows, useSort } from "../../components/DataTable";
import { fmtMoney, fmtNum, fmtPct } from "../../lib/format";
import { useArmsRisk } from "../../lib/api";
import { useFeatures } from "../../lib/useFeatures";
import { isModuleVisible } from "../../lib/visibility";

/**
 * Compare's risk tab: every arm in the suite on one table, each over its own window, with the
 * shape of its result beside the dollars -- the figures each module's analysis tab already shows
 * (core.analysis), picked server-side and never computed here. The arms tab beside it is the
 * paired comparison with control; this is the risk each arm carried and how much of its result
 * survives without its best trades, which that comparison does not say.
 *
 * Tone comes only from a number's sign. A drawdown is shown as the loss it is. A null is an em dash.
 */

function pct(v: number | null): string {
  return fmtPct(v === null ? null : v * 100, 1);
}

function tone(v: number | null): string | undefined {
  return v === null ? undefined : v >= 0 ? "pnl-pos" : "pnl-neg";
}

/** A positive drawdown depth, drawn as the loss. */
function loss(v: number | null): number | null {
  return v === null || v === 0 ? v : -v;
}

function run(r: ArmRiskRow): string {
  return r.currentRun === null ? "—" : `${String(r.currentRun.length)} ${r.currentRun.kind === "win" ? "up" : "down"}`;
}

export function RiskTable({ data, visible }: { data: ArmsRiskResult; visible: (module: string) => boolean }) {
  const sort = useSort();
  const rows = sortRows(
    data.rows.filter((r) => visible(r.module)),
    sort,
    {
      module: (r) => r.module,
      arm: (r) => r.tag,
      from: (r) => r.window?.start ?? "",
      sessions: (r) => r.sessions,
      net: (r) => r.net,
      peak: (r) => r.peakRisk,
      netPeak: (r) => r.netOverLargestPeak,
      dd: (r) => r.maxDrawdown,
      ddPeak: (r) => r.drawdownOverPeak,
      stress: (r) => r.stressBlocks,
      top10: (r) => r.withoutTop10,
      breakeven: (r) => r.breakevenMultiple,
    },
  );
  const failures = data.failures.filter((f) => visible(f.module));
  return (
    <>
      {failures.map((f) => (
        <p key={f.module} className="muted" style={{ fontSize: 12 }}>
          <span className="chip chip-warn" style={{ marginRight: 6 }}>
            {f.module}
          </span>
          could not be read — {f.error}
        </p>
      ))}
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <SortTh label="module" k="module" sort={sort} />
              <SortTh label="arm" k="arm" sort={sort} />
              <SortTh label="read from" k="from" sort={sort} />
              <SortTh label="sessions" k="sessions" sort={sort} />
              <SortTh label="net" k="net" sort={sort} />
              <SortTh label="peak risk" k="peak" sort={sort} />
              <SortTh label="net / largest peak" k="netPeak" sort={sort} />
              <SortTh label="max drawdown" k="dd" sort={sort} />
              <SortTh label="drawdown / peak" k="ddPeak" sort={sort} />
              <SortTh label="blocks 95th" k="stress" sort={sort} />
              <SortTh label="without top 10%" k="top10" sort={sort} />
              <th>run</th>
              <SortTh label="slippage breakeven" k="breakeven" sort={sort} />
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.module}-${r.tag}`}>
                <td>
                  <Link to={`/${r.module}/analysis`}>{r.module}</Link>
                </td>
                <td className="mono">{r.tag}</td>
                <td title={r.window?.reason}>
                  {r.window?.start ?? "—"}
                  {r.window?.scope === "arm_era" && <span className="muted"> · era</span>}
                </td>
                <td>{String(r.sessions)}</td>
                <td className={tone(r.net)}>{fmtMoney(r.net)}</td>
                <td>{fmtMoney(r.peakRisk)}</td>
                <td className={tone(r.netOverLargestPeak)}>{pct(r.netOverLargestPeak)}</td>
                <td className={r.maxDrawdown ? "pnl-neg" : undefined}>{fmtMoney(loss(r.maxDrawdown))}</td>
                <td>{pct(r.drawdownOverPeak)}</td>
                <td title={r.stressWithheld ?? undefined}>{fmtMoney(loss(r.stressBlocks))}</td>
                <td className={tone(r.withoutTop10)}>
                  {fmtMoney(r.withoutTop10)}
                  {r.top10SignFlips && (
                    <span className="chip chip-warn" style={{ marginLeft: 6, fontSize: 9 }}>
                      sign flips
                    </span>
                  )}
                </td>
                <td>{run(r)}</td>
                <td>{r.breakevenMultiple === null ? "—" : `${fmtNum(r.breakevenMultiple, 1)}×`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted" style={{ fontSize: 11 }}>
        each arm over its own window ("era" where its module's breaks name it, else the suite window); peak risk is
        the most it had at risk at once; blocks 95th is the drawdown with its sessions reordered in blocks of five,
        a dash under 20 sessions; slippage breakeven is the multiple of the modelled slippage at which the net
        reaches zero, a dash where it is not positive. A module's analysis tab has the detail.
      </p>
    </>
  );
}

export function RiskTab() {
  const { data, isLoading, isError, dataUpdatedAt } = useArmsRisk("paper");
  const features = useFeatures();
  const visible = (module: string) => (features === undefined ? true : isModuleVisible(module, features));
  return (
    <div className="cards cards-wide">
      <Card title="every arm's risk" updatedAt={dataUpdatedAt} isError={isError}>
        {isLoading || data === undefined ? (
          <span className="skeleton skeleton-text" style={{ width: "50%" }} />
        ) : (
          <RiskTable data={data} visible={visible} />
        )}
      </Card>
    </div>
  );
}
