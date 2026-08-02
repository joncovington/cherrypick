"""cherrypick-gex — the suite's standalone GEX (gamma-exposure) dashboard.

Imports as ``cherrypick.gex``, composing with ``cherrypick.core`` (and every other module's
``cherrypick.<module>``) under one ``cherrypick.*`` root. The parent ``src/cherrypick/`` directory
deliberately has **no** ``__init__.py`` — it is a PEP 420 native namespace, which is what lets
separately-installed distributions share the root. This file, one level down, is an ordinary package
marker and is required.

Not to be confused with ``cherrypick.core.gex``: that is the shared dollar-gamma / walls / zero-gamma
*math* (deliberately one implementation — the two hand-maintained copies once drifted ~75x apart);
this package is the read-only viewer and section card built on top of it.
"""
