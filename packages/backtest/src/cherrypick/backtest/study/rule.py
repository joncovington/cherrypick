"""A declared pass rule as data, and the verdict block it produces (#1069, with #1077's cut).

A study script declares its rule as a module-level `RULE` literal, committed with the declaration and
before any run:

    RULE = {
        "t_bar": 2.0,                  # the capital and delta bars' one-sided t
        "min_n": 80,                   # the design part's floor; fewer is no verdict
        "holdout_min_n": 30,           # each later part's floor (needed when there are splits)
        "tail": {"per_year": "sample", "resamples": 10_000, "seed": 77},
        "delta": True,                 # False: a neutral structure, the delta bar not applicable
        "splits": ["2021-06-01"],      # the dates the one run is cut at; [] reads it whole
    }

`evaluate(rule, arms)` takes each arm's trades from ONE run over the whole history, each a dict with
its entry `date`, `net`, `bp` (the buying power it held; 1 when `net` is already a return on it),
`days` held, the bill `rate`, and `financed` (its entry delta financed on the same capital) when the
delta bar applies. It cuts them at the splits by entry date: the first part is the design part, each
later part a holdout, read only while every part before it passed (so a holdout is never spent on a
design that failed). Each part is judged on the three bars (`checks`): capital and delta on the part
alone, the tail bar on every trade read so far (the drawdown bootstrap needs more trades than a
holdout usually holds, and a bad year inside a holdout still shows). An arm passes only when every
part read passes.

`per_year` is a whole number of trades a year, or `"sample"` for the part's own (a year's days over
the mean gap between its entries). Holm across arms, when a declaration asks for it, is the study's:
the block keeps each bar's p.

The block is the one shape the queue and the register read: per arm its `n` (trades judged, which
the queue fails at zero), each part's range, `n` and bars, and the arm's `verdict`; and the top-level
`verdict` per arm the studies board reads. `read_rule` parses a script's RULE without importing it.
"""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

from . import checks as ck

KEYS = {"t_bar", "min_n", "holdout_min_n", "tail", "delta", "splits"}
TAIL_KEYS = {"per_year", "resamples", "seed"}
NOT_READ = "not read"


class BadRule(ValueError):
    """A RULE that is not a literal of the declared shape."""


def check_rule(rule) -> dict:
    """The rule, its every key present and of the right kind: a declaration fixes all of them."""
    if not isinstance(rule, dict):
        raise BadRule("RULE must be a dict")
    need = KEYS - ({"holdout_min_n"} if not rule.get("splits") else set())
    missing, extra = need - set(rule), set(rule) - KEYS
    if missing or extra:
        raise BadRule(f"RULE: missing {sorted(missing)}, unknown {sorted(extra)}")
    if not isinstance(rule["t_bar"], (int, float)) or rule["t_bar"] <= 0:
        raise BadRule("RULE: t_bar must be a positive number")
    for k in ("min_n", "holdout_min_n"):
        if k in rule and (not isinstance(rule[k], int) or rule[k] < 1):
            raise BadRule(f"RULE: {k} must be a whole number of trades, 1 or more")
    tail = rule["tail"]
    if not isinstance(tail, dict) or set(tail) != TAIL_KEYS:
        raise BadRule(f"RULE: tail needs exactly {sorted(TAIL_KEYS)}")
    if tail["per_year"] != "sample" and (not isinstance(tail["per_year"], int) or tail["per_year"] < 1):
        raise BadRule("RULE: tail.per_year is a whole number or 'sample'")
    if not isinstance(rule["delta"], bool):
        raise BadRule("RULE: delta is true or false")
    splits = rule["splits"]
    if not isinstance(splits, list) or any(not isinstance(s, str) for s in splits):
        raise BadRule("RULE: splits is a list of YYYY-MM-DD dates")
    try:
        parsed = [date.fromisoformat(s) for s in splits]
    except ValueError as exc:
        raise BadRule(f"RULE: {exc}") from None
    if parsed != sorted(set(parsed)):
        raise BadRule("RULE: splits run oldest first, each once")
    return rule


def read_rule(script: Path) -> dict | None:
    """The script's RULE, checked; None when it declares none. Parsed, never imported."""
    tree = ast.parse(Path(script).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "RULE" for t in node.targets
        ):
            try:
                return check_rule(ast.literal_eval(node.value))
            except ValueError as exc:
                if isinstance(exc, BadRule):
                    raise
                raise BadRule(f"{Path(script).name}: RULE must be a literal") from exc
    return None


def cut(rows: list[dict], splits: list[str]) -> list[list[dict]]:
    """The rows in each part, by entry date: before the first split, between each pair, from the last."""
    parts: list[list[dict]] = [[] for _ in range(len(splits) + 1)]
    for r in sorted(rows, key=lambda r: r["date"]):
        i = sum(1 for s in splits if r["date"] >= s)
        parts[i].append(r)
    return parts


def per_year_of(rows: list[dict]) -> int:
    """A year's days over the mean gap between the part's entries (1 for fewer than two)."""
    if len(rows) < 2:
        return 1
    first, last = date.fromisoformat(rows[0]["date"]), date.fromisoformat(rows[-1]["date"])
    gap = max((last - first).days, 1) / (len(rows) - 1)
    return max(1, round(365.25 / gap))


def judge_part(rule: dict, rows: list[dict], min_n: int, so_far: list[dict] | None = None) -> dict:
    """One part on the three bars. Capital and delta read the part alone; the tail bar reads
    `so_far`, every trade read up to and including this part (the part itself for the design), since
    the drawdown bootstrap needs more trades than a holdout usually holds (`killcriteria`'s floor)."""
    nets = [r["net"] for r in rows]
    tail = rule["tail"]
    capital = ck.capital_check(
        nets, [r.get("bp", 1.0) for r in rows], [r["days"] for r in rows], [r["rate"] for r in rows],
        t_bar=rule["t_bar"], min_n=min_n,
    )  # fmt: skip
    tail_rows = so_far if so_far is not None else rows
    per_year = per_year_of(tail_rows) if tail["per_year"] == "sample" else tail["per_year"]
    tail_out = ck.tail_check(
        [r["net"] for r in tail_rows], per_year=per_year, min_n=min_n, resamples=tail["resamples"],
        seed=tail["seed"],
    )  # fmt: skip
    tail_out["reads"] = "so far" if so_far is not None else "this part"
    delta = (
        ck.delta_check(nets, [r["financed"] for r in rows], t_bar=rule["t_bar"], min_n=min_n)
        if rule["delta"]
        else None
    )
    return ck.verdict(capital, tail_out, delta)


def evaluate(rule: dict, arms: dict[str, list[dict]]) -> dict:
    """The verdict block for every arm of one run, cut at the rule's splits."""
    rule = check_rule(rule)
    splits = rule["splits"]
    bounds = [None, *splits, None]
    out = {}
    for arm, rows in arms.items():
        parts, word, read = [], None, []
        for i, part in enumerate(cut(rows, splits)):
            span = {"from": bounds[i], "before": bounds[i + 1]}
            if word is not None and word != ck.PASS:
                parts.append({**span, "n": len(part), "verdict": NOT_READ})
                continue
            read += part
            floor = rule["min_n"] if i == 0 else rule["holdout_min_n"]
            block = judge_part(rule, part, floor, so_far=list(read) if i > 0 else None)
            parts.append({**span, "n": len(part), **block})
            word = block["verdict"]
        out[arm] = {"n": len(rows), "parts": parts, "verdict": word}
    return {"rule": rule, "arms": out, "verdict": {a: b["verdict"] for a, b in out.items()}}


def zero_trade_arms(block: dict) -> list[str]:
    """The arms a verdict block judged on no trades at all: a study that read nothing."""
    arms = block.get("arms") if isinstance(block, dict) else None
    if not isinstance(arms, dict):
        return []
    return sorted(a for a, b in arms.items() if isinstance(b, dict) and b.get("n") == 0)
