import { useMeic } from "../../lib/api";
import { useMode } from "../../lib/useMode";
import { ModeToggle } from "../../components/ModeToggle";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { DataCard, PnlCell, fmtMoney, fmtNum } from "../../components/DataTable";

export function MeicPage() {
  const [mode, setMode] = useMode();
  const { data, isLoading, isError } = useMeic(mode);

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>MEIC</h1>
        <PaperLiveBadge mode={mode} />
        <ModeToggle mode={mode} onChange={setMode} />
      </div>

      <div className="cards cards-wide">
        <DataCard
          title="Iron condor trades"
          headers={["date", "entry", "sym", "put", "call", "wing", "credit", "qty", "status", "P&L", "exit reason"]}
          loading={isLoading}
          isError={isError}
          rowCount={data?.trades.length ?? 0}
          skeletonRows={10}
        >
          {data?.trades.map((t) => (
            <tr key={`${t.mode}-${t.id}`}>
              <td>{t.tradeDate}</td>
              <td className="muted">{t.entryTime?.slice(11, 16) ?? "—"}</td>
              <td>{t.symbol}</td>
              <td>{fmtNum(t.putStrike, 0)}</td>
              <td>{fmtNum(t.callStrike, 0)}</td>
              <td>{fmtNum(t.wingWidth, 0)}</td>
              <td>{fmtMoney(t.netCredit)}</td>
              <td>{fmtNum(t.quantity, 0)}</td>
              <td>{t.status}</td>
              <td><PnlCell v={t.pnl} /></td>
              <td className="muted">{t.exitReason ?? "—"}</td>
            </tr>
          ))}
        </DataCard>

        <DataCard
          title="Daily summaries"
          headers={["date", "sym", "entries", "filled", "stopped", "win %", "net P&L"]}
          loading={isLoading}
          isError={isError}
          rowCount={data?.summaries.length ?? 0}
        >
          {data?.summaries.map((s) => (
            <tr key={`${s.summaryDate}-${s.symbol}`}>
              <td>{s.summaryDate}</td>
              <td>{s.symbol ?? "—"}</td>
              <td>{fmtNum(s.totalEntries, 0)}</td>
              <td>{fmtNum(s.entriesFilled, 0)}</td>
              <td>{fmtNum(s.entriesStopped, 0)}</td>
              <td>{fmtNum(s.winRatePct, 1)}</td>
              <td><PnlCell v={s.netPnl} /></td>
            </tr>
          ))}
        </DataCard>
      </div>
    </div>
  );
}
