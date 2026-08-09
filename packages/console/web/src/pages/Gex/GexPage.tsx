import { useGex } from "../../lib/api";
import { DataCard, fmtNum } from "../../components/DataTable";

function fmtEt(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  return new Date(t).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour12: false });
}

function fmtGex(v: number | null): string {
  if (v === null) return "—";
  const abs = Math.abs(v);
  if (abs >= 1e9) return `${(v / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  return v.toFixed(0);
}

export function GexPage() {
  const { data, isLoading, isError } = useGex();

  return (
    <div className="page">
      <div className="page-title-row">
        <h1>GEX</h1>
        <span className="chip">regime history (recorder)</span>
      </div>

      <div className="cards cards-wide">
        <DataCard
          title="Latest regime per symbol"
          headers={["sym", "as of", "spot", "net GEX", "net GEX (vol)", "zero gamma", "call wall", "put wall"]}
          loading={isLoading}
          isError={isError}
          rowCount={data?.latest.length ?? 0}
          skeletonRows={3}
        >
          {data?.latest.map((g) => (
            <tr key={g.symbol}>
              <td>{g.symbol}</td>
              <td className="muted">{fmtEt(g.ts)}</td>
              <td>{fmtNum(g.spot, 2)}</td>
              <td>{fmtGex(g.netGex)}</td>
              <td>{fmtGex(g.netGexVol)}</td>
              <td>{fmtNum(g.zeroGamma, 0)}</td>
              <td>{fmtNum(g.callWall, 0)}</td>
              <td>{fmtNum(g.putWall, 0)}</td>
            </tr>
          ))}
        </DataCard>

        <DataCard
          title="Today's regime snapshots"
          headers={["time", "sym", "spot", "net GEX", "zero gamma", "call wall", "put wall"]}
          loading={isLoading}
          isError={isError}
          rowCount={data?.recent.length ?? 0}
          skeletonRows={10}
        >
          {data?.recent.map((g, i) => (
            <tr key={`${g.symbol}-${g.ts}-${i}`}>
              <td className="muted">{fmtEt(g.ts)}</td>
              <td>{g.symbol}</td>
              <td>{fmtNum(g.spot, 2)}</td>
              <td>{fmtGex(g.netGex)}</td>
              <td>{fmtNum(g.zeroGamma, 0)}</td>
              <td>{fmtNum(g.callWall, 0)}</td>
              <td>{fmtNum(g.putWall, 0)}</td>
            </tr>
          ))}
        </DataCard>
      </div>
    </div>
  );
}
