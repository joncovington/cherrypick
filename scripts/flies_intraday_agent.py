"""The flies intraday agent's model call (packages/flies/docs/intraday-agent-plan.md).

The one place the agent's model is invoked, and outside the package on purpose (root CLAUDE.md):
everything deterministic -- when a tick is worth a call, what a valid reply is, the decision file a
loop reads, the record of every pack -- is `cherrypick.flies.intraday_advice`. This script supplies
the model call, fenced exactly as `advisor_checkpoint.py` fences it: the pack on stdin, no tools,
no MCP servers, no skills. Deleting this file costs the advice and nothing else; every loop falls
back to its fixed rule on a missing decision.

    python scripts/flies_intraday_agent.py --target paper|live [--at HH:MM] [--session YYYY-MM-DD]

- `paper` runs when `intraday_agent.enabled` and `.paper` are set, armed or not.
- `live` runs only when today's arm record carries an `intraday_agent` mode chosen in
  /live-flies-start, and only up to `intraday_agent.live_mode_max`. Shadow records; nothing here
  ever places an order.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from advisor_checkpoint import CREATE_NO_WINDOW, TOOL_FENCE  # noqa: E402 -- the one shared fence
from cherrypick.core import calendar as _cal  # noqa: E402
from cherrypick.core import home as _home  # noqa: E402
from cherrypick.core import live as _live  # noqa: E402
from cherrypick.core.db import connect_ro  # noqa: E402
from cherrypick.flies import intraday_advice  # noqa: E402
from cherrypick.flies.cli import load_config  # noqa: E402
from cherrypick.flies.clock import ET  # noqa: E402

PROMPT = """You review a 0DTE SPX butterfly book during the session. The JSON on stdin is everything
you have: SPX candles and price action since the open, GEX (net gamma, zero gamma, the walls, now and
at the open), options flow, SPY VWAP when recorded, the volatility complex, breadth, and the book's
positions. The book legs into each fly: it sells a vertical, then completes it with the other side.
On a trend day the completion never comes and the vertical rides to settlement at a full loss.

Decide two things:
- trend_gate: "on" if the session is still trending (keep blocking new entries on the trend's
  losing side), "off" if the trend has failed (entries may resume).
- close_stranded: the labels of open verticals (state "open_vertical") to close now rather than
  ride to settlement. Only labels shown in the pack. An empty list is a valid answer.

The economics of a close: a vertical's loss is capped at its wing width. spot_past_short_points is
how far spot has moved through its short strike; at or beyond the width the loss is already about
full, so closing saves almost nothing, pays fees and gives up any reversal. A close is worth it while
the vertical is still near or just through its short strike and the trend is likely to carry on.

Decide from the data in the pack only. Reply with ONE JSON object and nothing else:
{"trend_gate": "on"|"off", "close_stranded": ["p1", ...], "confidence": 0.0-1.0,
 "reason": "<= 40 words, citing the pack"}"""


def ask_claude(model: str | None, timeout: int = 180):
    """The model call `run_tick` is handed: fenced, stdin only, with usage read back."""

    def ask(prompt: str, pack_json: str) -> dict:
        exe = shutil.which("claude")
        if not exe:
            return {"error": "claude not on PATH"}
        argv = [
            exe,
            "-p",
            prompt,
            *(["--model", model] if model else []),
            "--output-format",
            "json",
            *TOOL_FENCE,
        ]
        try:
            proc = subprocess.run(
                argv,
                input=pack_json,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired:
            return {"error": f"claude timed out after {timeout}s"}
        except Exception as exc:  # noqa: BLE001 -- advice is never worth raising over
            return {"error": f"{type(exc).__name__}: {exc}"}
        if proc.returncode != 0:
            return {"error": (proc.stderr or "")[:500] or f"claude exited {proc.returncode}"}
        try:
            out = json.loads(proc.stdout)
        except ValueError:
            return {"reply": proc.stdout, "model": model, "cost_usd": None}
        return {
            "reply": out.get("result"),
            "model": intraday_advice.answering_model(out.get("modelUsage")) or model,
            "cost_usd": out.get("total_cost_usd"),
        }

    return ask


def _arm_record() -> dict | None:
    try:
        return json.loads(Path(_live.arm_record_path("flies")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):  # model text in output; never die on a cp1252 console
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", choices=intraday_advice.TARGETS, required=True)
    ap.add_argument("--session", help="YYYY-MM-DD; defaults to today (ET)")
    ap.add_argument("--at", help="HH:MM ET; defaults to now")
    args = ap.parse_args(argv)

    now = datetime.now(ET)
    session = args.session or now.date().isoformat()
    if args.at:
        hh, mm = (int(x) for x in args.at.split(":"))
        now = datetime.fromisoformat(session).replace(hour=hh, minute=mm, tzinfo=ET)
    cfg = load_config()
    acfg = intraday_advice.agent_config(cfg)

    def done(**out) -> int:
        print(json.dumps({"target": args.target, "session": session, **out}, default=str))
        return 0

    if not acfg["enabled"]:
        return done(skipped="intraday_agent disabled")
    if not _cal.is_trading_day(now.date()) or not (9 * 60 + 30 <= now.hour * 60 + now.minute < 16 * 60):
        return done(skipped="outside the session")
    if args.target == "paper":
        if not acfg["paper"]:
            return done(skipped="paper agent off")
        arm, ledger = acfg["paper_arm"], "paper_trades.db"
    else:
        mode = intraday_advice.live_mode_today(acfg, _arm_record(), session)
        if mode == "off":
            return done(skipped="no live agent mode chosen today")
        arm, ledger = (cfg.get("live") or {}).get("arm", "control"), "live_trades.db"

    data = _home.data_dir("flies")
    gex_conn = connect_ro(_home.data_dir("gex") / "gex_history.db")
    ledger_conn = connect_ro(Path(os.environ.get("FLIES_LEDGER_DIR") or data) / ledger)
    try:
        out = intraday_advice.run_tick(
            cfg=cfg,
            target=args.target,
            session=session,
            as_of=now.timestamp(),
            arm=arm,
            gex_conn=gex_conn,
            ledger_conn=ledger_conn,
            ask=ask_claude(acfg["model"]),
            prompt=PROMPT,
        )
    finally:
        gex_conn.close()
        ledger_conn.close()
    return done(**out)


if __name__ == "__main__":
    raise SystemExit(main())
