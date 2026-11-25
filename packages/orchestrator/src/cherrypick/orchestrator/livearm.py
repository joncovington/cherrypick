"""The one door to `live.arm`: which arm a live loop runs.

`/live/arm` is in `configedit.GUARDED` for every live-path module, so the Config page's general save,
over HTTP or through `configcli`, refuses it in both directions. Choosing the arm from the console was
decided on 2026-11-23 (user), as an exception for this one pointer and no other. It is its own op, with
its own refusals, rather than a hole in the guard:

- **Only `/live/arm` changes.** The new text is checked to parse to the old document with that one key
  moved; anything else refuses. It is spliced, so the file's notes and key order survive, backed up first
  (`configedit.backup_and_write`) and written atomically. The master switch, the symbol, the caps and the
  breakers are not reachable from here, and the general save still refuses `/live/arm`.
- **Typed confirmation, checked here.** `CHANGE ARM`, exactly, as the halt's resume phrase is checked.
- **Only an arm the module can measure.** For MEIC the arm must be in the machine's arm registry, not a
  follower or a twin, and must have a plain paper twin (`shadow_of`): live results are read per arm, and a
  live arm with no paper twin is measured against nothing.
- **Only when flat and settled.** The module's own `live_loop --status` is asked, and the change is
  refused unless the loop is not armed today, holds no position and no working order, the day is
  settled, nothing is overdue, held or orphaned, no entry is sent but not yet reconciled, and the ledger is
  readable. A status that cannot be read refuses: unknown is not flat. This is a pre-open or
  after-settlement change, never a mid-session one. The file is re-read just before the write and the
  change refused (`changed`) if it is no longer as it was read: a hand edit is never overwritten.
- **Journaled first.** The change is appended to the arm journal (`core.live.record_event`) before the
  config is touched; a journal that cannot be written stops it. The journal is the audit trail.
- **It takes effect at the next arming** (the live loop reads its config each tick, and nothing runs
  without the day's arm record), and it arms nothing.

Only MEIC's main login is covered. Another login's `live_accounts.<login>.arm` is whole under the guarded
`/live_accounts`, and the ladder's status does not say whether it holds lots, so neither can be judged flat
here; both refuse as unsupported rather than guess. A refusal is data, with a `code`, never an exception.
"""

from __future__ import annotations

import copy
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cherrypick.core import home as core_home
from cherrypick.core import live as _live

from . import config as cfgmod
from . import configedit
from .execution import twins_of
from .util import CREATE_NO_WINDOW, first_json

CONFIRMATION = "CHANGE ARM"
MODULE = "meic"
POINTER = "/live/arm"
STATUS_TIMEOUT_S = 60

StatusFn = Callable[[dict[str, Any]], "dict[str, Any] | None"]


def _refuse(code: str, error: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "code": code, "error": error, **extra}


def run_module_status(cfg: dict[str, Any]) -> dict[str, Any] | None:
    """The module's own `live_loop --status` JSON (files and the ledger, never the broker), or None."""
    mcfg = (cfg.get("modules") or {}).get(MODULE) or {}
    try:
        r = subprocess.run(
            [cfgmod.python_exe(), "-m", f"cherrypick.{MODULE}.live_loop", "--status"],
            cwd=str(cfgmod.module_root(mcfg, MODULE)),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=STATUS_TIMEOUT_S,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return first_json(r.stdout) or None


def not_flat(status: dict[str, Any]) -> list[str]:
    """Why the loop cannot change its arm now, in words; empty when it is flat and settled. Every field
    is the module's own answer, and a missing one counts against: unknown is not flat."""
    out: list[str] = []
    if status.get("ledger_unreadable"):
        out.append("the live ledger cannot be read")
    if status.get("armed_for") is not None and status.get("armed_for") == status.get("date"):
        out.append("the loop is armed for today (disarm it first)")
    open_positions = status.get("open_positions")
    if open_positions is None:
        out.append("the open positions are unknown")
    elif open_positions:
        out.append(f"{open_positions} position(s) open")
    if status.get("pending_orders"):
        out.append(f"{len(status['pending_orders'])} order(s) working")
    if status.get("session_settled") is not True:
        out.append("today's session is not settled")
    if status.get("overdue_settlement"):
        out.append("a past session is still unsettled")
    if status.get("entry_intents"):
        out.append("an entry was sent and is not yet reconciled with the broker")
    held = status.get("broker_held") or {}
    if held.get("unresolved") or held.get("unrecorded"):
        out.append("a broker submission is unresolved or unrecorded")
    if status.get("orphaned_orders"):
        out.append("working orders at the broker are not in the ledger")
    return out


def _registry_profiles() -> dict[str, Any]:
    try:
        doc = json.loads((core_home.config_dir() / "meic.risk.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    profiles = doc.get("profiles") if isinstance(doc, dict) else None
    return profiles if isinstance(profiles, dict) else {}


def candidates(profiles: dict[str, Any] | None = None) -> dict[str, str]:
    """{arm: twin}: the arms live may run, each with the paper twin that measures it. An arm that is a
    follower or itself a twin is not an arm live can run, and an arm with no plain twin is measured
    against nothing."""
    profiles = _registry_profiles() if profiles is None else profiles
    twins = twins_of(profiles)
    out = {}
    for arm, twin in twins.items():
        spec = profiles.get(arm)
        if not isinstance(spec, dict) or arm.startswith("_"):
            continue
        if spec.get("follows_live") or spec.get("shadow_of"):
            continue
        out[arm] = twin
    return dict(sorted(out.items()))


def current_arm(doc: Any) -> str | None:
    live = doc.get("live") if isinstance(doc, dict) else None
    arm = live.get("arm") if isinstance(live, dict) else None
    return str(arm).strip() or None if arm is not None else None


def options(cfg: dict[str, Any], status_fn: StatusFn = run_module_status) -> dict[str, Any]:
    """What a surface needs to offer the choice: the arm live runs, the arms it may run (each with its
    twin), and why a change is refused right now (empty when it would be allowed). Read-only."""
    path = core_home.config_path(MODULE)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _refuse("not_found", f"{MODULE} has no readable config")
    status = status_fn(cfg)
    blockers = ["the module's status cannot be read"] if status is None else not_flat(status)
    return {
        "ok": True,
        "module": MODULE,
        "current": current_arm(doc),
        "arms": [{"arm": a, "twin": t} for a, t in candidates().items()],
        "blockers": blockers,
        "can_change": not blockers,
    }


def _unchanged(path: Path, text: str) -> bool:
    """Whether the file still holds exactly `text`; one that cannot be read counts as changed."""
    try:
        return path.read_text(encoding="utf-8") == text
    except OSError:
        return False


def set_live_arm(
    cfg: dict[str, Any], req: dict[str, Any], status_fn: StatusFn = run_module_status
) -> dict[str, Any]:
    """Change the arm MEIC's live loop runs. See the module docstring for every refusal."""
    module = req.get("module")
    arm = req.get("arm")
    if module != MODULE or req.get("login") not in (None, "", "main"):
        return _refuse(
            "unsupported",
            "only MEIC's main login can change its arm from here; another login's arm is edited by hand",
        )
    if not isinstance(arm, str) or not arm.strip():
        return _refuse("invalid", "arm (a non-empty string) required")
    arm = arm.strip()
    if req.get("confirm") != CONFIRMATION:
        return _refuse("confirm_required", f'changing the live arm needs confirm: "{CONFIRMATION}"')

    path = core_home.config_path(MODULE)
    try:
        old_text = path.read_text(encoding="utf-8")
        old_doc = json.loads(old_text)
    except (OSError, ValueError):
        return _refuse("not_found", f"{MODULE} has no readable config")
    before = current_arm(old_doc)

    allowed = candidates()
    if arm not in allowed:
        registry = _registry_profiles()
        spec = registry.get(arm)
        if not isinstance(spec, dict) or arm.startswith("_"):
            return _refuse("unknown_arm", f"{arm!r} is not an arm in the registry")
        if spec.get("follows_live") or spec.get("shadow_of"):
            return _refuse("unknown_arm", f"{arm!r} is a follower or a twin: live cannot run it")
        return _refuse(
            "no_twin",
            f"{arm!r} has no paper twin (a profile with shadow_of: {arm!r}), so live would be measured "
            "against nothing",
        )
    if arm == before:
        return {"ok": True, "changed": False, "module": MODULE, "arm": arm, "note": "already the live arm"}

    status = status_fn(cfg)
    if status is None:
        return _refuse("status_unreadable", "the module's status cannot be read, so flat is not known")
    blockers = not_flat(status)
    if blockers:
        return _refuse(
            "not_flat", "the loop is not flat and settled: " + "; ".join(blockers), blockers=blockers
        )

    want = copy.deepcopy(old_doc)
    live = want.get("live")
    if not isinstance(live, dict):
        return _refuse("invalid", "the config has no live block to change")
    live["arm"] = arm
    try:
        if "arm" in (old_doc.get("live") or {}):
            new_text = configedit.splice_value(old_text, POINTER, arm)
        else:
            new_text = configedit.insert_key(old_text, "/live", "arm", arm)
    except (KeyError, ValueError) as exc:
        return _refuse("invalid", f"cannot place the arm in the config: {exc}")
    # Nothing but the one key may differ: the splice proved it by parsing to exactly the intended document.
    try:
        if json.loads(new_text) != want:
            return _refuse("invalid", "the edit would change more than live.arm; nothing was written")
    except ValueError:
        return _refuse("invalid", "the edit does not parse; nothing was written")

    # The module's status read can take up to a minute. A hand edit made meanwhile is not ours to
    # overwrite (the new text was built from the old read), so the file must still be as it was read.
    if not _unchanged(path, old_text):
        return _refuse("changed", "the config changed while the loop's status was read; nothing was written")
    if not _live.record_event(MODULE, "live_arm_changed", actor="config", from_arm=before, to_arm=arm):
        return _refuse("journal_unwritable", "the arm journal cannot be written, so the change was not made")
    written = configedit.backup_and_write(Path(path), new_text, MODULE)
    return {
        "ok": True,
        "changed": True,
        "module": MODULE,
        "from": before,
        "arm": arm,
        "twin": allowed[arm],
        **written,
        "note": (
            "takes effect at the next arming; the kill criteria are derived per arm, so check the "
            "module's status for the new arm before arming"
        ),
    }
