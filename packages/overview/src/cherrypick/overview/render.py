"""Render one morning fact pack as markdown. Renders, never computes.

Every number here is read from the pack; where the pack says null the render prints an em dash,
because an unmeasured value is a fact about the morning worth showing, not a hole to paper over.
Prior-session values are labeled prior -- the same "prior confirmed" honesty the reference reports
practice pre-open.
"""

from __future__ import annotations

from . import facts as _facts
from . import paths as _paths

DASH = "—"

_PHASE_LABEL = {"green": "GREEN", "yellow": "YELLOW", "red": "RED"}
_STATUS_LABEL = {"met": "Met", "not_met": "Not met", "unknown": "Unknown"}
_ZONE_LABEL = {"full": "FULL DEPLOY", "reduced": "REDUCED", "defensive": "DEFENSIVE"}


def _value(reading: dict | None, fmt: str = "{:,.2f}") -> str:
    reading = reading or {}
    value = reading.get("value")
    if not isinstance(value, (int, float)):
        return DASH
    return fmt.format(value)


def _basis(reading: dict | None) -> str:
    reading = reading or {}
    if reading.get("basis") == "live":
        return "live pre-open"
    if reading.get("basis") == "prior":
        return f"prior ({reading.get('session') or 'unknown session'})"
    return "not measured"


def _pct(value) -> str:
    return f"{value:+.2f}%" if isinstance(value, (int, float)) else DASH


def render(session: str) -> str | None:
    pack = _facts.read(session)
    if not pack:
        return None

    readings = pack.get("readings") or {}
    levels = pack.get("levels") or {}
    sectors = pack.get("sectors") or {}
    phase = pack.get("phase") or {}
    cal = pack.get("calendar") or {}

    lines: list[str] = []
    lines.append(f"# Morning Overview — {session}")
    lines.append("")
    lines.append(
        f"**FRAMEWORK PHASE: {_PHASE_LABEL.get(phase.get('phase'), DASH)}** — {phase.get('reason', DASH)}"
    )
    lines.append(
        f"Gates: {phase.get('gates_met', DASH)} met of "
        f"{phase.get('gates_measured', DASH)} measured "
        f"({phase.get('gates_total', DASH)} declared)"
    )
    lines.append("")

    lines.append("## Scorecard")
    lines.append("")
    lines.append("| Reading | Value | Basis |")
    lines.append("|---|---|---|")
    for key, fmt in (
        ("spx", "{:,.2f}"),
        ("vix", "{:.2f}"),
        ("vix3m", "{:.2f}"),
        ("vvix", "{:.2f}"),
        ("wti_proxy", "{:.2f}"),
        ("gold_proxy", "{:.2f}"),
    ):
        reading = readings.get(key) or {}
        lines.append(f"| {reading.get('label', key)} | {_value(reading, fmt)} | {_basis(reading)} |")
    change = (readings.get("spx_prior_change_pct") or {}).get("value")
    lines.append(f"| SPX prior-session change | {_pct(change)} | prior |")
    lines.append("")

    lines.append("## Gamma levels (own GEX engine)")
    lines.append("")
    lines.append("| Level | Value |")
    lines.append("|---|---|")
    for key, label in (("zero_gamma", "Gamma flip"), ("call_wall", "Call wall"), ("put_wall", "Put wall")):
        value = levels.get(key)
        text = f"{value:,.2f}" if isinstance(value, (int, float)) else DASH
        lines.append(f"| {label} | {text} |")
    ref = levels.get("reference_price")
    ref_text = f"{ref:,.2f}" if isinstance(ref, (int, float)) else DASH
    lines.append(f"| Reference price | {ref_text} ({levels.get('reference_basis') or 'not measured'}) |")
    lines.append("")
    lines.append(
        f"_Levels as of {levels.get('session') or DASH} — the last confirmed "
        f"recording, which pre-open means the prior session._"
    )
    lines.append("")

    lines.append("## Gate checklist")
    lines.append("")
    for gate in pack.get("gates") or []:
        status = _STATUS_LABEL.get(gate.get("status"), "Unknown")
        lines.append(f"- **{status}** — {gate.get('label', DASH)}: {gate.get('detail', DASH)}")
    lines.append("")

    deployment = pack.get("deployment") or {}
    if deployment:
        lines.append("## Deployment score (record-only)")
        lines.append("")
        score, zone = deployment.get("score"), deployment.get("zone")
        if isinstance(score, (int, float)):
            lines.append(
                f"**{score:.1f} / 100 — {_ZONE_LABEL.get(zone, DASH)}**"
                + (
                    " _(weights renormalized over measured signals)_"
                    if deployment.get("weights_renormalized")
                    else ""
                )
            )
        else:
            lines.append(f"**No score** — {deployment.get('reason', DASH)}")
        lines.append("")
        lines.append("| Signal | Score | Weight | Detail |")
        lines.append("|---|---|---|---|")
        for signal in deployment.get("signals") or []:
            value = signal.get("score")
            text = f"{value:.1f}" if isinstance(value, (int, float)) else DASH
            lines.append(
                f"| {signal.get('label', DASH)} | {text} | "
                f"{signal.get('weight', 0) * 100:.0f}% | {signal.get('detail', DASH)} |"
            )
        lines.append("")
        deferred = deployment.get("deferred") or []
        lines.append(
            f"_{deployment.get('signals_measured', DASH)} of "
            f"{deployment.get('signals_total', DASH)} signals measured"
            + (f"; deferred: {', '.join(deferred)}" if deferred else "")
            + f". {deployment.get('note', '')}_"
        )
        lines.append("")

    rr = (pack.get("vol_regime") or {}).get("risk_reversal_25d_30d")
    if rr:
        lines.append(
            f"_30-day 25-delta risk reversal ({rr.get('session')} close): "
            f"**{rr.get('rr_vol_pts'):+.2f} vol pts** "
            f"(call {rr.get('call_25d_iv_pct')}% vs put {rr.get('put_25d_iv_pct')}%) — near-the-money skew, "
            "not the SKEW index._"
        )
        lines.append("")

    moves = pack.get("moves") or {}
    if moves:
        lines.append("## Expected and realized moves")
        lines.append("")
        wk = moves.get("weekly_expected_move") or {}
        if isinstance(wk.get("pct"), (int, float)):
            pts = f" (±{wk['points']:.0f} SPX points)" if isinstance(wk.get("points"), (int, float)) else ""
            lines.append(
                f"- Weekly expected move: **±{wk['pct']:.2f}%**{pts}, VIX {wk.get('vix')} "
                f"({wk.get('basis') or 'unknown basis'}) ÷ √52 — a one-sigma band, not a bound"
            )
        else:
            lines.append(f"- Weekly expected move: not measured ({wk.get('reason', DASH)})")
        rv = moves.get("realized_vol") or {}
        if isinstance(rv.get("pct"), (int, float)):
            gap = moves.get("vix_minus_realized")
            gap_text = f"; VIX {gap:+.2f} points over it" if isinstance(gap, (int, float)) else ""
            lines.append(
                f"- SPX realized volatility, {rv.get('sessions')} sessions to {rv.get('through')}: "
                f"**{rv['pct']:.2f}%**{gap_text}"
            )
        else:
            lines.append(f"- SPX realized volatility: not measured ({rv.get('reason', DASH)})")
        lines.append("")

    yields = pack.get("yields") or {}
    if yields:
        lines.append(f"## Treasury yields ({yields.get('session') or 'not measured'})")
        lines.append("")
        if yields.get("session"):
            ys, ch = yields.get("yields") or {}, yields.get("change_bp") or {}
            lines.append("| Tenor | Yield | 1-day change |")
            lines.append("|---|---|---|")
            for tenor in ("3m", "2y", "5y", "10y", "30y"):
                if tenor in ys:
                    c = ch.get(tenor)
                    lines.append(
                        f"| {tenor} | {ys[tenor]:.2f}% | {f'{c:+d} bp' if isinstance(c, int) else DASH} |"
                    )
            lines.append("")
            lines.append(
                f"2s10s {yields.get('spread_2s10s_bp', DASH)} bp · "
                f"3m10y {yields.get('spread_3m10y_bp', DASH)} bp _(Treasury par curve, prior session)_"
            )
        else:
            lines.append(f"Not measured ({yields.get('reason', DASH)}).")
        lines.append("")

    lines.append("## Sector board (prior session)")
    lines.append("")
    strongest, weakest = sectors.get("strongest"), sectors.get("weakest")
    if strongest and weakest:
        lines.append(
            f"Strongest: **{strongest['sector']}** ({_pct(strongest.get('change_pct'))}) "
            f"· Weakest: **{weakest['sector']}** ({_pct(weakest.get('change_pct'))})"
        )
    else:
        lines.append(
            f"Sector board not measured ({sectors.get('measured', 0)} of 11 sectors had prior-session data)."
        )
    lines.append("")
    board = sectors.get("board") or []
    if any(isinstance(s.get("change_pct"), (int, float)) for s in board):
        lines.append("| Sector | ETF | Prior change |")
        lines.append("|---|---|---|")
        ranked = sorted(board, key=lambda s: (s.get("change_pct") is None, -(s.get("change_pct") or 0)))
        for s in ranked:
            lines.append(f"| {s['sector']} | {s['symbol']} | {_pct(s.get('change_pct'))} |")
        lines.append("")

    lines.append("## Calendar")
    lines.append("")
    fomc = cal.get("is_fomc_day")
    fomc_text = (
        "FOMC decision day"
        if fomc
        else "not an FOMC day"
        if fomc is False
        else "FOMC calendar unknown for this year"
    )
    lines.append(
        f"- Today: {fomc_text}"
        + (" · triple witching" if cal.get("is_triple_witching") else "")
        + (" · quarterly expiry" if cal.get("is_quarterly_expiry") else "")
    )
    if cal.get("next_fomc"):
        lines.append(f"- Next FOMC: {cal['next_fomc']}")
    lines.append(f"- Next trading day: {cal.get('next_trading_day', DASH)}")
    releases = cal.get("releases")
    if releases:
        lines.append("- Scheduled releases, next seven days (no consensus estimates):")
        for r in releases:
            when = f"{r['date']} {r['time_et']} ET" if r.get("time_et") else r["date"]
            lines.append(
                f"  - {'**today** ' if r.get('today') else ''}{when} — {r.get('name')} ({r.get('source')})"
            )
    elif releases == []:
        lines.append("- Scheduled releases, next seven days: none on file")
    lines.append("")

    lines.append("---")
    lines.append(
        f"_Rendered from `morning-{session}.json` (fact pack v{pack.get('fact_version')}). "
        f"Generated {pack.get('generated_at', DASH)}. The pack is the record; this render "
        f"recomputes nothing._"
    )
    lines.append("")
    return "\n".join(lines)


def write(session: str) -> str | None:
    text = render(session)
    if text is None:
        return None
    path = _paths.render_path(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return str(path)
