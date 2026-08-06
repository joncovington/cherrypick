"""cherrypick-meic — the MEIC 0DTE multiple-entry iron-condor engine.

Imports as ``cherrypick.meic``, composing with ``cherrypick.core`` (and every other module's
``cherrypick.<module>``) under one ``cherrypick.*`` root. The parent ``src/cherrypick/`` directory
deliberately has **no** ``__init__.py`` — it is a PEP 420 native namespace, which is what lets
separately-installed distributions share the root. This file, one level down, is an ordinary package
marker and is required.

Note ``cherrypick.meic.streamer`` is MEIC's own wrapper (REST sidecar + the 7699 API), distinct from
both ``cherrypick.core.streamer`` (the shared engine) and ``cherrypick.streamer`` (the standalone
daemon that is the suite's sole market-data producer).
"""
