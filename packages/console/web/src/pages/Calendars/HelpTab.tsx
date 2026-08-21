import type { CalendarsPayload } from "@console/shared";
import { Card, fmtNum, fmtPct } from "../../components/DataTable";

/**
 * What this module is, in the terms it judges itself by.
 *
 * Written because the numbers on the other tabs are easy to misread as a strategy's P&L. They are
 * not: this is a paper experiment whose output is a ranking of exit rules, its entry is
 * deliberately unconditional and un-optimised, and its sample is deliberately biased by the weeks
 * it refuses. A reader who takes the top row of the policy table as a recommendation has been let
 * down by the page, not by the module.
 */
export function HelpTab({ data }: { data: CalendarsPayload | undefined }) {
  const p = data?.params;
  return (
    <div className="cards cards-wide">
      <Card title="what this module is" collapseKey="cal-help-what" className="view-fade">
        <div className="cal-prose">
          <p>
            Every Monday — Tuesday after a Monday holiday — at the entry window: one put calendar at the
            expected-move-down strike and one call calendar at the expected-move-up strike, shorts expiring
            that week&rsquo;s Friday and longs the following Monday. The entry is{" "}
            <strong>unconditional and mechanical</strong> on purpose. The module exists to answer one
            question honestly: <em>which exit rule makes this structure worth anything, net of costs?</em>
          </p>
          <p>
            So there is no entry filter to tune here and no view being expressed about the market. Two real
            books collect the substrate — <span className="mono">control</span> closes everything at
            Friday&rsquo;s bell, <span className="mono">path</span> never closes and records the per-tick mark
            path — and the twelve-rule exit grid is derived read-side over that path rather than run as
            twelve more books.
          </p>
          <p>
            <strong>Paper only, and structurally so.</strong> There is no live loop and no order-placement
            code. The config&rsquo;s <span className="mono">live.enabled</span> is an inert placeholder that
            exists so the suite&rsquo;s surfaces can report &ldquo;paper only&rdquo; instead of
            &ldquo;unknown&rdquo;; flipping it does nothing. A live path would first need post-assignment
            management and a calculated ex-dividend decision, and neither exists.
          </p>
        </div>
      </Card>

      <Card title="reading the numbers" collapseKey="cal-help-reading" className="view-fade">
        <ul className="cal-rules">
          <li>
            <strong>Every figure is net of the modeled cost stack.</strong> Exchange fees, the per-ITM-symbol
            settlement event, the SEC and FINRA pass-throughs on a share disposal, and the suite&rsquo;s
            slippage model. Gross is not a result.
          </li>
          <li>
            <strong>Structure tags never pool.</strong> A Tuesday-entry <span className="mono">dc_3_6</span>{" "}
            is a different trade from <span className="mono">dc_4_7</span>, not a smaller sample of it, so
            every table here groups by the tag and none of them sums across tags.
          </li>
          <li>
            <strong>A hole is not a zero.</strong> A week whose recorded path cannot answer a policy is
            excluded and counted as excluded. An open week has a null net, never $0.00.
          </li>
          <li>
            <strong>The sample is biased, deliberately.</strong> Roughly four weeks a year go untraded and
            they are exactly the quarterly-expiration weeks, because ex-dividend weeks are skipped rather
            than modelled. Read the pooled table as covering ordinary weeks only.
          </li>
          <li>
            <strong>Capital is not the whole risk story for <span className="mono">path</span>.</strong> A
            long calendar&rsquo;s max loss is its debit, which is exactly right for every policy that exits
            before the bell. A book that holds to expiry under physical settlement can be assigned, and the
            delivered shares&rsquo; weekend move is not bounded by the debit.
          </li>
          <li>
            <strong>The exit rules were declared up front.</strong> They are measured, never tuned
            mid-experiment, and a removed rule keeps its negative result on the record.
          </li>
        </ul>
      </Card>

      <Card title="the settings it runs on" collapseKey="cal-help-params" className="view-fade">
        {p === undefined ? (
          <p className="muted">config not readable</p>
        ) : (
          <dl className="cal-defs">
            <dt>symbols</dt>
            <dd className="mono">{p.symbols.join(", ") || "—"}</dd>
            <dt>contracts per side</dt>
            <dd>{p.quantity ?? "—"}</dd>
            <dt>expected-move factor</dt>
            <dd>
              {fmtNum(p.emFactor, 2)} <span className="muted">× the front ATM straddle mid</span>
            </dd>
            <dt>entry window</dt>
            <dd className="mono">
              {p.entryWindowStart ?? "—"}–{p.entryWindowEnd ?? "—"} ET
            </dd>
            <dt>exit window</dt>
            <dd className="mono">
              {p.exitWindowStart ?? "—"}–{p.exitWindowEnd ?? "—"} ET
            </dd>
            <dt>max quote age</dt>
            <dd>{p.maxQuoteAgeSeconds === null ? "—" : `${p.maxQuoteAgeSeconds}s`}</dd>
            <dt>max leg spread</dt>
            <dd>{fmtPct(p.maxLegSpreadPct === null ? null : p.maxLegSpreadPct * 100, 0)}</dd>
            <dt>books</dt>
            <dd>
              {p.books.length === 0
                ? "—"
                : p.books.map((b) => (
                    <span key={b.name} className="mono">
                      {b.name}
                      {b.enabled ? "" : " (off)"}{" "}
                    </span>
                  ))}
            </dd>
            <dt>advised book</dt>
            <dd>{p.adviceEnabled ? "enabled" : "off"}</dd>
          </dl>
        )}
        <p className="integrity-note">
          Read from the module&rsquo;s own config in the module&rsquo;s own resolution order. This page cannot
          change any of it — the console is read-only over every module&rsquo;s data, and these fields are not
          on the Config page&rsquo;s allow-list.
        </p>
      </Card>

      <Card title="where the data comes from" collapseKey="cal-help-data" className="view-fade">
        <div className="cal-prose">
          <p>
            The module holds <strong>no broker credentials at all</strong> and runs no streamer. It is a pure
            read-only consumer of the suite&rsquo;s shared stream cache, whose 4DTE/7DTE chains exist there
            because the module declares both expirations to the streamer every tick.
          </p>
          <p>
            That is why an entry refusal reading <span className="mono">no_fresh_quotes</span> points at the
            streamer rather than at the market: the module priced nothing because nothing recent was in the
            cache to price. The provider refuses rather than guesses — a stale quote, a crossed one, a missing
            chain, a spot that will not print are each a recorded refusal the loop steps past.
          </p>
        </div>
      </Card>
    </div>
  );
}
