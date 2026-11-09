# Charts and GEX

Three read-only places to look at price and gamma. Charts is `/charts`, with tabs `/charts/intraday`,
`/charts/technicals` and `/charts/setups`. GEX is `/gex`, with tabs `/gex/gex` and `/gex/skew`. The GEX
panel, `/gexpanel` (one tab, `/gexpanel/panel`), is experimental. None of them can place an order.

## Charts

### What you see

- **Intraday** (`/charts/intraday`) is a live candlestick chart of a futures contract, with volume under it.
  The buttons pick the contract (the futures in the ticker strip across the top of the console, such as `/ES`
  and `/NQ`) and the candle width (`1m`, `5m` or `15m`). A `chart only` chip is a reminder
  that nothing here trades. A line above the chart gives the last price, the time of the last bar in
  Eastern time, and how many bars are loaded.
- **Technicals** (`/charts/technicals`) is one name's chart: price bars, RSI 14 in a middle pane and a
  short-term trend score (-4 to +4) under it. A symbol box at the top picks the name; with no symbol in the
  address it opens on SPX. Chips beside the name show the sentiment (Bullish, Bearish or Neutral, from the
  close against two moving averages) and the IV rank. Buttons pick a setup family and a side, and whether to
  draw our own support and resistance levels. Arrows mark where that setup would have opened and closed a
  position. Under the chart, **Scan-rule matches** lists the sessions where a scan rule matched.
- **Setups** (`/charts/setups`) is a watchlist, **Setup signals**: every entry and exit the chart setups
  made lately, and every position they still hold, across every charted name. Switch between **Entries &
  exits** and **Open positions**, narrow by window (`1d`, `5d`, `20d`), event, family, side, a relative-strength
  floor (`RS ≥ 7`) or a symbol. Each row opens "the read" (why it fired, its record, direction, volatility,
  levels and a structure lean), and the symbol opens its chart with that setup drawn.

The Technicals and Setups tabs are built from files the technicals package writes after the close, and they
disappear if the suite's technicals feature is switched off. On a fresh install, or on the demo, they can be
empty until a first nightly run has written them: Technicals then says "No chart for" the name, and Setups
says "No watchlist yet".

### How to read it

- Intraday draws what the feed sends and computes nothing. Extended hours are included and the time axis is
  Eastern. CME futures trade from Sunday 18:00 to Friday 17:00 with a break from 17:00 to 18:00, and the axis
  skips periods with no bars, so a weekend does not show as a gap. If the data feed is down a chip says "feed
  ... not live".
- Technicals and Setups cards carry a `record-only` chip. The arrows are a rule walked over history, not a
  recommendation. "Move" on Setups is close to close in the trade's direction and is never P&L.
- A rule match is a pattern on the chart, not a trade idea.
- Setups cuts the list to options-tradable names by default (see the verdict explained in
  [screeners.md](screeners.md)); the `Options-tradable` switch turns that off. The `Tested edge` switch keeps
  only the one rule a historical study confirmed (mean reversion, long, on names trading at least $300M a
  day). That study used shares, filled at the next open, not options, so it does not carry over to an
  options trade.
- A symbol such as SPX has no volume of its own; the chart then says whose volume the breakout test reads.

### What to do

Use Intraday for a quick look at the day. Use Technicals to check a name before you look at it as a
candidate on [screeners.md](screeners.md). Treat Setups as a place to find names to look at, not a list to
act on.

## GEX

GEX is dealer gamma exposure: an estimate of where options positioning makes the market steadier or
choppier, worked out from the option chain and open interest. It is recorded all day by the GEX recorder
service, so the page works only while that service is on.

### What you see

A symbol picker (SPX to begin with) and header chips sit at the top: `exp` and spot, an `as of` time for the
cached spot (dated if it is not from today), and, outside regular hours, a `last session` chip. A
**measurement integrity** drawer on the right holds the freshness checks.

- **gex** (`/gex/gex`). **GEX by strike** is a chart of net gamma at each strike, with the spot, the
  zero-gamma flip and the call and put walls marked. A filter and sort button switches the view between OI
  vs Vol, Net and Abs. To the right, two panels of the same totals: **open interest (positioning)** and
  **volume (flow)**, each with total call GEX, total put GEX, net GEX, the call and put walls and the zero
  gamma. Below it, **Net GEX recorded today** is a line of the day's recorded net GEX, how many snapshots,
  and when each symbol was last recorded.
- **iv skew** (`/gex/skew`). Three charts of the same chain: **IV skew** (call against put by strike),
  **Open interest by strike** (calls to the right, puts to the left, volume as the lighter bar beside the
  open interest) and **Volume by strike (calls vs puts)**.

### How to read it

- The **flip** (zero gamma) is the nearest strike where net gamma changes sign. The **walls** are the
  strikes carrying the most call or put gamma. They are estimates built on assumptions about who is on
  which side of each trade. Treat them as a map of where positioning sits, not as a prediction.
- A wall can show as two numbers, such as `7740 / 7600`. That is a near tie: a single number would hop
  between the two on noise, so you are shown both.
- **Open interest** is positioning, counted once a day. **Volume** is the day's flow. They can disagree.
- **Why the pages lead with volume.** tastytrade does not offer level 2 data, so open interest does not
  stream: the figure the suite has is the broker's once-a-day count, as of the open, and it does not move
  during the session. GEX by volume (gamma times the session's traded volume) is the reading that changes
  intraday, so it is what the GEX pages show by default. Read the open-interest figures as where
  positioning stood at the open. Both pages say this in a note above the readings.
- Look at the time before the shape. A reading is a claim about now. A chip saying `last session` means the
  market is closed and you are looking at the previous session's chain. A dated `as of` means the spot is
  stale.
- The integrity drawer warns when a symbol's last reading is more than 15 minutes old, or when a daily
  close series is more than five days behind the freshest one. It also says how many regime rows were
  recorded today. A short session and a stalled recorder both give a small number, so check the timestamps.
- If the page says there is no expiration with cached greeks, the data feed has nothing for that symbol yet.
  The demo is in this state outside market hours.

### What to do

Use GEX as context beside the morning pack, whose Gamma levels card shows the same kind of levels. Judge
the numbers by their time stamp. If a symbol looks stale, open the integrity drawer, then see [faq.md](faq.md).

## GEX panel (experimental)

`/gexpanel` (tab `/gexpanel/panel`) is a copy of a third party's 0DTE walls panel. It is SPX only and it is
experimental: its levels are untested, and the page says so at its foot ("Untested: display only"). It uses
broker data only, and it has no depth feed, so the copied panel's depth rows are left out. Do not size a
trade from it.

### What you see

A card on the left and a chart on the right. The card shows SPX, its spot, the expiry, an `above flip` or
`below flip` chip, and a `late session` chip in the last 30 minutes of the day. Below that, in order:

- net GEX by open interest and by volume, gross GEX and the put/call ratios;
- two buttons, `walls by volume` (the default) and `walls by OI`, then the call walls above spot (`CW1`, `CW2` ...) and the
  put walls below it (`PW1` ...), each with its share, open interest and GEX;
- zero gamma, the **magnet** (the strike with the most open interest inside the expected-move band, with the
  sign of its gamma), **max pain** (marked `(edge)` when it sits at the edge of the strikes covered, so do not
  trust it) and ATM IV;
- three move bands: a one-sigma move from the straddle, one from implied volatility, and the copied panel's
  rule of thumb at 0.85 times the straddle, which is about 0.68 sigma, not one;
- a **flow** section from today's prints, signed by who initiated them, when it has been read.

The chart, **SPX today, with the panel's levels**, draws today's spot trail with all of these as lines.
Outside hours the header shows `next session` or `last session` and the chain is not live.

### How to read it

The magnet, the walls and the bands are our reading of the panel's definitions, not a tested signal. The
moves show nothing once the chain has expired. In the flow section, an initiating buyer may be closing a
trade, not opening one, so "dealers are shorter" is an assumption.

### What to do

Look, compare it with the GEX page and the morning pack, and keep it as a curiosity until it has been tested.
