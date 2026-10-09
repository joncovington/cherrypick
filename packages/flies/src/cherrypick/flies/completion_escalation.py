"""Raising a live completion's limit once it looks stranded, replayed on its own quoted path. Pure.

**The question (2026-10-07).** Every completed fly locks in about $25 of credit, and a stranded
vertical loses about $250, so could a resting completion that has not filled be allowed to pay
more -- up to a capped amount above its live limit -- to turn a strand into a fly? Paying more at
placement is pure cost (75-78% of live completions fill at today's limit), so each policy raises
the limit only on a trigger: minutes unfilled, or spot moving away from the completing side.

**Not the first look.** `scripts/flies_stranded_replay.py`'s `salvage` family asked the same thing
of paper control on 2026-09-28 and found nothing (experiment-log: 0 of 24 cells beat the base on
the 5-wide, because a stranded spread's best completing debit comes ~3 minutes after entry). That
replay priced completions with paper's modelled debit. This one uses what paper never had: the live
order path's own quotes (`fly_order_path`, recorded from 2026-10-02), and the fill rule those quotes
were measured to obey -- a resting completion fills once its mid comes within `FILL_SLACK` of the
limit (`analytics.fill_realism`'s `rule_fit`: 16 of 16 resolved orders at +0.05).

**How a policy is replayed.** Walk the completion's quoted observations in order. A step's trigger
latches the first time it fires, and the working limit is the limit live had at that observation
plus the largest raise latched so far. The completion fills at the first observation whose mid is
within `FILL_SLACK` of that limit, and pays the limit. The path ends when live's own order resolved,
so a live fill with no rule touch before it fills at that moment at the limit then working: a
higher limit can only fill sooner, never later. A position the rule never fills is a strand, settled
as the short vertical it was. Every outcome settles at the row's own print through
`fly.position_pnl` on the modelled fee stack (one vertical's open fee, two if it completed, plus the
settlement fee that print triggers), so every policy shares one cost basis.

**The entries a rescue would have let in.** Live sizes by `live.max_open_margin_dollars`, counting
each open position at `max(0, -floor)` (`live_loop.open_margin_dollars`), so a stranded vertical
holds about $255 of it all day and a completed fly at +0.50 still holds its -$47 floor. Every entry
the cap refused is in `fly_decisions` (`max_open_margin_reached`, with the would-be total). Under a
policy, `admit_blocked` re-totals each refusal with the policy's exposure in place of the
baseline's, at the refusal's first and last moment, and lets it in where it now fits. An admitted
entry is scored from paper control's position at the same centre: live never traded it, and paper
completes more often than live, so that column reads optimistic and is never added to the
live-scored one. The admitted entry then holds its own margin until paper completed it.
"""

from __future__ import annotations

from cherrypick.flies import fill_model, fly

FILL_SLACK = 0.05  # points: mid within this of the limit fills it (fill_realism rule_fit, 16/16)
CAPS = (0.25, 0.50)  # the side-by-side comparison asked for on 2026-10-07
FIRST_STEP = 0.10  # a time ladder's first raise, before its cap


def policies(caps=CAPS) -> list[dict]:
    """The replayed grid: the baseline, then every trigger at every cap.

    `steps` is a list of (trigger, raise): `("start", 0)` fires at once, `("after_min", T)` once the
    completion has worked T minutes, `("away_widths", d)` once spot is d widths away from the
    completing side. Raises are points above the limit live had at that observation.
    """
    out = [{"name": "baseline", "family": "baseline", "cap": 0.0, "steps": []}]
    for cap in caps:
        out.append({"name": f"flat +{cap:.2f}", "family": "flat", "cap": cap, "steps": [(("start", 0), cap)]})
        for t1, t2 in ((45, 90), (45, 150), (90, 150)):
            out.append(
                {
                    "name": f"ladder +{FIRST_STEP:.2f}@{t1}m, +{cap:.2f}@{t2}m",
                    "family": f"ladder {t1}/{t2}",
                    "cap": cap,
                    "steps": [(("after_min", t1), FIRST_STEP), (("after_min", t2), cap)],
                }
            )
        for d in (0.5, 1.0):
            out.append(
                {
                    "name": f"away {d:.1f}w +{cap:.2f}",
                    "family": f"away {d:.1f}w",
                    "cap": cap,
                    "steps": [(("away_widths", d), cap)],
                }
            )
    return out


def _fired(trigger: tuple, *, minutes: float, dist_widths: float | None) -> bool:
    kind, value = trigger
    if kind == "start":
        return True
    if kind == "after_min":
        return minutes >= value
    if kind == "away_widths":
        return dist_widths is not None and dist_widths <= -value
    raise ValueError(f"unknown trigger {kind!r}")


def simulate(
    position: dict, path: list[dict], policy: dict, *, placed_at: str, live_filled_at: str | None
) -> dict:
    """One position's completion under `policy`. `path` is its quoted `fly_order_path` completion
    rows, oldest first; `placed_at` is when its first completion order went in; `live_filled_at`
    is when live's own completion filled (None for a strand)."""
    side, center, width = position["side"], position["center"], position["wing_width"]
    t0 = fill_model.parse_ts(placed_at)
    raised = 0.0
    latched: set[int] = set()
    for r in path:
        if r.get("limit_price") is None:
            continue
        minutes = (fill_model.parse_ts(r["observed_at"]) - t0).total_seconds() / 60
        dist = fill_model.spot_distances(side, center, width, r.get("spot"))["dist_widths"]
        for i, (trigger, amount) in enumerate(policy["steps"]):
            if i not in latched and _fired(trigger, minutes=minutes, dist_widths=dist):
                latched.add(i)
                raised = max(raised, amount)
        limit = round(r["limit_price"] + raised, 2)
        prices = fill_model.spread_prices(
            fill_model.COMPLETION, r.get("buy_bid"), r.get("buy_ask"), r.get("sell_bid"), r.get("sell_ask")
        )
        if prices is not None and prices["mid"] <= limit + FILL_SLACK + 1e-9:
            return {"completed": True, "at": r["observed_at"], "paid": limit, "raised": raised, "via": "rule"}
    if live_filled_at is not None:
        last = next((r for r in reversed(path) if r.get("limit_price") is not None), None)
        limit = round(last["limit_price"] + raised, 2) if last else None
        return {"completed": True, "at": live_filled_at, "paid": limit, "raised": raised, "via": "live_fill"}
    return {"completed": False, "at": None, "paid": None, "raised": raised, "via": None}


def settle(position: dict, outcome: dict) -> dict:
    """The outcome's P&L and floor at the row's own settlement print, on the modelled fee stack."""
    qty = position.get("quantity") or 1
    opened = fly.vertical_open_fee(position["symbol"], qty)
    completed = outcome["completed"] and outcome["paid"] is not None
    shaped = {
        "kind": "fly" if completed else "short_vertical",
        "side": position["side"],
        "center": position["center"],
        "wing_width": position["wing_width"],
        "net": position["credit"] - outcome["paid"] if completed else position["credit"],
        "quantity": qty,
        "fees": opened * (2 if completed else 1),
    }
    return {
        "pnl": round(fly.position_pnl(shaped, position["settlement_price"]), 2),
        "floor": round(fly.position_floor(shaped), 2),
    }


def compare(rows: list[dict], policy: dict, baseline: list[dict]) -> dict:
    """One policy against the baseline over the same positions. `rows` and `baseline` are aligned
    lists of {"outcome", "settled"} per position."""
    rescued = overpaid = 0
    overpaid_dollars = 0.0
    negative_floors = 0
    per_session: dict[str, float] = {}
    for r, b in zip(rows, baseline, strict=True):
        delta = r["settled"]["pnl"] - b["settled"]["pnl"]
        per_session[r["trade_date"]] = round(per_session.get(r["trade_date"], 0.0) + delta, 2)
        if r["outcome"]["completed"] and not b["outcome"]["completed"]:
            rescued += 1
        if (
            r["outcome"]["completed"]
            and b["outcome"]["completed"]
            and r["outcome"]["paid"] > b["outcome"]["paid"]
        ):
            overpaid += 1
            overpaid_dollars += (r["outcome"]["paid"] - b["outcome"]["paid"]) * fly.CONTRACT_MULTIPLIER
        if r["outcome"]["completed"] and r["settled"]["floor"] < 0:
            negative_floors += 1
    total = round(sum(r["settled"]["pnl"] for r in rows), 2)
    base = round(sum(b["settled"]["pnl"] for b in baseline), 2)
    return {
        "policy": policy["name"],
        "family": policy["family"],
        "cap": policy["cap"],
        "positions": len(rows),
        "completed": sum(1 for r in rows if r["outcome"]["completed"]),
        "rescued": rescued,
        "overpaid": overpaid,
        "overpaid_dollars": round(overpaid_dollars, 2),
        "negative_floors": negative_floors,
        "pnl": total,
        "delta": round(total - base, 2),
        "worst_session_delta": min(per_session.values()) if per_session else None,
        "per_session_delta": per_session,
    }


# --------------------------------------------------------------------------- the entries a rescue frees
ADMIT_MATCH_MINUTES = 10  # a paper control entry this close to the admission scores it


def _shape(position: dict, outcome: dict | None) -> dict:
    """The position as it stands once `outcome` has happened (None: still the open vertical)."""
    qty = position.get("quantity") or 1
    opened = fly.vertical_open_fee(position["symbol"], qty)
    completed = outcome is not None and outcome["completed"] and outcome["paid"] is not None
    return {
        "kind": "fly" if completed else "short_vertical",
        "wing_width": position["wing_width"],
        "net": position["credit"] - outcome["paid"] if completed else position["credit"],
        "quantity": qty,
        "fees": opened * (2 if completed else 1),
    }


def exposure_at(position: dict, outcome: dict, when) -> float:
    """The live margin rule's worst case for one position at `when` (`live_loop.open_margin_dollars`):
    nothing before it was entered, the open vertical's until it completed, the fly's after."""
    if fill_model.parse_ts(position["entry_time"]) > when:
        return 0.0
    done = outcome["completed"] and outcome["at"] is not None and fill_model.parse_ts(outcome["at"]) <= when
    return max(0.0, -fly.position_floor(_shape(position, outcome if done else None)))


def _paper_match(paper: list[dict], center: float, when) -> dict | None:
    best = None
    for p in paper:
        if p["center"] != center or p.get("entry_time") is None:
            continue
        gap = abs((fill_model.parse_ts(p["entry_time"]) - when).total_seconds()) / 60
        if gap <= ADMIT_MATCH_MINUTES and (best is None or gap < best[0]):
            best = (gap, p)
    return best[1] if best else None


def _admitted_exposure(admitted: list[dict], when) -> float:
    total = 0.0
    for a in admitted:
        p = a["paper"]
        if p is None or fill_model.parse_ts(a["at"]) > when:
            continue
        done = p.get("completed_at") and fill_model.parse_ts(p["completed_at"]) <= when
        if not done:
            total += max(0.0, (p["wing_width"] - p["credit"]) * fly.CONTRACT_MULTIPLIER)
    return total


def admit_blocked(
    blocks: list[dict],
    day: list[dict],
    paper: list[dict],
    *,
    taken_centers: set,
) -> list[dict]:
    """The margin-refused entries one policy would have let in, on one session.

    `blocks` are the session's `max_open_margin_reached` decisions ({first_seen, last_seen,
    would_be, cap, center}), oldest first; each carries the cap it was refused under. `day` is every live position of the session with its
    baseline and policy outcome ({position, base, policy}). A refusal is let in at the first of its
    two moments where the logged total, less what the policy frees and plus what earlier admissions
    hold, fits under its cap. A centre live traded anyway that session is skipped (it would have been
    a duplicate structure), and so is a refusal paper control has no matching entry for -- reported,
    never guessed.
    """
    admitted: list[dict] = []
    for b in blocks:
        if b["center"] in taken_centers or any(a["center"] == b["center"] for a in admitted):
            continue
        for moment in (b["first_seen"], b["last_seen"]):
            when = fill_model.parse_ts(moment)
            freed = sum(
                exposure_at(d["position"], d["base"], when) - exposure_at(d["position"], d["policy"], when)
                for d in day
            )
            total = b["would_be"] - freed + _admitted_exposure(admitted, when)
            if total <= b["cap"] + 1e-9:
                match = _paper_match(paper, b["center"], when)
                admitted.append(
                    {
                        "center": b["center"],
                        "at": moment,
                        "would_be": round(total, 2),
                        "paper": match,
                        "paper_pnl": round(match["pnl"], 2)
                        if match and match.get("pnl") is not None
                        else None,
                    }
                )
                break
    return admitted
