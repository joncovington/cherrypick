"""Every package that declares tastytrade caps its major version.

A tastytrade major moves the API the live order path (core.broker, core.execution, bwb, flies, meic,
desk) submits through: 13 rewrote order placement into per-type order classes and switched httpx for
httpx2. An uncapped `>=12` let any fresh install pull a new major straight into that path. The
suite moved to 13 on 2026-10-05, after checking that orders serialize and dry-run identically
(core/tests/test_order_sdk.py); the cap now sits below 14, and moves only with the same check. The
guard reads the packages' own pyproject files, so a new package declaring tastytrade is covered too.
"""

import re
from pathlib import Path

import tomllib

PACKAGES = Path(__file__).resolve().parents[2]
NEXT_UNCHECKED_MAJOR = 14  # the lowest major nobody has checked the order path against


def _tastytrade_specs() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for path in sorted(PACKAGES.glob("*/pyproject.toml")):
        project = tomllib.loads(path.read_text(encoding="utf-8")).get("project", {})
        declared = list(project.get("dependencies", []))
        for extra in (project.get("optional-dependencies") or {}).values():
            declared += extra
        specs = [d for d in declared if re.match(r"tastytrade(\W|$)", d)]
        if specs:
            out[path.parent.name] = specs
    return out


def _capped(spec: str, major: int = NEXT_UNCHECKED_MAJOR) -> bool:
    """Whether `spec` excludes `major` and everything above it."""
    return any(
        op == "<" and int(m) <= major or op == "<=" and int(m) < major
        for op, m in re.findall(r"(<=|<)\s*(\d+)", spec)
    )


def test_every_tastytrade_declaration_is_capped_below_an_unchecked_major():
    specs = _tastytrade_specs()
    assert specs, "no package declares tastytrade -- the scan is looking in the wrong place"
    uncapped = {pkg: s for pkg, ss in specs.items() for s in ss if not _capped(s)}
    assert uncapped == {}


def test_the_cap_check_rejects_an_uncapped_spec():
    assert not _capped("tastytrade>=12")
    assert not _capped("tastytrade")
    assert not _capped("tastytrade>=12,<15")
    assert _capped("tastytrade>=12.4.3,<14")
    assert _capped("tastytrade>=12.4.3,<13")
