import type { CurvePayload } from "@console/shared";
import { Card, fmtMoney, fmtPct } from "../../components/DataTable";

/**
 * What this experiment is, in the module's own terms -- the pmcc HelpTab precedent: curve's four
 * arms differ by one stated rule each rather than by a set of overridden parameters, so this is
 * prose kept in one place rather than a derived diff view.
 */
export function HelpTab({ data }: { data: CurvePayload | undefined }) {
  const p = data?.params;
  return (
    <div className="cards cards-wide">
      <Card title="what curve is" collapseKey="curve-help-what" defaultCollapsed className="view-fade">
        <div className="pmcc-prose">
          <p>
            Sell a VXX call credit spread -- short call ~30-delta, long wing a declared width higher, same
            ~30-45 DTE monthly expiration -- gated by a daily VIX/VIX3M regime read. Every arm trades the same
            shape; the arms differ only in their declared entry gate and exit rule, never in what is traded.
          </p>
          <p>
            Close both legs once the cost to close has fallen to
            {p?.profitTakePct != null && <> {fmtPct(p.profitTakePct * 100, 0)} of</>} the entry credit, OR the
            regime-flip hard exit, OR {p?.closeDte ?? 7} days to expiration.
          </p>
          <p className="muted">
            Paper only: there is no live loop and no order-placement code anywhere in the module. Built
            2026-08-22 -- the regime series accumulates value immediately even before any arm has traded.
          </p>
        </div>
      </Card>

      <Card title="the four arms -- one variable each" collapseKey="curve-help-arms" defaultCollapsed>
        <div className="pmcc-prose">
          <dl className="pmcc-defs">
            <dt>control</dt>
            <dd>
              The strategy as pitched: enter on a contango day (ratio &lt;{" "}
              {p?.contangoMax != null ? p.contangoMax : "contango_max"}), close at the profit-take fraction OR
              the regime-flip hard exit (a MEASURED ratio crossing ≥ 1.0 mid-trade closes next tick regardless
              of P&amp;L) OR close_dte.
            </dd>
            <dt>noflip</dt>
            <dd>
              Control's entry EXACTLY, same tick, same fills. Its exit is control's minus the flip rule, so it
              holds through backwardation to target or close_dte. Until a flip actually fires, control and
              noflip are byte-identical by construction -- the effective comparison sample is{" "}
              <span className="mono">flip_divergence_count</span>, not the trade count.
            </dd>
            <dt>hook</dt>
            <dd>
              Enters ONLY on the two-day-confirmed hook signal (ratio &gt;{" "}
              {p?.hookThreshold != null ? p.hookThreshold : "hook_threshold"} AND below yesterday's -- a deep
              backwardation spike that has started to mean-revert), exits by control's rules. Expected to be
              nearly always idle -- the idleness is the honest state, not a failure.
            </dd>
            <dt>near</dt>
            <dd>
              Control's gate and exits, flip exit included, on a 0.40-delta short instead of 0.30 (from
              2026-10-06). It was the advisor experiment short-delta-nearer-money, which entered twice where
              control could not: a short nearer the money pays a credit that clears the floor more often.
              Planned from its own strikes, so it is NOT paired fill-for-fill with control.
            </dd>
            <dt>advised:&lt;experiment name&gt;</dt>
            <dd>
              One arm per advisor experiment, each carrying that experiment's admitted parameters frozen on each
              row at entry and measured against the base arm its experiment names. Off by default. curve is a
              structurally slow advisor target -- one position per arm at ~30-45 DTE with 50% takes closes maybe
              2-4 trades a month -- so an early "underpowered" verdict here means "not enough data yet", never a
              failure.
            </dd>
          </dl>
        </div>
      </Card>

      <Card title="why the arms are only partly comparable" collapseKey="curve-help-pairing" defaultCollapsed>
        <div className="pmcc-prose">
          <p>
            <strong>control and noflip are exactly paired.</strong> They enter from the same plan on the same
            tick, so any difference between them is the flip rule and nothing else -- measured by{" "}
            <span className="mono">flip_divergence_count</span>, the count of positions where control's flip
            fired while noflip held past that point. A season of pure contango with zero flips proves nothing
            about the flip rule.
          </p>
          <p>
            <strong>near, hook and the advised arms are not.</strong> near is planned from its own 0.40-delta
            strikes; hook's variable IS the entry condition, its own rare tick; an advised arm runs its own
            admitted params. Each holds a different set of fills by construction, so compare them by net per
            cycle, and never treat the arms as a fully paired grid.
          </p>
        </div>
      </Card>

      <Card title="the regime series, recorded every session" collapseKey="curve-help-regime" defaultCollapsed>
        <div className="pmcc-prose">
          <p>
            The daily VIX/VIX3M ratio, its contango/backwardation classification, and the hook flag are written
            every session, whether or not any arm trades -- the series' value is its continuity. It is
            RTH-gated and basis-stamped from day one: a recorder that freezes on the last streamed value
            overnight would double-weight whatever sign the session ended on. A stale or missing read writes a
            row marked unusable, never a frozen ratio.
          </p>
        </div>
      </Card>

      <Card title="the honesty rules" collapseKey="curve-help-honesty" defaultCollapsed>
        <div className="pmcc-prose">
          <ol className="pmcc-rules">
            <li>
              <strong>Every result is net of the modeled fee and slippage stack.</strong> Gross is not a
              result.
            </li>
            <li>
              <strong>Early assignment is unmodelled but measured, so the paper result is an upper bound.</strong>{" "}
              Every mark where the short's extrinsic sits under the exposure threshold
              {p?.assignmentExposureTv != null && <> ({fmtMoney(p.assignmentExposureTv)})</>} is flagged. VXX
              pays no dividend, but a spike still puts the short call ITM.
            </li>
            <li>
              <strong>VXX reverse splits are unmodelled.</strong> A split mid-position is a manual settle plus a
              journaled measurement break.
            </li>
            <li>
              <strong>ETN plumbing risk is declared, not modelled.</strong> VXX is an ETN; shares can in
              principle decouple from the index. This module cannot detect that.
            </li>
            <li>
              <strong>A hole in the mark path is refused, never zero.</strong>
            </li>
            <li>
              <strong>Missing regime data blocks entry and can never force an exit.</strong> No ratio means no
              new position; an open position with no ratio holds its last verdict. The flip-exit fires only on
              a MEASURED crossing.
            </li>
            <li>
              <strong>The regime row is written every session</strong>, traded or not.
            </li>
          </ol>
        </div>
      </Card>
    </div>
  );
}
