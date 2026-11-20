"""The ladder's live readiness: a list of named gates, each with a verdict and a plain reason.

Pure. Every input is passed in (the config, the suite-config switch, the arm record's date, the
designated account, the halt flag, the clock), so nothing here reads a keyring, a file or a broker.
There is no order path in this module or beside it yet: these gates say whether a future loop may
act, and `live_loop` shows them.

Two kinds of gate, told apart by `blocks`:

- `all`: while it fails the loop may do nothing on the account.
- `new_buys`: while it fails the loop may place no new buy but still manages what is held (the halt
  flag). Held lots keep their resting sells either way.

The first three gates (`CONFIG_GATES`) are the ones `--arm` checks before it writes the day's record.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from cherrypick.core import live as _live
from cherrypick.core.clock import hhmm_to_min
from cherrypick.core.redact import mask_account

CONFIG_GATES = ("live.enabled", "live.gate0_confirmed", "modules.ladder.enabled")

DEFAULT_ARM = "session"
DEFAULT_DISARM_AT = "20:00"
DEFAULT_SESSION_START = "08:15"


@dataclass(frozen=True)
class Gate:
    name: str
    ok: bool
    reason: str
    blocks: str = "all"  # "all" or "new_buys"

    def as_dict(self) -> dict:
        return asdict(self)


def live_block(config: dict) -> dict:
    block = (config or {}).get("live")
    return block if isinstance(block, dict) else {}


def _is_number(value) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _is_count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _hhmm(value) -> int | None:
    """Minutes into the day for a strict `HH:MM`, else None."""
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        return None
    hh, mm = value[:2], value[3:]
    if not (hh.isdigit() and mm.isdigit()) or int(hh) > 23 or int(mm) > 59:
        return None
    return int(hh) * 60 + int(mm)


def attested(value) -> bool:
    """`gate0_confirmed` carries a human attestation: a non-empty string (who/when, as the suite's
    other live blocks write it) or an explicit true. Anything else, `false` included, is not."""
    if value is True:
        return True
    return isinstance(value, str) and value.strip() != ""


def limit_problems(live: dict) -> list[str]:
    """Every way the numeric limits and the clock settings are invalid; empty means valid."""
    bad: list[str] = []
    lot = live.get("lot_dollars")
    per_fund = live.get("max_open_lots")
    total = live.get("max_open_lots_total")
    ceiling = live.get("max_open_dollars")
    if not (_is_number(lot) and lot > 0):
        bad.append("lot_dollars must be a positive number")
    if not _is_count(per_fund):
        bad.append("max_open_lots must be a whole number of at least 1")
    if not _is_count(total):
        bad.append("max_open_lots_total must be a whole number of at least 1")
    elif _is_count(per_fund) and total < per_fund:
        bad.append("max_open_lots_total must be at least max_open_lots")
    if not (_is_number(ceiling) and ceiling > 0):
        bad.append("max_open_dollars must be a positive number")
    elif _is_number(lot) and lot > 0 and ceiling < lot:
        bad.append("max_open_dollars must cover at least one lot (lot_dollars)")
    if not _is_count(live.get("max_open_lots_per_group")):
        bad.append("max_open_lots_per_group must be a whole number of at least 1")
    frac = live.get("drawdown_breaker_frac")
    if not (_is_number(frac) and 0 < frac <= 1):
        bad.append("drawdown_breaker_frac must be a number above 0 and at most 1")
    if not _is_count(live.get("hospital_alert_days")):
        bad.append("hospital_alert_days must be a whole number of at least 1")
    start = _hhmm(live.get("session_start"))
    end = _hhmm(live.get("disarm_at"))
    if start is None:
        bad.append("session_start must be HH:MM")
    if end is None:
        bad.append("disarm_at must be HH:MM")
    if start is not None and end is not None and end <= start:
        bad.append("disarm_at must be after session_start")
    groups = live.get("correlated_groups")
    if not isinstance(groups, dict):
        bad.append("correlated_groups must be an object of group name to fund list")
    else:
        for name, members in groups.items():
            if str(name).startswith("_"):
                continue
            if not (
                isinstance(members, list)
                and members
                and all(isinstance(m, str) and m.strip() for m in members)
            ):
                bad.append(f"correlated_groups.{name} must be a non-empty list of symbols")
    return bad


def disarm_minute(config: dict) -> int:
    return hhmm_to_min(live_block(config).get("disarm_at") or DEFAULT_DISARM_AT, 20 * 60)


def _add(gates: list[Gate], name: str, ok: bool, ok_reason: str, bad_reason: str, blocks: str = "all"):
    gates.append(Gate(name, bool(ok), ok_reason if ok else bad_reason, blocks))


def config_gates(config: dict, *, module_enabled: bool) -> list[Gate]:
    """The three `CONFIG_GATES`: the ones `--arm` checks before it writes the day's record."""
    live = live_block(config)
    gates: list[Gate] = []
    _add(gates, "live.enabled", live.get("enabled") is True, "on", "live.enabled is not true")
    _add(
        gates,
        "live.gate0_confirmed",
        attested(live.get("gate0_confirmed")),
        "a human attestation is on file",
        "live.gate0_confirmed is empty: a human must attest the gate (who and when)",
    )
    _add(
        gates,
        "modules.ladder.enabled",
        module_enabled,
        "the module is switched on in the suite config",
        "modules.ladder.enabled is false in the suite config",
    )
    return gates


def evaluate(
    config: dict,
    *,
    module_enabled: bool,
    armed_for: str | None,
    today: str,
    now_min: int,
    designated: str | None,
    halted: bool,
    arm_names: list[str] | tuple[str, ...],
    fund_names: list[str] | tuple[str, ...],
) -> list[Gate]:
    """Every gate, in a fixed order, each with its verdict and a plain reason.

    `arm_names` are the enabled paper arms and `fund_names` the funds the ladder trades, as
    `paper_loop.arms` and `stream_request.names` give them. A designated account is checked for
    presence and shown masked."""
    live = live_block(config)
    gates = config_gates(config, module_enabled=module_enabled)

    def add(name: str, ok: bool, ok_reason: str, bad_reason: str, blocks: str = "all") -> None:
        _add(gates, name, ok, ok_reason, bad_reason, blocks)

    why = _live.should_disarm(
        armed_for,
        today=today,
        now_min=now_min,
        disarm_min=disarm_minute(config),
        disarm_label=str(live.get("disarm_at") or DEFAULT_DISARM_AT),
    )
    add("arm.record", why is None, f"armed for {today}", why or "")
    add(
        "account.designated",
        bool(designated and str(designated).strip()),
        f"a designated account is set ({mask_account(designated)})",
        "no designated account: set one with `cherrypick account --module ladder --set <last4>`",
    )
    add(
        "halt.flag",
        not halted,
        "no halt flag",
        "the halt flag is set: no new buy; held lots keep their resting sells",
        blocks="new_buys",
    )
    arm = str(live.get("arm") or "").strip()
    add(
        "live.arm",
        arm in arm_names,
        f"{arm!r} is an enabled paper arm",
        f"live.arm {arm!r} is not an enabled paper arm (have: {', '.join(arm_names) or 'none'})",
    )
    symbols = live.get("symbols")
    listed = [str(s).strip().upper() for s in symbols] if isinstance(symbols, list) else []
    funds = {str(f).upper() for f in fund_names}
    unknown = [s for s in listed if s not in funds]
    if not isinstance(symbols, list) or not listed:
        add("live.symbols", False, "", "live.symbols must be a non-empty list of funds")
    elif unknown:
        add("live.symbols", False, "", f"not in the ladder's fund list: {', '.join(unknown)}")
    else:
        add("live.symbols", True, f"{', '.join(listed)}", "")
    bad = limit_problems(live)
    add("live.limits", not bad, "lot, caps, breaker and hours are valid", "; ".join(bad))
    return gates


def unmet(gates: list[Gate]) -> list[Gate]:
    return [g for g in gates if not g.ok]


def ready(gates: list[Gate]) -> bool:
    """The loop may act on the account at all: every gate that blocks everything holds. The halt
    flag does not count here."""
    return all(g.ok for g in gates if g.blocks == "all")


def new_buys_allowed(gates: list[Gate]) -> bool:
    return all(g.ok for g in gates)


def config_unmet(config: dict, *, module_enabled: bool) -> list[Gate]:
    """The `CONFIG_GATES` that fail: what `--arm` refuses on."""
    return unmet(config_gates(config, module_enabled=module_enabled))
