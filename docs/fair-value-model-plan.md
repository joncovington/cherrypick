# Fair value: closing the model's gaps (plan, 2026-10-28; built 2026-10-29)

The fair-value tab (`core.valuation`, `technicals valuation`, the Screeners tab) was checked name by
name against a reference DCF platform the user subscribes to. The comparison found two arithmetic
errors, two data gaps and several places where our method is simpler than standard practice. The
user asked to close them, using whatever public data it takes. This plan says what changes, in
what order, and where each input will come from. It names no vendor and copies none of the
platform's material: every method below is public (SEC filings, the Federal Reserve, Aswath
Damodaran's published data and textbook DCF practice).

The tab's use does not change: a description beside the PMCC, wheel and skate lists ("how much has
to go right"), never a selector. The cheap-vs-rich gap was tested 2018-26 with no edge. That result
is re-run once at the end (stage 5), not after each stage.

## What the comparison found

| Name | Ours | Finding | Kind |
|---|---|---|---|
| AAL | $40 (price $12.61) | debt priced at its coupon; the WACC fell under its floor | fixed by a refusal (#657); replaced in stage 2 |
| NFLX | $123 (price $62) | content amortisation added back as D&A; its cash cost is in operating cash flow | error |
| GM | $289 (price $81) | the finance arm's debt counted, its funding cost not | error |
| MU | refused | the store's newest MU year is FY2025; trough-year medians | data |
| BMY | $112 (price $62) | one year of consensus; the patent cliff in years 2-3 never seen; interest expense missing | data / method |
| ORCL | refused | a build-out needs more than five forecast years | method |
| DELL, TSM | low | beta from three years of price returns, bounded rather than adjusted | method |
| CVNA | agrees | | |

Also found while reading Dolt: the forecast's year one uses the "Next Year" consensus, which is the
fiscal year *after* the current one. Year one is the current fiscal year. Every value since 2026-10-27
is a year early on growth.

## Stage 1: data (public sources, fetched by script into `~/.cherrypick`)

- **Company filings (SEC EDGAR XBRL `companyfacts`, public domain).** The primary source of annual
  and trailing-twelve-month figures, with each figure's filing date (true point in time for the
  history study). Tags for revenue, operating income, interest expense, pretax income and taxes,
  depreciation of property and equipment kept apart from amortisation (the NFLX fix),
  capital expenditure, working-capital lines, cash and marketable securities, short and long-term
  debt, finance and operating lease liabilities, preferred stock, noncontrolling interest, basic
  shares from the cover page, unvested RSUs, options and convertibles. A fetch keeps a raw copy per
  company and refreshes nightly for names with a new filing. SEC asks for a contact in the request
  header and at most ten requests a second; the fetch sends far fewer and reads its header from the
  local `.env` (`SEC_USER_AGENT`), never from the repo.
- **Dolt stays, as the cross-check.** Where the filing and Dolt disagree on a line by more than 10%
  the row is flagged and the filing is used.
- **Consensus (Dolt `sales_estimate`).** "Current Year" becomes forecast year one, "Next Year" year
  two (the year-early fix). The analyst count and the high/low range give a quality score; a thin
  or stale consensus falls back to history. Year three has no free source yet; it fades from year
  two.
- **Equity risk premium (Damodaran's implied ERP, published monthly).** Replaces the fixed 5%.
- **Credit spreads by interest coverage (Damodaran's synthetic-rating table).** The cost of debt
  becomes the risk-free rate plus the spread for the company's coverage ratio, not its coupon.
- **Industry returns on capital (Damodaran's industry data),** for an optional override only.
- **Risk-free rate:** the 10-year Treasury, as now (`core.treasury`).
- **Prices:** total-return weekly bars (dividends included) from the history store.

Every new store is declared in the same change as its fetch code, and licensed or vendor data never
enters the repo. These sources are free and public; each install fetches its own copy and none is
redistributed.

## Stage 2: the model (`core.valuation`, a pure function as now)

- **Ten forecast years:** consensus for years one and two, then growth fades linearly to the
  long-run rate by year ten.
- **History ratios over five years:** operating margin, D&A, capex and working capital as shares of
  revenue; working capital as a level, its change as the cash flow; the tax rate as the five-year
  median effective rate, excluding loss years.
- **Mid-year discounting.**
- **A terminal value that pays for its growth:** year-eleven reinvestment is growth over the return
  on capital, with the return on capital set equal to the WACC after year ten (the stable-growth
  method in Damodaran's teaching). Growth then neither adds nor removes value, and the perpetuity
  stops counting free growth.
- **Beta:** five years of weekly total returns against SPY, Blume-adjusted (0.67 × raw + 0.33)
  instead of bounded.
- **Cost of debt:** synthetic rating from interest coverage, taxed at the US marginal rate (25%).
  This replaces the `debt_dominated` refusal: AAL's coverage maps to a junk spread, and the
  arithmetic then shows what is left for shareholders.
- **Equity bridge:** plus cash and marketable securities; minus short and long-term debt, finance
  leases, preferred stock and noncontrolling interest. Shares: basic plus unvested RSUs, plus
  options by the treasury-stock method at the modelled value, plus convertibles that would convert.
- **Refused, with the reason in words:** banks, insurers, brokers and lenders; REITs; funds; no
  revenue; under three years of history; a manufacturer whose finance arm holds a large share of
  its assets (GM, Ford); a foreign filer (form 20-F or 40-F); latest results more than 200 days old.
- **Flags, each with what it would change:** a value far from the price, with the discount rate and
  the margin the price implies; a consensus year that dips below long-run growth; a filing and Dolt
  that disagree; a D&A or capex share that moved sharply in the last year; terminal-heavy.

## Stage 3: the tab's Advanced view

The detail panel gains an Advanced section that lists every input with its value and **its
source** (the filing and period, Dolt and its date, the Treasury day, the ERP month, the coverage
ratio and rating), and four tables: the WACC build, the year-by-year forecast to year eleven, the
share count and dilution, and the consensus years with their quality. No overrides in the first
version: the page shows its working, and a custom value can come later if the user wants one.

## Stage 4: check against the reference

The user re-checks the comparison list (AAL, NFLX, GM, MU, BMY, ORCL, DELL, TSM, CVNA and the groups
in the 2026-10-28 list) by hand against the reference. A difference left after this stage is
written down here with its cause, not tuned away.

## Stage 5: the history study again

`scripts/fair_value_history.py` re-runs on the new model, at month-ends 2018-26, point in time from
the filings' own dates. The verdict stands or changes as the study says. Until then every surface
keeps "Tested 2018-26: no edge".

**Result (2026-10-29): FAIL in both halves; "no edge" stands.** The declared test of 2026-10-28,
unchanged, on version 2 valued by the nightly build's own code: 716 liquid optionable stocks, 441
ever valued, 102 month-ends from 2018. The cheap-less-rich 63-session return spread was positive in
19 of 51 sessions in the first half (median −1.7%) and 16 of 51 in the second (median −2.1%); the
short-put proxy failed both halves too; Spearman's mean +0.01. The names version 2 reads as cheap did
slightly worse than the rich ones, as under version 1 (median −1.25% a half). Public data and our own
model; the full result is `~/.cherrypick/data/fundamentals/study/fair-value-history-v2-20261029.json`.
The fair value stays a description: what a price asks of a company, never a reason to pick one.

## Order and boundaries

One PR per stage, the data before the model, the model before the view. Stage 2 changes every value
on the tab, which is a read surface and records nothing, so it lands when it is ready. Each stage
shows its new guards failing on purpose before it merges.
