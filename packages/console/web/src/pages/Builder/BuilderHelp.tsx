import { HelpSection, HelpWarn, type CardHelpContent } from "../../components/CardHelp";

/**
 * The Builder's section help (the suite's card-help pattern): what each section's fields mean to the
 * engine, from the automations package's own rules (packages/automations/CLAUDE.md). It explains;
 * whether a value is right is still the module's Check.
 */

const LABELS = (
  <HelpSection title="The label beside the heading">
    <p>
      What a backtest judged of this block. <strong>Backtested</strong>: it stands as the research study ran it.{" "}
      <strong>Changed since its backtest</strong>: it was edited after. <strong>Not backtested</strong>: no study ran it. A paper result
      on a block that is not backtested is descriptive until its forward check is read.
    </p>
  </HelpSection>
);

export const SECTION_HELP: Record<string, CardHelpContent> = {
  underlying: {
    title: "The underlying, its expiry and the days it trades",
    body: (
      <>
        <HelpSection title="Expiry">
          <ul>
            <li>
              <strong>dte</strong>, <strong>calendar</strong>: the listed expiry that many calendar days out, within its tolerance.
            </li>
            <li>
              <strong>market days</strong>: the expiry that many trading days out.
            </li>
            <li>
              <strong>at least</strong>: the nearest listed expiry that many days out or more.
            </li>
            <li>
              <strong>between</strong>: the nearest inside the window, or the farthest with prefer "last".
            </li>
          </ul>
        </HelpSection>
        <HelpSection title="Weekdays and events">
          <p>
            It enters only on the weekdays ticked. A day carrying a ticked event is not entered. An event label the calendar has no data
            for on that day passes, recorded as unknown, never read as quiet.
          </p>
        </HelpSection>
        {LABELS}
      </>
    ),
  },
  signal: {
    title: "When it enters",
    body: (
      <>
        <HelpSection title="The three kinds">
          <ul>
            <li>
              <strong>time</strong>: at a fixed time, for a window of minutes.
            </li>
            <li>
              <strong>orb</strong>: the first minute spot breaks the opening range of that many minutes, above or below, until the latest
              time. Single shot: it looks once and does not look again.
            </li>
            <li>
              <strong>trigger</strong>: a fixed time for <em>within</em> minutes, or a core signal's first firing inside a window, open
              for <em>within</em> minutes after it, or until the window closes when left blank.
            </li>
          </ul>
        </HelpSection>
        <HelpSection title="Signals">
          <p>
            A signal reads bars of <em>bar minutes</em> built from the minute trail, or completed daily bars when <em>daily</em> is
            true (it acts today on yesterday's close). An intraday signal reads the earlier sessions too, so an indicator is warm at the
            open. A signal that reads volume names the symbol whose volume weighs the bars (SPY for SPX).
          </p>
        </HelpSection>
        <HelpWarn title="Cadence">
          <p>
            Paper reads every minute. A backtest reads every stored minute, or one tick at 15:59 on end-of-day chains, where an entry
            window that does not hold that tick counts "needs intraday" and is never moved to the close.
          </p>
        </HelpWarn>
        {LABELS}
      </>
    ),
  },
  gates: {
    title: "Gates read before the legs",
    body: (
      <>
        <HelpSection title="How they run">
          <p>
            In the order listed, before any leg is chosen. The first gate that fails stops the entry, and the entry records that gate and
            the value it read. A gate's min and max are inclusive; leave one blank to bound one side only.
          </p>
        </HelpSection>
        <HelpSection title="What they read">
          <ul>
            <li>
              <strong>Market</strong>: the move from the previous close in one day's sigma (from the previous close's VIX), change and
              gap, VIX level and change, moving averages, RSI and stochastic on completed five-minute bars.
            </li>
            <li>
              <strong>Vol index</strong>: the underlying's Cboe vol index (VIX for SPX, GVZ for GLD) against its own past closes, or
              against realised vol. An underlying with no index fails these gates.
            </li>
            <li>
              <strong>Chain</strong>: GEX ratio and put total over a window of strikes, the expected move.
            </li>
          </ul>
        </HelpSection>
        {LABELS}
      </>
    ),
  },
  structure: {
    title: "Legs, then their price",
    body: (
      <>
        <HelpSection title="Each leg">
          <p>
            Side −1 is sold, 1 is bought; right P or C. The strike rule places it: by delta or premium, at spot, the previous close, the
            open or the entry's level shifted by points or percent, from another leg, at the expected move or a standard deviation, at
            the opening range, or at a GEX strike. A leg that refers to another must come after it.
          </p>
        </HelpSection>
        <HelpSection title="Match">
          <p>
            How a target meets the listed strikes: exact, nearest, above or below. Left at the default, the rule's own is used; a rule
            with no default needs one. GEX picks and "same as" place the strike themselves.
          </p>
        </HelpSection>
        <HelpWarn title="A sold leg needs a bid">
          <p>A structure whose sold leg has no bid is unpriced and skipped, never filled at a guess.</p>
        </HelpWarn>
        <HelpSection title="Price gates">
          <p>
            Read once the legs are chosen: the structure's mid price, its whole bid/ask width (the legs' spreads summed), and reward
            over risk (max profit over max loss at the mid).
          </p>
        </HelpSection>
        {LABELS}
      </>
    ),
  },
  exits: {
    title: "Exits, size and costs",
    body: (
      <>
        <HelpSection title="Exits, in the order checked">
          <p>
            The touch first, then avoid-events (a forced close the session before a listed event), the stops (a multiple of the credit,
            a percent of the debit, dollars), the trailing stop, the profit targets, the delta limit, the signal exit, days to expiry,
            and minutes before expiry. Dollar rules read the open P&amp;L at mid, whole-position dollars. Max spread holds every exit while
            the structure is wider than that a share.
          </p>
        </HelpSection>
        <HelpSection title="Size and limits">
          <p>
            A count of contracts, a risk budget (the largest count whose max loss at mid fits), or a percent of an allocation; any can
            step after losses or wins with a progression. <strong>Max open</strong> and <strong>per day</strong> are required and read with
            no fallback; per day is 1.
          </p>
        </HelpSection>
        <HelpSection title="Costs">
          <p>
            Not set here. Fills are at mid with the suite's slippage model, fees are the suite's, and P&amp;L is net at that fill, with
            fees, settlement and slippage each in their own column.
          </p>
        </HelpSection>
        {LABELS}
      </>
    ),
  },
  today: {
    title: "Today, live",
    body: (
      <>
        <HelpSection title="What it reads">
          <p>
            The automation decided now, as the paper loop would decide it: the same snapshot from today's stream cache and the paper
            ledger, opened read-only, and the same decision. An unsaved edit is decided as it stands.
          </p>
        </HelpSection>
        <HelpSection title="What it shows">
          <p>
            Every check the engine read, with its value and result, then the legs it would take or the check that would stop it.
            Outside its entry window, or without a quote, it says why it could not decide.
          </p>
        </HelpSection>
        <HelpWarn title="Nothing is placed">
          <p>It writes no decision, no register row and no record, and places nothing.</p>
        </HelpWarn>
      </>
    ),
  },
  runs: {
    title: "Explore runs",
    body: (
      <>
        <HelpSection title="What a run is">
          <p>
            The saved automation backtested through the engine paper runs, over the window and store its suggestion names. It is
            descriptive: no trial, no verdict, and no label moves.
          </p>
        </HelpSection>
        <HelpWarn title="Every run is a look">
          <p>
            Explore runs are not trials, so a study declared later should name how many versions were looked at first. The count is
            shown at the top of the list.
          </p>
        </HelpWarn>
        <HelpSection title="The run card">
          <p>
            Every figure is the run's own: P&amp;L is net at the verdict fill, each year's gross less fees, settlement and slippage, and
            the drawdown is the closed trades'. A run over licensed data is shown only while licensed results may be.
          </p>
        </HelpSection>
      </>
    ),
  },  ideas: {
    title: "Ideas",
    body: (
      <>
        <HelpSection title="What a search is">
          <p>
            Find ideas reads the newest Builder run with at least 120 sessions traded, up to a split two thirds of the way through
            it. It tries entry filters from a short fixed menu: the prior close's VIX, RSI, trend and move, VIX over realised
            volatility, the opening gap, the weekday and event days, alone or two at once. It never changes the automation.
          </p>
        </HelpSection>
        <HelpSection title="How a filter is scored">
          <p>
            By what it skips. Skipping days raises P&amp;L only if those days lost, so the skipped days' net must sit below zero.
            It must beat the best of the random filters the menu could have found by luck, its neighbouring bins must agree, it must
            lose in most folds and both halves, each part must add on its own, and the days it keeps must still pay at 1.5x costs.
          </p>
        </HelpSection>
        <HelpWarn title="The held-out part is spent once per idea">
          <p>
            The later sessions are never read by the search. Declare drafts a surviving idea's register row with the split fixed;
            Test once reads the later sessions for that idea, and every use is logged on the search. An idea the later part cannot
            detect reads not testable on our history.
          </p>
        </HelpWarn>
      </>
    ),
  },
};
