# The demo home

A fake suite home for taking screenshots of the console on invented data. It exists for the
documentation and for nothing else: it is not a mode of the console, the installer does not know
about it, and nothing in it came from a broker, a vendor or a real account.

**The demo must never see real credentials, a real session or a real quote.** That is why the only
supported way to serve it is `scripts/demo_console.py`, which refuses anything that is not a generated
demo home and starts the server with the OS keyring switched off. Do not start the console on a demo home
any other way.

## Build one

```
python scripts/demo_home.py --out ~/demo-home [--seed 7] [--day 2026-11-06] [--days 30]
```

Run it from a checkout with the suite installed (`scripts/dev-install.ps1` or `.sh`); it uses the
packages' own writers wherever one exists (the manage evaluation, the end-of-day review, the morning
pack, the stream cache and the GEX tables), and builds the ledgers from the schemas the console's test
fixtures pin.

`--out` is emptied first and gets a `.demo-home` marker, which the launcher looks for. The script
refuses a directory called `.cherrypick`. The same seed, day and day count always write the same
files. `--day` is the latest session and must be a trading day; everything else is dated relative to it.
Generating on a trading day, with `--day` set to that day, makes the Live "today" tabs current.

Generation fails if the checkout's real path or your OS username appears in any file it wrote. The
configs name the checkout as `/opt/cherrypick`.

What you get: configs for the modules the release ships (MEIC, earnings, calendars, contango and weekly
put on; flies, curve, PMCC and BWB off), thirty sessions of paper ledgers for each, a manage evaluation
for the Live tabs (one wheel cycle with a put assigned into shares and a covered call suggested, one
PMCC, and a few ordinary positions), skate, put, call and credit-spread screener files, the signal
board, the end-of-day reviews and a morning pack, GEX history with a one-expiry chain, a stream cache,
and the supervisor and watchdog files that make System read healthy.

## Serve it

Build the console once (`pnpm --dir packages/console install`, then the shared, server and web builds),
then:

```
python scripts/demo_console.py --home ~/demo-home --keepalive
```

The home names its own port, `5391`, in `config/console.json`. The launcher refuses port 5070, any
port your real console config names, a home with no marker, and a `CHERRYPICK_HOME` that resolves to
your real home. It starts the server with the Python keyring set to the null backend, the Node keyring
binding replaced by an empty one, and credential-looking environment variables removed, so the footer
reads `dxlink idle` and every price on a page is the demo's. `--keepalive` also runs the heartbeat
writer (`demo_home.py --keepalive`), without which System reads stale. Ctrl-C stops both processes it
started and nothing else.

## Shoot it

`ui-check` reads the port from the home, so give it the same `CHERRYPICK_HOME`:

```
export CHERRYPICK_HOME=~/demo-home
cd packages/console
node tools/ui-check.mjs --route live/wheel --shot wheel.png --full
```

Check the first line it prints: the URL must say 5391. Routes are the rail's, with tabs after a slash:
`live/positions`, `screeners/skate`, `system/health`. `--card <title>` crops to one card and
`--scale 2` doubles the pixels for print.

## The fake-data rules

- Index and fund trades use only SPX, XSP, SPY, QQQ and IWM. Single names are AAA, BBB, CCC and DDD.
- The account is `****0000`.
- Every price, fill, strike and P&L is invented and the rows add up the way the real ones must
  ([money-layout.md](conventions/money-layout.md)): entry plus exit is gross, gross less each cost
  column is net, fees, settlement and slippage in their own columns.
- No number comes from a vendor, and nothing is derived from vendor data.
- No real person, company or source is named.
- Dates are relative to `--day`.

Two things name real instruments that are not trades. The morning pack and some config text carry a few
index, volatility and fund names the packages declare as labels (the readings next to them are empty or
the demo's own), and the contango arms hold QQQ and IWM in place of the volatility fund and bills the
module normally holds. Look at a screenshot before it goes into a document.

## Not yet populated

These pages render their empty state on the demo home: Screeners earnings calendar, earnings record,
time spreads and fair value; Charts technicals and setups; Earnings decisions and strategies; MEIC regime
cuts and analysis; System environment; Live flies. Advisor, Backtest and the retired
modules (flies, curve, PMCC, BWB) are off by design.
