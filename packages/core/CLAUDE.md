# cherrypick-core

Shared library for the suite, consumed by every package as `cherrypick.core.*`. See
[README.md](README.md) for the design invariants and layout; [CUTOVER.md](CUTOVER.md) is the frozen
record of the original submodule cutover.

**This monorepo is the source of truth.** Core was landed from the standalone `cherrypick-core` repo
by `git subtree add` (history preserved); that repo is archived read-only and there is nothing to keep
in sync. Its one former out-of-repo consumer, `tastytrade-mcp`, is retired.

## Stay import-self-contained

**The core imports nothing from a consumer.** Everything a consumer supplies is injected or
parameterized, never reached back into; a new core module imports only `cherrypick.core.*` and the
standard library. This keeps `git subtree split --prefix=packages/core` a byte-identical reproduction
of the standalone repo — the escape hatch if core ever has to be split out again.

**Do not use that split as the check — it segfaults on this machine** (exit 139 under Git Bash, git
2.44.0.windows.1, at commits predating any change), so a failure there says nothing about yours. Check
the invariant directly:

```bash
test -f packages/core/cherrypick/__init__.py && echo VIOLATION || echo "PEP 420 intact"
grep -rn "^\s*\(from\|import\) cherrypick\.\(meic\|flies\|bwb\|calendars\|pmcc\|curve\|contango\|gex\|earnings\|orchestrator\|streamer\|console\|review\|advisor\|desk\|overview\|technicals\|manage\|backtest\)\b"   packages/core/cherrypick/ --include=*.py    # must print nothing
```

If the split must ever run for real, run it where `git subtree` works.

## Layout stays flat, not src-layout

`packages/core/cherrypick/` sits directly under the package root, unlike the other packages, on
purpose: it matches the standalone repo so the split needs no path rewriting, and `tests/conftest.py`
bootstraps `parents[1]` onto `sys.path`, which depends on it. **`cherrypick/` must never gain an
`__init__.py`** — it is a PEP 420 namespace package that composes with every module's
`cherrypick.<module>` under one import root, and an `__init__.py` would break that for every consumer
at once. Accepted cost: `tests/` would be a top-level importable name if `packages/core` ever landed
directly on `sys.path`; nothing does that (core is always reached as an installed distribution).

## The module map

Which module owns what, one line each, is in [docs/module-map.md](docs/module-map.md). Open it
before adding a helper: what you need may already have a home.

**The bar for putting something here is that two packages would otherwise disagree** — on what a fee
is, what a trading day is, what "net" means. A helper only one package uses belongs in that package.

## The `advised:<experiment>` mechanism

`cherrypick.core.advice` is the contract behind the AI advisor's one loop back into the trading
modules. `packages/advisor` reads a module's facts up to four times a trading day and, with a
validated proposal, writes a **bounded, expiring paper-advice artifact** naming one or more
experiments. Each consuming module opens a synthetic **`advised:<experiment name>` book** — a full
paper position stream planned from the same entry as `control`, running BESIDE it under the proposed
parameters. The advisor can influence a paper result; it never touches a control book, an order, or
the live path. Both the orchestrator and the module loop validate through this same code.

- **Off by default, twice over.** The suite must schedule the advisor (`scripts/advisor_checkpoint.py`)
  and the module must declare `advice.enabled` plus its own `advice.bounds` — closed ranges the
  proposal is validated against. A bound over a parameter the module doesn't read, or a range that
  can't move the book from control, is a spent experiment slot, not a safe default. A rule may name
  `arms`, the base arms the param acts on at all; the validator refuses it on any other base (both
  sides, from the artifact entry's `base`). Absent means every base.
- **Every module's `advice` block is its own**, never inherited from another module's bounds.
- **One book per experiment.** The artifact and decision carry an `experiments` list; each validated
  entry opens its own slug-safe `advised:<experiment name>` book (`advised_tag`, `slug`). An artifact
  or decision from before 2026-09-17 reads as one legacy `advised:<base>` entry, and modules still
  read that tag by name for history.
- **`advised_books(decision)`** is what every consumer opens one book per; **`stamp_for(book,
  decision)`** is the one rule that puts `experiment_id` on an advised row and never on control's
  (`experiment_for` resolves it per book; older artifacts fall back to parsing the advisor stamp).
- **`session_decision` is the read-once rule, and a baseline decision is never persisted** — a process
  reaching the day with an advice-less config must not fix that day for the loop that runs after it
  (meic and earnings each once lost their most informative session that way).

A module's own experiment names, `advice.bounds` and book naming live in that module's CLAUDE.md.

## Commands

```bash
pip install -e ".[dev]"
ruff check .
pytest
```

## Guardrails

`auth` and `broker` are where account numbers and credentials flow, and every package imports this
one, so a slip here surfaces everywhere: mask account numbers to `****1234` in logs, docs and commit
messages without exception.
