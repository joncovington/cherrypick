"""cherrypick-scout — a self-hosted options research surface for the cherrypick suite.

Imports as ``cherrypick.scout``, composing with ``cherrypick.core`` (and every other module's
``cherrypick.<module>``) under one ``cherrypick.*`` root. The parent ``src/cherrypick/`` directory
deliberately has **no** ``__init__.py`` — it is a PEP 420 native namespace, which is what lets
separately-installed distributions share the root. This file, one level down, is an ordinary package
marker and is required.

Research surface with order *staging*, never order *placement* — see the package README for the
full invariant. Standalone: own port (5057), no orchestrator embed/section registration.
"""
