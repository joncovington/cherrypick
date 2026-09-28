# cherrypick-technicals

The market report's end-of-day store and technical engines (see
[`docs/market-report-plan.md`](../../docs/market-report-plan.md)).

Every morning after the Dolt pull, it lands raw daily bars, splits, dividends and implied-volatility
history for the stock universe's candidates, the 35 rotation ETFs and the benchmarks into its own
SQLite store, `~/.cherrypick/data/technicals/eod.db`. Adjusted prices are computed from raw on read,
with the same proportional dividend and split adjustment the vendor uses. On the vendor's own
MSFT chart data it matches all 753 closes to the cent.

```
pip install -e packages/core
pip install -e "packages/technicals[dev]"
python -m cherrypick.technicals land
python -m cherrypick.technicals bars MSFT --last 5
python -m cherrypick.technicals check-vendor
```

It needs the local `dolt sql-server` that the earnings module runs. It uses no credentials and no
network. Operating rules are in [CLAUDE.md](CLAUDE.md).
