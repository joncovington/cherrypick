#!/usr/bin/env python3
"""Self-contained, offline dashboard for the strategy-testing program (see
docs/strategy-testing-plan.md). Renders reports/strategy_dashboard.html --
a single static file with no server and no CDN/network dependency, so it
opens anywhere (respects the project's cross-machine/no-absolute-paths
guardrail). Every number comes from strategy_metrics.py, the same module
strategy_report.py reads, so the two can never disagree.

Charts are `cherrypick.core.viz` cards with their payloads baked inline
(`viz.card_inline_html`) and drawn client-side on a plain canvas -- the
suite's shared chart contract, replacing the old matplotlib-PNG pipeline.
That swap put the equity curves on a real date axis (the PNGs indexed by
trade #), and dropped both the matplotlib dependency and the disk chart
cache that existed only because PNG rendering was expensive. Surfaces the
viz contract doesn't cover (regime heatmap, rejection histogram, weekly
P&L) render as plain HTML/CSS -- still zero dependencies.

Design (see the dashboard-design research in the strategy-testing plan):
dark, dense "Bloomberg" layout for an operator doing multi-strategy
analytical review (not a mobile glance); a 5-KPI header with the primary
decision metric (portfolio net expectancy) top-left; trade-level stats
throughout (each earnings play is a round-trip, not a return-series
period); pass/fail status shown with color AND a glyph, never color alone;
one justified interaction -- a timeframe toggle (Cumulative / Rolling
4-week / Rolling 1-week / Per-week) on the portfolio headline equity
curve, implemented as pre-baked panels swapped by inline JS.

Usage:
    python strategy_dashboard.py
    python strategy_dashboard.py --since 2026-07-01 --profile strat_test
"""

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from cherrypick.core import viz  # noqa: E402

import paths as _paths  # noqa: E402
import scanner  # noqa: E402
import strategy_metrics as sm  # noqa: E402
from strategy_report import STRATEGY_NAMES  # noqa: E402

try:  # stdlib zoneinfo first (tzdata supplies the db on Windows); pytz only as fallback
    from zoneinfo import ZoneInfo

    _ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - only where zoneinfo has no tz database
    import pytz

    _ET = pytz.timezone("America/New_York")

# Generated dashboards live under the shared cherrypick home (~/.cherrypick/data/earnings/reports),
# resolved by paths.py — never in the checkout. Pure path; main() mkdirs before writing.
REPORTS_DIR = _paths.reports_dir()

# Dark palette -- background/text tuned for a dark page. The four tone colors double as the
# CSS variables the viz canvas renderer reads (--pos/--neg/--accent/--warn), so the inline
# cards and the hand-rolled HTML surfaces stay on one color system.
BG = "#0d1117"
PANEL_BG = "#161b22"
FG = "#e6edf3"
MUTED = "#8b949e"
GRID = "#30363d"
GOOD = "#3fb950"
WARN = "#d29922"
BAD = "#f85149"
ACCENT = "#58a6ff"


def _status_span(passed) -> str:
    if passed is None:
        return f'<span style="color:{MUTED};">- n/a</span>'
    if passed:
        return f'<span style="color:{GOOD};">&#10003; PASS</span>'
    return f'<span style="color:{BAD};">&#10007; FAIL</span>'


def _sample_bar(sample: dict) -> str:
    n = sample["count"]
    target = sample["significant_target"]
    pct = min(n / target, 1.0) if target else 0.0
    filled = int(pct * 20)
    bar = "#" * filled + "-" * (20 - filled)
    color = GOOD if sample["significant_met"] else (WARN if sample["directional_met"] else MUTED)
    return f'<span style="color:{color};font-family:monospace;">[{bar}] {n}/{target}</span>'


def _metrics_table_html(core_five: dict, iv_crush: dict | None = None) -> str:
    wr = core_five["win_rate"]["value"]
    pf = core_five["profit_factor"]
    exp = core_five["expectancy"]
    sh = core_five["sharpe"]
    mdd = core_five["max_drawdown"]["value"]
    rows = [
        ("Win rate", f"{wr * 100:.1f}%" if wr is not None else "n/a", ""),
        (
            "Profit factor",
            f"{pf['value']:.2f}" if pf["value"] not in (None,) else "n/a",
            _status_span(pf["pass"]),
        ),
        ("Expectancy (net)", viz.fmt_money(exp["value"], none="n/a"), _status_span(exp["pass"])),
        (
            "Sharpe (trade)",
            f"{sh['value']:.2f}" if sh["value"] is not None else "n/a",
            _status_span(sh["pass"]),
        ),
        (
            "Max drawdown",
            f"{viz.fmt_money(mdd['absolute'])} ({mdd['pct'] * 100:.1f}%)",
            _status_span(core_five["max_drawdown"]["pass"]),
        ),
    ]
    if iv_crush is not None:
        if iv_crush["avg_crush"] is not None:
            label = "Avg IV crush" if iv_crush["avg_crush"] >= 0 else "Avg IV expansion"
            value = f"{abs(iv_crush['avg_crush']) * 100:.1f} vol pts (n={iv_crush['sample_count']})"
        else:
            value = "n/a"
            label = "Avg IV crush"
        rows.append((label, value, ""))
    body = "".join(
        f'<tr><td style="color:{MUTED};padding:2px 10px 2px 0;">{label}</td>'
        f'<td style="padding:2px 10px;">{value}</td><td style="padding:2px;">{status}</td></tr>'
        for label, value, status in rows
    )
    return f'<table style="font-size:12px;">{body}</table>'


def _portfolio_ts_payload(trades: list[dict], days: int | None) -> dict:
    """viz section payload for the portfolio equity curve, optionally windowed to the last
    `days` days of closes. Cumulative net P&L on a date axis, one point per close session."""
    if days is not None:
        cutoff = (datetime.now() - timedelta(days=days)).timestamp()
        trades = [t for t in trades if (t.get("closed_at") or 0) >= cutoff]
    labels, values = sm.daily_equity_series(trades)
    if not labels:
        return {"ok": True, "subtitle": "no closed trades in this window"}
    return {
        "ok": True,
        "subtitle": f"{len(trades)} closed trade(s)",
        "timeseries": {
            "labels": labels,
            "series": [{"name": "cum net P&L", "values": values, "tone": "accent"}],
        },
    }


def _strategy_ts_payload(trades: list[dict]) -> dict:
    """Per-strategy card payload: the equity curve plus its drawdown-from-peak as a second
    series -- the old separate underwater PNG folded into one date-axis chart."""
    labels, values = sm.daily_equity_series(trades)
    if not labels:
        return {"ok": True, "subtitle": "no closed trades"}
    peak = 0.0
    drawdown = []
    for v in values:
        peak = max(peak, v)
        drawdown.append(round(v - peak, 2))
    return {
        "ok": True,
        "timeseries": {
            "labels": labels,
            "series": [
                {"name": "equity", "values": values, "tone": "accent"},
                {"name": "drawdown", "values": drawdown, "tone": "neg"},
            ],
        },
    }


def _weekly_pnl_html(trades: list[dict]) -> str:
    """Per-week net P&L as signed, zero-centred CSS bars (ISO week buckets). Categorical
    bars aren't in the viz contract, and plain divs need no dependency either."""
    weekly: dict[str, float] = {}
    for t in trades:
        if not t.get("closed_at"):
            continue
        d = datetime.fromtimestamp(t["closed_at"]).date()
        iso_year, iso_week, _ = d.isocalendar()
        weekly[f"{iso_year}-W{iso_week:02d}"] = weekly.get(f"{iso_year}-W{iso_week:02d}", 0.0) + sm.net_pnl(t)
    if not weekly:
        return f'<div style="color:{MUTED};font-size:12px;">no closed trades</div>'
    mx = max(abs(v) for v in weekly.values()) or 1.0
    rows = []
    for key in sorted(weekly):
        v = weekly[key]
        width = abs(v) / mx * 50
        left = 50.0 if v >= 0 else 50.0 - width
        color = GOOD if v >= 0 else BAD
        rows.append(
            f'<div style="display:grid;grid-template-columns:84px 1fr 110px;gap:8px;align-items:center;'
            f'font-size:12px;margin:2px 0;">'
            f'<div style="color:{MUTED};">{key}</div>'
            f'<div style="position:relative;height:12px;background:{BG};border-radius:2px;">'
            f'<div style="position:absolute;left:50%;top:0;bottom:0;width:1px;background:{GRID};"></div>'
            f'<div style="position:absolute;left:{left:.2f}%;width:{width:.2f}%;top:2px;bottom:2px;'
            f'background:{color};border-radius:2px;"></div></div>'
            f'<div style="text-align:right;color:{color};">{viz.fmt_money(v)}</div></div>'
        )
    return "".join(rows)


def _regime_table_html(all_buckets: dict[str, dict[str, int]]) -> str:
    """Regime coverage (IV/RV x dispersion) as an HTML heat table: cell background intensity
    scales with trade count. all_buckets: {strategy_name: {bucket_label: count}}."""
    labels = sorted({label for buckets in all_buckets.values() for label in buckets})
    strategies = list(all_buckets.keys())
    if not labels or not strategies:
        return f'<div style="color:{MUTED};font-size:12px;">no regime data yet</div>'
    mx = max((c for b in all_buckets.values() for c in b.values()), default=1) or 1
    head = "".join(
        f'<th style="text-align:center;padding:3px 6px;font-weight:400;color:{MUTED};'
        f'font-size:10px;">{label}</th>'
        for label in labels
    )
    body_rows = []
    for s in strategies:
        cells = []
        for label in labels:
            v = all_buckets[s].get(label, 0)
            if v:
                alpha = 0.15 + 0.55 * (v / mx)
                cells.append(
                    f'<td style="text-align:center;padding:3px 6px;'
                    f'background:rgba(88,166,255,{alpha:.2f});">{v}</td>'
                )
            else:
                cells.append('<td style="text-align:center;padding:3px 6px;"></td>')
        body_rows.append(
            f'<tr><td style="padding:3px 8px;color:{FG};white-space:nowrap;">{s}</td>{"".join(cells)}</tr>'
        )
    return (
        f'<table style="border-collapse:collapse;font-size:12px;">'
        f'<thead><tr><th style="text-align:left;padding:3px 8px;font-weight:400;color:{MUTED};">'
        f"trades</th>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"
    )


def _rejection_bars_html(reason_counts: dict[str, int], top_n: int = 15) -> str:
    """Top scan_log rejection reasons as horizontal CSS bars."""
    items = sorted(reason_counts.items(), key=lambda x: -x[1])[:top_n]
    if not items:
        return f'<div style="color:{MUTED};font-size:12px;">no rejections logged</div>'
    mx = items[0][1] or 1
    rows = []
    for reason, count in items:
        width = count / mx * 100
        rows.append(
            f'<div style="display:grid;grid-template-columns:minmax(160px,45%) 1fr 50px;gap:8px;'
            f'align-items:center;font-size:11px;margin:2px 0;">'
            f'<div style="color:{MUTED};overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" '
            f'title="{reason}">{reason}</div>'
            f'<div style="position:relative;height:10px;background:{BG};border-radius:2px;">'
            f'<div style="position:absolute;left:0;width:{width:.2f}%;top:1px;bottom:1px;'
            f'background:{ACCENT};border-radius:2px;"></div></div>'
            f'<div style="text-align:right;color:{FG};">{count}</div></div>'
        )
    return "".join(rows)


def _open_positions_section(profile: str) -> str:
    """Active (unclosed) positions for `profile`, in dollar terms, preceded by a short legend
    defining the columns. `entry_credit` is stored in premium points (per the
    `pnl = (entry_credit - exit_debit) * 100` convention), so it is x100 here for dollars;
    `capital_at_risk` and `entry_cost` are already dollar-denominated."""
    try:
        conn = sqlite3.connect(sm.DB_PATH)
        conn.row_factory = sqlite3.Row
        frag, fparams = sm.book_family_filter(profile)
        rows = conn.execute(
            "SELECT strategy, symbol, quantity, entry_credit, capital_at_risk, entry_cost, expiration "
            f"FROM trades WHERE {frag} AND closed_at IS NULL ORDER BY symbol, strategy",
            fparams,
        ).fetchall()
        conn.close()
    except Exception:
        rows = []

    legend = f"""
    <div style="color:{MUTED};font-size:11px;margin-bottom:10px;line-height:1.6;">
      <b style="color:{FG};">Credit / (Debit)</b> — premium collected at entry (&times; $100 per
      contract); a value in (parentheses) is a net <b>debit paid</b> instead. &nbsp;&middot;&nbsp;
      <b style="color:{FG};">Net of cost</b> — that credit/debit after subtracting the modeled entry
      cost; the approximate cash you actually keep (credit) or lay out (debit). &nbsp;&middot;&nbsp;
      <b style="color:{FG};">Max loss</b> — capital at risk: the most a defined-risk position can
      lose, already net of the credit collected. &nbsp;&middot;&nbsp;
      <b style="color:{FG};">Entry cost</b> — modeled tastytrade fees + a slippage haircut charged at
      entry; held out of P&amp;L until the trade closes.
    </div>
    """

    if not rows:
        return legend + f'<div style="color:{MUTED};font-size:12px;">No open positions.</div>'

    trs = []
    tot_risk = tot_cost = 0.0
    for r in rows:
        credit_usd = (r["entry_credit"] or 0.0) * 100
        cost = r["entry_cost"] or 0.0
        risk = r["capital_at_risk"] or 0.0
        net = credit_usd - cost
        tot_risk += risk
        tot_cost += cost
        credit_str = f"${credit_usd:,.0f}" if credit_usd >= 0 else f"(${abs(credit_usd):,.0f})"
        net_str = f"${net:,.0f}" if net >= 0 else f"(${abs(net):,.0f})"
        net_color = GOOD if net >= 0 else BAD
        trs.append(
            f"<tr>"
            f'<td style="padding:3px 10px;">{r["strategy"]}</td>'
            f'<td style="padding:3px 10px;">{r["symbol"]}</td>'
            f'<td style="padding:3px 10px;text-align:right;">{r["quantity"]}</td>'
            f'<td style="padding:3px 10px;text-align:right;">{credit_str}</td>'
            f'<td style="padding:3px 10px;text-align:right;color:{net_color};">{net_str}</td>'
            f'<td style="padding:3px 10px;text-align:right;">${risk:,.0f}</td>'
            f'<td style="padding:3px 10px;text-align:right;">${cost:,.0f}</td>'
            f'<td style="padding:3px 10px;">{r["expiration"] or ""}</td>'
            f"</tr>"
        )
    total_row = (
        f'<tr style="border-top:1px solid {GRID};color:{MUTED};">'
        f'<td style="padding:3px 10px;" colspan="5">Total ({len(rows)} open)</td>'
        f'<td style="padding:3px 10px;text-align:right;">${tot_risk:,.0f}</td>'
        f'<td style="padding:3px 10px;text-align:right;">${tot_cost:,.0f}</td>'
        f"<td></td></tr>"
    )
    table = f"""
    <table style="width:100%;border-collapse:collapse;font-size:12px;">
      <thead><tr style="color:{MUTED};border-bottom:1px solid {GRID};">
        <th style="text-align:left;padding:3px 10px;">Strategy</th>
        <th style="text-align:left;padding:3px 10px;">Symbol</th>
        <th style="text-align:right;padding:3px 10px;">Qty</th>
        <th style="text-align:right;padding:3px 10px;">Credit / (Debit)</th>
        <th style="text-align:right;padding:3px 10px;">Net of cost</th>
        <th style="text-align:right;padding:3px 10px;">Max loss</th>
        <th style="text-align:right;padding:3px 10px;">Entry cost</th>
        <th style="text-align:left;padding:3px 10px;">Expiration</th>
      </tr></thead>
      <tbody>{"".join(trs)}{total_row}</tbody>
    </table>
    """

    return legend + table


def build_dashboard(profile: str, since: str | None, mode: str = "paper") -> str:
    config = scanner._load_config()
    capital_basis = config.get("available_capital_paper_mode")

    all_trades = sm.load_closed_trades(profile=profile, since=since)
    per_strategy = {
        name: sm.load_closed_trades(profile=profile, strategy=name, since=since) for name in STRATEGY_NAMES
    }

    # --- Header KPIs (5, decision metric top-left) ---
    portfolio_summary = sm.strategy_summary(all_trades, capital_basis)
    net_total = sum(sm.net_pnl(t) for t in all_trades)
    total_trades = len(all_trades)
    exp = portfolio_summary["core_five"]["expectancy"]["value"]

    # --- Timeframe panels (portfolio headline equity curve, one inline viz card each) ---
    tf_panels = {
        "cumulative": viz.card_inline_html(
            "tf-cumulative", "Portfolio net P&L — cumulative", _portfolio_ts_payload(all_trades, None)
        ),
        "rolling4w": viz.card_inline_html(
            "tf-rolling4w", "Portfolio net P&L — rolling 4-week", _portfolio_ts_payload(all_trades, 28)
        ),
        "rolling1w": viz.card_inline_html(
            "tf-rolling1w", "Portfolio net P&L — rolling 1-week", _portfolio_ts_payload(all_trades, 7)
        ),
        "perweek": (
            '<section class="card"><h2>Portfolio net P&L — per-week</h2>'
            + _weekly_pnl_html(all_trades)
            + "</section>"
        ),
    }

    # --- Regime heat table + rejection histogram ---
    all_buckets = {name: sm.regime_buckets(trades) for name, trades in per_strategy.items() if trades}
    regime_html = _regime_table_html(all_buckets)

    reason_counts: dict[str, int] = {}
    try:
        conn = sqlite3.connect(sm.DB_PATH)
        frag, fparams = sm.book_family_filter(profile)
        rows = conn.execute(
            f"SELECT reason FROM scan_log WHERE {frag} AND reason IS NOT NULL", fparams
        ).fetchall()
        conn.close()
        for (reason,) in rows:
            for part in reason.split(";"):
                part = part.strip()
                if part:
                    reason_counts[part] = reason_counts.get(part, 0) + 1
    except Exception:
        pass
    rejection_html = _rejection_bars_html(reason_counts)

    # --- Per-strategy cards ---
    strategy_cards = []
    comparison_rows = []
    for name in STRATEGY_NAMES:
        trades = per_strategy[name]
        summary = sm.strategy_summary(trades, capital_basis)
        chart_card = viz.card_inline_html(f"strategy-{name}", name, _strategy_ts_payload(trades))
        metrics_html = _metrics_table_html(summary["core_five"], summary["iv_crush"])
        sample_html = _sample_bar(summary["sample"])

        strategy_cards.append(f"""
        <div style="display:flex;flex-wrap:wrap;gap:16px;align-items:stretch;margin-bottom:14px;">
          <div style="flex:1 1 420px;min-width:320px;">{chart_card}</div>
          <div style="background:{PANEL_BG};border:1px solid {GRID};border-radius:6px;padding:14px;">
            <h3 style="margin:0 0 6px 0;color:{FG};font-size:13px;">{name}</h3>
            <div style="margin-bottom:8px;">{sample_html}</div>
            {metrics_html}
          </div>
        </div>
        """)

        cf = summary["core_five"]
        wr = cf["win_rate"]["value"]
        pf = cf["profit_factor"]["value"]
        exp_v = cf["expectancy"]["value"]
        comparison_rows.append(
            f'<tr><td style="padding:3px 10px;">{name}</td>'
            f'<td style="padding:3px 10px;text-align:right;">{summary["total_trades"]}</td>'
            f'<td style="padding:3px 10px;text-align:right;">{f"{wr * 100:.1f}%" if wr is not None else "n/a"}</td>'
            f'<td style="padding:3px 10px;text-align:right;">{f"{pf:.2f}" if pf not in (None,) else "n/a"}</td>'
            f'<td style="padding:3px 10px;text-align:right;">{viz.fmt_money(exp_v, none="n/a")}</td>'
            f'<td style="padding:3px 10px;">{_sample_bar(summary["sample"])}</td></tr>'
        )

    comparison_table = f"""
    <table style="width:100%;border-collapse:collapse;font-size:12px;">
      <thead><tr style="color:{MUTED};border-bottom:1px solid {GRID};">
        <th style="text-align:left;padding:3px 10px;">Strategy</th>
        <th style="text-align:right;padding:3px 10px;">Trades</th>
        <th style="text-align:right;padding:3px 10px;">Win rate</th>
        <th style="text-align:right;padding:3px 10px;">Profit factor</th>
        <th style="text-align:right;padding:3px 10px;">Expectancy</th>
        <th style="text-align:left;padding:3px 10px;">Sample progress</th>
      </tr></thead>
      <tbody>{"".join(comparison_rows)}</tbody>
    </table>
    """

    open_positions_html = _open_positions_section(profile)

    now_str = datetime.now(_ET).strftime("%Y-%m-%d %H:%M:%S")

    # Mode badge -- amber "PAPER" vs red "LIVE" (red signals real-money caution, matching the
    # dashboard's existing status coloring). The whole point of this flag is to never confuse
    # a live-money view for a simulated one, so the banner and <title> both carry the mode.
    if mode == "live":
        badge_color, badge_text, title_suffix = BAD, "LIVE — Real Money", "Live"
    else:
        badge_color, badge_text, title_suffix = WARN, "PAPER — Simulated", "Paper"
    mode_badge = (
        f'<span style="background:{badge_color};color:{BG};font-weight:bold;'
        f'padding:3px 10px;border-radius:4px;font-size:12px;letter-spacing:0.5px;">{badge_text}</span>'
    )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Strategy Test Dashboard — {title_suffix}</title>
<style>
:root{{--pos:{GOOD};--neg:{BAD};--accent:{ACCENT};--warn:{WARN};--muted:{MUTED}}}
body{{background:{BG};color:{FG};font-family:monospace;padding:20px;margin:0}}
.card{{background:{PANEL_BG};border:1px solid {GRID};border-radius:6px;padding:14px;margin-bottom:14px}}
.card h2{{margin:0 0 8px 0;font-size:13px;color:{FG}}}
.muted{{color:{MUTED}}}
{viz.SECTION_STYLE}
</style></head>
<body>

<div style="display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid {GRID};padding-bottom:10px;margin-bottom:16px;">
  <div style="display:flex;align-items:center;gap:12px;">
    <h1 style="margin:0;font-size:18px;">EarningsAgent -- Strategy Test Dashboard</h1>
    {mode_badge}
  </div>
  <div style="color:{MUTED};font-size:12px;">profile={profile} | last updated {now_str} ET</div>
</div>

<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:20px;">
  <div style="background:{PANEL_BG};border:1px solid {ACCENT};border-radius:6px;padding:12px;">
    <div style="color:{MUTED};font-size:11px;">NET EXPECTANCY / TRADE</div>
    <div style="font-size:22px;color:{GOOD if (exp or 0) >= 0 else BAD};">{viz.fmt_money(exp, none="n/a")}</div>
  </div>
  <div style="background:{PANEL_BG};border:1px solid {GRID};border-radius:6px;padding:12px;">
    <div style="color:{MUTED};font-size:11px;">TOTAL NET P&amp;L</div>
    <div style="font-size:22px;color:{GOOD if net_total >= 0 else BAD};">{viz.fmt_money(net_total)}</div>
  </div>
  <div style="background:{PANEL_BG};border:1px solid {GRID};border-radius:6px;padding:12px;">
    <div style="color:{MUTED};font-size:11px;">CLOSED TRADES</div>
    <div style="font-size:22px;">{total_trades}</div>
  </div>
  <div style="background:{PANEL_BG};border:1px solid {GRID};border-radius:6px;padding:12px;">
    <div style="color:{MUTED};font-size:11px;">STRATEGIES ACTIVE</div>
    <div style="font-size:22px;">{sum(1 for t in per_strategy.values() if t)}/{len(STRATEGY_NAMES)}</div>
  </div>
  <div style="background:{PANEL_BG};border:1px solid {GRID};border-radius:6px;padding:12px;">
    <div style="color:{MUTED};font-size:11px;">CAPITAL BASIS</div>
    <div style="font-size:22px;">${capital_basis:,.0f}</div>
  </div>
</div>

<div style="margin-bottom:20px;">
  <div style="margin-bottom:8px;">
    <button onclick="showTF('cumulative')" style="background:{ACCENT};color:{BG};border:none;padding:4px 10px;margin-right:4px;cursor:pointer;">Cumulative</button>
    <button onclick="showTF('rolling4w')" style="background:{PANEL_BG};color:{FG};border:1px solid {GRID};padding:4px 10px;margin-right:4px;cursor:pointer;">Rolling 4-week</button>
    <button onclick="showTF('rolling1w')" style="background:{PANEL_BG};color:{FG};border:1px solid {GRID};padding:4px 10px;margin-right:4px;cursor:pointer;">Rolling 1-week</button>
    <button onclick="showTF('perweek')" style="background:{PANEL_BG};color:{FG};border:1px solid {GRID};padding:4px 10px;cursor:pointer;">Per-week</button>
  </div>
  <div id="tf-cumulative" class="tf-panel">{tf_panels["cumulative"]}</div>
  <div id="tf-rolling4w" class="tf-panel" style="display:none;">{tf_panels["rolling4w"]}</div>
  <div id="tf-rolling1w" class="tf-panel" style="display:none;">{tf_panels["rolling1w"]}</div>
  <div id="tf-perweek" class="tf-panel" style="display:none;">{tf_panels["perweek"]}</div>
</div>

<h2 style="font-size:15px;border-bottom:1px solid {GRID};padding-bottom:6px;">Open positions</h2>
{open_positions_html}

<h2 style="font-size:15px;border-bottom:1px solid {GRID};padding-bottom:6px;margin-top:20px;">Cross-strategy comparison</h2>
{comparison_table}

<h2 style="font-size:15px;border-bottom:1px solid {GRID};padding-bottom:6px;margin-top:20px;">Regime coverage &amp; rejections</h2>
<div style="display:flex;flex-wrap:wrap;gap:24px;align-items:flex-start;">
  <div><div style="color:{MUTED};font-size:11px;margin-bottom:6px;">Regime coverage (IV/RV x dispersion)</div>{regime_html}</div>
  <div style="flex:1;min-width:320px;"><div style="color:{MUTED};font-size:11px;margin-bottom:6px;">Top rejection reasons (scan_log)</div>{rejection_html}</div>
</div>

<h2 style="font-size:15px;border-bottom:1px solid {GRID};padding-bottom:6px;margin-top:20px;">Per-strategy detail</h2>
{"".join(strategy_cards)}

<div style="color:{MUTED};font-size:11px;margin-top:20px;border-top:1px solid {GRID};padding-top:10px;">
  Caveats: forward-only sample (no historical backfill); trades sharing a symbol/night are
  correlated (one earnings event), not fully independent; paper fills are cost-adjusted but
  still lack real queue position/slippage depth -- expect live drawdown 1.5-2x paper.
  &lt;100 trades isn't statistically significant; &lt;30 isn't even directional. Generate
  tearsheets for end-of-window evaluation, not to fine-tune gates mid-test.
</div>

<script>
function showTF(id) {{
  document.querySelectorAll('.tf-panel').forEach(function(el) {{ el.style.display = 'none'; }});
  document.getElementById('tf-' + id).style.display = 'block';
}}
</script>
<script>{viz.SECTION_JS}</script>

</body></html>
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["live", "paper"],
        default="paper",
        help="'live' reads earnings_trades.db and writes "
        "strategy_dashboard_live.html; 'paper' (default) reads "
        "paper_trades.db and writes strategy_dashboard.html. Both the DBs and the "
        "generated reports live under the cherrypick data home "
        "(~/.cherrypick/data/earnings by default or $EARNINGS_DATA_DIR); the reports "
        "subdir is created on write.",
    )
    parser.add_argument("--db", default=None, help="Overrides the mode-based default DB path.")
    parser.add_argument(
        "--profile",
        default=None,
        help="Book to report on. Defaults to 'strat_test' in paper mode, 'default' in live mode.",
    )
    parser.add_argument("--since", default=None)
    args = parser.parse_args()

    # Point every read (sm.load_closed_trades and the dashboard's own rejection-histogram
    # query) at the mode's DB before building.
    sm.DB_PATH = sm.db_path_for_mode(args.mode, args.db)
    profile = args.profile or ("strat_test" if args.mode == "paper" else "default")

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    html = build_dashboard(profile, args.since, args.mode)
    # Separate output file per mode (the static-file analog of MEICAgent's separate ports),
    # so generating one never clobbers the other's view.
    filename = "strategy_dashboard_live.html" if args.mode == "live" else "strategy_dashboard.html"
    out_path = REPORTS_DIR / filename
    out_path.write_text(html, encoding="utf-8")
    print(f"wrote {out_path}  (mode={args.mode}, db={sm.DB_PATH}, profile={profile})")


if __name__ == "__main__":
    main()
