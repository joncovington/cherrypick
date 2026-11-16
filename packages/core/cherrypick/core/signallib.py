"""A named library of `core.signals` specs, each kept in versions, so a spec points at a signal by
reference and never carries a silent copy that drifts.

The library is one file, `~/.cherrypick/config/signals.json`:

    {"signals": {"<name>": {"versions": [{"version": "<hash>", "spec": {...}, "added": "YYYY-MM-DD"}]}}}

A version is the first 12 hex digits of the sha256 of the spec's canonical JSON (keys sorted, no spaces),
so the same settings always have the same version whatever they are called, and a copy filed under a
second name is visible as the same version (`same_as`). Editing a signal adds a version; old versions stay,
because a finished run names the one it read.

A reference is `{"library": "<name>", "version": "<hash>"}` wherever a signal spec goes (a trigger's
`signal`, an `only_when` entry). The version is required: a reference never floats to the latest, so a
paper automation moves to a new version only when its config is edited to name it, which is a
measurement change and is made at a boundary. `resolve` replaces every reference with a copy of its spec
and lists the (name, version) pairs it used, which a run card records.

    python -m cherrypick.core.signallib list
    python -m cherrypick.core.signallib show NAME
    python -m cherrypick.core.signallib add NAME SPEC.json
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from . import home, jsonio, signals

REF_KEYS = frozenset({"library", "version"})
VERSION_LEN = 12


def path() -> Path:
    return home.config_path("signals")


def content_hash(spec: Mapping) -> str:
    """The version of a spec's settings: sha256 of its canonical JSON, first 12 hex digits."""
    text = json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:VERSION_LEN]


def empty() -> dict:
    return {"signals": {}}


def load(at: Path | None = None) -> dict:
    """The library on disk, or an empty one when there is no file. A malformed file raises."""
    p = at or path()
    if not p.exists():
        return empty()
    lib = json.loads(p.read_text(encoding="utf-8"))
    check(lib)
    return lib


def save(lib: dict, at: Path | None = None) -> Path:
    check(lib)
    return jsonio.write_json_atomic(at or path(), lib)


def check(lib: object) -> None:
    """Refuse a library whose versions do not match their specs or whose specs do not validate."""
    if not isinstance(lib, Mapping) or not isinstance(lib.get("signals"), Mapping):
        raise ValueError("signal library: a mapping with 'signals'")
    for name, entry in lib["signals"].items():
        versions = entry.get("versions") if isinstance(entry, Mapping) else None
        if not isinstance(versions, list) or not versions:
            raise ValueError(f"signal library: {name!r} has no versions")
        seen = set()
        for v in versions:
            signals.validate(v.get("spec"), f"signal library {name!r}")
            if content_hash(v["spec"]) != v.get("version"):
                raise ValueError(
                    f"signal library: {name!r} version {v.get('version')!r} does not match its spec"
                )
            if v["version"] in seen:
                raise ValueError(f"signal library: {name!r} lists version {v['version']!r} twice")
            seen.add(v["version"])


def latest(lib: Mapping, name: str) -> dict | None:
    entry = lib["signals"].get(name)
    return entry["versions"][-1] if entry else None


def get(lib: Mapping, name: str, version: str) -> dict:
    """A copy of the spec `name` holds at `version`. Raises naming what is missing."""
    entry = lib["signals"].get(name)
    if entry is None:
        raise ValueError(f"signal library: no signal {name!r}")
    for v in entry["versions"]:
        if v["version"] == version:
            return copy.deepcopy(v["spec"])
    raise ValueError(f"signal library: {name!r} has no version {version!r}")


def same_as(lib: Mapping, version: str) -> list[str]:
    """Every name holding `version`: one setting filed under several names."""
    return sorted(n for n, e in lib["signals"].items() if any(v["version"] == version for v in e["versions"]))


def add(lib: dict, name: str, spec: Mapping, *, today: str | None = None) -> tuple[str, bool]:
    """File `spec` under `name`: (its version, whether a version was added). The same settings as the
    name's newest version add nothing; any edit adds one. The spec must validate."""
    if not isinstance(name, str) or not name.strip() or name != name.strip():
        raise ValueError(f"signal library: bad name {name!r}")
    signals.validate(spec, f"signal {name!r}")
    version = content_hash(spec)
    entry = lib["signals"].setdefault(name, {"versions": []})
    if entry["versions"] and entry["versions"][-1]["version"] == version:
        return version, False
    if any(v["version"] == version for v in entry["versions"]):
        raise ValueError(
            f"signal library: {name!r} already holds {version!r} as an older version; name it by version"
        )
    entry["versions"].append(
        {"version": version, "spec": copy.deepcopy(dict(spec)), "added": today or date.today().isoformat()}
    )
    return version, True


def is_ref(x: object) -> bool:
    return isinstance(x, Mapping) and "library" in x


def resolve(obj: Any, lib: Mapping) -> tuple[Any, list[tuple[str, str]]]:
    """`obj` with every reference replaced by a copy of its spec, and the (name, version) pairs used in
    the order met. A reference without a version, with other keys, or naming what the library lacks
    raises."""
    used: list[tuple[str, str]] = []

    def walk(x, where):
        if is_ref(x):
            extra = set(x) - REF_KEYS
            if extra:
                raise ValueError(
                    f"{where}: a signal reference takes only library and version, not {sorted(extra)[0]!r}"
                )
            if not isinstance(x.get("version"), str) or not x["version"]:
                raise ValueError(f"{where}: a signal reference names its version")
            spec = get(lib, x["library"], x["version"])
            if (x["library"], x["version"]) not in used:
                used.append((x["library"], x["version"]))
            return spec
        if isinstance(x, Mapping):
            return {k: walk(v, f"{where}.{k}") for k, v in x.items()}
        if isinstance(x, list):
            return [walk(v, f"{where}[{i}]") for i, v in enumerate(x)]
        return x

    return walk(obj, "spec"), used


def refs(obj: Any) -> list[tuple[str, str | None]]:
    """The (name, version) references `obj` holds, unresolved, in the order met."""
    out: list[tuple[str, str | None]] = []

    def walk(x):
        if is_ref(x):
            pair = (x.get("library"), x.get("version"))
            if pair not in out:
                out.append(pair)
            return
        if isinstance(x, Mapping):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(obj)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cherrypick.core.signallib")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    s = sub.add_parser("show")
    s.add_argument("name")
    a = sub.add_parser("add")
    a.add_argument("name")
    a.add_argument("spec", help="a JSON file holding one core.signals spec")
    args = ap.parse_args(argv)
    lib = load()
    if args.cmd == "list":
        rows = {
            n: {"latest": e["versions"][-1]["version"], "versions": len(e["versions"]),
                "same_as": [x for x in same_as(lib, e["versions"][-1]["version"]) if x != n]}
            for n, e in sorted(lib["signals"].items())
        }  # fmt: skip
        print(json.dumps({"ok": True, "path": str(path()), "signals": rows}, indent=2))
        return 0
    if args.cmd == "show":
        entry = lib["signals"].get(args.name)
        if entry is None:
            print(json.dumps({"ok": False, "error": f"no signal {args.name!r}"}))
            return 1
        print(json.dumps({"ok": True, "name": args.name, **entry}, indent=2))
        return 0
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    try:
        version, added = add(lib, args.name, spec)
    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    if added:
        save(lib)
    print(json.dumps({"ok": True, "name": args.name, "version": version, "added": added,
                      "reference": {"library": args.name, "version": version}}))  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
