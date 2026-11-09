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

Since the screenshots needed them it also holds the earnings calendar and its forward record, the
time-spread scan (built by manage's own `timespreads.build_all` from invented quotes), the fair-value file
(a small DCF whose bridge adds up), a chart for each name and the setups watchlist (technicals' own
`chart.build` and `watchlist.rows` over an invented bar history), MEIC's regime cuts (MEIC's own writer
over the demo ledger, whose entries carry regime tags), the earnings strategies' control arms, and two
external reports, one plain and one using every presentation hint.

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
node tools/ui-check.mjs --route live/wheel --selector .mf-slide --viewport 1500x2600 --shot wheel.png
```

Check the first line it prints: the URL must say 5391. **Set `CHERRYPICK_HOME` before you run it, on its
own line:** with it unset, `ui-check` falls back to the real console's port and photographs the real
console. Routes are the rail's, with tabs after a slash: `live/positions`, `screeners/skate`,
`system/health`. `--card <title>` crops to one card that has drawn a chart, `--selector <css>` crops to any
element (`.mf-slide` is a tab's whole content without the rail, header and footer; the app scrolls an inner
container, so give a viewport tall enough that it does not) and `--scale 2` doubles the pixels for print.

## Screenshots

The images in [images/console/](images/console/) are shots of this demo, listed in
[images/shots.json](images/shots.json). Each entry says which route it shows, how it was taken (`card`,
`full_page` and a `viewport` or `selector`, a `crop` box in pixels, the `scale`), the demo `seed` and `day`
it was built from, a one-line `caption` of what it teaches, and its `sources`: the console files that draw
it (the tab's manifest and its page components). `full_page: true` means the tab's whole content, taken
with `--selector .mf-slide`.

- **Retake them all** at release time with `python scripts/reshoot.py`. It builds the home from the
  manifest's seed and day, serves it on a free port (never 5070, never a real console's), takes each shot,
  compresses it (pngquant if it is installed, Pillow's palette reduction otherwise; each image is well
  under 150 KB), and lists which images changed by more than `--threshold` percent of their pixels
  (default 0.5). Without `--write` it writes nothing in the repository and names the temporary folder the
  new images are in. It needs the console built, Chrome or Edge, and `pip install pillow`. It stops the
  server it started and nothing else, and refuses a `--home` folder that is not empty and is not a demo
  home, because the build empties it.
- **CI** runs `tools/check_shots.py`. It fails when an image has no manifest entry, an entry's image is
  missing, its route is not one the console serves (read from `navGroups.ts` and `moduleOrder.ts`), or a
  source file does not exist. It warns, without failing, when a pull request changes a file in an entry's
  `sources` but not its image. Put the label `screenshots-ok` on a pull request whose change cannot be seen
  in the pictures, and the warning is skipped.
- **Clock.** The console reads the wall clock, so the pages that mean "today" (Live, today) read as a quiet
  day when the shot is taken outside a trading day, and an external report says how many hours old it is.
  The reshoot threshold absorbs the hours; take Live, today on a trading day when it matters.
- **Look at every image** before it goes into a document: the Config shots leave out the hero strip because
  it prints the home's real folder, and System, environment is not shot because it reads this machine's own
  host name, interpreter path and git state.

## The fake-data rules

- Index and fund trades use only SPX, XSP, SPY, QQQ and IWM. Single names are KOBA, TOVA, ZELV and VYNT.
  They were chosen as pronounceable four-letter names that are not in technicals' seed universe and were in
  no tracked file before the demo took them, so none of them can be read as a real company. Before changing
  one, check it against `packages/technicals/src/cherrypick/technicals/seed_universe.json` and `git grep -w`; a test in
  `packages/orchestrator/tests/test_demo_home_script.py` fails if a demo name is in the seed universe.
  Company names on the calendar (Koba Systems and the like) are as invented as the tickers.
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

These pages render their empty state on the demo home: Live flies (the module is retired), and System
environment, which reads the machine it runs on (host name, interpreter, the checkout's git state) and has
nothing a home could stand in for. Advisor, Backtest and the retired modules (flies, curve, PMCC, BWB) are
off by design, which is why Reports, end of day and Compare list them as absent. Screeners earnings
calendar, earnings record, time spreads and fair value; Charts technicals and setups; Earnings decisions
and strategies; and MEIC regime cuts and analysis are populated. The income lists do not carry the
fair-value columns, so a row there reads "no fair value" beside a fair-value tab that has a value.
