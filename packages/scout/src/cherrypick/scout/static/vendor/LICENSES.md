# Vendored frontend assets

No CDN, no npm/Node, no build step (suite rule) — these are the exact bytes fetched from each
project's official release, checked in once and never modified. Re-vendor by re-running the same
fetch against a new pinned version; do not hand-edit any file in this directory.

| File | Project | Version | License | Source |
|---|---|---|---|---|
| `lightweight-charts.standalone.production.js` | [TradingView Lightweight Charts](https://github.com/tradingview/lightweight-charts) | 5.2.0 | Apache-2.0 | `https://unpkg.com/lightweight-charts@5.2.0/dist/lightweight-charts.standalone.production.js` |
| `tabulator.min.js` | [Tabulator](https://github.com/olifolkerd/tabulator) | 6.5.0 | MIT | `https://unpkg.com/tabulator-tables@6.5.0/dist/js/tabulator.min.js` |
| `tabulator_midnight.min.css` | Tabulator | 6.5.0 | MIT | `https://unpkg.com/tabulator-tables@6.5.0/dist/css/tabulator_midnight.min.css` |
| `htmx.min.js` | [htmx](https://github.com/bigskysoftware/htmx) | 2.0.4 | 0BSD | `https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js` |
| `alpine.min.js` | [Alpine.js](https://github.com/alpinejs/alpine) | 3.14.9 | MIT | `https://unpkg.com/alpinejs@3.14.9/dist/cdn.min.js` |

**Apache-2.0 attribution (Lightweight Charts):** the license requires a visible attribution back to
TradingView. `index.html` carries a footer link, `https://www.tradingview.com/`, which the
self-contained-page test (`test_self_contained.py`) explicitly allows as the one non-local
`https://` reference on the page — every other fetched resource (`<script src>`, `<link href>`,
`<img src>`) must resolve locally under `/static/`.

Total vendored payload: ~768 kB uncompressed (~70 kB gzipped over the wire, per the design budget).
