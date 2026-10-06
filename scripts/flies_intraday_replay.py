"""The flies intraday agent's historical replay (packages/flies/docs/intraday-agent-plan.md, step 5).

Asks the model, minute by minute, over the recorded sessions in `intraday_replay`'s window, through
the forward job's own `run_tick`, prompt and fenced call (`flies_intraday_agent.py`), then scores the
trend gate (`intraday_replay.score`) and re-judges the qualification (`intraday_eval.run`). Opus only
(the config model), by the user's choice on 2026-10-06.

Paced and resumable, because it runs on the Max plan's limits: `--max-calls` stops a run after that
many model calls, and the next run resumes after the last minute checked. A session counts only once
it has run to the bell. Records go to the replay's own store, never the forward record.

    python scripts/flies_intraday_replay.py --dry-run          # calls it would make, no model
    python scripts/flies_intraday_replay.py --max-calls 60     # one paced batch
    python scripts/flies_intraday_replay.py --max-calls 3000 --stop-at 08:45   # overnight, before the open
    python scripts/flies_intraday_replay.py --score-only       # re-score what has run
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cherrypick.core import home as _home  # noqa: E402
from cherrypick.core.db import connect_ro  # noqa: E402
from cherrypick.flies import db as dbmod  # noqa: E402
from cherrypick.flies import intraday_advice, intraday_eval, intraday_pack, intraday_replay  # noqa: E402
from cherrypick.flies.cli import load_config  # noqa: E402
from cherrypick.flies.clock import ET  # noqa: E402
from flies_intraday_agent import PROMPT, ask_claude  # noqa: E402

COST_PER_CALL = 0.17  # Opus, measured 2026-10-05; an estimate for --dry-run only


class CallFailed(Exception):
    """The model call itself failed (a usage limit, a timeout, the CLI missing). Raised inside
    `run_tick` before it records anything, so the batch stops and the minute is retried next run
    rather than recorded as a decision that never happened."""


def _strict(ask):
    def call(prompt, pack_json):
        out = ask(prompt, pack_json)
        if out.get("error"):
            raise CallFailed(out["error"])
        return out

    return call


def dry_run(cfg, gex_conn, ledger_conn, days) -> dict:
    """The calls each session would make, applying the trigger, the gap and the cap, with no model."""
    acfg = intraday_advice.agent_config(cfg)
    gap = float(acfg["min_minutes_between_calls"]) * 60
    cap = int(acfg["max_calls_per_session"])
    per = {}
    for day in days:
        last, n = None, 0
        for at in intraday_replay.minutes(day):
            if n >= cap:
                break
            pack = intraday_pack.build_pack(
                gex_conn=gex_conn,
                ledger_conn=ledger_conn,
                session=day,
                as_of=at,
                arm=intraday_replay.REPLAY_ARM,
            )
            if intraday_advice.trigger(pack, acfg) is None or (last is not None and at - last < gap):
                continue
            last, n = at, n + 1
        per[day] = n
    total = sum(per.values())
    return {
        "sessions": len(per),
        "calls": total,
        "estimated_usd": round(total * COST_PER_CALL, 2),
        "per_session": per,
    }


def replay(cfg, gex_conn, ledger_conn, days, max_calls: int, stop_at: datetime | None = None) -> dict:
    acfg = intraday_advice.agent_config(cfg)
    ask = _strict(ask_claude(acfg["model"]))
    store = intraday_advice.REPLAY_STORE
    calls, finished = 0, []
    for day in days:
        recs = intraday_advice.records(day, store)
        if intraday_replay.is_done(recs):
            continue
        after = intraday_replay.resume_from(recs)
        stopped = False
        for at in intraday_replay.minutes(day):
            if after is not None and at <= after:
                continue
            if calls >= max_calls or (stop_at is not None and datetime.now(ET) >= stop_at):
                stopped = True
                break
            try:
                out = intraday_advice.run_tick(
                    cfg=cfg,
                    target="paper",
                    session=day,
                    as_of=at,
                    arm=intraday_replay.REPLAY_ARM,
                    gex_conn=gex_conn,
                    ledger_conn=ledger_conn,
                    ask=ask,
                    prompt=PROMPT,
                    write_file=False,
                    store=store,
                )
            except CallFailed as exc:
                print(
                    json.dumps(
                        {
                            "stopped": "model call failed; nothing recorded for this minute",
                            "error": str(exc)[:300],
                        }
                    )
                )
                return {"calls": calls, "finished": finished, "stopped_on_error": True}
            if out.get("called"):
                calls += 1
                print(
                    json.dumps(
                        {k: out.get(k) for k in ("session", "as_of", "ok", "decision", "cost_usd")},
                        default=str,
                    )
                )
        if stopped:
            break
        intraday_advice._append(day, {intraday_replay.DONE: True, "session": day}, store)
        finished.append(day)
    return {"calls": calls, "finished": finished}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="count the calls it would make; no model")
    ap.add_argument("--score-only", action="store_true", help="re-score and re-judge; no model")
    ap.add_argument("--max-calls", type=int, default=60, help="stop after this many model calls (pacing)")
    ap.add_argument("--session", action="append", help="only these sessions (YYYY-MM-DD; repeatable)")
    ap.add_argument(
        "--stop-at",
        help="HH:MM ET: start no call after this (keep a batch off the session agent's plan limits)",
    )
    args = ap.parse_args(argv)

    cfg = load_config()
    data = _home.data_dir("flies")
    gex_conn = connect_ro(_home.data_dir("gex") / "gex_history.db")
    ledger_conn = connect_ro(data / "paper_trades.db")
    try:
        days = intraday_replay.sessions(ledger_conn)
        if args.session:
            days = [d for d in days if d in set(args.session)]
        if args.dry_run:
            print(json.dumps(dry_run(cfg, gex_conn, ledger_conn, days), indent=2))
            return 0
        stop_at = None
        if args.stop_at:
            hh, mm = (int(x) for x in args.stop_at.split(":"))
            now = datetime.now(ET)
            stop_at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if stop_at <= now:
                stop_at += timedelta(days=1)
        ran = {} if args.score_only else replay(cfg, gex_conn, ledger_conn, days, args.max_calls, stop_at)
    finally:
        gex_conn.close()
    ledger = dbmod.connect(str(data / "paper_trades.db"))
    scored = intraday_replay.score(ledger, cfg, write=True)
    qual = intraday_eval.run(ledger, cfg, write=True)
    remaining = [d for d in days if d not in scored["sessions"]]
    print(
        json.dumps(
            {
                **ran,
                "sessions_scored": len(scored["sessions"]),
                "sessions_remaining": len(remaining),
                "replay_calls": scored["calls"],
                "replay_cost_usd": scored["cost_usd"],
                "offered_modes": qual["offered_modes"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
