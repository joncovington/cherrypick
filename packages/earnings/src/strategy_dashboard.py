#!/usr/bin/env python3
"""Self-contained, offline dashboard for the strategy-testing program (see
docs/strategy-testing-plan.md). Renders reports/strategy_dashboard.html --
a single file with matplotlib charts embedded as base64 PNGs, no server, no
CDN/network dependency, so it opens anywhere (respects the project's
cross-machine/no-absolute-paths guardrail). Every number comes from
strategy_metrics.py, the same module strategy_report.py reads, so the two
can never disagree.

Design (see the dashboard-design research in the strategy-testing plan):
dark, dense "Bloomberg" layout for an operator doing multi-strategy
analytical review (not a mobile glance); a 5-KPI header with the primary
decision metric (portfolio net expectancy) top-left; trade-level stats
throughout (each earnings play is a round-trip, not a return-series
period); pass/fail status shown with color AND a glyph, never color alone;
one justified interaction -- a timeframe toggle (Cumulative / Rolling
4-week / Rolling 1-week / Per-week) on the portfolio headline equity
curve, implemented as pre-rendered image sets swapped by inline JS (no
external JS framework, no network).

Usage:
    python strategy_dashboard.py
    python strategy_dashboard.py --since 2026-07-01 --profile strat_test
"""

import argparse
import base64
import io
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

import scanner
import strategy_metrics as sm
from strategy_report import STRATEGY_NAMES

REPORTS_DIR = Path(__file__).resolve().parent.parent / "reports"

# Dark palette -- background/text tuned for a dark page, categorical hues
# chosen for contrast against that background and against each other
# (not relying on hue alone: line styles/markers differ too where it matters).
BG = "#0d1117"
PANEL_BG = "#161b22"
FG = "#e6edf3"
MUTED = "#8b949e"
GRID = "#30363d"
GOOD = "#3fb950"
WARN = "#d29922"
BAD = "#f85149"
ACCENT = "#58a6ff"
CATEGORICAL = ["#58a6ff", "#f0883e", "#a371f7", "#3fb950", "#f85149", "#79c0ff", "#e3b341", "#db61a2", "#56d4dd", "#8b949e"]


def _mpl_dark_style():
    plt.rcParams.update({
        "figure.facecolor": BG,
        "axes.facecolor": PANEL_BG,
        "axes.edgecolor": GRID,
        "axes.labelcolor": FG,
        "text.color": FG,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "grid.color": GRID,
        "font.family": "monospace",
        "font.size": 9,
    })


def _fig_to_base64(fig) -> str:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _img_tag(b64: str, alt: str = "") -> str:
    return f'<img alt="{alt}" src="data:image/png;base64,{b64}" style="max-width:100%;">'


def plot_equity_curve(curve: list[tuple[float, float]], title: str) -> str:
    _mpl_dark_style()
    fig, ax = plt.subplots(figsize=(6.5, 2.6))
    if curve:
        xs = list(range(1, len(curve) + 1))
        ys = [c for _, c in curve]
        ax.plot(xs, ys, color=ACCENT, linewidth=1.5)
        ax.axhline(0, color=MUTED, linewidth=0.6, linestyle="--")
        ax.fill_between(xs, ys, 0, where=[y >= 0 for y in ys], color=GOOD, alpha=0.12)
        ax.fill_between(xs, ys, 0, where=[y < 0 for y in ys], color=BAD, alpha=0.12)
    else:
        ax.text(0.5, 0.5, "no closed trades", ha="center", va="center", color=MUTED, transform=ax.transAxes)
    ax.set_title(title, color=FG, fontsize=10, loc="left")
    ax.set_xlabel("trade #")
    ax.set_ylabel("cum. net P&L ($)")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return _fig_to_base64(fig)


def plot_underwater(curve: list[tuple[float, float]], title: str) -> str:
    _mpl_dark_style()
    fig, ax = plt.subplots(figsize=(6.5, 1.6))
    if curve:
        xs = list(range(1, len(curve) + 1))
        ys = [c for _, c in curve]
        peak = 0.0
        underwater = []
        for y in ys:
            peak = max(peak, y)
            underwater.append(y - peak)
        ax.fill_between(xs, underwater, 0, color=BAD, alpha=0.35)
        ax.plot(xs, underwater, color=BAD, linewidth=1.0)
    else:
        ax.text(0.5, 0.5, "no closed trades", ha="center", va="center", color=MUTED, transform=ax.transAxes)
    ax.set_title(title, color=FG, fontsize=9, loc="left")
    ax.set_ylabel("drawdown ($)")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return _fig_to_base64(fig)


def plot_regime_heatmap(all_buckets: dict[str, dict[str, int]]) -> str:
    """all_buckets: {strategy_name: {bucket_label: count}}"""
    _mpl_dark_style()
    labels = sorted({label for buckets in all_buckets.values() for label in buckets})
    strategies = list(all_buckets.keys())
    if not labels or not strategies:
        fig, ax = plt.subplots(figsize=(8, 2))
        ax.text(0.5, 0.5, "no regime data yet", ha="center", va="center", color=MUTED, transform=ax.transAxes)
        ax.axis("off")
        return _fig_to_base64(fig)

    data = [[all_buckets[s].get(label, 0) for label in labels] for s in strategies]
    fig, ax = plt.subplots(figsize=(max(6, len(labels) * 1.1), max(3, len(strategies) * 0.4)))
    im = ax.imshow(data, cmap="viridis", aspect="auto")
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=35, ha="right", fontsize=7)
    ax.set_yticks(range(len(strategies)))
    ax.set_yticklabels(strategies, fontsize=8)
    for i in range(len(strategies)):
        for j in range(len(labels)):
            v = data[i][j]
            if v:
                ax.text(j, i, str(v), ha="center", va="center", color=BG, fontsize=7, fontweight="bold")
    fig.colorbar(im, ax=ax, shrink=0.7, label="trades")
    ax.set_title("Regime coverage (IV/RV x dispersion)", color=FG, fontsize=10, loc="left")
    fig.tight_layout()
    return _fig_to_base64(fig)


def plot_rejection_histogram(reason_counts: dict[str, int], top_n: int = 15) -> str:
    _mpl_dark_style()
    items = sorted(reason_counts.items(), key=lambda x: -x[1])[:top_n]
    fig, ax = plt.subplots(figsize=(7, max(2.5, len(items) * 0.3)))
    if items:
        labels = [k for k, _ in items][::-1]
        values = [v for _, v in items][::-1]
        ax.barh(labels, values, color=CATEGORICAL[0])
        ax.tick_params(labelsize=7)
    else:
        ax.text(0.5, 0.5, "no rejections logged", ha="center", va="center", color=MUTED, transform=ax.transAxes)
    ax.set_title("Top rejection reasons (scan_log)", color=FG, fontsize=10, loc="left")
    ax.grid(True, axis="x", alpha=0.3)
    fig.tight_layout()
    return _fig_to_base64(fig)


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
        ("Win rate", f"{wr*100:.1f}%" if wr is not None else "n/a", ""),
        ("Profit factor", f"{pf['value']:.2f}" if pf["value"] not in (None,) else "n/a", _status_span(pf["pass"])),
        ("Expectancy (net)", f"${exp['value']:,.2f}" if exp["value"] is not None else "n/a", _status_span(exp["pass"])),
        ("Sharpe (trade)", f"{sh['value']:.2f}" if sh["value"] is not None else "n/a", _status_span(sh["pass"])),
        ("Max drawdown", f"${mdd['absolute']:,.2f} ({mdd['pct']*100:.1f}%)", _status_span(core_five["max_drawdown"]["pass"])),
    ]
    if iv_crush is not None:
        if iv_crush["avg_crush"] is not None:
            label = "Avg IV crush" if iv_crush["avg_crush"] >= 0 else "Avg IV expansion"
            value = f"{abs(iv_crush['avg_crush'])*100:.1f} vol pts (n={iv_crush['sample_count']})"
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


def _portfolio_curve_for_window(trades: list[dict], days: int | None) -> list[tuple[float, float]]:
    if days is None:
        return sm.equity_curve(trades)
    cutoff = (datetime.now() - timedelta(days=days)).timestamp()
    windowed = [t for t in trades if (t.get("closed_at") or 0) >= cutoff]
    return sm.equity_curve(windowed)


def _weekly_pnl_chart(trades: list[dict]) -> str:
    _mpl_dark_style()
    weekly: dict[str, float] = {}
    for t in trades:
        if not t.get("closed_at"):
            continue
        d = datetime.fromtimestamp(t["closed_at"]).date()
        iso_year, iso_week, _ = d.isocalendar()
        key = f"{iso_year}-W{iso_week:02d}"
        weekly[key] = weekly.get(key, 0.0) + sm.net_pnl(t)

    fig, ax = plt.subplots(figsize=(6.5, 2.6))
    if weekly:
        keys = sorted(weekly)
        values = [weekly[k] for k in keys]
        colors = [GOOD if v >= 0 else BAD for v in values]
        ax.bar(keys, values, color=colors)
        ax.tick_params(axis="x", rotation=45, labelsize=7)
        ax.axhline(0, color=MUTED, linewidth=0.6)
    else:
        ax.text(0.5, 0.5, "no closed trades", ha="center", va="center", color=MUTED, transform=ax.transAxes)
    ax.set_title("Per-week net P&L (portfolio)", color=FG, fontsize=10, loc="left")
    ax.grid(True, axis="y", alpha=0.3)
    fig.tight_layout()
    return _fig_to_base64(fig)


def build_dashboard(profile: str, since: str | None, mode: str = "paper") -> str:
    config = scanner._load_config()
    capital_basis = config.get("available_capital_paper_mode")

    all_trades = sm.load_closed_trades(profile=profile, since=since)
    per_strategy = {name: sm.load_closed_trades(profile=profile, strategy=name, since=since) for name in STRATEGY_NAMES}

    # --- Header KPIs (5, decision metric top-left) ---
    portfolio_summary = sm.strategy_summary(all_trades, capital_basis)
    net_total = sum(sm.net_pnl(t) for t in all_trades)
    total_trades = len(all_trades)
    exp = portfolio_summary["core_five"]["expectancy"]["value"]

    # --- Timeframe panels (portfolio headline equity curve) ---
    timeframes = {
        "cumulative": (None, "Cumulative"),
        "rolling4w": (28, "Rolling 4-week"),
        "rolling1w": (7, "Rolling 1-week"),
    }
    tf_images = {}
    for key, (days, label) in timeframes.items():
        curve = _portfolio_curve_for_window(all_trades, days)
        tf_images[key] = plot_equity_curve(curve, f"Portfolio net P&L -- {label}")
    tf_images["perweek"] = _weekly_pnl_chart(all_trades)

    # --- Regime heatmap + rejection histogram ---
    all_buckets = {name: sm.regime_buckets(trades) for name, trades in per_strategy.items() if trades}
    regime_img = plot_regime_heatmap(all_buckets)

    reason_counts: dict[str, int] = {}
    try:
        import sqlite3
        conn = sqlite3.connect(sm.DB_PATH)
        rows = conn.execute(
            "SELECT reason FROM scan_log WHERE profile = ? AND reason IS NOT NULL", (profile,)
        ).fetchall()
        conn.close()
        for (reason,) in rows:
            for part in reason.split(";"):
                part = part.strip()
                if part:
                    reason_counts[part] = reason_counts.get(part, 0) + 1
    except Exception:
        pass
    rejection_img = plot_rejection_histogram(reason_counts)

    # --- Per-strategy cards ---
    strategy_cards = []
    comparison_rows = []
    for name in STRATEGY_NAMES:
        trades = per_strategy[name]
        summary = sm.strategy_summary(trades, capital_basis)
        curve_img = plot_equity_curve(summary["equity_curve"], f"{name} -- equity curve")
        underwater_img = plot_underwater(summary["equity_curve"], f"{name} -- underwater")
        metrics_html = _metrics_table_html(summary["core_five"], summary["iv_crush"])
        sample_html = _sample_bar(summary["sample"])

        strategy_cards.append(f"""
        <div style="background:{PANEL_BG};border:1px solid {GRID};border-radius:6px;padding:14px;margin-bottom:14px;">
          <h3 style="margin:0 0 6px 0;color:{FG};">{name}</h3>
          <div style="margin-bottom:8px;">{sample_html}</div>
          <div style="display:flex;flex-wrap:wrap;gap:16px;">
            <div>{_img_tag(curve_img, name)}{_img_tag(underwater_img, name)}</div>
            <div>{metrics_html}</div>
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
            f'<td style="padding:3px 10px;text-align:right;">{f"{wr*100:.1f}%" if wr is not None else "n/a"}</td>'
            f'<td style="padding:3px 10px;text-align:right;">{f"{pf:.2f}" if pf not in (None,) else "n/a"}</td>'
            f'<td style="padding:3px 10px;text-align:right;">{f"${exp_v:,.2f}" if exp_v is not None else "n/a"}</td>'
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

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
<html><head><meta charset="utf-8"><title>Strategy Test Dashboard — {title_suffix}</title></head>
<body style="background:{BG};color:{FG};font-family:monospace;padding:20px;">

<div style="display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid {GRID};padding-bottom:10px;margin-bottom:16px;">
  <div style="display:flex;align-items:center;gap:12px;">
    <h1 style="margin:0;font-size:18px;">EarningsAgent -- Strategy Test Dashboard</h1>
    {mode_badge}
  </div>
  <div style="color:{MUTED};font-size:12px;">profile={profile} | last updated {now_str}</div>
</div>

<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:20px;">
  <div style="background:{PANEL_BG};border:1px solid {ACCENT};border-radius:6px;padding:12px;">
    <div style="color:{MUTED};font-size:11px;">NET EXPECTANCY / TRADE</div>
    <div style="font-size:22px;color:{GOOD if (exp or 0) >= 0 else BAD};">{f"${exp:,.2f}" if exp is not None else "n/a"}</div>
  </div>
  <div style="background:{PANEL_BG};border:1px solid {GRID};border-radius:6px;padding:12px;">
    <div style="color:{MUTED};font-size:11px;">TOTAL NET P&amp;L</div>
    <div style="font-size:22px;color:{GOOD if net_total >= 0 else BAD};">${net_total:,.2f}</div>
  </div>
  <div style="background:{PANEL_BG};border:1px solid {GRID};border-radius:6px;padding:12px;">
    <div style="color:{MUTED};font-size:11px;">CLOSED TRADES</div>
    <div style="font-size:22px;">{total_trades}</div>
  </div>
  <div style="background:{PANEL_BG};border:1px solid {GRID};border-radius:6px;padding:12px;">
    <div style="color:{MUTED};font-size:11px;">STRATEGIES ACTIVE</div>
    <div style="font-size:22px;">{sum(1 for t in per_strategy.values() if t)}/10</div>
  </div>
  <div style="background:{PANEL_BG};border:1px solid {GRID};border-radius:6px;padding:12px;">
    <div style="color:{MUTED};font-size:11px;">CAPITAL BASIS</div>
    <div style="font-size:22px;">${capital_basis:,.0f}</div>
  </div>
</div>

<div style="background:{PANEL_BG};border:1px solid {GRID};border-radius:6px;padding:14px;margin-bottom:20px;">
  <div style="margin-bottom:8px;">
    <button onclick="showTF('cumulative')" style="background:{ACCENT};color:{BG};border:none;padding:4px 10px;margin-right:4px;cursor:pointer;">Cumulative</button>
    <button onclick="showTF('rolling4w')" style="background:{PANEL_BG};color:{FG};border:1px solid {GRID};padding:4px 10px;margin-right:4px;cursor:pointer;">Rolling 4-week</button>
    <button onclick="showTF('rolling1w')" style="background:{PANEL_BG};color:{FG};border:1px solid {GRID};padding:4px 10px;margin-right:4px;cursor:pointer;">Rolling 1-week</button>
    <button onclick="showTF('perweek')" style="background:{PANEL_BG};color:{FG};border:1px solid {GRID};padding:4px 10px;cursor:pointer;">Per-week</button>
  </div>
  <div id="tf-cumulative" class="tf-panel">{_img_tag(tf_images['cumulative'])}</div>
  <div id="tf-rolling4w" class="tf-panel" style="display:none;">{_img_tag(tf_images['rolling4w'])}</div>
  <div id="tf-rolling1w" class="tf-panel" style="display:none;">{_img_tag(tf_images['rolling1w'])}</div>
  <div id="tf-perweek" class="tf-panel" style="display:none;">{_img_tag(tf_images['perweek'])}</div>
</div>

<h2 style="font-size:15px;border-bottom:1px solid {GRID};padding-bottom:6px;">Cross-strategy comparison</h2>
{comparison_table}

<h2 style="font-size:15px;border-bottom:1px solid {GRID};padding-bottom:6px;margin-top:20px;">Regime coverage &amp; rejections</h2>
<div style="display:flex;flex-wrap:wrap;gap:16px;">
  <div>{_img_tag(regime_img)}</div>
  <div>{_img_tag(rejection_img)}</div>
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

</body></html>
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["live", "paper"], default="paper",
                        help="'live' reads data/earnings_trades.db and writes "
                             "reports/strategy_dashboard_live.html; 'paper' (default) reads "
                             "data/paper_trades.db and writes reports/strategy_dashboard.html.")
    parser.add_argument("--db", default=None, help="Overrides the mode-based default DB path.")
    parser.add_argument("--profile", default=None,
                        help="Book to report on. Defaults to 'strat_test' in paper mode, "
                             "'default' in live mode.")
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
