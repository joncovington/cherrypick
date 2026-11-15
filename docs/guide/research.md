# Research pages

Three pages look across the suite rather than at one module: Compare at `/compare`, Backtest at `/backtest` and the Advisor at `/advisor`. Compare is always there. Backtest and the Advisor are off by default and do not appear until you switch them on.

## Compare (`/compare`)

Compare puts every arm of every module beside its own control, from one reader, so the numbers are measured the same way everywhere. Its tabs are `arms`, `risk`, `calendar`, `portfolio` and `signals`. Another, `slices`, appears only while licensed results may be shown (see Backtest below).

### What you see

![Compare arms tab: a scope selector, a note on how the figures are computed, then one block per module with each arm's trades, gross, costs, net and its comparison against control](../images/compare-arms.png)

`arms` is a block per module. A module with no ledger on your machine says so ("No ledger on this machine: no paper ledger"), which is what you see for a module that has not run here. Where a module has trades, each arm gets a row: trades, gross, costs, net, win rate, max drawdown, the mean per session, net per $100 of risk, and then a group of columns against the control, over the sessions both arms closed. Click an arm's row to open its per-trade net histogram.

The scope selector at the top has two settings. "each module's era" is the default and reads each module from its own latest measurement break. "all rows (pooled across breaks)" mixes eras and is a stated widening, never the default.

`risk` is a table of every arm's risk over its own window: peak risk, a drawdown estimate from resampling, and the slippage at which the net would reach zero. `calendar` is a month grid of each arm's settled net per session, with weekly totals. `portfolio` takes one arm from each module and holds them together: how correlated they are, how many independent bets you really have, and whether combining them cuts the drawdown.

`signals` shows a signal studied on the underlying alone, before any option is priced. For each time it fired, it shows what the price did:
- the move to the close and to a number of sessions later;
- how far it ran against the expected move;
- how often it reached a distance you chose.

Each figure sits beside a control: the same minute on sessions where the signal had not fired by then. It is shown by year and for every firing. Studies are run with `packages/backtest/scripts/signal_study.py`. One run on public daily bars or the recorder's minutes is always shown. One run on the vendor's data appears only while the licensed-data feature is on. A signal that does nothing for the underlying is unlikely to be rescued by an option structure built on it. A signal that does something is a lead to declare and test, never a result.

### How to read it

- **Net is after every cost.** Gross less costs, where costs are fees, settlement and slippage for the modules that charge slippage as a cost. MEIC concedes its slippage inside the fill, so it is already in gross.
- **Intervals are bootstrap intervals,** not predictions. An interval that spans zero says the data cannot tell the arm from nothing yet. The "sessions until" column projects how many more sessions the interval would need to exclude zero, at the observed mean and spread. It is a projection, not a forecast.
- **Paired columns use only sessions both arms closed.** A session that only one side closed is counted but never filled with zero.
- **A row marked thin** has too few sessions or periods to read. It is shown and never a finding.
- **The same trap applies as everywhere:** paper is an upper bound. See [strategy-pages.md](strategy-pages.md).

### What to do

Use Compare to decide whether an arm is worth keeping, and be patient. Prefer the paired difference against control to an arm's own net, and a figure over dozens of sessions to one over a few. Don't promote or switch off an arm on a thin row.

## Backtest (`/backtest`)

Backtest shows replays of the strategies over licensed historical data. It has four tabs: `studies`, `queue`, `runs` and `data`. `studies` is what was tested and what was found, `queue` is the research tasks the supervisor runs one at a time, `runs` is the replays, and `data` is whether the history can be trusted, the licence and the data stores.

**The page is always there; what shows licensed data is not.** Results derived from licensed vendor history may not be redistributed. So the `runs` and `data` tabs, a study's figures, report and notes, a queue task's log and Compare's `slices` tab appear only while those results may be shown. That is from setup until the licence's purge date, which includes the weeks after the licence ends. Otherwise those tabs are gone and those panels say "Withheld". The studies' names, statuses and verdict words, the queue and studies on our own data are always shown. Nothing licensed is shown in this guide, and the data and anything derived from it stay on your machine. The data and anything derived from it never go in a public issue, a pull request or a shared page. How the research register, the queue and the backtest package work is in [research-and-backtests.md](research-and-backtests.md).

## Advisor (`/advisor`)

The advisor is an AI assistant that watches the day, proposes changes and runs them as paper experiments against a control. It never touches a live account: it can only propose, and a module's own loop decides. The page has four tabs: `today`, `proposals`, `experiments` and `history`, all inside the one page at `/advisor`.

It is off by default, and it needs the Claude Code command-line tool on your machine. While it is off, the page and each module's `advisor` tab are hidden from the console.

### What you see

`today` is the latest checkpoint for each slot of the session. `proposals` lists what the advisor proposed that session, with the ones it rejected shown beside the ones that were admitted and the reason for each. `experiments` has a card per experiment: its own advised arm beside its base arm, how many sessions it has run, and a verdict. `history` is a table of checkpoints. A session picker chooses the day.

If the advisor has been paused, a notice at the top says so. While paused, a new experiment is admitted but queued and none activates, and the model cannot change or end a running one.

### How to read it

- **The numbers are computed by the suite, not by the model.** The model's keep, kill or promote is a recommendation beside those numbers, never in place of them.
- **Underpowered is not failed.** An experiment below the promotion gate's sample and day thresholds has not been measured. That is neither a pass nor a fail.
- **Written and applied are two things.** The advisor writing advice and a module's loop applying it can come apart, and the page says which happened.

### What to do

Read the proposals and the reasons. The page has only two write actions, both of which narrow things: kill an experiment, and dismiss a proposal. Leave an underpowered experiment running until it has enough sessions. Do nothing with the advisor if you have not switched it on; nothing in the suite depends on it.
