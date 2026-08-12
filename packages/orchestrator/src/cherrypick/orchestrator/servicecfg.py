"""Detect a managed service that is running *stale config*, so the watchdog can recycle it.

The single-instance guard promises one writer. It cannot promise that the one writer loaded the
config you are looking at. On 2026-07-23 a `gex-recorder` daemon that had been up since 07-19 was
still holding a config from before `source.stream_cache_db` moved off the retired meic cache; when
that cache went dead it kept writing a frozen spot into the trail for days. Nothing recycled it,
because nothing was wrong with the *process*: the watchdog only ever starts a service that is down,
and this one was up, healthy, and answering `--status` truthfully. It was simply wrong.

That is the shape of the bug: a long-lived process reads config once, at launch, and a config edit
afterwards reaches the file but never the process. No liveness check can see it — the staleness is
in the gap between the file and the process, so that gap is what gets measured here.

The orchestrator stamps a hash of a service's effective config when it launches it, and compares on
each tick. A moved hash means the running process predates the current config and must be recycled
(stop, then start) so it re-reads. Two things go into that hash, because either can go stale:

  * the service's OWN config file — what actually bit us, and what the process reads at startup, and
  * the orchestrator's `services[]` entry — its argv and path; a changed launch command describes a
    different process than the one currently running.

**An unstamped service is never recycled on sight.** With no stamp there is nothing to compare
against, so "stale" is unknowable — and guessing would restart every running service once, on the
first tick after this ships, for no reason. The first observation records the hash and leaves the
process alone; the *next* config change is caught. Likewise an unreadable config yields no hash and
no recycle: silence must not be read as change.

A market-data producer has a **second** way to go stale, on the same shape of gap. Its underlyings
come from the union of every module's stream request (`cherrypick.core.streamrequests`), and that
union binds once, when it builds its streamer — so a module that starts needing a new symbol writes
its request file and the running producer never sees it. That is the same file-versus-process gap as
above, measured the same way: the launch stamp records the subscription snapshot, and a later tick
compares. It is opt-in (`check_subscriptions=`) because only a producer consumes those files.

The subscription comparison is deliberately **growth-only**, not a hash. A module that stops needing
a symbol, or narrows a window, leaves the producer over-subscribed — harmless, and not worth the
settling window a restart costs. Treating a shrink as staleness would be actively hostile to a module
whose request tracks its open positions: it rewrites that file as positions close through the morning,
and every close would recycle the producer during the exact window its consumers need marks.

Files and the OS shell only, like the rest of the reliability path — no network, no broker, no AI.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from cherrypick.core import home as _home
from cherrypick.core import streamrequests as _streamrequests

from . import config as cfgmod
from . import util

_STAMP_PREFIX = "service-"
_STAMP_SUFFIX = ".launch.json"


def stamp_path(service_id: str) -> Path:
    """`~/.cherrypick/state/service-<id>.launch.json` — what config the running process was launched
    with. Slashes are flattened so an id can never escape the state directory."""
    safe = str(service_id).replace("/", "-").replace("\\", "-")
    return cfgmod.state_file(f"{_STAMP_PREFIX}{safe}{_STAMP_SUFFIX}")


def config_candidates(svc: dict[str, Any], root: Path) -> list[Path]:
    """Where a service's own config might live, most-authoritative first — the same order liveops
    reads a module's live flag in, so the two never disagree about which file is in force.

    The service id is not the config name: the recorder's id is `gex-recorder` but its config is
    gex's. The checkout directory name is the better default, with `config_name` to override when a
    service's config is named for neither.
    """
    names = [svc.get("config_name"), root.name, svc.get("id")]
    seen: list[Path] = []
    for name in [n for n in names if n]:
        p = _home.config_path(str(name))
        if p not in seen:
            seen.append(p)
    seen.extend([root / "config" / "config.json", root / "config.json"])
    return seen


def effective_config(svc: dict[str, Any], root: Path) -> tuple[str | None, str | None]:
    """`(hash, source)` for a service's effective config, or `(None, None)` when nothing is readable.

    None is deliberately not a hash of "nothing": a config that has become unreadable is an unknown,
    and comparing an unknown against a stamp would recycle a healthy process on the strength of a
    transient read error.
    """
    spec = {k: v for k, v in svc.items() if not str(k).startswith("_")}
    digest = hashlib.sha256(json.dumps(spec, sort_keys=True, default=str).encode("utf-8"))

    for path in config_candidates(svc, root):
        try:
            if not path.exists():
                continue
            digest.update(path.read_bytes())
        except OSError:
            return None, None
        return digest.hexdigest()[:16], cfgmod.portable_path(path)
    # No config file of its own: the service entry alone still describes the launch, so a changed
    # argv is still catchable. A service configured entirely by argv is a legitimate shape.
    return digest.hexdigest()[:16], None


def read_stamp(service_id: str) -> dict[str, Any]:
    return util.read_json(stamp_path(service_id))


def write_stamp(
    service_id: str,
    config_hash: str | None,
    source: str | None,
    subscriptions: dict[str, Any] | None = None,
) -> None:
    """Record what the running process was launched with. Best-effort: a stamp that cannot be written
    costs a future recycle, which is strictly better than failing the tick that tried to write it.

    `subscriptions` is written only for a producer (the caller that asked for the check); its absence
    means "not tracked for this service", not "asked for nothing".
    """
    if not config_hash:
        return
    payload: dict[str, Any] = {"config_hash": config_hash, "config_source": source}
    if subscriptions is not None:
        payload["subscriptions"] = subscriptions
    try:
        path = stamp_path(service_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def subscription_snapshot() -> dict[str, Any] | None:
    """What every installed module currently asks a producer to subscribe, or None if unreadable.

    None is "cannot tell", the same posture `effective_config` takes to an unreadable config — a
    transient read error must not read as "nobody wants anything" and recycle a healthy producer.
    """
    try:
        return _streamrequests.subscription_snapshot()
    except Exception:
        return None


def subscription_shortfall(stamped: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    """What `current` asks for that a producer launched on `stamped` cannot already serve.

    Growth only — see the module docstring for why a shrink is deliberately not staleness.
    """
    if not isinstance(stamped, dict):
        return {}
    new_symbols = sorted(set(current.get("symbols") or []) - set(stamped.get("symbols") or []))
    was_hints = stamped.get("window_hints") or {}
    widened = {
        symbol: count
        for symbol, count in (current.get("window_hints") or {}).items()
        if count > int(was_hints.get(symbol, 0) or 0)
    }
    out: dict[str, Any] = {}
    if new_symbols:
        out["symbols"] = new_symbols
    if widened:
        out["window_hints"] = widened
    return out


def describe_shortfall(short: dict[str, Any]) -> str:
    """One human-readable clause naming what the running producer is short of."""
    parts = []
    if short.get("symbols"):
        parts.append("new symbols " + ", ".join(short["symbols"]))
    if short.get("window_hints"):
        widened = short["window_hints"]
        parts.append("wider windows " + ", ".join(f"{s}={widened[s]}" for s in sorted(widened)))
    return " and ".join(parts) or "nothing"


def clear_stamp(service_id: str) -> None:
    """Forget a service's stamp — used on uninstall, so a later reinstall adopts rather than recycles."""
    try:
        stamp_path(service_id).unlink(missing_ok=True)
    except OSError:
        pass


def staleness(
    svc: dict[str, Any],
    root: Path,
    service_id: str | None = None,
    *,
    check_subscriptions: bool = False,
) -> dict[str, Any]:
    """Whether a *running* service is on config older than what is on disk now.

    `stale` is True only when a stamp exists AND a current hash is readable AND they differ. Every
    other combination is "cannot tell", which reports `adopt` (record the hash, change nothing)
    rather than a restart nobody asked for.

    `service_id` names the stamp. `services[]` entries carry their own `id`; the streamer blocks do
    not, so their caller passes the watchdog's finding label ("streamer", "meic.streamer") — which
    also keeps two producers from sharing one stamp and recycling each other.

    `check_subscriptions` adds the producer-only second axis: the stream-request union grew past what
    the running process subscribed at launch. Config change is checked first, since a config recycle
    re-reads the union anyway. `kind` names which axis fired, for the caller's wording.
    """
    current, source = effective_config(svc, root)
    stamp = read_stamp(service_id or svc.get("id", ""))
    stamped = stamp.get("config_hash")
    subscriptions = subscription_snapshot() if check_subscriptions else None
    base = {"stale": False, "adopt": False, "hash": current, "source": source}
    if subscriptions is not None:
        base["subscriptions"] = subscriptions

    if current is None:
        return {**base, "reason": "config unreadable"}
    if not stamped:
        return {**base, "adopt": True, "reason": "no launch stamp"}
    if stamped != current:
        return {
            **base,
            "stale": True,
            "kind": "config",
            "reason": "config changed since launch",
            "stamped": stamped,
        }
    if subscriptions is not None:
        if "subscriptions" not in stamp:
            # A producer stamped before subscriptions were tracked has an unknown launch union. Same
            # rule as an unstamped service: record it now, catch the next change.
            return {**base, "adopt": True, "reason": "no subscription stamp"}
        short = subscription_shortfall(stamp.get("subscriptions"), subscriptions)
        if short:
            return {
                **base,
                "stale": True,
                "kind": "subscriptions",
                "reason": f"stream requests grew since launch: {describe_shortfall(short)}",
                "shortfall": short,
            }
    return {**base, "reason": "config unchanged"}
