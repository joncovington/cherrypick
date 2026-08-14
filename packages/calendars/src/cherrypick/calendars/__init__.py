"""cherrypick.calendars — weekly SPX double-calendar paper module.

Every Monday (Tuesday after a Monday holiday), one put calendar at the expected-move-down strike and
one call calendar at the expected-move-up strike: short legs expiring that week's Friday, long legs
the following Monday. Paper-only, and built as a forward exit-parameter experiment — a `control` book
that closes everything at Friday's bell, a permissive `path` book that holds to every expiry and
records the full per-tick mark path, and a read-side derivation (`exit_policies.py`) that replays
every candidate exit rule over that recorded path with exact pairing.

NOTE: the parent `src/cherrypick/` directory has NO __init__.py, on purpose. It is a PEP 420
namespace package, which is what lets `cherrypick.calendars` compose with `cherrypick.core` (and
every sibling module) under one `cherrypick.*` import root. Adding one would break every consumer.
"""
