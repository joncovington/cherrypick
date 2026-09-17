"""The loop side of the agentic layer for earnings — advised twins of the strat_test books.

An out-of-band advisor proposes management parameters; this re-validates them with the SAME core
code the producer used (`cherrypick.core.advice`) against this module's own `advice.bounds`
manifest, and the harness opens a **twin** position beside each admitted strategy's ordinary one.
Absent, stale, expired or invalid advice all mean baseline: no twins, and nothing else changes.

Why a twin rather than an overlay on the real book: the comparison has to be paired. The twin gets
byte-identical fill economics — same legs, same credit, same quantity, same modeled costs — so the
only thing separating `advised:<experiment name>:<strategy>` from `strat_test:<strategy>` is the
management params, which is the variable under test. Anything else and the difference in P&L is
unattributable.

One twin PER EXPERIMENT per strategy (2026-09-17). The day's artifact carries one entry per
concurrent experiment, and every entry whose params touch a strategy opens its own twin of that
strategy's entry, tagged `advised:<experiment name>:<strategy>` (`cherrypick.core.advice.advised_tag`),
so two experiments on iron_condor run side by side as two twins of the same control rather than one
queueing behind the other. A decision recorded before that date carries no experiment name and
resolves to the legacy `advised:strat_test:<strategy>` tag its rows were always written under.

Why the params are frozen ON THE ROW: exit thresholds are read from config at *decision* time, not
from the trade. A read-once overlay held in memory would govern entries today and silently stop
governing exits tomorrow, leaving an open position managed by rules nobody chose. Stamped on the
row, `management.effective_config` restates them at every later tick, so exit continuity is free:
advice stops, no new twins open, and the twins already on the book keep being managed and closed
under their own terms.

Bounds use **dotted** param names, `"<strategy>.<param>"` — `cherrypick.core.advice` treats a param
name as opaque, so the convention needs no contract change, and this module splits on the first dot.
v1 bounds are management/exit params only. Entry-side screens and sizing change *which* trades open,
which a twin cannot express: propose those as `creative` and let a human decide.
"""

from __future__ import annotations

import json
from typing import Any

from cherrypick.core import advice as _core_advice
from cherrypick.core import home as _core_home

from cherrypick.earnings import paths as _paths

ADVISED_PREFIX = "advised:"


def decision_path() -> str:
    return str(_paths.data_path("advice_active.json"))


def decision(config: dict, session: str, *, persist: bool = True) -> dict[str, Any]:
    """Today's advice decision, derived ONCE per session and replayed thereafter.

    Read-once across processes: the entry scan records what it decided, and anything later replays
    that record, so advice cannot start, stop or change mid-session however late an artifact lands
    or however the config is flipped.

    The mechanics live in `cherrypick.core.advice.session_decision`, which earnings, meic and five
    other modules had each written out separately. Folding this copy in was not tidying: the
    2026-08-25 fix — a baseline decision is never made sticky — landed in core, and earnings was one
    of the two modules that lost a session to the bug it fixes. Earnings broke a thirteen-session
    drought and opened four iron_condors that day, all at the control target, because an 03:03 entry
    pass had already recorded `advice_disabled` against a live, valid artifact.

    `base_key=None`: earnings names no base book. Its advice is keyed by strategy
    (`iron_condor.profit_target_pct`) and each strategy carries its own, so there is nothing for a
    single base name to mean here. `persist=False` is for a caller replaying a past session — the
    harness runs against arbitrary dates, and a replay must not fix the live day's decision.
    """
    return _core_advice.session_decision(
        _core_home.state_dir(),
        "earnings",
        session,
        config,
        decision_path(),
        base_key=None,
        persist=persist,
    )


def params_for(decided: dict, strategy: str) -> dict[str, Any]:
    """The admitted params that belong to one strategy, with the dotted prefix stripped.

    `{"iron_fly.profit_target_pct": 0.3}` for `iron_fly` becomes `{"profit_target_pct": 0.3}` —
    strategy-local names, because that is what a strategy's own config block holds and what
    `management.effective_config` overlays onto it.

    `decided` is one experiment entry (`{"params": ...}` from `twins_for`), or the whole session
    decision, whose top-level `params` mirror its FIRST experiment only -- the pre-2026-09-17 shape,
    still honoured for a legacy decision file. New code goes through `twins_for`.
    """
    out: dict[str, Any] = {}
    for dotted, value in (decided.get("params") or {}).items():
        prefix, _, name = str(dotted).partition(".")
        if prefix == strategy and name:
            out[name] = value
    return out


def twins_for(decided: dict, strategy: str) -> list[dict[str, Any]]:
    """Every experiment entry whose admitted params touch `strategy` -- one twin each -- as
    `{"name", "experiment_id", "params"}` with the params strategy-local. An entry that names no
    param of this strategy opens nothing for it (its twin belongs to another strategy's entry).
    Empty for baseline. A legacy decision yields at most one entry with `name` None."""
    out = []
    for entry in _core_advice.advised_books(decided):
        params = params_for(entry, strategy)
        if not params:
            continue
        name = entry.get("name")
        if not name and _core_advice.is_advised(entry.get("tag")):
            # An entry named only by its tag: the name is what follows the prefix, unless the tag
            # is the legacy `advised:<base>` a pre-09-17 decision resolves to.
            suffix = str(entry["tag"])[len(ADVISED_PREFIX) :]
            name = suffix if suffix != str(entry.get("base") or "") else None
        out.append({"name": name, "experiment_id": entry.get("experiment_id"), "params": params})
    return out


def advised_book(book: str, name: str | None = None) -> str:
    """The twin's profile tag beside `strat_test:<strategy>`: `advised:<experiment name>:<strategy>`
    (2026-09-17, `cherrypick.core.advice.advised_tag`), or the legacy `advised:strat_test:<strategy>`
    for a decision that carries no experiment name -- what every row before that date was tagged.

    One place, because three surfaces have to agree on it — the row written here, the verdict that
    groups by it, and the console that renders it next to its control.
    """
    if name:
        strategy = book.rpartition(":")[2] or book
        return _core_advice.advised_tag(name, strategy)
    return f"{ADVISED_PREFIX}{book}"


def strategy_of(profile: str | None) -> str | None:
    """The strat_test strategy an advised twin shadows -- the tag's LAST segment in either shape
    (`advised:<name>:<strategy>`, or the legacy `advised:strat_test:<strategy>`) -- else None."""
    if not is_advised(profile):
        return None
    rest = str(profile)[len(ADVISED_PREFIX) :]
    if ":" not in rest:
        return None
    return rest.rpartition(":")[2] or None


def twin_spec(
    save_spec: dict, params: dict[str, Any], experiment_id: str | None = None, name: str | None = None
) -> dict:
    """The advised twin of a saved entry: identical fills, its own book, its params stamped on,
    and the experiment it ran under (2026-09-16) so the ledger can split one twin tag back into
    the experiments that used it in turn.

    `order_id` is prefixed rather than regenerated so the pair is obvious in the ledger and a twin
    can never collide with a real order id -- and carries the experiment's slug when there is one,
    because two experiments twinning the same entry must not collide with each other either.
    """
    slug = _core_advice.slug(name) if name else ""
    prefix = f"{ADVISED_PREFIX.rstrip(':')}-{slug}-" if slug else f"{ADVISED_PREFIX.rstrip(':')}-"
    return {
        **save_spec,
        "order_id": f"{prefix}{save_spec['order_id']}",
        "profile": advised_book(save_spec["profile"], name),
        "advice_params": json.dumps(params),
        "experiment_id": experiment_id,
    }


def is_advised(profile: str | None) -> bool:
    return bool(profile) and str(profile).startswith(ADVISED_PREFIX)
