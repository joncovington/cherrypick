"""`cherrypick study-report` — one study's result files read as a report (docs/studies-board-plan.md).

The board (`hypotheses.board`) shows each result file's verdict word. This reads the figures behind it,
so the console's Report tab can chart every arm and say in plain words what they show. Everything
here is a pure function over parsed result files; the CLI reads them, and nothing is written. The
files are licensed or derived from licensed data, so the console shows this only behind the backtest
gate, and none of it is ever committed: tests use invented figures.

Studies write their files in several shapes. `arms` reads the ones in use: a table of arms (a
top-level dict whose values are dicts), each arm's own figures under `own`, `net`, `paired`, `diff`,
`boot` or itself, split by `design`/`holdout` where the arm holds both. A shape it does not know
yields no rows, never a guess; the study's own notes and files remain the record.

The reading states what the figures say and nothing more. The register's outcome is the decision:
a script's per-arm `verdict` can apply a check the declaration does not (item 43's holdout script
fails w20 on net over buying power, which the declared holdout rule does not test), so where the
register has an outcome the reading leads with it and labels the file words as the files'.
"""

from __future__ import annotations

import re

MEAN_KEYS = ("mean_net", "mean_bp", "mean", "net_per_trade", "mean_pct", "mean_return")
STAT_PATHS = ("own", "net", "paired", "diff", "boot", "")
#: Top-level blocks that are never a table of arms.
NOT_ARMS = frozenset({
    "coverage", "counts", "declared", "guards", "data", "paper", "family", "drops", "notes", "verdict",
    "checks", "summary", "grid", "store", "span", "borrowed_from_item43", "macro_calendar",
    "declared_limits", "basis", "tables", "symbols", "flip_divergence", "chains", "reports",
    "broker_span", "samplings", "plan", "requests",
})  # fmt: skip
#: Inside an arm, blocks that hold breakdowns of its figures, never the figures themselves.
SKIP_DEEP = (
    "halves",
    "by_year",
    "by_quarter",
    "by_month",
    "cost_stress",
    "by_name",
    "checks",
    "stress",
    "rows",
)
#: The baseline comparison an arm is judged on, most specific first.
PAIRED_PREFER = ("vs_base", "vs_live", "vs_control", "vs_w10", "vs_live-1400", "pair", "paired", "diff")
NOT_BASELINE = ("vs_placebo", "vs_ungated_twin")
MAX_ROWS = 120
T_BAR = 2.0
WINDOW_ORDER = ("design", "holdout")

#: The measures an arm can be compared on, in the order the Report tab offers them. `t` names the
#: field whose t-statistic goes with the measure, or None.
MEASURES = (
    {"key": "paired", "label": "Gain over baseline", "t": "paired",
     "note": "The arm minus its baseline on the same sessions, with days it didn't trade counted at zero. "
             "Most pass rules test this, and it is the fairest comparison between arms that trade at "
             "different rates."},
    {"key": "paired_both", "label": "Gain over baseline, both entered", "t": "paired_both",
     "note": "The same, but only on sessions where both the arm and its baseline traded: how the arm's "
             "trades compare, setting aside how often it trades."},
    {"key": "mean", "label": "Net per session", "t": "mean",
     "note": "The arm's own average in the study's unit (see Measure). For MEIC studies it is net per "
             "session with days off counted at zero."},
    {"key": "net_per_trade", "label": "Net per trade", "t": None,
     "note": "The average per trade taken. It flatters an arm that skips its weaker trades, so read it "
             "beside net per session."},
    {"key": "per_risk", "label": "Return on buying power", "t": None,
     "note": "Net over the buying power the arm ties up, in %. The dashed line is the baseline. Making "
             "more dollars by risking more money is not an edge."},
    {"key": "entry_rate", "label": "Entries per session", "t": None,
     "note": "How often the arm traded: 1.0 is every session, 0.5 half of them; above 1, several "
             "entries a session."},
)  # fmt: skip

#: Plain-words glossary of the gates studies use, each shown on a study whose text or arms match.
GATES = (
    {"key": "meic-live",
     "name": "MEIC's live gates (credit floor and call-delta ceiling)",
     "match": r"credit floor|call-delta ceiling|call ceiling|live-1400|live's gates|\bg(?:15|20)\b",
     "text": "The two checks the live 14:00 condor runs before it opens. **Credit floor:** the condor must "
             "collect at least 15% of its wing width by default (about $1.50 on a 10-wide, $3.00 on a "
             "20-wide), a little less when implied volatility is very low. **Call-delta ceiling:** the "
             "short call can be at most 0.20 delta by default (0.19 late in the day or after a volatile "
             "open). If either check fails, it doesn't trade that tick. Because the floor scales with "
             "width, a wider condor has to bring in more credit and is turned away more often. "
             "packages/meic/GATES.md is the full catalogue."},
    {"key": "stabilization",
     "name": "Stabilization gate (wait for the market to calm down)",
     "match": r"stabili[sz]ation|-multi\b|perday|multi-3b",
     "text": "Waits to enter until SPX has gone quiet: the last two completed five-minute bars must have "
             "a combined range under an adaptive threshold, and the 5-bar average true range must be at "
             "or below its 20-bar average. Until both hold, the rule waits and re-checks every tick. "
             "**multi** measures volatility across the last five sessions' bars, **perday** today's bars "
             "only, **3b** needs three flat bars instead of two. A **-skip** arm is a placebo that skips "
             "the same share of entries at random, to tell better timing from trading less."},
    {"key": "vix9d",
     "name": "VIX9D gate",
     "match": r"VIX9D below VIX|/gated|/ungated",
     "text": "Trades only on days when 9-day VIX is below 30-day VIX at the open: near-term fear lower "
             "than the month's, the volatility curve in its normal upward slope. The idea is to skip "
             "stressed days, when the premium is least likely to be kept. An /ungated arm trades every day."},
    {"key": "earnings-g",
     "name": "Earnings gates G1 and G2",
     "match": r"\bG1\b|\bG2\b|\+g1|\+g2",
     "text": "**G1, rich premium:** trade only when the report's expected move is at least 1.2 times the "
             "stock's average move over its last eight reports. **G2, cheap to trade:** trade only when "
             "the round-trip cost of crossing the spread is at most 25% of the credit."},
    {"key": "vix-curve",
     "name": "VIX-curve stress flags",
     "match": r":inverted|:hump|VIX curve is stressed",
     "text": "Flags rather than gates: they mark days on which the rule trades differently instead of "
             "skipping the day. **inverted:** VIX at or above 3-month VIX. **hump:** 9-day VIX at or above "
             "VIX while VIX is still below 3-month VIX, a short-term fear spike."},
)  # fmt: skip


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _stat_paths(d: dict) -> dict | None:
    for p in STAT_PATHS:
        s = d.get(p) if p else d
        if not isinstance(s, dict):
            continue
        if p == "" and isinstance(s.get("boot"), dict):
            s = {**s["boot"], **{k: v for k, v in s.items() if _num(v)}}
        mean = next((s[k] for k in MEAN_KEYS if _num(s.get(k))), None)
        if mean is None:
            continue
        n = next((s[k] for k in ("trades", "n", "cycles") if _num(s.get(k))), None)
        npt = s.get("net_per_trade")
        return {
            "mean": mean,
            "t": s["t"] if _num(s.get("t")) else None,
            "n": n,
            "p": s["p"] if _num(s.get("p")) else None,
            # `n` beside `trades` is the session count (a session with no entry counted at zero).
            "sessions": s["n"] if _num(s.get("trades")) and _num(s.get("n")) else None,
            "net_per_trade": npt if _num(npt) and npt != mean else None,
            "win_rate": s["win_rate"] if _num(s.get("win_rate")) else None,
        }
    return None


def stat_of(d, depth: int = 0) -> dict | None:
    """An arm's own figures: the first block along STAT_PATHS holding a mean, then a shallow search."""
    if not isinstance(d, dict):
        return None
    s = _stat_paths(d)
    if s or depth >= 2:
        return s
    for k, v in d.items():
        if k in SKIP_DEEP or k.startswith("by_") or not isinstance(v, dict):
            continue
        s = stat_of(v, depth + 1)
        if s:
            return s
    return None


def _small(x) -> dict | None:
    if isinstance(x, dict) and _num(x.get("mean")):
        return {k: (x[k] if _num(x.get(k)) else None) for k in ("mean", "t", "n", "p")}
    return None


def paired_of(d) -> dict:
    """The arm's paired difference against its baseline, and over sessions where both entered."""
    if not isinstance(d, dict):
        return {}
    keys = [k for k in PAIRED_PREFER if k in d] + sorted(
        k
        for k in d
        if k.startswith("vs_") and not k.endswith("_both_entered") and k not in PAIRED_PREFER + NOT_BASELINE
    )
    for k in keys:
        s = _small(d.get(k))
        if not s:
            continue
        out = {"paired": s, "paired_vs": k[3:] if k.startswith("vs_") else "its matched comparison"}
        both = _small(d.get(k + "_both_entered"))
        if both:
            out["paired_both"] = both
        return out
    return {}


def per_risk_of(d) -> dict:
    pr = d.get("per_risk") if isinstance(d, dict) else None
    if isinstance(pr, dict) and _num(pr.get("arm")):
        base = next((v for k, v in pr.items() if k != "arm" and _num(v)), None)
        return {"per_risk": pr["arm"], "per_risk_base": base}
    return {}


def _enrich(row: dict, d: dict) -> dict:
    for src in (d, d.get("own")):
        if isinstance(src, dict):
            for k, v in {**paired_of(src), **per_risk_of(src)}.items():
                row.setdefault(k, v)
    if row.get("sessions") and row.get("n"):
        row["entry_rate"] = row["n"] / row["sessions"]
        if row.get("net_per_trade") is None:
            # Net per session counts sessions with no entry at zero, so the total is mean x sessions.
            row["net_per_trade"] = row["mean"] * row["sessions"] / row["n"]
            row["net_per_trade_derived"] = True
    return row


#: Working files in a study folder that never hold a verdict (requests, coverage counts, ledgers).
_WORKING = ("request", "chain", "trades", "coverage", "plan", "listing", "pilot", "risk", "cycles",
            "expirations", "ledger", "runcard", "replay-check", "version")  # fmt: skip


def is_window_file(name: str) -> bool:
    """A study folder's design/holdout/results file, as opposed to one of its working files."""
    low = name.lower()
    return (
        low.endswith(".json")
        and not any(k in low for k in _WORKING)
        and any(k in low for k in ("design", "holdout", "results", "study"))
    )


def window_of(data: dict, name: str) -> str | None:
    w = data.get("window")
    if w in WINDOW_ORDER:
        return w
    low = name.lower()
    for w in WINDOW_ORDER:
        if w in low:
            return w
    span = data.get("span")
    if isinstance(span, list) and len(span) == 2:
        return f"{span[0]} to {span[1]}"
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", name)
    return f"{dates[0]} to {dates[1]}" if len(dates) == 2 else None


def _word(x) -> str | None:
    if isinstance(x, str):
        return x.strip().lower().replace("_", " ")
    if isinstance(x, dict) and isinstance(x.get("verdict"), str):
        return _word(x["verdict"])
    return None


def verdict_rows(data: dict, name: str) -> list[dict]:
    """Per-arm verdicts from a file's top-level verdict table, with the study's reason where given."""
    v = data.get("verdict")
    out = []
    if isinstance(v, dict):
        for arm, x in v.items():
            word = _word(x)
            if arm in NOT_ARM_WORDS or word is None:
                continue
            if arm == "verdict":
                arm = None  # a single word written as {"verdict": ..., "why": ...}
                why = v.get("why")
                out.append(
                    {"arm": None, "verdict": word, "why": why if isinstance(why, str) else None, "file": name}
                )
                continue
            why = x.get("why") or x.get("reason") if isinstance(x, dict) else None
            out.append(
                {"arm": arm, "verdict": word, "why": why if isinstance(why, str) else None, "file": name}
            )
    notes = data.get("notes")
    if isinstance(notes, dict):
        for r in out:
            if not r["why"] and isinstance(notes.get(r["arm"]), str):
                r["why"] = notes[r["arm"]]
    return out


#: Keys a verdict table carries beside its arms: a reason or a list, never an arm's word.
NOT_ARM_WORDS = ("why", "confirmed", "reason")
#: Files whose verdict word is a data check, not a study's answer.
CHECK_FILES = ("validation", "coverage", "pilot", "replay-check")


def final_words(entries) -> dict[str, str]:
    """Each arm's one word from `(arm, window, word)` entries, in the order the declarations read
    them: a design fail stands (a holdout only confirms a design pass), else the holdout's word, else
    the design's, else any other window's. A single-word file is the arm `None`."""
    by_arm: dict = {}
    for arm, window, word in entries:
        if word:
            by_arm.setdefault(arm, {})[window or "whole"] = word
    out = {}
    for arm, w in by_arm.items():
        if _cls(w.get("design")) == "fail":
            out[arm] = w["design"]
        else:
            out[arm] = w.get("holdout") or w.get("design") or next(iter(w.values()))
    return out


def _file_window(name: str, window) -> str | None:
    if window in WINDOW_ORDER:
        return window
    low = name.lower()
    return next((w for w in WINDOW_ORDER if w in low), None)


def _rounded(o):
    if isinstance(o, float):
        return round(o, 6) if abs(o) < 1 else round(o, 4)
    if isinstance(o, dict):
        return {k: _rounded(v) for k, v in o.items()}
    return o


def arms(data: dict, name: str) -> list[dict]:
    """Rows of figures from one result file: one per arm and window."""
    if not isinstance(data, dict):
        return []
    rows: list[dict] = []
    fw = window_of(data, name)
    top = data.get("verdict") if isinstance(data.get("verdict"), dict) else {}
    for group, box in data.items():
        if group in NOT_ARMS or not isinstance(box, dict) or not box:
            continue
        if not all(isinstance(x, dict) for x in box.values()):
            continue
        for arm, d in box.items():
            v = _word(d.get("verdict")) or _word(d.get("decision")) or _word(top.get(arm))
            split = [w for w in WINDOW_ORDER if isinstance(d.get(w), dict)]
            base = {"group": group, "arm": arm, "file": name}
            if split:
                for w in split:
                    s = stat_of(d[w])
                    if s:
                        rows.append(
                            _enrich({**s, **base, "window": w, "verdict": v if w == "design" else None}, d[w])
                        )
            else:
                s = stat_of(d)
                if s:
                    rows.append(_enrich({**s, **base, "window": fw or "whole", "verdict": v}, d))
    return [_rounded(r) for r in rows[:MAX_ROWS]]


def is_comparison(row: dict) -> bool:
    return "_vs_" in row["arm"] or re.search(r"\bvs\b", row["arm"]) is not None


def _fmt(x) -> str:
    if x is None:
        return "–"
    a = abs(x)
    return f"{x:.0f}" if a >= 100 else f"{x:.2f}" if a >= 1 else f"{x:.2g}"


def _cls(word: str | None) -> str:
    w = (word or "").lower()
    if re.match(r"pass|confirmed", w):
        return "pass"
    if "fail" in w:
        return "fail"
    return "no_verdict" if w else ""


def _order(windows) -> list[str]:
    return sorted(set(windows), key=lambda w: (WINDOW_ORDER.index(w) if w in WINDOW_ORDER else 2, w))


def gates_for(text: str) -> list[dict]:
    return [
        {"key": g["key"], "name": g["name"], "text": g["text"]}
        for g in GATES
        if re.search(g["match"], text, re.I)
    ]


def reading(study: dict, rows: list[dict], verdicts: list[dict], failed_steps: list[str]) -> list[dict]:
    """What the figures say, as `[{kind, text}]` paragraphs (`**bold**` marks names). Every sentence is
    worked out from the rows; none judges anything the study did not."""
    out: list[dict] = []
    say = lambda kind, text: out.append({"kind": kind, "text": text})  # noqa: E731
    own = [r for r in rows if not is_comparison(r)]
    if study.get("outcome"):
        say("decision", f"**Decision (the register):** {study['outcome']}")
    entries = [(v["arm"], _file_window(v.get("file") or "", None), v["verdict"]) for v in verdicts]
    if not entries:
        entries = [(r["arm"], r["window"], r["verdict"]) for r in own if r.get("verdict")]
    words = final_words(entries)
    if words:
        p = [a for a, w in words.items() if _cls(w) == "pass"]
        f = sum(_cls(w) == "fail" for w in words.values())
        n = len(words) - len(p) - f
        lead = "As the study's files wrote them" if study.get("outcome") else "Judged"
        text = f"**{lead}:** {len(words)} rule{'s' if len(words) != 1 else ''}, {len(p)} passed, {f} failed"
        text += f", {n} couldn't be judged." if n else "."
        if p:
            text += " Passing: " + ", ".join(f"**{a or 'the study as a whole'}**" for a in p) + "."
        if study.get("outcome"):
            text += (
                " Where these differ from the decision above, the decision stands: a script can apply a"
                " check its declaration does not."
            )
        say("tally", text)
    windows = _order(r["window"] for r in own)
    for w in windows:
        rs = [r for r in own if r["window"] == w]
        up = [r for r in rs if r["mean"] > 0]
        best, worst = max(rs, key=lambda r: r["mean"]), min(rs, key=lambda r: r["mean"])
        label = {"design": "Design window", "holdout": "Holdout", "whole": "Whole sample"}.get(w, w)
        text = (
            f"**{label}:** {len(up)} of {len(rs)} averaged above zero. Best was **{best['arm']}** at "
            f"{_fmt(best['mean'])}"
            + (f" (t {_fmt(best['t'])})" if best["t"] is not None else "")
            + f"; worst **{worst['arm']}** at {_fmt(worst['mean'])}."
        )
        if any(r["t"] is not None for r in rs):
            strong = [r for r in rs if r["t"] is not None and r["t"] > T_BAR]
            text += (
                f" {len(strong)} cleared t over 2, the usual bar before any correction for testing"
                " many rules."
                if strong
                else " None cleared t over 2, so none is distinguishable from noise."
            )
        say("window", text)
    if "design" in windows and "holdout" in windows:
        d = {r["arm"]: r["mean"] for r in own if r["window"] == "design"}
        h = {r["arm"]: r["mean"] for r in own if r["window"] == "holdout"}
        both = [a for a in d if a in h]
        held = [a for a in both if d[a] > 0 and h[a] > 0]
        flipped = [a for a in both if d[a] > 0 and h[a] <= 0]
        if both:
            text = "**Did it hold up?** " + (
                ", ".join(held) + " stayed above zero in both windows."
                if held
                else "No arm was above zero in both windows."
            )
            if flipped:
                text += (
                    f" {', '.join(flipped)} made money in design but not in the holdout, the classic sign"
                    " of a result that was partly luck."
                )
            say("held", text)
    for w in windows:
        rs = [r for r in own if r["window"] == w and r.get("paired")]
        if rs:
            vs = " / ".join(sorted({r["paired_vs"] for r in rs}))
            up = [r for r in rs if r["paired"]["mean"] > 0]
            strong = [r for r in rs if (r["paired"]["t"] or 0) > T_BAR]
            text = (
                f"**Against the baseline ({w}, same sessions, vs {vs}):** {len(up)} of {len(rs)} beat it"
                " on average"
            )
            if any(r["paired"]["t"] is not None for r in rs):
                text += ", " + (
                    ", ".join(
                        f"**{r['arm']}** (+{_fmt(r['paired']['mean'])}, t {_fmt(r['paired']['t'])})"
                        for r in strong
                    )
                    + " by enough to clear t over 2"
                    if strong
                    else "none by enough to clear t over 2"
                )
            say("baseline", text + ". This is the like-for-like test most studies pass or fail on.")
            for r in rs:
                pb = r.get("paired_both")
                if pb and (pb["t"] or 0) > T_BAR and not (r["paired"]["t"] or 0) > T_BAR:
                    rate = (
                        f"It entered on only {r['entry_rate'] * 100:.0f}% of sessions, though, and"
                        if r.get("entry_rate") is not None
                        else "But"
                    )
                    say(
                        "trades_less",
                        f"**{r['arm']} trades better but trades less ({w}).** On the sessions where both"
                        f" it and the baseline entered, it beat the baseline by {_fmt(pb['mean'])} a"
                        f" session (t {_fmt(pb['t'])}, {pb['n']} sessions). {rate} counting the days it"
                        f" sat out as zero, the gain over all sessions is {_fmt(r['paired']['mean'])}"
                        f" (t {_fmt(r['paired']['t'])}). The trades it takes are better, but there aren't"
                        " enough of them to win overall.",
                    )
        rated = [r for r in own if r["window"] == w and r.get("entry_rate") is not None]
        if len(rated) >= 2:
            lo, hi = min(rated, key=lambda r: r["entry_rate"]), max(rated, key=lambda r: r["entry_rate"])
            if hi["entry_rate"] >= lo["entry_rate"] * 1.2:
                say(
                    "entries",
                    f"**Entry counts differ ({w}):** from {_rate(lo)} (**{lo['arm']}**) to {_rate(hi)}"
                    f" (**{hi['arm']}**). Net per session counts days off as zero, so an arm isn't"
                    " flattered by sitting out bad days; net per trade shows the trades on their own.",
                )
    risk = [r for r in own if r.get("per_risk") is not None and r.get("per_risk_base") is not None]
    if risk:
        below = [r for r in risk if r["per_risk"] < r["per_risk_base"]]
        text = (
            f"**Return on buying power:** {len(risk) - len(below)} of {len(risk)} arm-windows earned at"
            " least the baseline's per dollar of buying power"
        )
        if below:
            text += (
                "; "
                + ", ".join(
                    f"**{r['arm']}** ({r['window']}: {r['per_risk'] * 100:.2f}% against"
                    f" {r['per_risk_base'] * 100:.2f}%)"
                    for r in below
                )
                + " earned less. More dollars from tying up more money is not an edge."
            )
        say("risk", text + ("" if below else "."))
    whys = [v for v in verdicts if v.get("why")]
    if whys:
        say(
            "why",
            "**Why rules missed:** "
            + "; ".join(f"**{v['arm'] or 'the study'}**: {v['why']}" for v in whys[:8])
            + ".",
        )
    if failed_steps:
        say(
            "failed_step",
            f"**A step failed** and hasn't been rerun: {', '.join(failed_steps)}. Read its queue log"
            " before trusting any reading here.",
        )
    if not own and not words:
        say(
            "empty",
            "This study wrote no per-arm figures or verdicts that can be read automatically. Its notes"
            " and files carry the result.",
        )
    if study.get("provisional"):
        say(
            "provisional",
            "**Provisional:** the replay this study runs on isn't validated yet, so read it as a strong"
            " hint rather than a decision.",
        )
    return out


def _rate(r: dict) -> str:
    if r["entry_rate"] > 1.05:
        return f"{r['entry_rate']:.1f} entries a session"
    return f"{r['entry_rate'] * 100:.0f}% of sessions"


def failed_steps(tasks: list[dict]) -> list[str]:
    """Queue steps whose latest run failed with no later done run of the same step."""
    out = []
    for t in tasks:
        if t.get("status") != "failed":
            continue
        later = any(
            u.get("step") == t.get("step")
            and u.get("status") == "done"
            and (u.get("finished_at") or "") > (t.get("finished_at") or "")
            for u in tasks
        )
        if not later and t.get("step") not in out:
            out.append(t.get("step"))
    return out


def report(row: dict, files: list[dict]) -> dict:
    """One study's report. `row` is its board row; `files` its result files as `{name, data}`, the
    register's and any design/holdout file in its folder the register does not list."""
    rows, verdicts = [], []
    for f in files:
        rows += arms(f["data"], f["name"])
        if isinstance(f["data"], dict) and not any(k in f["name"].lower() for k in CHECK_FILES):
            verdicts += verdict_rows(f["data"], f["name"])
    provisional = any(isinstance(f["data"], dict) and f["data"].get("provisional") is True for f in files)
    provisional = provisional or "provisional" in (row.get("outcome") or "").lower()
    steps = failed_steps((row.get("queue") or {}).get("tasks") or [])
    text = " ".join(str(row.get(k) or "") for k in ("title", "measure", "rule", "mechanism"))
    text += " " + " ".join(str(x["arm"] or "") for x in rows + verdicts)
    own = [r for r in rows if not is_comparison(r)]
    return {
        "provisional": provisional,
        "windows": _order(r["window"] for r in own),
        "arms": own,
        "comparisons": [r for r in rows if is_comparison(r)],
        "verdicts": verdicts,
        "measures": [
            {k: m[k] for k in ("key", "label", "note", "t")}
            for m in MEASURES
            if any(_measure(r, m["key"]) is not None for r in own)
        ],
        "gates": gates_for(text),
        "reading": reading({**row, "provisional": provisional}, rows, verdicts, steps),
        "failed_steps": steps,
    }


def _measure(r: dict, key: str):
    v = r.get(key)
    return v["mean"] if isinstance(v, dict) else v


def tally(results: list[dict]) -> dict:
    """Pass/fail/no-verdict counts over a board row's result files' verdict words, each arm once and
    read as `final_words` reads them. A data check's file (validation, coverage, pilot) is skipped."""
    entries = []
    for res in results:
        name = res.get("name") or ""
        if any(k in name.lower() for k in CHECK_FILES):
            continue
        for v in res.get("verdicts") or []:
            if v.get("arm") in NOT_ARM_WORDS:
                continue
            arm = None if v.get("arm") == "verdict" else v.get("arm")
            entries.append((arm, _file_window(name, res.get("window")), v["verdict"]))
    out = {"pass": 0, "fail": 0, "no_verdict": 0}
    for w in final_words(entries).values():
        out[_cls(w) or "no_verdict"] += 1
    return out


def digest(board_rows: list[dict], since: str) -> dict:
    """Every study with a result file written or a queue step finished on or after `since`
    (YYYY-MM-DD), newest first, with its verdict counts: the Studies tab's This week view."""
    out = []
    for r in board_rows:
        stamps = [x.get("modified") for x in r.get("results") or [] if x.get("modified")]
        tasks = (r.get("queue") or {}).get("tasks") or []
        stamps += [
            t.get("finished_at")
            for t in tasks
            if t.get("finished_at") and t.get("status") in ("done", "failed")
        ]
        recent = [s for s in stamps if s >= since]
        if not recent:
            continue
        present = [x for x in r.get("results") or [] if x.get("present")]
        out.append({
            "id": r.get("id"),
            "study": r.get("study"),
            "title": r.get("title"),
            "status": r.get("status"),
            "outcome": r.get("outcome"),
            "finished": max(recent),
            "tally": tally(present),
            "provisional": any(x.get("provisional") for x in present),
            "failed_steps": failed_steps(tasks),
        })  # fmt: skip
    out.sort(key=lambda x: x["finished"], reverse=True)
    totals = {k: sum(s["tally"][k] for s in out) for k in ("pass", "fail", "no_verdict")}
    no_reading = [s["id"] for s in out if not any(s["tally"].values())]
    return {"since": since, "studies": out, "totals": totals, "no_reading": no_reading}
