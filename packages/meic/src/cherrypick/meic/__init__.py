"""cherrypick-meic — the MEIC 0DTE multiple-entry iron-condor engine.

Imports as ``cherrypick.meic``, composing with ``cherrypick.core`` (and every other module's
``cherrypick.<module>``) under one ``cherrypick.*`` root. The parent ``src/cherrypick/`` directory
deliberately has **no** ``__init__.py`` — it is a PEP 420 native namespace, which is what lets
separately-installed distributions share the root. This file, one level down, is an ordinary package
marker and is required.

MEIC has no streamer of its own (its rollback streamer was removed on 2026-10-18). It reads the shared
cache that ``cherrypick.streamer`` (the standalone daemon, on the ``cherrypick.core.streamer`` engine)
writes as the suite's sole market-data producer.
"""
