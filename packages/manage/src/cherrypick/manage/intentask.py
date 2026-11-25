"""What a trade is asked about its intent (`trade["intent_ask"]`).

An intent is the operator's word on what a trade is for, and manage asks for it only where the legs cannot
say: a naked short put is a wheel or not (`wheel`, `no_wheel`), a call diagonal is a PMCC or not (`pmcc`,
`diagonal`). Anything else has no question. The block is everything a surface needs to put the question to
a person and write the answer, so none of them works out a shape rule, an option list or a config key:

- `kind`: "wheel" or "pmcc"; `options`: the intents it may be answered with.
- `asked`: no operator intent applies yet; `current`: the one that does (a trade's own, or a name's that can
  describe it), else None. A free label on a put (`hedge`, `income`) is an answer, not an option.
- `verdicts`: for a put, the verdict the trade would get under each option, so a person sees what each
  answer changes. Empty for a diagonal.
- `suggested`, `evidence`, `note`: manage's suggestion, why, and what it noticed about the name's own intent.
  Set only while the trade is asked, and never applied.
- `key`, `scope`: where an answer is written. A put's own `trades.<ID>.intent` (scope "trade"), or its name's
  `positions.<SYM>.intent` while the ID is still provisional (scope "name"); a diagonal's
  `trades.<long contract>.intent` (scope "contract"), which is how the PMCC view reads it.

Pure: values in, a dict out. The verdicts are judged by the caller, which holds the evaluation.
"""

from __future__ import annotations

from typing import Any

from cherrypick.manage import rules as _rules

WHEEL_OPTIONS = ("wheel", "no_wheel")
PMCC_OPTIONS = ("pmcc", "diagonal")


def answer_key(trade_id: str | None, underlying: str, stable: bool) -> tuple[str, str]:
    """Where a put's answer is written, and the scope that says so: its own trade entry once its ID is
    stable (an entry under a provisional ID would not survive the next run), else its name's."""
    if stable:
        return f"trades.{trade_id}.intent", "trade"
    return f"positions.{underlying}.intent", "name"


def wheel_reading(
    cycles: list[dict[str, Any]], underlying: str, legs: list[dict[str, Any]], named: str | None
) -> tuple[str | None, list[str], str | None]:
    """A put's suggestion, its evidence and a note. `suggested` is "wheel" when a wheel cycle on the name
    reconciles to the account (a completed one, or one running), with each such cycle as evidence; `note`
    says the name's own intent (`named`) is for another trade on it, which is why this put is asked about."""
    done = sum(1 for c in cycles if c.get("status") == "complete" and c.get("reconciled"))
    running = any(c.get("status") != "complete" and c.get("reconciled") for c in cycles)
    evidence = []
    if done:
        evidence.append(f"{done} completed wheel cycle{'s' if done != 1 else ''} on {underlying}")
    if running:
        evidence.append(f"an open wheel cycle on {underlying} reconciles to the account's legs")
    note = None
    if named and not _rules.intent_fits(named, legs):
        note = f"the name's intent '{named}' is for another trade on {underlying}, not this put"
    return ("wheel" if done or running else None), evidence, note


def wheel_ask(
    *,
    intent: str | None,
    legs: list[dict[str, Any]],
    underlying: str,
    verdicts: dict[str, dict[str, Any]],
    cycles: list[dict[str, Any]],
    trade_id: str | None,
    stable: bool,
    named: str | None,
) -> dict[str, Any]:
    """The question for a naked short put: `intent` is its current one (None while it has none), `verdicts`
    the verdict under each of WHEEL_OPTIONS."""
    asked = intent is None
    suggested, evidence, note = wheel_reading(cycles, underlying, legs, named)
    key, scope = answer_key(trade_id, underlying, stable)
    return {
        "kind": "wheel",
        "asked": asked,
        "current": intent,
        "options": list(WHEEL_OPTIONS),
        "verdicts": verdicts,
        "suggested": suggested if asked else None,
        "evidence": evidence if asked else [],
        "note": note if asked else None,
        "key": key,
        "scope": scope,
    }


def diagonal_asks(views: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The one question a trade carries when it holds more than one PMCC view: the first still open, else
    the first there is, so a diagonal that needs an answer is never hidden behind one that has it."""
    asks = [ask for ask in map(diagonal_ask, views) if ask]
    return next((ask for ask in asks if ask["asked"]), asks[0] if asks else None)


def diagonal_ask(view: dict[str, Any]) -> dict[str, Any] | None:
    """The question for a call diagonal, from the PMCC view's entry for it. A calendar is not asked. The
    suggestion is the view's own reading (entered at a delta over or under the floor); where the delta at
    entry cannot be solved there is none, and the evidence says why. Only the operator's `pmcc` or
    `diagonal` answers it: a delta decided it, and a name's `wheel` is for a put. A view with no long
    contract to write an answer under is not asked."""
    if view.get("structure") != "diagonal" or not view.get("intent_key"):
        return None
    intent = view.get("intent")
    current = intent if intent in PMCC_OPTIONS else None
    asked = current is None
    reading = view.get("recognition")
    return {
        "kind": "pmcc",
        "asked": asked,
        "current": current,
        "options": list(PMCC_OPTIONS),
        "verdicts": {},
        "suggested": reading if asked and reading in PMCC_OPTIONS else None,
        "evidence": [view["recognized_by"]] if asked and view.get("recognized_by") else [],
        "note": None,
        "key": f"trades.{view['intent_key']}.intent",
        "scope": "contract",
    }
