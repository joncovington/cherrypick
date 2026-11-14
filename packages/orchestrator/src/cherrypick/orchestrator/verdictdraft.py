"""Draft a study's Result paragraph and register outcome from its verdict block (#1074).

A study on the kit writes one verdict block (`cherrypick.backtest.study.rule.evaluate`): per arm its
verdict, its parts (the design part, then each holdout, read only after a pass) and each part's
bars. This turns that block into the plan's `- **Result (...)**` paragraph and the register row's
`status` and `outcome`, in words only: which arms passed, which bars failed, how far the holdouts
were read. It writes nothing; a person reads the draft, checks it against the declared rule
(`register-read-declared-rule`), and commits it in a docs PR.

The licence forbids figures from a study in the repo, so the draft is built from words and the
block's verdicts alone, and `figures` refuses any number in it other than the item's own, the
declared split dates and the arms' names.
"""

from __future__ import annotations

import re

PASS, FAIL, NO_VERDICT, NOT_READ = "pass", "fail", "no verdict", "not read"
BAR_WORDS = {
    "capital": "the capital bar (the return on its buying power over the bill)",
    "tail": "the tail bar (a bad year earned back within a year)",
    "delta": "the delta bar (edge beyond its financed delta)",
}
STATUS = {PASS: "passed", FAIL: "failed", NO_VERDICT: "no_verdict"}
NUMBER = re.compile(r"\d[\d.,]*")
ORDINALS = {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth"}


def _arm_words(arm: str, a: dict) -> str:
    parts = a.get("parts") or []
    word = a.get("verdict")
    if word == PASS:
        read = "every part" if len(parts) > 1 else "the whole run"
        return f"`{arm}` passes every bar in {read}"
    if word == NO_VERDICT:
        return f"`{arm}` is no verdict: too few trades for its declared floor"
    for i, p in enumerate(parts):
        if p.get("verdict") == FAIL:
            where = "the design part" if i == 0 else f"the {ORDINALS.get(i, 'later')} holdout"
            failed = [BAR_WORDS.get(b, b) for b in p.get("failed", [])]
            return f"`{arm}` fails {' and '.join(failed) or 'its bars'} in {where}"
    return f"`{arm}` is {word}"


def study_word(block: dict) -> str:
    words = list((block.get("verdict") or {}).values())
    if words and all(w == PASS for w in words):
        return PASS
    if PASS in words:
        return PASS  # one arm passing is a pass for the item, its arms named
    if FAIL in words:
        return FAIL
    return NO_VERDICT


def draft(item: int, block: dict, *, day: str, provisional: bool, results_dir: str) -> dict:
    """The Result paragraph, the register status and outcome, and any figure the check found."""
    arms = block.get("arms") or {}
    word = study_word(block)
    passed = sorted(a for a, b in arms.items() if b.get("verdict") == PASS)
    head = {
        PASS: f"pass, {', '.join(f'`{a}`' for a in passed)}",
        FAIL: "fail, every arm",
        NO_VERDICT: "no verdict",
    }
    tag = f"{day}, provisional" if provisional else day
    lines = [f"- **Result ({tag}): {head[word]}.**"]
    for arm, a in sorted(arms.items()):
        unread = any(p.get("verdict") == NOT_READ for p in a.get("parts", []))
        tail = "; the holdouts after it are not read" if unread else ""
        lines.append(f"  {_arm_words(arm, a)}{tail}.")
    lines.append(f"  Every figure is in the local result (`{results_dir}`).")
    paragraph = "\n".join(lines)
    outcome = "; ".join(_arm_words(arm, a) for arm, a in sorted(arms.items()))
    if provisional:
        outcome = f"provisional: {outcome}"
    allowed = {str(item), *(block.get("rule") or {}).get("splits", []), *arms, *day.split("-"), day}
    return {
        "status": STATUS[word],
        "paragraph": paragraph,
        "outcome": outcome,
        "figures": figures(paragraph + "\n" + outcome, allowed),
    }


def figures(text: str, allowed: set[str]) -> list[str]:
    """Numbers in `text` that are not allowed: the draft must carry no study figure."""
    cleaned = text
    for a in sorted(allowed, key=len, reverse=True):
        cleaned = cleaned.replace(a, " ")
    return NUMBER.findall(cleaned)
