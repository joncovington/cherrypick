"""cherrypick-earnings — defined-risk earnings plays held overnight around a company's report.

Imports as ``cherrypick.earnings``, composing with ``cherrypick.core`` (and every other module's
``cherrypick.<module>``) under one ``cherrypick.*`` root. The parent ``src/cherrypick/`` directory
deliberately has **no** ``__init__.py`` — it is a PEP 420 native namespace, which is what lets
separately-installed distributions share the root. This file, one level down, is an ordinary package
marker and is required.

The six strategies live in ``cherrypick.earnings.strategies``; the strategy-agnostic engine
(calendar, IV/RV, winrate, liquidity gates, ranking) is ``cherrypick.earnings.scanner``.
"""
