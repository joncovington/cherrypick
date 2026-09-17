"""cherrypick.core.advice — bounded, expiring, deterministically-validated parameter advice.

The suite's contract for letting an AI advisor influence a paper loop WITHOUT touching the
loop-decision guardrail: the advisor runs out-of-band and proposes parameter values; a
deterministic validator — this module — admits them against a per-module ``advice_bounds``
manifest of closed legal ranges before any loop reads them. The orchestrator validates before
writing the artifact, and the loop re-validates with this same code at session start, so a
disagreement between the two sides is impossible by construction.

The failure posture is the whole point:

- **Absent, stale, expired, or invalid ⇒ baseline.** A loop that finds no admissible advice
  behaves exactly as it does today. Advice can only ever *narrow* into declared ranges, never
  widen behavior.
- **Reject-all on any violation.** One out-of-bounds proposal invalidates the whole artifact
  (empty proposals, every rejection recorded). Partial admission would let an advisor smuggle
  an aggressive value behind innocuous ones and still land the rest.
- **Single-session, never sticky.** The artifact names the one session it is for, and carries
  an ``expires_at`` on top; both must hold at read time.

Artifact (JSON, written by the orchestrator only — the advisor itself never gets a file handle):

    {
      "module": "meic",
      "session": "2026-07-29",
      "generated_at": "...iso...",
      "advisor": "claude -p / eod-advise-v1",
      "expires_at": "...iso...",
      "experiments": [                                  # one entry per CONCURRENT experiment
        {"experiment_id": "exp-2026-09-09-meic-1",
         "name": "entry-window-truncate-vs-control",   # unique per module; names the book
         "tag": "advised:entry-window-truncate-vs-control",
         "base": "control",                             # the book the advised twin shadows
         "proposals": [{"param": "entry_window_end", "value": "13:30", "rationale": "..."}],
         "rejected":  []}
      ],
      "experiment_id": "...", "proposals": [...], "rejected": [...]   # mirror of experiments[0]
    }

**Many experiments per module, each its own book (2026-09-17).** Until then an artifact carried
ONE flat `proposals` list and every module built exactly one `advised:<base>` book from it, so a
second experiment on the same base had nowhere to be measured and queued behind the first. Now
`experiments` carries one entry per active experiment, each validated on its own (one entry's
violation rejects THAT entry, never its neighbours -- reject-all is per overlay, because an overlay
is the unit an advisor can smuggle a value inside), and each names its own book,
`advised:<experiment name>`, so two experiments on one base run side by side as two twins of the
same control. The top-level `proposals` / `rejected` / `experiment_id` mirror the first entry so an
artifact stays readable by anything written against the old shape; an artifact WITHOUT
`experiments` (every one written before this date) is read as a single legacy entry whose tag the
consumer resolves to `advised:<base>`, which is what those rows were tagged.

Bounds manifest (config, per module):

    {"stop_trigger_ratio": {"min": 0.85, "max": 0.95},       # closed numeric range
     "entry_price_strategy": {"choices": ["mid", "auto"]}}   # enumerated membership

Pure stdlib, no network, no broker.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ADVICE_DIR = "advice"


def advice_path(state_dir: Path | str, module: str, session: str) -> Path:
    """`<state_dir>/advice/<module>-<session>.json` — one artifact per (module, session)."""
    return Path(state_dir) / ADVICE_DIR / f"{module}-{session}.json"


def _check_proposal(p: Any, bounds: dict[str, Any]) -> str | None:
    """None if admissible, else the rejection reason."""
    if not isinstance(p, dict):
        return "proposal is not an object"
    param = p.get("param")
    if not isinstance(param, str) or not param:
        return "missing param name"
    rule = bounds.get(param)
    if rule is None:
        return f"param {param!r} not in advice_bounds"
    if not isinstance(rule, dict):
        # A rule that is a bare number or a list is a config mistake, and it must read as one --
        # not raise out of the validator and, on the producer side, abort every other module's
        # issuance for the night (2026-09-12).
        return f"{param!r} bounds rule is malformed (expected {{min, max}} or {{choices}})"
    value = p.get("value")
    if "choices" in rule:
        if value not in rule["choices"]:
            return f"{param!r} value {value!r} not in declared choices"
        return None
    # Closed numeric range. bool is an int subclass, but True is not the number 1 here.
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return f"{param!r} value {value!r} is not numeric"
    lo, hi = rule.get("min"), rule.get("max")
    if lo is None or hi is None:
        return f"{param!r} bounds rule declares no closed range"
    if not (lo <= value <= hi):
        return f"{param!r} value {value!r} outside [{lo}, {hi}]"
    return None


def params_map(proposals: Any) -> dict[str, Any]:
    """`{param: value}` for a proposals list, skipping anything that is not a proposal object --
    the one place this shape is turned into a map (it was four; 2026-09-12)."""
    return {p["param"]: p["value"] for p in proposals or [] if isinstance(p, dict) and "param" in p}


def validate_proposals(proposals: Any, bounds: dict[str, Any]) -> dict[str, Any]:
    """Admission check for ONE overlay -- one experiment's proposals list -- against the bounds.
    Returns {"ok", "reason", "proposals", "rejected"}; ok False always means empty proposals
    (reject-all, per overlay)."""

    def reject_all(reason: str, rejected: list | None = None) -> dict[str, Any]:
        return {"ok": False, "reason": reason, "proposals": [], "rejected": rejected or []}

    if not isinstance(proposals, list):
        return reject_all("proposals is not a list")

    seen: set[str] = set()
    rejected: list[dict[str, Any]] = []
    admitted: list[dict[str, Any]] = []
    for p in proposals:
        reason = _check_proposal(p, bounds)
        if reason is None and isinstance(p, dict):
            if p.get("param") in seen:
                reason = f"duplicate param {p.get('param')!r}"
            else:
                seen.add(p["param"])
        if reason is not None:
            rejected.append(
                {
                    "param": (p or {}).get("param") if isinstance(p, dict) else None,
                    "value": (p or {}).get("value") if isinstance(p, dict) else p,
                    "reason": reason,
                }
            )
        else:
            admitted.append(
                {"param": p["param"], "value": p["value"], "rationale": str(p.get("rationale") or "")}
            )
    if rejected:
        # One violation rejects the whole overlay: partial admission would let an advisor
        # smuggle an aggressive value behind innocuous ones and still land the rest.
        return reject_all(f"{len(rejected)} proposal(s) violated advice_bounds (reject-all)", rejected)
    return {"ok": True, "reason": None, "proposals": admitted, "rejected": []}


def _experiment_entries(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    """The artifact's experiment entries, or the single legacy entry an old-shape artifact implies.
    A legacy entry has no `name`/`tag`/`base`: the consumer resolves its tag to `advised:<base>`."""
    entries = artifact.get("experiments")
    if isinstance(entries, list) and entries:
        return [e for e in entries if isinstance(e, dict)]
    return [
        {
            "experiment_id": artifact_experiment_id(artifact),
            "name": None,
            "tag": None,
            "base": None,
            "proposals": artifact.get("proposals"),
            "rejected": artifact.get("rejected") or [],
        }
    ]


def validate(
    artifact: Any, bounds: dict[str, Any], session: str, now: datetime | None = None
) -> dict[str, Any]:
    """Deterministic admission check. Returns {"ok", "reason", "proposals", "rejected",
    "experiments"}. The artifact-level checks (an object, this session, not expired) reject
    everything; past them each experiment entry is validated ON ITS OWN, so one experiment's
    out-of-bounds overlay is that experiment's baseline day and nobody else's. The top-level
    "ok"/"proposals"/"rejected"/"reason" mirror the FIRST entry, the shape every reader written
    before 2026-09-17 expects; ok False there still means that entry's proposals are empty."""
    now = now or datetime.now(timezone.utc)

    def reject_all(reason: str, rejected: list | None = None) -> dict[str, Any]:
        return {"ok": False, "reason": reason, "proposals": [], "rejected": rejected or [], "experiments": []}

    if not isinstance(artifact, dict):
        return reject_all("artifact is not an object")
    if artifact.get("session") != session:
        return reject_all(f"artifact session {artifact.get('session')!r} is not {session!r} (never sticky)")
    try:
        expires = datetime.fromisoformat(str(artifact.get("expires_at")))
    except (TypeError, ValueError):
        return reject_all("expires_at missing or unparseable")
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= now:
        return reject_all("advice expired")

    experiments: list[dict[str, Any]] = []
    for entry in _experiment_entries(artifact):
        checked = validate_proposals(entry.get("proposals"), bounds)
        experiments.append(
            {
                "experiment_id": entry.get("experiment_id"),
                "name": entry.get("name"),
                "tag": entry.get("tag"),
                "base": entry.get("base"),
                "ok": checked["ok"],
                "reason": checked["reason"],
                "proposals": checked["proposals"],
                "rejected": checked["rejected"],
            }
        )
    if not experiments:
        return reject_all("artifact names no experiments")
    first = experiments[0]
    return {
        "ok": first["ok"],
        "reason": first["reason"],
        "proposals": first["proposals"],
        "rejected": first["rejected"],
        "experiments": experiments,
    }


_STAMP_EXPERIMENT = re.compile(r"\((exp-[^)]+)\)\s*$")


def artifact_experiment_id(artifact: Any) -> str | None:
    """The experiment an artifact was issued for, or None for one that names none.

    Read from the explicit `experiment_id` field (written since 2026-09-16), falling back to the
    id the advisor has always stamped in parentheses at the end of `advisor` -- so an artifact
    written before the field existed still attributes. This is the identity a module stamps on
    the rows its advised book writes, which is what lets a ledger tell one experiment's rows from
    the next's under the same `advised:<base>` tag."""
    if not isinstance(artifact, dict):
        return None
    explicit = artifact.get("experiment_id")
    if isinstance(explicit, str) and explicit:
        return explicit
    stamp = artifact.get("advisor")
    if isinstance(stamp, str):
        m = _STAMP_EXPERIMENT.search(stamp)
        if m:
            return m.group(1)
    return None


ADVISED_PREFIX = "advised:"

_SLUG_KEEP = re.compile(r"[^a-z0-9]+")


def slug(name: Any) -> str:
    """A book-safe experiment name: lowercase, runs of anything but [a-z0-9] collapsed to one
    dash, trimmed. `Forecast Range (floor probe)` -> `forecast-range-floor-probe`. Every ledger
    keys on the tag and the console renders it, so the tag must never carry a colon (the
    strategy separator earnings uses), whitespace, or a shell-hostile character."""
    return _SLUG_KEEP.sub("-", str(name or "").strip().lower()).strip("-")


def advised_tag(name: str, strategy: str | None = None) -> str:
    """The book an experiment writes to: `advised:<name>`, or `advised:<name>:<strategy>` for a
    per-strategy twin (earnings). ONE rule, here, because five consumers, the advisor's verdicts
    and the console all key on it."""
    tag = f"{ADVISED_PREFIX}{slug(name)}"
    return f"{tag}:{strategy}" if strategy else tag


def is_advised(book: str | None) -> bool:
    return isinstance(book, str) and book.startswith(ADVISED_PREFIX)


def advised_books(decision: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The experiment entries of a session decision that admitted params -- one advised book each:
    `{experiment_id, name, tag, base, params, ...}`. Empty for baseline. A decision recorded before
    the `experiments` list existed is read as its single legacy entry."""
    if not isinstance(decision, dict):
        return []
    entries = decision.get("experiments")
    if not isinstance(entries, list):
        params = decision.get("params")
        if not params:
            return []
        base = _legacy_base(decision)
        entries = [
            {
                "experiment_id": decision.get("experiment_id"),
                "name": None,
                "tag": f"{ADVISED_PREFIX}{base}",
                "base": base,
                "params": params,
            }
        ]
    return [e for e in entries if isinstance(e, dict) and e.get("params")]


def _legacy_base(decision: dict[str, Any]) -> str:
    for key in ("base_arm", "base_profile", "base_book", "base_prefix"):
        if decision.get(key):
            return str(decision[key])
    return "control"


def experiment_for(decision: dict[str, Any] | None, book: str | None) -> str | None:
    """The experiment a book belongs to under this decision, by tag (an earnings twin
    `advised:<name>:<strategy>` matches its experiment's `advised:<name>`), or None."""
    if not is_advised(book):
        return None
    for entry in advised_books(decision):
        tag = entry.get("tag")
        if tag and (book == tag or str(book).startswith(tag + ":")):
            return entry.get("experiment_id")
    return None


def stamp_for(book: str | None, experiment: Any) -> str | None:
    """The experiment id a row should carry: its book's experiment when the row belongs to an
    advised book, else None. `experiment` is the session decision (looked up by the book's tag)
    or, for a caller that already resolved it, the id itself. One rule for every module rather
    than seven inline conditions -- a control row stamped with an experiment would attribute the
    baseline to the thing it is the baseline for."""
    if not is_advised(book):
        return None
    if isinstance(experiment, dict):
        return experiment_for(experiment, book)
    return experiment or None


def write(
    path: Path | str,
    module: str,
    session: str,
    proposals: list[dict[str, Any]],
    advisor: str,
    expires_at: str,
    rejected: list[dict[str, Any]] | None = None,
    experiment_id: str | None = None,
    experiments: list[dict[str, Any]] | None = None,
) -> Path:
    """Write the artifact atomically (tmp + replace) — a loop must never read a half-written file.

    `experiments` is the list of per-experiment entries (`{experiment_id, name, tag, base,
    proposals, rejected}`); when given, the top-level `proposals`/`rejected`/`experiment_id` are
    overwritten with the FIRST entry's so the mirror can never disagree with the list. Without it
    the artifact is written in the legacy single-overlay shape."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if experiments:
        first = experiments[0]
        proposals = list(first.get("proposals") or [])
        rejected = list(first.get("rejected") or [])
        experiment_id = first.get("experiment_id")
    payload = {
        "module": module,
        "session": session,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "advisor": advisor,
        "experiment_id": experiment_id,
        "expires_at": expires_at,
        "proposals": proposals,
        "rejected": rejected or [],
    }
    if experiments:
        payload["experiments"] = [
            {
                "experiment_id": e.get("experiment_id"),
                "name": e.get("name"),
                "tag": e.get("tag"),
                "base": e.get("base"),
                "proposals": list(e.get("proposals") or []),
                "rejected": list(e.get("rejected") or []),
            }
            for e in experiments
        ]
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def load(
    state_dir: Path | str, module: str, session: str, bounds: dict[str, Any], now: datetime | None = None
) -> dict[str, Any]:
    """The loop-side read: one call at session start, never per tick. Absent/unreadable/invalid
    all come back {"ok": False, "reason", "proposals": []} — i.e. baseline. The loop logs the
    reason and moves on; it never waits for, retries, or alerts about advice."""
    path = advice_path(state_dir, module, session)
    if not path.exists():
        return {"ok": False, "reason": "absent", "proposals": [], "rejected": [], "experiments": []}
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {
            "ok": False,
            "reason": f"unreadable: {exc}",
            "proposals": [],
            "rejected": [],
            "experiments": [],
        }
    out = validate(artifact, bounds, session, now=now)
    # The experiment rides on the result whether or not the artifact was admitted: a rejected
    # artifact still cost that experiment a session, and the decision record should say whose.
    out["experiment_id"] = artifact_experiment_id(artifact)
    return out


def disabled_reason(config: dict[str, Any]) -> str | None:
    """Why this config accepts no advice, or None if it does.

    One string per distinct cause, because collapsing them cost four sessions of two experiments.
    On 2026-08-25 meic and earnings both recorded a bare ``advice_disabled`` against live, valid
    artifacts while three sibling modules from the same batch applied theirs — and "the flag is
    off", "the bounds are empty" and "there is no advice block at all" were indistinguishable in
    the one field the advisor can read. It could not diagnose which, and said so.

    The wording matches ``advisor.bounds._off`` on purpose: the advisor decides whether to WRITE an
    artifact from its side of the same question, and two sides answering it in different words is
    how a mismatch stays invisible.
    """
    acfg = config.get("advice")
    if not acfg:
        return "advice_disabled: no advice block in config"
    if not acfg.get("enabled"):
        return "advice_disabled: advice.enabled is false"
    if not acfg.get("bounds"):
        return "advice_disabled: advice.bounds is empty"
    return None


def session_decision(
    state_dir: Path | str,
    module: str,
    session: str,
    config: dict[str, Any],
    decision_path: Path | str,
    *,
    base_key: str | None = "base_book",
    log: Any = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Today's advice decision, derived ONCE per session and replayed for the rest of it.

    This is the read-once rule, and it is a safety property rather than an optimisation. A module's
    loop is a series of short `--once` processes, so "read the artifact at session start" only means
    anything if the first tick RECORDS what it decided and every later tick replays that record.
    Without it, an artifact landing at 11:00 — or a config flag flipped intraday — would change what
    an already-open book is being managed under, halfway through the session that book is evidence
    for. Three modules had written this out identically; it belongs beside the `load` it wraps.

    `base_key` is the config key naming the book the advised twin shadows, and it varies on purpose:
    flies calls its books arms (`base_arm`), calendars and pmcc call them books (`base_book`). It is
    carried through to the returned dict, because that shape is already persisted on disk and read
    by each module's own loop. `None` omits it — earnings names no base book, because its advice is
    keyed by strategy (`iron_condor.profit_target_pct`) and each strategy carries its own.

    A decision from a previous day is discarded rather than replayed. A write failure is swallowed:
    the decision still governs the process that made it, and the next `--once` simply re-derives the
    same one.

    **A baseline decision is never made sticky.** The read-once rule exists to stop advice starting
    or changing under an already-open book; a decision that admitted no params has nothing to
    protect, and persisting one lets any process that reached this function with an unreadable or
    advice-less config decide the whole session for every process after it. That is not
    hypothetical — on 2026-08-25 a 01:05 ET forced meic iteration and an 03:03 earnings entry pass
    each wrote `advice_disabled` hours before the market-open iteration that would have applied a
    valid artifact, and the open iteration dutifully replayed it. Both experiments lost their most
    informative session to a file written before the session began.

    `persist=False` is the same guard from the caller's side, for a process that is deriving a
    decision it has no business fixing for the day — a replay of a past date, or an iteration forced
    outside the trading window. It still gets a correct decision to run under; it just does not get
    to be the one that recorded it.
    """
    acfg = config.get("advice") or {}
    base = {} if base_key is None else {base_key: acfg.get(base_key, "control")}
    path = Path(decision_path)

    decision = None
    if path.exists():
        try:
            decision = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            decision = None
        if decision is not None and decision.get("day") != session:
            decision = None  # yesterday's decision; today re-derives its own
    if decision is not None:
        return decision

    off = disabled_reason(config)
    if off is None:
        result = load(state_dir, module, session, acfg.get("bounds") or {})
        default_base = next(iter(base.values()), "control") if base else "control"
        # One entry per experiment the artifact carried (2026-09-17), each its own book. A legacy
        # artifact yields one entry with no name; its tag is the base's, which is what its rows
        # were always tagged. An entry whose overlay was rejected keeps its record here (reason
        # and rejections) and opens no book -- baseline for that experiment alone.
        experiments = []
        for entry in result.get("experiments") or []:
            entry_base = str(entry.get("base") or default_base)
            tag = entry.get("tag") or (
                advised_tag(entry["name"]) if entry.get("name") else f"{ADVISED_PREFIX}{entry_base}"
            )
            experiments.append(
                {
                    "experiment_id": entry.get("experiment_id"),
                    "name": entry.get("name"),
                    "tag": tag,
                    "base": entry_base,
                    "params": {p["param"]: p["value"] for p in entry.get("proposals") or []} or None,
                    "reason": entry.get("reason"),
                    "proposals": entry.get("proposals") or [],
                    "rejected": entry.get("rejected") or [],
                }
            )
        params = {p["param"]: p["value"] for p in result["proposals"]} or None
        decision = {
            "day": session,
            **base,
            # The first experiment's overlay and id, mirrored for readers of the old shape. A
            # consumer that builds books reads `experiments`, never these.
            "params": params,
            "reason": result["reason"],
            "derived_at": datetime.now(timezone.utc).isoformat(),
            "proposals": result["proposals"],
            "rejected": [r for e in experiments for r in e["rejected"]] or (result.get("rejected") or []),
            "experiment_id": result.get("experiment_id"),
            "experiments": experiments,
        }
        if log is not None:
            for entry in experiments:
                for proposal in entry["proposals"]:
                    log(
                        f"advice applied [{entry['tag']}]: {proposal['param']}={proposal['value']!r} — "
                        f"{proposal.get('rationale', '')}"
                    )
                if not entry["proposals"]:
                    log(f"advice [{entry['tag']}]: baseline ({entry['reason'] or 'no proposals'})")
            if not experiments:
                log(f"advice: baseline ({result['reason'] or 'no proposals'})")
    else:
        decision = {
            "day": session,
            **base,
            "params": None,
            "reason": off,
            "derived_at": datetime.now(timezone.utc).isoformat(),
            "experiment_id": None,
            "experiments": [],
        }

    # `off` decisions are deliberately not recorded: see the docstring. `derived_at` rides on the
    # ones that are, because "when was the day's decision fixed" is the question that diagnosed the
    # 08-25 loss, and it should be answerable from the data rather than from a file mtime.
    if persist and off is None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(decision, indent=2), encoding="utf-8")
        except OSError:
            pass  # the decision still applies to this process; the next --once re-derives it
    return decision
