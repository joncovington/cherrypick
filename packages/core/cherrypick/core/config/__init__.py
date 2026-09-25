"""Reading a module's config by key, when the key has more than one accepted spelling.

`core.home.load_module_config` finds the file and hands back a dict. This is the layer above it:
which key in that dict holds what, given that the same setting is spelled differently per module.

The suite already had exactly one of these, hand-rolled for one field —
``cherrypick.core.advice._legacy_base``, which tries ``base_arm``/``base_profile``/``base_book``/
``base_prefix`` in turn — plus two more copies of the same list in TypeScript, in a different
order. This generalises that one mechanism rather than inventing a second.

**The reason it exists is the failure mode, not the tidiness.** Every config read in the suite is
of the form ``cfg.get("books", {})`` or ``.get("arms") or {}`` or ``.get("base_book") or
"control"``. Not one of them raises. So a key that moves — renamed, mistyped, edited out — does
not produce an error: the registry resolves empty, every arm falls through to
``.get(name, {}).get("enabled", True)``, and the loop runs every arm on ``defaults``. All arms
become identical, the A/B measures nothing, and the P&L looks entirely plausible. In a suite whose
purpose is measuring whether strategies make money, that is the worst available outcome, and today
it is silent.

So the distinction this module draws is between **absent** and **declared empty**, which
``.get(key, {})`` collapses and which are not the same fact:

    {"arms": {}}   an operator turned every arm off. Intentional. Say nothing.
    {}             nobody declared arms at all. Either a fresh config or a key that moved.

`registry` tests membership rather than truthiness, and warns only on the second.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

# The variant registry, in canonical-first order. `arms` is the suite's word (see the root
# CLAUDE.md's vocabulary table); `books` and `profiles` are the spellings the modules shipped with
# and are accepted for good. A module config is a file a person edits and keeps across upgrades --
# there is no migration for it, so an accepted spelling is never withdrawn.
ARM_REGISTRY_KEYS: tuple[str, ...] = ("arms", "books", "profiles")

# Which arm an experiment's advised twin is measured against. Canonical first; the order matters
# only in the impossible case of a config declaring two of them, and first-wins is what
# `core.advice._legacy_base` has always done.
BASE_ARM_KEYS: tuple[str, ...] = ("base_arm", "base_profile", "base_book", "base_prefix")

# The qualification rule's session bar, and the reading field it is checked against. Both were
# spelled `days` until 2026-09-24 -- a count of distinct trading sessions, which a calendar day is
# not (a weekend is two days and no sessions). Live configs carry `min_days` and 30 advisor artifacts
# on disk carry readings with `days`, so both spellings are read for good. Canonical first.
MIN_SESSIONS_KEYS: tuple[str, ...] = ("min_sessions", "min_days")
SESSIONS_KEYS: tuple[str, ...] = ("sessions", "days")

_warned: set[tuple[str, tuple[str, ...]]] = set()


def first_present(doc: Mapping[str, Any] | None, *keys: str, default: Any = None) -> Any:
    """The value of the first key that is PRESENT, whatever it holds.

    Presence, not truthiness: a config that says ``{"base_arm": ""}`` has answered the question,
    and falling through to the next spelling would silently read a different key than the one the
    operator wrote.
    """
    if not isinstance(doc, Mapping):
        return default
    for key in keys:
        if key in doc:
            return doc[key]
    return default


def registry(
    cfg: Mapping[str, Any] | None,
    *keys: str,
    label: str,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """A ``{name: settings}`` block under any of its accepted spellings.

    Returns ``{}`` when the block is absent OR declared empty -- callers want a dict either way --
    but warns (once per label, so a per-tick loop cannot spam) only when NO spelling was present
    at all. That is the case where the config is not saying "no arms"; it is not saying anything,
    and the caller is about to proceed on defaults as though it had.
    """
    keys = keys or ARM_REGISTRY_KEYS
    if isinstance(cfg, Mapping):
        for key in keys:
            if key in cfg:
                value = cfg[key]
                return {str(k): v for k, v in value.items()} if isinstance(value, Mapping) else {}

    once = (label, keys)
    if log is not None and once not in _warned:
        _warned.add(once)
        log(
            f"WARNING: {label} declares none of {', '.join(keys)} -- running on defaults for every "
            "arm. If this config used to name one, the key moved and every arm is now identical."
        )
    return {}


def _reset_warnings() -> None:
    """Test seam: the once-per-label memo is process-global by design."""
    _warned.clear()
