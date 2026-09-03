import type { BwbPayload } from "@console/shared";
import { Card } from "../../components/DataTable";

/**
 * What this experiment is, in the module's own terms -- the pmcc/curve HelpTab precedent: bwb's
 * four books differ by one stated add-on trigger rule each, so this is prose kept in one place
 * rather than a derived diff view.
 */
export function HelpTab({ data: _data }: { data: BwbPayload | undefined }) {
  return (
    <div className="cards cards-wide">
      <Card title="what bwb is" collapseKey="bwb-help-what" defaultCollapsed className="view-fade">
        <div className="pmcc-prose">
          <p>
            A daily-laddered SPX put broken-wing butterfly, entered every session at the expected move,
            ~7 DTE, PM-settled. Every book enters the IDENTICAL BWB from the same plan on the same tick --
            the books differ only in whether/when a reversal-triggered put credit spread add-on fires,
            turning the fly into a 1-3-2.
          </p>
          <p className="muted">
            Paper only: there is no live loop and no order-placement code anywhere in the module. Built
            2026-08-23.
          </p>
        </div>
      </Card>

      <Card title="the four books -- one variable each" collapseKey="bwb-help-books" defaultCollapsed>
        <div className="pmcc-prose">
          <dl className="pmcc-defs">
            <dt>control</dt>
            <dd>The add-on never fires -- the BWB rides alone to expiry.</dd>
            <dt>delta</dt>
            <dd>The near wing's |delta| reaches the delta trigger (50Δ default) -- raw proximity.</dd>
            <dt>bounce</dt>
            <dd>
              Peak |delta| since entry cleared the trigger AND current |delta| has pulled back by the
              declared bounce_pullback -- a confirmed reversal, not a touch.
            </dd>
            <dt>flip</dt>
            <dd>
              Spot has traded below gamma_flip at some point since entry AND reclaimed above it by the
              declared flip_buffer.
            </dd>
            <dt>advised:&lt;base&gt;</dt>
            <dd>The AI advisor's admitted parameters, frozen on each row at entry. Off by default, and deliberately unbounded.</dd>
          </dl>
        </div>
      </Card>

      <Card title="the add-on" collapseKey="bwb-help-addon" defaultCollapsed>
        <div className="pmcc-prose">
          <p>
            Identical construction for all three arms: a put credit spread bracketing the far wing -- SELL
            one increment above it, BUY one increment below. Must itself price as a credit. Once the
            trigger is met the position is <span className="mono">armed</span>; every tick re-prices the
            add-on until the first credit tick fires it. One add-on maximum per position; after firing the
            trigger disarms permanently. Armed until expiry, no cutoff. After firing: hold everything to
            expiry -- no profit-take, no stop, on any book.
          </p>
        </div>
      </Card>

      <Card title="why the effective sample is fire count, not trade count" collapseKey="bwb-help-pairing" defaultCollapsed>
        <div className="pmcc-prose">
          <p>
            Until an arm's add-on actually fires, that arm's positions are byte-identical to control's --
            an expected collision, not a defect. Each arm-vs-control comparison's effective sample is that
            arm's fire count. The three arms will NOT fire equally often: delta fires most, bounce needs
            the move plus a turn, flip needs spot to have entered negative-gamma territory at all and come
            back. A quiet flip book is the honest state, not a broken one.
          </p>
          <p>
            <strong>Daily-ladder correlation caveat:</strong> concurrent positions share regime context --
            one sharp selloff can fire the same trigger across several overlapping positions in one
            session. Rows are not independent samples; the honest unit is closer to distinct fire
            episodes than fired positions.
          </p>
        </div>
      </Card>

      <Card title="the trigger tick path -- the module's second product" collapseKey="bwb-help-ticks" defaultCollapsed>
        <div className="pmcc-prose">
          <p>
            Every loop tick, for every open cohort (entry_session x structure_signature), the near-wing
            delta, peak delta, spot, gamma_flip and the below-flip latch are recorded -- byte-identical
            across the four base books that share one signature. This is what makes a read-side threshold
            replay possible over data the module itself recorded, rather than a vendor-imagined backtest.
          </p>
        </div>
      </Card>

      <Card title="the honesty rules" collapseKey="bwb-help-honesty" defaultCollapsed>
        <div className="pmcc-prose">
          <ol className="pmcc-rules">
            <li>
              <strong>Net of the full modeled fee and slippage stack.</strong> Entry is 4 legs/2 sells, the
              add-on 2 legs/1 sell, and each distinct ITM leg at settlement pays the $5 cash-settlement
              event fee.
            </li>
            <li>
              <strong>Settlement fidelity is a stated caveat, not a bias.</strong> Paper settles each leg at
              intrinsic against the last cached tick, not the official closing print -- uniform across arms.
            </li>
            <li>
              <strong>A hole in the mark path is refused, never zero.</strong>
            </li>
            <li>
              <strong>A trigger can only fire on a measured tick.</strong> Missing/stale greeks or GEX
              inputs mean the trigger cannot evaluate that tick -- never a guess, never carried forward.
            </li>
            <li>
              <strong>Correlated ladder rows</strong> -- surfaced, not buried.
            </li>
            <li>
              <strong>Zero credit floors are a declared design choice.</strong>
            </li>
            <li>
              <strong>No fallback paths in v1</strong> -- the delta triggers refuse-on-missing rather than
              degrade.
            </li>
            <li>
              <strong>Measurement breaks are journaled rows.</strong>
            </li>
          </ol>
        </div>
      </Card>
    </div>
  );
}
