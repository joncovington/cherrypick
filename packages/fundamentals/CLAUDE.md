# cherrypick-fundamentals — Operational Instructions

> Company fundamentals and the fair-value model's inputs, and the fair-value file the Screeners tab and
> the technicals income lists read. Moved out of `packages/technicals` on 2026-10-29. The method and its
> gaps are in [`docs/fair-value-model-plan.md`](../../docs/fair-value-model-plan.md); the arithmetic is
> `cherrypick.core.valuation` (version 2).

## Posture

- **Credential-free and network-free.** The package reads local files and the local Dolt server. The
  network is reached only by two scripts, run as jobs: `scripts/fetch_sec_facts.py` (SEC EDGAR, no
  credential; its request header is `SEC_USER_AGENT` in the checkout's untracked `.env`) and
  `scripts/fetch_damodaran.py` (Damodaran's public files at NYU Stern).
- **Writes only its own store**, `~/.cherrypick/data/fundamentals/` (`FUNDAMENTALS_DATA_DIR`):
  `fundamentals.db` (SEC facts and filers, Dolt's annual statements, the revenue consensus), `sec/`,
  `damodaran/` and `valuation/<session>.json`.
- **Reads technicals for prices only** (bars, splits, the security list, the sector map, the liquidity
  verdict, the broker's IV file): the one package besides core another declares as a dependency.
  **Technicals never imports this package**; it reads the fair-value file by path
  (`technicals.paths.fair_value_dir()`), so there is no cycle. CI reruns this package when technicals
  changes (`scripts/ci_changed_packages.py`, `DEPENDENTS`), and every installer installs technicals
  before it.
- **No AI, no broker, no order path.**

## The rule that outranks the model

The fair value is **a description, never a selector**. Its cheap-vs-rich gap was tested 2018-26 with no
edge; every surface says so. Its agreed use is "how much has to go right" for a PMCC long leg or a stock
a wheel or skate put may be assigned (reach and stretch), never a ranking or a filter. A model change
re-runs `scripts/fair_value_history.py` before any verdict is read again.

## Data

- **Point in time.** Every SEC fact keeps the date it was filed (`sec_facts.filed`); a read "as of" a
  session sees only what was filed by then. Dolt's statements, which have no filing dates, count 45
  days after their period ends. The consensus is the newest weekly snapshot no older than three weeks.
- **The filing first, Dolt as the fallback and the cross-check** (`secfacts.merged_years`): a line the
  filing does not state, or a year the filings miss, is Dolt's and says so; a line further than 10%
  from Dolt's is recorded (`disagree`). Money is read in US dollars only.
- **Tags are chosen when read, not when landed** (`secfacts.FIELDS`): a new tag is a chain entry and a
  re-land, never a migration.
- These sources are public and free; each install fetches its own copy into its home. Nothing fetched
  is ever committed or redistributed. The reference DCF platform the model was checked against is never
  named in a file, a commit or a PR, and none of its material is stored.

## Jobs (the orchestrator's `fundamentals` block, ET, every day)

`fundamentals-sec` 06:00, `-damodaran` 06:02, `-land` 06:30 (after technicals' 06:25 landing),
`-valuation` 06:35. On wherever technicals and its history landing are.

## Commands

    python -m cherrypick.fundamentals valuation [--day YYYY-MM-DD]
    python -m cherrypick.fundamentals land sec|statements|consensus|all
