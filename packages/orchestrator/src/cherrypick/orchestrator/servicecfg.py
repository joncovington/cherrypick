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

Files and the OS shell only, like the rest of the reliability path — no network, no broker, no AI.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from cherrypick.core import home as _home

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


def write_stamp(service_id: str, config_hash: str | None, source: str | None) -> None:
    """Record what the running process was launched with. Best-effort: a stamp that cannot be written
    costs a future recycle, which is strictly better than failing the tick that tried to write it."""
    if not config_hash:
        return
    try:
        path = stamp_path(service_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"config_hash": config_hash, "config_source": source}, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError:
        pass


def clear_stamp(service_id: str) -> None:
    """Forget a service's stamp — used on uninstall, so a later reinstall adopts rather than recycles."""
    try:
        stamp_path(service_id).unlink(missing_ok=True)
    except OSError:
        pass


def staleness(svc: dict[str, Any], root: Path) -> dict[str, Any]:
    """Whether a *running* service is on config older than what is on disk now.

    `stale` is True only when a stamp exists AND a current hash is readable AND they differ. Every
    other combination is "cannot tell", which reports `adopt` (record the hash, change nothing)
    rather than a restart nobody asked for.
    """
    current, source = effective_config(svc, root)
    stamped = read_stamp(svc.get("id", "")).get("config_hash")
    base = {"stale": False, "adopt": False, "hash": current, "source": source}
    if current is None:
        return {**base, "reason": "config unreadable"}
    if not stamped:
        return {**base, "adopt": True, "reason": "no launch stamp"}
    if stamped == current:
        return {**base, "reason": "config unchanged"}
    return {
        "stale": True,
        "adopt": False,
        "reason": "config changed since launch",
        "hash": current,
        "source": source,
        "stamped": stamped,
    }
