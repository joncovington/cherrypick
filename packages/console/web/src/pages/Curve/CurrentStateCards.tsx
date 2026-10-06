import type { CurveArmCell, CurveFlipDivergence, CurveOpenPosition, CurvePayload, CurveRegimeRow } from "@console/shared";
import { Card, DataCard, PnlCell, fmtMoney, fmtNum, fmtPct } from "../../components/DataTable";
import { UnrealisedPnlCell } from "../../components/UnrealisedPnlCell";
import { SignedBar } from "../../components/Charts";
import { fmtStrike } from "../../lib/optionFormat";
import { fmtCash, fmtPrice } from "../../lib/format";

/** The arms whose identity the page knows (`near` from 2026-10-06), in the order the arm table
 *  lists them; any other arm (an advisor experiment's `advised:<name>`) sorts after, by name. */
const CORE_ARMS = ["control", "noflip", "near", "hook"];

function strikeAt(strike: number | null, expiration: string | null): string {
  if (strike === null) return "—";
  return `${fmtStrike(strike)}${expiration === null ? "" : ` @ ${expiration}`}`;
}

/**
 * Close cost against the entry credit -- the whole trade is this number decaying toward the
 * profit-take threshold. A null renders as an em-dash with its reason on the title, never as
 * $0.00 -- "no usable mark" and "already at zero cost" are different facts.
 */
function CloseCostCell({ cost, credit }: { cost: number | null; credit: number | null }) {
  if (cost === null) {
    return (
      <span className="muted" title="no usable mark recorded yet -- not the same as a zero cost to close">
        —
      </span>
    );
  }
  const pctOfCredit = credit !== null && credit > 0 ? (cost / credit) * 100 : null;
  return (
    <span>
      {fmtMoney(cost)}
      {pctOfCredit !== null && <span className="muted"> ({fmtPct(pctOfCredit, 0)} of credit)</span>}
    </span>
  );
}

const OPEN_HEADERS = [
  "symbol", "arm", "opened", "expiry", "mark (net of costs to date)", "short/long", "spot", "qty", "price",
  "entry", "max loss", "credit % of width", "ratio/regime", "assignment",
];

function PositionRows({ rows }: { rows: CurveOpenPosition[] }) {
  return (
    <>
      {rows.map((p) => (
        <tr key={p.positionId}>
          <td>{p.symbol}</td>
          <td>{p.arm}</td>
          <td>{p.entrySession === "" ? "—" : p.entrySession}</td>
          <td>{p.expiration ?? "—"}</td>
          <td>
            <UnrealisedPnlCell
              gross={p.unrealisedGross}
              net={p.unrealisedNet}
              fees={p.feesToDate}
              detail={p.currentCloseCost === null ? undefined : `costs ${fmtMoney(p.currentCloseCost)}/share to close`}
            />
          </td>
          <td>
            {strikeAt(p.shortStrike, p.expiration)}
            <span className="muted"> / </span>
            {strikeAt(p.longStrike, p.expiration)}
          </td>
          <td>{fmtNum(p.currentSpot ?? p.entrySpot, 2)}</td>
          <td>{p.quantity ?? "—"}</td>
          <td>{fmtPrice(p.entryCredit)}</td>
          <td>{fmtCash(p.entryCash)}</td>
          <td>{p.entryMaxLoss === null ? "—" : fmtCash(-p.entryMaxLoss * 100 * (p.quantity ?? 1))}</td>
          <td>{fmtPct(p.entryCreditPctOfWidth === null ? null : p.entryCreditPctOfWidth * 100, 1)}</td>
          <td>
            {p.entryRatio === null ? "—" : fmtNum(p.entryRatio, 3)}
            {p.entryRegime !== null && <span className="muted"> ({p.entryRegime})</span>}
            {p.entryHook && (
              <span className="chip chip-warn integrity-chip" title="entered on the two-day-confirmed hook signal">
                hook
              </span>
            )}
          </td>
          <td>
            {p.exposureTicks !== null && p.exposureTicks > 0 ? (
              <span
                className="chip chip-warn integrity-chip"
                title="ticks where the short's extrinsic sat under the assignment-exposure threshold. Telemetry only -- it gates nothing."
              >
                exposed {p.exposureTicks}
              </span>
            ) : (
              <span className="muted">—</span>
            )}
          </td>
        </tr>
      ))}
    </>
  );
}

/**
 * One card per symbol, listing only the arms actually holding a position.
 *
 * VXX is the module's only underlying, so in practice this is one card -- but an arm holding
 * nothing is signal, not absence: the hook arm idling all week is the experiment working exactly
 * as designed (the pmcc keltner precedent, restated for curve's rarer entry).
 */
export function OpenTradesCard({ data, updatedAt }: { data: CurvePayload | undefined; updatedAt?: number }) {
  if (data === undefined) {
    return (
      <DataCard title="open trades" headers={OPEN_HEADERS} loading rowCount={0} numFrom={5} updatedAt={updatedAt}>
        {null}
      </DataCard>
    );
  }
  const symbols = [...new Set(data.openPositions.map((p) => p.symbol))];
  if (symbols.length === 0) {
    return (
      <DataCard
        title="open trades"
        headers={OPEN_HEADERS}
        loading={false}
        rowCount={0}
        numFrom={5}
        empty="no open trades -- one position per arm at ~30-45 DTE, so idle stretches are the ordinary state"
        updatedAt={updatedAt}
      >
        {null}
      </DataCard>
    );
  }
  // One table, not one card per symbol. curve trades a single underlying today, so a per-symbol
  // split was a card whose title repeated the only value the column could hold; symbol and expiry
  // moved onto the row, where they identify the trade rather than the container.
  const rows = [...data.openPositions].sort(
    (a, b) =>
      b.entrySession.localeCompare(a.entrySession) ||
      a.symbol.localeCompare(b.symbol) ||
      a.arm.localeCompare(b.arm),
  );
  return (
    <Card title="open trades" collapseKey="curve-open-trades" updatedAt={updatedAt}>
      <div className="table-scroll">
        <table className="data-table num-from-5">
          <thead>
            <tr>
              {OPEN_HEADERS.map((h) => (
                <th key={h}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            <PositionRows rows={rows} />
          </tbody>
        </table>
      </div>
    </Card>
  );
}

/** Today's regime read: the module's second product, standing on its own beside any position. */
export function RegimeCard({ series, today, updatedAt }: { series: CurveRegimeRow[]; today: CurveRegimeRow | undefined; updatedAt?: number }) {
  return (
    <Card title="VIX/VIX3M regime, recorded every session" collapseKey="curve-regime" updatedAt={updatedAt} className="view-fade">
      <p className="integrity-note">
        Written every session, traded or not -- the series' value is its continuity, never only what fed a trade.
      </p>
      {today === undefined ? (
        <p className="muted">no regime row for the current session yet</p>
      ) : !today.usable ? (
        <p className="integrity-warn">
          today's reading is unusable{today.refusal !== null && <> ({today.refusal})</>} -- a stale or missing quote
          refuses rather than freezing the last value forward
        </p>
      ) : (
        <p>
          ratio <strong>{fmtNum(today.ratio, 3)}</strong> ({today.regime}) -- VIX {fmtNum(today.vix, 2)} / VIX3M{" "}
          {fmtNum(today.vix3m, 2)}
          {today.hook === true && (
            <span className="chip chip-warn integrity-chip" title="the two-day-confirmed deep-backwardation hook signal">
              hook
            </span>
          )}
        </p>
      )}
      {series.length > 1 && (
        <div className="table-scroll">
          <table className="data-table num-from-1">
            <thead>
              <tr>
                <th>date</th>
                <th>ratio</th>
                <th>VIX</th>
                <th>VIX3M</th>
                <th>regime</th>
                <th>hook</th>
                <th>usable</th>
              </tr>
            </thead>
            <tbody>
              {series
                .slice()
                .reverse()
                .slice(0, 60)
                .map((r) => (
                  <tr key={r.tradeDate}>
                    <td>{r.tradeDate}</td>
                    <td>{fmtNum(r.ratio, 3)}</td>
                    <td>{fmtNum(r.vix, 2)}</td>
                    <td>{fmtNum(r.vix3m, 2)}</td>
                    <td>{r.regime ?? "—"}</td>
                    <td>{r.hook === true ? "hook" : ""}</td>
                    <td>{r.usable ? "" : <span className="chip chip-warn integrity-chip">{r.refusal ?? "unusable"}</span>}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

/** The suite's arm-qualification floor (`core.profiles.QUALIFICATION_RULE.min_sample`): below it a
 *  figure is shown, but flagged as too few cycles to judge an arm on. */
const MIN_SAMPLE = 20;

function armOrder(a: string, b: string): number {
  const ia = CORE_ARMS.indexOf(a);
  const ib = CORE_ARMS.indexOf(b);
  if (ia !== -1 || ib !== -1) return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
  return a.localeCompare(b);
}

function SampleChip({ n }: { n: number }) {
  if (n >= MIN_SAMPLE) return null;
  return (
    <span
      className="chip chip-warn integrity-chip"
      title={`${String(n)} of the ${String(MIN_SAMPLE)} closed cycles the suite asks for before judging an arm`}
    >
      n={n}
    </span>
  );
}

/** One arm's closed cycles in the money layout: the row adds up, gross - fees - settle - slip = net. */
function ArmMoneyRow({ c }: { c: CurveArmCell }) {
  return (
    <tr>
      <td>
        {c.arm} <SampleChip n={c.positions} />
      </td>
      <td>{c.positions}</td>
      <td>{fmtPct(c.winRate === null ? null : c.winRate * 100, 0)}</td>
      <td>
        <PnlCell v={c.grossPnl} />
      </td>
      <td>{fmtMoney(-c.fees)}</td>
      <td>{fmtMoney(-c.settlementFees)}</td>
      <td>{fmtMoney(-c.slippage)}</td>
      <td>
        <PnlCell v={c.netPnl} />
      </td>
      <td>
        <PnlCell v={c.positions > 0 ? c.netPnl / c.positions : null} />
      </td>
    </tr>
  );
}

/**
 * The arm comparison. Every arm's closed cycles are one row in the suite's money layout, so where
 * the money went is on the page: an arm can be net-negative on a positive gross, and that is
 * curve's whole cost question. The pairing caveats follow the table: control/noflip are exactly
 * paired and byte-identical until a flip fires, so their effective sample is `flip_divergence`, not
 * the cycle count; near and hook enter on their own plans and are not row-comparable with control.
 */
export function ArmComparison({
  data,
  flipDivergence,
  updatedAt,
}: {
  data: CurvePayload | undefined;
  flipDivergence: CurveFlipDivergence | undefined;
  updatedAt?: number;
}) {
  const arms = [...(data?.arms ?? [])].sort((a, b) => armOrder(a.arm, b.arm));
  const control = arms.find((c) => c.arm === "control");
  const noflip = arms.find((c) => c.arm === "noflip");
  const maxAbs = Math.max(1, ...arms.map((c) => Math.abs(c.netPnl)));
  const divergence = flipDivergence?.flipDivergenceCount ?? 0;
  const flipExits = flipDivergence?.controlFlipExits ?? 0;

  return (
    <Card title="arm comparison" collapseKey="curve-arms" updatedAt={updatedAt}>
      {arms.length === 0 ? (
        <p className="muted">
          no completed cycles yet -- per-arm results fill in as positions close
          {data !== undefined && data.openCount > 0 && (
            <> ({data.openCount} open position{data.openCount === 1 ? "" : "s"} so far)</>
          )}
        </p>
      ) : (
        <>
          <section className="pmcc-compare">
            <h3>closed cycles by arm, after every cost</h3>
            <div className="table-scroll">
              <table className="data-table num-from-1">
                <thead>
                  <tr>
                    <th>arm</th>
                    <th>cycles</th>
                    <th>win rate</th>
                    <th>gross</th>
                    <th>fees</th>
                    <th>settle</th>
                    <th>slip</th>
                    <th>net</th>
                    <th>net / cycle</th>
                  </tr>
                </thead>
                <tbody>
                  {arms.map((c) => (
                    <ArmMoneyRow key={c.arm} c={c} />
                  ))}
                </tbody>
              </table>
            </div>
            <p className="integrity-note">
              Each row adds up: gross - fees - settle - slip = net. Fills are modelled at mid, so slippage is a
              charged cost here, not a concession inside gross. Every figure is still an upper bound while early
              assignment sits unmodelled.
            </p>
          </section>

          <section className="pmcc-compare">
            <h3>control vs noflip -- the effective sample is the flip divergence, not the cycle count</h3>
            <p>
              noflip minus control net: <PnlCell v={control && noflip ? noflip.netPnl - control.netPnl : null} />, over{" "}
              <strong>{divergence}</strong> diverged position{divergence === 1 ? "" : "s"} ({flipExits} control flip exit
              {flipExits === 1 ? "" : "s"} in total)
            </p>
            <p className="integrity-note">
              Both arms enter from the same plan on the same tick, so until a flip fires they are byte-identical
              and every shared cycle counts twice. Only the diverged positions say anything about the flip rule.
            </p>
          </section>

          <section className="pmcc-compare">
            <h3>near, hook and advised arms -- their own entries, not paired with control</h3>
            <p className="integrity-note">
              near sells a 0.40-delta short (from 2026-10-06) on its own plan. hook enters only on the
              two-day-confirmed deep-backwardation spike, so idleness is its honest state. An
              advised:&lt;experiment&gt; arm runs its admitted params beside the base it shadows. Compare them by
              net per cycle, never by summed net.
            </p>
          </section>

          <section className="pmcc-compare">
            <h3>net by arm</h3>
            <table className="data-table num-from-1">
              <tbody>
                {arms.map((c) => (
                  <tr key={c.arm}>
                    <td>{c.arm}</td>
                    <td style={{ width: "50%" }}>
                      <SignedBar value={c.netPnl} maxAbs={maxAbs} compact />
                    </td>
                    <td>
                      <PnlCell v={c.netPnl} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </>
      )}
    </Card>
  );
}
