# cherrypick-technicals

The market report's end-of-day store and technical engines (see
[`docs/market-report-plan.md`](../../docs/market-report-plan.md)).

Every morning after the 05:30 ET Dolt pull, it lands raw daily bars, splits, dividends and
implied-volatility history for the 35 rotation ETFs, the benchmarks and the stock universe's
candidates into its own SQLite store, `~/.cherrypick/data/technicals/eod.db` (`technicals-land`,
06:15 ET). The candidates exist only once the stock-universe builder has run; it is off by default
(`market_report.universe`). At 06:30 ET `report` writes that session's readings,
`report-<session>.json`, for the console. Adjusted prices are computed from raw on read, with the
same proportional dividend and split adjustment the vendor uses. On the vendor's own MSFT chart
data it matched all 753 sessions to the cent.

The engines' parameters were fitted against saved vendor editions and are scored out of sample by
the `score-*` commands. The daily path needs no vendor data. `check-vendor` and the
`score-*` commands need vendor captures on file, which only the vendor-edition collector writes;
that collector is off by default and runs only with `market_report.collector` on and
`market_report.vendor_dashboard_url` and `market_report.vendor_edition_title` set.

```
pip install -e packages/core
pip install -e "packages/technicals[dev]"
python -m cherrypick.technicals land
python -m cherrypick.technicals status
python -m cherrypick.technicals bars MSFT --last 5
python -m cherrypick.technicals report
python -m cherrypick.technicals check-vendor          # needs vendor captures (see above)
```

It needs the local `dolt sql-server` that the earnings module runs. The suite schedules it only when
the machine has the `dolt` capability (recorded by `run.py capabilities`) and the earnings module is
on; otherwise it is off and the console hides it. The package uses no credentials and no network;
the broker reads it relies on (dividend history, IV rank, SPX index bars) are scripts outside it,
which write files it reads. Operating rules are in [CLAUDE.md](CLAUDE.md).
