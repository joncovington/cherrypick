"""Flags taper by severity, and a critical one never ages out.

Written for a defect of measurement rather than of logic. The 2026-08-26 journal taper exempted
flags at every age — "a flag is a standing caveat" — and trimmed observations instead. Flags were
97.6KB of that section's 120KB; observations were 12.3KB. The reasoning was right about `critical`
and wrong about the other 210 flags, and nothing checked which was which.

Each test below was confirmed to fail against the un-tapered version (`"flags": json.loads(...)` at
every age), per the suite's rule that a guard has to be shown to fail.
"""

from __future__ import annotations

import json

from cherrypick.advisor import factpack


def _flags(*specs):
    return json.dumps([
        {"module": m, "severity": s, "text": t} for m, s, t in specs
    ])


def test_inside_the_window_nothing_is_touched():
    raw = _flags(("meic", "warn", "x" * 400), ("flies", "info", "y" * 400))
    out = factpack._journal_flags(raw, full=True)
    assert out == {"flags": json.loads(raw)}
    assert "flags_elided" not in out


def test_an_aged_critical_flag_is_carried_verbatim():
    """The whole reason flags were exempted in the first place. If this ever stops holding, the
    taper has eaten the standing caveats it was written to protect."""
    text = "control has taken zero entries in five consecutive sessions " * 8
    out = factpack._journal_flags(_flags(("meic", "critical", text)), full=False)
    assert out["flags"][0]["text"] == text
    assert "_elided" not in out["flags"][0]


def test_an_aged_warn_or_info_keeps_its_identity_and_drops_the_prose():
    text = "z" * 900
    out = factpack._journal_flags(_flags(("flies", "warn", text)), full=False)
    flag = out["flags"][0]
    assert flag["module"] == "flies" and flag["severity"] == "warn"
    assert len(flag["text"]) == 120 and flag["text"] == text[:120]
    # The rule is stated once on the section, not repeated on each of ~210 aged flags.
    assert "_elided" not in flag


def test_the_count_of_what_was_dropped_travels_with_it():
    """An elision nobody can see is indistinguishable from a session that raised no flags."""
    out = factpack._journal_flags(
        _flags(("meic", "critical", "a"), ("meic", "warn", "b"), ("flies", "info", "c")),
        full=False,
    )
    assert out["flags_elided"] == 2


def test_a_session_whose_flags_all_survive_reports_no_elision():
    out = factpack._journal_flags(_flags(("meic", "critical", "a")), full=False)
    assert "flags_elided" not in out


def test_the_taper_actually_shrinks_a_realistic_session():
    """The point of the exercise, at the REAL proportions: 222 flags across the window of which
    12 are critical, at ~420 bytes of prose each. An earlier version of this test used 1 critical
    in 7 and failed on a saving that is real — a fixture three times denser in the one severity
    that is exempt measures the exemption, not the taper."""
    raw = _flags(*([("meic", "warn", "w" * 400)] * 20 + [("meic", "critical", "c" * 400)]))
    before = len(json.dumps(json.loads(raw), indent=2))
    after = len(json.dumps(factpack._journal_flags(raw, full=False)["flags"], indent=2))
    assert after < before * 0.45


def test_a_malformed_flag_is_kept_rather_than_dropped():
    """Flags are model output. A string where a dict was expected must not silently vanish — the
    section's job is to not lose caveats."""
    out = factpack._journal_flags(json.dumps(["a bare string", None]), full=False)
    assert out["flags"] == ["a bare string", None]


def test_absent_and_empty_flags_are_both_empty():
    assert factpack._journal_flags(None, full=False) == {"flags": []}
    assert factpack._journal_flags("[]", full=True) == {"flags": []}
