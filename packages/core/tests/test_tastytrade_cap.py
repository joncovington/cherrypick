"""Every package that declares tastytrade caps it below 13.

tastytrade 13 (2026-07) rewrote order placement into per-type order classes and switched httpx for
httpx2 -- the API the live order path (core.broker, core.execution, bwb, flies, meic, desk) submits
through. An uncapped `>=12` let any fresh install or reinstall pull 13 into that path. The guard
reads the packages' own pyproject files, so a new package declaring tastytrade is covered too.
"""

import re
import tomllib
from pathlib import Path

PACKAGES = Path(__file__).resolve().parents[2]


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


def _capped_below_13(spec: str) -> bool:
    return any(
        op == "<" and int(major) <= 13 or op == "<=" and int(major) < 13
        for op, major in re.findall(r"(<=|<)\s*(\d+)", spec)
    )


def test_every_tastytrade_declaration_is_capped_below_13():
    specs = _tastytrade_specs()
    assert specs, "no package declares tastytrade -- the scan is looking in the wrong place"
    uncapped = {pkg: s for pkg, ss in specs.items() for s in ss if not _capped_below_13(s)}
    assert uncapped == {}


def test_the_cap_check_rejects_an_uncapped_spec():
    assert not _capped_below_13("tastytrade>=12")
    assert not _capped_below_13("tastytrade")
    assert not _capped_below_13("tastytrade>=12,<14")
    assert _capped_below_13("tastytrade>=12.4.3,<13")
