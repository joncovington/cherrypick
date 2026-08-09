import { useFlies } from "../../lib/api";
import { useMode } from "../../lib/useMode";
import { ModeToggle } from "../../components/ModeToggle";
import { PaperLiveBadge } from "../../components/shell/PaperLiveBadge";
import { DataCard, PnlCell, fmtMoney, fmtNum } from "../../components/DataTable";

export function FliesPage() {
  const [mode, setMode] = useMode();
  const { data, isLoading, isError } = useFlies(mode);

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>Flies</h1>
        <PaperLiveBadge mode={mode} />
        <ModeToggle mode={mode} onChange={setMode} />
      </div>

      <div className="cards cards-wide">
        <DataCard
          title="Books"
          headers={["date", "arm", "sym", "credit", "debits", "fees", "net cash", "floor", "band", "status", "P&L"]}
          loading={isLoading}
          isError={isError}
          rowCount={data?.books.length ?? 0}
        >
          {data?.books.map((b) => (
            <tr key={b.bookId}>
              <td>{b.tradeDate}</td>
              <td className="muted">{b.arm ?? "—"}</td>
              <td>{b.symbol}</td>
              <td>{fmtMoney(b.creditCollected)}</td>
              <td>{fmtMoney(b.debitsPaid)}</td>
              <td>{fmtMoney(b.fees)}</td>
              <td>{fmtMoney(b.netCash)}</td>
              <td>{b.floorHolds === null ? "—" : b.floorHolds ? "holds" : "no"}</td>
              <td className="muted">
                {b.bandLow !== null && b.bandHigh !== null
                  ? `${fmtNum(b.bandLow, 0)}–${fmtNum(b.bandHigh, 0)}`
                  : "—"}
              </td>
              <td>{b.status}</td>
              <td><PnlCell v={b.pnl} /></td>
            </tr>
          ))}
        </DataCard>

        <DataCard
          title="Positions"
          headers={["date", "entry", "sym", "kind", "side", "center", "wing", "qty", "net", "status", "P&L"]}
          loading={isLoading}
          isError={isError}
          rowCount={data?.positions.length ?? 0}
          skeletonRows={10}
        >
          {data?.positions.map((p) => (
            <tr key={p.positionId}>
              <td>{p.tradeDate}</td>
              <td className="muted">{p.entryTime?.slice(11, 16) ?? "—"}</td>
              <td>{p.symbol}</td>
              <td>{p.kind ?? "—"}</td>
              <td>{p.side ?? "—"}</td>
              <td>{fmtNum(p.center, 0)}</td>
              <td>{fmtNum(p.wingWidth, 0)}</td>
              <td>{fmtNum(p.quantity, 0)}</td>
              <td>{fmtMoney(p.net)}</td>
              <td>{p.status}</td>
              <td><PnlCell v={p.pnl} /></td>
            </tr>
          ))}
        </DataCard>
      </div>
    </div>
  );
}
